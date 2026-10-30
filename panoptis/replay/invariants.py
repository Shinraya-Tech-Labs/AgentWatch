"""Invariant declaration and falsification engine.

Allows declared invariants to be evaluated against replay outcomes and reports
which invariant failed, at which step, and why.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus


@dataclass(slots=True)
class InvariantResult:
    """Outcome of evaluating a single declared invariant."""

    name: str
    passed: bool
    failed_at_step: int | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "failed_at_step": self.failed_at_step,
            "reason": self.reason,
            "details": self.details,
        }


EvaluatorFn = Callable[[list[AgentEvent]], tuple[bool, str | None, int | None] | InvariantResult]


@dataclass(slots=True)
class Invariant:
    """A declared behavioral or structural invariant."""

    name: str
    evaluator: EvaluatorFn
    description: str = ""

    def evaluate(self, events: list[AgentEvent]) -> InvariantResult:
        try:
            res = self.evaluator(events)
        except Exception as exc:
            return InvariantResult(
                name=self.name,
                passed=False,
                failed_at_step=len(events) - 1 if events else None,
                reason=f"Invariant evaluator raised exception: {type(exc).__name__}: {exc}",
            )
        if isinstance(res, InvariantResult):
            return res
        passed, reason, failed_step = res
        return InvariantResult(
            name=self.name,
            passed=passed,
            failed_at_step=failed_step,
            reason=reason if not passed else None,
        )


@dataclass(slots=True)
class InvariantFalsificationReport:
    """Report detailing invariant evaluation and any falsification."""

    falsified: bool
    total_evaluated: int
    passed_count: int
    falsified_count: int
    failed_invariants: list[InvariantResult] = field(default_factory=list)
    passed_invariants: list[InvariantResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "falsified": self.falsified,
            "total_evaluated": self.total_evaluated,
            "passed_count": self.passed_count,
            "falsified_count": self.falsified_count,
            "failed_invariants": [f.to_dict() for f in self.failed_invariants],
            "passed_invariants": [p.to_dict() for p in self.passed_invariants],
            "summary": self.summary,
        }


def evaluate_invariants(
    events: list[AgentEvent],
    invariants: Iterable[Invariant],
) -> InvariantFalsificationReport:
    """Evaluate declared invariants against an event trajectory."""
    passed: list[InvariantResult] = []
    failed: list[InvariantResult] = []

    for inv in invariants:
        res = inv.evaluate(events)
        if res.passed:
            passed.append(res)
        else:
            failed.append(res)

    is_falsified = len(failed) > 0
    if is_falsified:
        fail_msgs = [f"'{f.name}' at step {f.failed_at_step}: {f.reason}" for f in failed]
        summary = f"Falsified {len(failed)}/{len(passed) + len(failed)} invariants: " + "; ".join(
            fail_msgs
        )
    else:
        summary = f"All {len(passed)} declared invariants passed."

    return InvariantFalsificationReport(
        falsified=is_falsified,
        total_evaluated=len(passed) + len(failed),
        passed_count=len(passed),
        falsified_count=len(failed),
        failed_invariants=failed,
        passed_invariants=passed,
        summary=summary,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Built-in Invariant Factories
# ─────────────────────────────────────────────────────────────────────────────


def no_tool_errors_invariant(name: str = "no_tool_errors") -> Invariant:
    """Invariant: no tool result in the trajectory contains an error or failure status."""

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        for idx, ev in enumerate(events):
            if ev.event_type == EventType.TOOL_ERROR or ev.status == ExecutionStatus.FAILURE:
                err_msg = (
                    ev.tool_result.error if ev.tool_result else "Tool execution marked failure"
                )
                return False, f"Tool error encountered: {err_msg}", idx
            if ev.tool_result and ev.tool_result.error:
                return False, f"Tool result error: {ev.tool_result.error}", idx
        return True, None, None

    return Invariant(name=name, evaluator=_eval, description="Ensure no tool errors occur")


def max_steps_invariant(max_steps: int, name: str = "max_steps") -> Invariant:
    """Invariant: total trajectory length must not exceed max_steps."""
    if max_steps < 0:
        raise ValueError("max_steps must be non-negative")

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        if len(events) > max_steps:
            return (
                False,
                f"Trajectory length {len(events)} exceeded maximum limit of {max_steps}",
                max_steps,
            )
        return True, None, None

    return Invariant(name=name, evaluator=_eval, description=f"Ensure steps <= {max_steps}")


def forbidden_tools_invariant(
    forbidden: Iterable[str],
    name: str = "forbidden_tools",
) -> Invariant:
    """Invariant: agent must never invoke any forbidden tool."""
    forbidden_set = set(forbidden)

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        for idx, ev in enumerate(events):
            tool = ev.tool_call.tool_name if ev.tool_call else None
            if tool in forbidden_set:
                return False, f"Forbidden tool '{tool}' invoked", idx
        return True, None, None

    return Invariant(
        name=name, evaluator=_eval, description=f"Forbidden tools: {sorted(forbidden_set)}"
    )


def required_tools_invariant(
    required: Iterable[str],
    name: str = "required_tools",
) -> Invariant:
    """Invariant: agent must invoke all required tools at least once."""
    required_set = set(required)

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        invoked = {ev.tool_call.tool_name for ev in events if ev.tool_call}
        missing = required_set - invoked
        if missing:
            return (
                False,
                f"Required tool(s) not invoked: {sorted(missing)}",
                len(events) - 1 if events else 0,
            )
        return True, None, None

    return Invariant(
        name=name, evaluator=_eval, description=f"Required tools: {sorted(required_set)}"
    )


def output_schema_invariant(
    required_keys: Iterable[str],
    name: str = "output_schema",
) -> Invariant:
    """Invariant: final output must be structured and contain all required keys."""
    req_keys_set = set(required_keys)

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        out_val = None
        for ev in reversed(events):
            if ev.planner_output_preview is not None:
                out_val = ev.planner_output_preview
                break
            if ev.tool_result and ev.tool_result.output is not None:
                out_val = ev.tool_result.output
                break

        if out_val is None:
            return False, "No output produced in trajectory", len(events) - 1 if events else 0

        data = None
        if isinstance(out_val, dict):
            data = out_val
        elif isinstance(out_val, str):
            try:
                parsed = json.loads(out_val)
                if isinstance(parsed, dict):
                    data = parsed
            except (json.JSONDecodeError, TypeError):
                data = None

        if data is None:
            return (
                False,
                f"Output is not a structured dictionary: {type(out_val).__name__}",
                len(events) - 1,
            )

        missing = req_keys_set - set(data.keys())
        if missing:
            return False, f"Missing required keys in output: {sorted(missing)}", len(events) - 1
        return True, None, None

    return Invariant(
        name=name, evaluator=_eval, description=f"Output must have keys: {sorted(req_keys_set)}"
    )


def no_safety_blocks_invariant(name: str = "no_safety_blocks") -> Invariant:
    """Invariant: no event was blocked or flagged critical by the safety engine."""

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        for idx, ev in enumerate(events):
            if ev.is_blocked:
                return False, "Action blocked by safety engine", idx
            if ev.is_dangerous:
                return (
                    False,
                    f"Dangerous risk level: {ev.safety.risk_level.value if ev.safety else 'high'}",
                    idx,
                )
        return True, None, None

    return Invariant(
        name=name, evaluator=_eval, description="Ensure zero safety blocks or critical risks"
    )


def custom_invariant(
    name: str,
    predicate: Callable[[list[AgentEvent]], bool],
    failure_reason: str = "Custom invariant violated",
    description: str = "",
) -> Invariant:
    """Create an invariant from an arbitrary boolean predicate."""

    def _eval(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        try:
            ok = predicate(events)
            if not ok:
                return False, failure_reason, len(events) - 1 if events else 0
            return True, None, None
        except Exception as exc:
            return False, f"{failure_reason} (exception: {exc})", len(events) - 1 if events else 0

    return Invariant(name=name, evaluator=_eval, description=description or failure_reason)


__all__ = [
    "EvaluatorFn",
    "Invariant",
    "InvariantFalsificationReport",
    "InvariantResult",
    "custom_invariant",
    "evaluate_invariants",
    "forbidden_tools_invariant",
    "max_steps_invariant",
    "no_safety_blocks_invariant",
    "no_tool_errors_invariant",
    "output_schema_invariant",
    "required_tools_invariant",
]
