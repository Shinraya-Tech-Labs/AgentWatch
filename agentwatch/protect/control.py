"""Emergency controls: global kill switch and per-agent / per-session suspension.

Two channels, both checked before every action:

* the local :class:`panopti.killswitch.EmergencyKillSwitch` (a kill-file sentinel and,
  on POSIX, ``SIGUSR1``): works with no database and no network;
* ``protect.control`` records in the ledger, so an operator can stop an agent running in
  another process or on another host from the dashboard or CLI.

Suspension stops an agent or a session from *acting*; it never terminates a process.
Records are append-only, so the state is the fold of the record sequence; it is refreshed
incrementally (only records newer than the last one seen) at most every ``refresh_s``.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentwatch.protect.ledger import KIND_CONTROL, ProtectLedger
from agentwatch.storage.store import agentwatch_home
from panopti.killswitch import EmergencyKillSwitch


def default_kill_file() -> Path:
    return Path(os.environ.get("AGENTWATCH_KILL_FILE") or agentwatch_home() / "protect.kill")


@dataclass
class ControlSnapshot:
    killed: bool = False
    kill_reason: str | None = None
    kill_actor: str | None = None
    killed_at: str | None = None
    suspended_agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    suspended_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "kill_actor": self.kill_actor,
            "killed_at": self.killed_at,
            "suspended_agents": self.suspended_agents,
            "suspended_sessions": self.suspended_sessions,
        }


def fold(records: list[dict[str, Any]], snap: ControlSnapshot | None = None) -> ControlSnapshot:
    """Apply control records (oldest first) to a snapshot."""
    snap = snap or ControlSnapshot()
    for r in records:
        p = r["payload"]
        action, scope, target = p.get("action"), p.get("scope", "global"), p.get("target")
        meta = {
            "reason": p.get("reason"),
            "actor": p.get("actor"),
            "at": r.get("observed_at"),
            "obs_id": r.get("obs_id"),
        }
        if scope == "global" and action == "trip":
            snap.killed, snap.kill_reason, snap.kill_actor, snap.killed_at = (
                True,
                p.get("reason"),
                p.get("actor"),
                r.get("observed_at"),
            )
        elif scope == "global" and action == "reset":
            snap.killed, snap.kill_reason, snap.kill_actor, snap.killed_at = False, None, None, None
        elif scope == "agent" and target:
            if action == "suspend":
                snap.suspended_agents[target] = meta
            elif action == "resume":
                snap.suspended_agents.pop(target, None)
        elif scope == "session" and target:
            if action == "suspend":
                snap.suspended_sessions[target] = meta
            elif action == "resume":
                snap.suspended_sessions.pop(target, None)
    return snap


class ControlState:
    def __init__(
        self,
        ledger: ProtectLedger | None = None,
        *,
        kill_switch: EmergencyKillSwitch | None = None,
        refresh_s: float = 1.0,
    ) -> None:
        self.ledger = ledger
        self.kill_switch = kill_switch or EmergencyKillSwitch(
            kill_file=default_kill_file(), enable_signals=False
        )
        self.refresh_s = refresh_s
        self._snap = ControlSnapshot()
        self._last_obs: str | None = None
        self._refreshed = 0.0
        self._lock = threading.Lock()

    def snapshot(self, *, force: bool = False) -> ControlSnapshot:
        if self.ledger is not None and (
            force or time.monotonic() - self._refreshed >= self.refresh_s
        ):
            with self._lock:
                new = self.ledger.query(
                    (KIND_CONTROL,), after=self._last_obs, limit=10_000, newest_first=False
                )
                if new:
                    self._snap = fold(new, self._snap)
                    self._last_obs = new[-1]["obs_id"]
                self._refreshed = time.monotonic()
        return self._snap

    def local_kill(self) -> tuple[bool, str | None]:
        if self.kill_switch.is_tripped():
            return True, self.kill_switch.reason
        return False, None

    # ── operator actions (each is an evidence record) ──────────────────────────────
    def _command(
        self, action: str, scope: str, target: str | None, reason: str, actor: str
    ) -> dict[str, Any]:
        payload = {
            "action": action,
            "scope": scope,
            "target": target,
            "reason": reason,
            "actor": actor,
        }
        obs_id = None
        if self.ledger is not None:
            obs_id = self.ledger.record(
                KIND_CONTROL,
                payload,
                {
                    "control_scope": scope,
                    "control_target": target,
                    "agent_id": target if scope == "agent" else None,
                    "session_id": target if scope == "session" else None,
                },
            )
        self.snapshot(force=True)
        return {**payload, "obs_id": obs_id}

    def trip(self, reason: str, actor: str = "operator") -> dict[str, Any]:
        self.kill_switch.trip(reason)
        try:
            self.kill_switch.kill_file.parent.mkdir(parents=True, exist_ok=True)
            self.kill_switch.kill_file.write_text(reason, encoding="utf-8")
        except OSError:
            pass
        return self._command("trip", "global", None, reason, actor)

    def reset(self, reason: str = "operator reset", actor: str = "operator") -> dict[str, Any]:
        self.kill_switch.reset()
        return self._command("reset", "global", None, reason, actor)

    def suspend_agent(self, agent_id: str, reason: str, actor: str = "operator") -> dict[str, Any]:
        return self._command("suspend", "agent", agent_id, reason, actor)

    def resume_agent(
        self, agent_id: str, reason: str = "resumed", actor: str = "operator"
    ) -> dict[str, Any]:
        return self._command("resume", "agent", agent_id, reason, actor)

    def suspend_session(
        self, session_id: str, reason: str, actor: str = "operator"
    ) -> dict[str, Any]:
        return self._command("suspend", "session", session_id, reason, actor)

    def resume_session(
        self, session_id: str, reason: str = "resumed", actor: str = "operator"
    ) -> dict[str, Any]:
        return self._command("resume", "session", session_id, reason, actor)

    def status(self) -> dict[str, Any]:
        snap = self.snapshot(force=True)
        local, local_reason = self.local_kill()
        return {
            **snap.to_dict(),
            "local_kill_file": str(self.kill_switch.kill_file),
            "local_killed": local,
            "local_kill_reason": local_reason,
        }
