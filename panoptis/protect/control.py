"""Emergency controls: global kill switch and per-agent / per-session suspension.

Two channels, both checked before every action:

* the local :class:`panopti.killswitch.EmergencyKillSwitch` (a kill file and,
  on POSIX, ``SIGUSR1``): works with no database and no network;
* ``protect.control`` records in the ledger, so an operator can stop an agent running in
  another process or on another host from the dashboard or CLI.

Suspension stops an agent or a session from *acting*; it never terminates a process.
Suspensions are scoped to a tenant (``<tenant>/<agent>``, ``<tenant>/<session>``); the
global kill switch stops every tenant. Records are append-only, so the state is the fold
of the record sequence; it is refreshed incrementally (only records newer than the last
one seen) at most every ``refresh_s``.

The kill file is the shared state between processes on one host: it exists exactly while
the switch is tripped, so a reset by any process (which removes it) resumes all of them.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from panopti.killswitch import EmergencyKillSwitch
from panoptis.protect.ledger import KIND_CONTROL, ProtectLedger
from panoptis.storage.store import panoptis_home


def default_kill_file() -> Path:
    return Path(os.environ.get("AGENTWATCH_KILL_FILE") or panoptis_home() / "protect.kill")


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
        if target and scope in ("agent", "session"):
            target = f"{p.get('tenant_id') or 'default'}/{target}"
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
        # set when this process tripped the switch but could not write the kill file
        self._latched: str | None = None

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
        """The host-wide switch: the kill file (shared by every process), or a trip of this
        process that could not be written to it."""
        f = self.kill_switch.kill_file
        try:
            if f.exists():
                try:
                    reason = f.read_text(encoding="utf-8").strip()
                except OSError:
                    reason = ""
                return True, reason or f"kill file present: {f}"
        except OSError:
            return True, f"kill file not checkable: {f}"  # fail closed
        if self._latched is not None:
            return True, self._latched
        return False, None

    def emergency(self, tenant_id: str, agent_id: str, session_id: str) -> tuple[str, str] | None:
        """``(code, reason)`` when this agent/session must not act now, else None."""
        killed, reason = self.local_kill()
        snap = self.snapshot()
        if killed or snap.killed:
            return "KILL_SWITCH", f"Emergency stop is active: {reason or snap.kill_reason}"
        meta = snap.suspended_agents.get(f"{tenant_id}/{agent_id}")
        if meta is not None:
            return "AGENT_SUSPENDED", f"Agent '{agent_id}' is suspended: {meta.get('reason')}"
        meta = snap.suspended_sessions.get(f"{tenant_id}/{session_id}")
        if meta is not None:
            return "SESSION_SUSPENDED", f"Session '{session_id}' is suspended: {meta.get('reason')}"
        return None

    # ── operator actions (each is an evidence record) ──────────────────────────────
    def _command(
        self,
        action: str,
        scope: str,
        target: str | None,
        reason: str,
        actor: str,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        if not reason or not str(reason).strip():
            raise ValueError("a control action needs a reason")
        payload = {
            "action": action,
            "scope": scope,
            "target": target,
            "tenant_id": tenant_id if scope != "global" else None,
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
        """Stop every agent: the kill file (this host, immediately) and a ledger record
        (every process sharing the store, within ``refresh_s``)."""
        self.kill_switch.trip(reason)
        try:
            self.kill_switch.kill_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.kill_switch.kill_file.with_name(self.kill_switch.kill_file.name + ".tmp")
            tmp.write_text(reason, encoding="utf-8")
            os.replace(tmp, self.kill_switch.kill_file)
            self._latched = None
        except OSError:
            self._latched = reason  # still stop this process
        return self._command("trip", "global", None, reason, actor)

    def reset(self, reason: str = "operator reset", actor: str = "operator") -> dict[str, Any]:
        self.kill_switch.reset()  # removes the kill file: every process on the host resumes
        self._latched = None
        return self._command("reset", "global", None, reason, actor)

    def suspend_agent(
        self, agent_id: str, reason: str, actor: str = "operator", *, tenant_id: str = "default"
    ) -> dict[str, Any]:
        return self._command("suspend", "agent", agent_id, reason, actor, tenant_id)

    def resume_agent(
        self,
        agent_id: str,
        reason: str = "resumed",
        actor: str = "operator",
        *,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        return self._command("resume", "agent", agent_id, reason, actor, tenant_id)

    def suspend_session(
        self, session_id: str, reason: str, actor: str = "operator", *, tenant_id: str = "default"
    ) -> dict[str, Any]:
        return self._command("suspend", "session", session_id, reason, actor, tenant_id)

    def resume_session(
        self,
        session_id: str,
        reason: str = "resumed",
        actor: str = "operator",
        *,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        return self._command("resume", "session", session_id, reason, actor, tenant_id)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        """The control records, newest first (the audit trail)."""
        if self.ledger is None:
            return []
        return [
            {**r["payload"], "obs_id": r.get("obs_id"), "at": r.get("observed_at")}
            for r in self.ledger.query((KIND_CONTROL,), limit=limit)
        ]

    def status(self, tenant_id: str | None = None) -> dict[str, Any]:
        """The current state; with ``tenant_id``, only that tenant's suspensions."""
        snap = self.snapshot(force=True)
        local, local_reason = self.local_kill()
        doc = snap.to_dict()
        if tenant_id is not None:
            prefix = f"{tenant_id}/"
            for key in ("suspended_agents", "suspended_sessions"):
                doc[key] = {
                    k[len(prefix) :]: v for k, v in doc[key].items() if k.startswith(prefix)
                }
        return {
            **doc,
            "local_kill_file": str(self.kill_switch.kill_file),
            "local_killed": local,
            "local_kill_reason": local_reason,
        }
