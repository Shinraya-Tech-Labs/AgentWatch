"""Panopti Forensic Shadow Replay & Counterfactual Perturbation Engine.

Deterministic offline replay and counterfactual chaos testing for autonomous
agent traces and tool executions. Pure standard library (Ponytail: Ultra).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any


def _canonical_json(data: Any) -> str:
    """Serialize object to canonical, deterministic JSON."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(slots=True)
class RecordedCall:
    """Single recorded tool execution on the replay tape."""

    step_index: int
    tool_name: str
    arguments: dict[str, Any]
    result: Any
    timestamp: float = field(default_factory=time.time)
    error: str | None = None
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        payload = f"{self.tool_name}:{_canonical_json(self.arguments)}"
        object.__setattr__(
            self,
            "fingerprint",
            hashlib.sha256(payload.encode()).hexdigest(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordedCall:
        return cls(
            step_index=data["step_index"],
            tool_name=data["tool_name"],
            arguments=data.get("arguments", {}),
            result=data.get("result"),
            timestamp=data.get("timestamp", 0.0),
            error=data.get("error"),
        )


class ReplayTape:
    """Deterministic event and tool execution tape for offline replay."""

    def __init__(self, calls: list[RecordedCall] | None = None) -> None:
        self._calls: list[RecordedCall] = list(calls or [])

    def record(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        error: str | None = None,
    ) -> RecordedCall:
        call = RecordedCall(
            step_index=len(self._calls),
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            error=error,
        )
        self._calls.append(call)
        return call

    @property
    def calls(self) -> list[RecordedCall]:
        return list(self._calls)

    def __len__(self) -> int:
        return len(self._calls)

    def to_json(self) -> str:
        return json.dumps([c.to_dict() for c in self._calls], indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> ReplayTape:
        raw_items = json.loads(json_str)
        calls = [RecordedCall.from_dict(item) for item in raw_items]
        return cls(calls)


class Perturbation:
    """Base class for counterfactual trace perturbations."""

    def apply(self, step_index: int, call: RecordedCall) -> RecordedCall:
        return call


class DropStepPerturbation(Perturbation):
    """Simulates an unhandled dropped call or failure at a target step."""

    def __init__(self, target_step: int) -> None:
        self.target_step = target_step

    def apply(self, step_index: int, call: RecordedCall) -> RecordedCall:
        if step_index == self.target_step:
            return RecordedCall(
                step_index=step_index,
                tool_name=call.tool_name,
                arguments=call.arguments,
                result=None,
                error=f"SimulatedTimeoutError: Step {step_index} dropped by chaos perturbation",
            )
        return call


class MutateResultPerturbation(Perturbation):
    """Simulates unexpected or corrupted tool output at a target step."""

    def __init__(
        self,
        target_step: int,
        mutator: Callable[[Any], Any],
    ) -> None:
        self.target_step = target_step
        self.mutator = mutator

    def apply(self, step_index: int, call: RecordedCall) -> RecordedCall:
        if step_index == self.target_step:
            return RecordedCall(
                step_index=step_index,
                tool_name=call.tool_name,
                arguments=call.arguments,
                result=self.mutator(call.result),
                error=call.error,
            )
        return call


@dataclass(slots=True)
class ReplayReport:
    """Outcome report of a deterministic shadow replay execution."""

    total_steps: int
    matched_steps: int
    divergence_step: int | None
    divergence_reason: str | None
    is_congruent: bool
    perturbations_applied: int


class ShadowReplayer:
    """Offline deterministic runner that verifies replay congruence."""

    def __init__(
        self,
        tape: ReplayTape,
        perturbations: list[Perturbation] | None = None,
    ) -> None:
        self._tape = tape
        self._perturbations = perturbations or []
        self._cursor = 0

    def step(self, tool_name: str, arguments: dict[str, Any]) -> tuple[Any, str | None]:
        """Fetch recorded or perturbed tool outcome for the next step.

        Raises:
            IndexError: If re-execution attempts more steps than recorded.
        """
        if self._cursor >= len(self._tape):
            raise IndexError(
                f"Replay exhausted: step {self._cursor} beyond tape length {len(self._tape)}"
            )

        recorded = self._tape.calls[self._cursor]
        current_step = self._cursor
        self._cursor += 1

        for p in self._perturbations:
            recorded = p.apply(current_step, recorded)

        expected_fp = recorded.fingerprint
        actual_payload = f"{tool_name}:{_canonical_json(arguments)}"
        actual_fp = hashlib.sha256(actual_payload.encode()).hexdigest()

        if actual_fp != expected_fp:
            return recorded.result, (
                f"Divergence at step {current_step}: expected tool '{recorded.tool_name}' "
                f"with args {recorded.arguments}, got '{tool_name}' with args {arguments}"
            )

        return recorded.result, recorded.error
