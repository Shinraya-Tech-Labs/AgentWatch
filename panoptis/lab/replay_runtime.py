"""In-process replay controller (runs inside the re-executed program).

Instrumented calls (``@aw.tool``, ``@aw.model``, ``@aw.retriever``) consult this
controller. For each call, identified by its call key ``kind|operation|ordinal``:

* a *substitution* for that key → the substituted value is returned (intervention);
* the operation is in ``live``      → the real function runs (partial re-execution);
* a *capture* exists               → the captured result is returned, or the captured
                                     exception re-raised (mock replay);
* a capture whose recorded input differs from the new input is *stale* and is not served;
* otherwise                        → L3: run live and record a capture miss;
                                     L2: raise :class:`ReplayDivergenceError`.

A report of what was mocked, substituted, run live or missing is written on exit.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ReplayDivergenceError(RuntimeError):
    """The re-executed program made a call that the original run never made."""


@dataclass
class ReplayController:
    level: str = "L2"
    captures: dict[str, dict[str, Any]] = field(default_factory=dict)
    substitutions: dict[str, Any] = field(default_factory=dict)
    live: set[str] = field(default_factory=set)
    report_file: str | None = None
    served: list[str] = field(default_factory=list)
    substituted: list[str] = field(default_factory=list)
    ran_live: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unfaithful: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str) -> ReplayController:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        ctl = cls(
            level=spec.get("level", "L2"),
            captures=spec.get("captures", {}),
            substitutions=spec.get("substitutions", {}),
            live=set(spec.get("live", [])),
            report_file=spec.get("report_file"),
        )
        global _active
        _active = ctl
        return ctl

    def serve(self, span: Any) -> tuple[bool, Any]:
        key = span.call_key
        if key is None:
            return False, None
        if key in self.substitutions:
            self.substituted.append(key)
            span.set(replay="substituted")
            return True, self.substitutions[key]
        if span.operation in self.live or f"{span.kind}|{span.operation}" in self.live:
            self.ran_live.append(key)
            span.set(replay="live")
            return False, None
        cap = self.captures.get(key)
        if cap is not None and cap.get("input_sha") and span.inputs:
            from panoptis.evidence.canonical import canonical_json, sha256_hex

            if sha256_hex(canonical_json(span.inputs[0].get("value"))) != cap["input_sha"]:
                # the capture was recorded for different input: serving it would hide the change
                self.stale.append(key)
                cap = None
        if cap is not None:
            if not cap.get("faithful", True):
                self.unfaithful.append(key)
            span.set(replay="mocked")
            self.served.append(key)
            if cap.get("status") in ("ERROR", "TIMEOUT"):
                err = cap.get("error") or {}
                exc_type = getattr(builtins, str(err.get("type")), None)
                if isinstance(exc_type, type) and issubclass(exc_type, Exception):
                    raise exc_type(err.get("message", "replayed failure"))
                raise RuntimeError(f"{err.get('type')}: {err.get('message')}")
            return True, cap.get("value")
        self.missing.append(key)
        if self.level == "L3":
            self.ran_live.append(key)
            span.set(replay="live_capture_miss")
            return False, None
        span.set(replay="divergence")
        raise ReplayDivergenceError(
            f"no captured result for {key}; the replayed program diverged from the original run"
        )

    def report(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "served": self.served,
            "substituted": self.substituted,
            "ran_live": self.ran_live,
            "missing": self.missing,
            "unfaithful_values": self.unfaithful,
            "stale_captures": self.stale,
        }


_active: ReplayController | None = None


def finish_controller() -> None:
    if _active is not None and _active.report_file:
        Path(_active.report_file).write_text(json.dumps(_active.report()), encoding="utf-8")
