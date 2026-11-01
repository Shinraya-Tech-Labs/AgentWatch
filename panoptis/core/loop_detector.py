"""
SAF-007 — Recursive Loop Detector.

Detect infinite agent loops. Alert and break loop automatically.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, EventType

DEFAULT_LOOP_THRESHOLD = 3


def _get_loop_threshold() -> int:
    value = os.getenv("AGENTWATCH_LOOP_THRESHOLD")

    if value is None:
        return DEFAULT_LOOP_THRESHOLD

    try:
        threshold = int(value)
    except ValueError:
        return DEFAULT_LOOP_THRESHOLD

    if threshold < 1:
        return DEFAULT_LOOP_THRESHOLD

    return threshold


@dataclass
class LoopReport:
    detected: bool
    cycle_length: int
    repetitions: int
    sample: list[str]


class LoopDetector:
    """
    Maintain a sliding window of action signatures. When the same sequence
    appears N times back-to-back, flag a loop.
    """

    def __init__(self, window: int = 50, min_cycle: int = 1, min_reps: int | None = None):
        self.window = window
        self.min_cycle = min_cycle
        self.min_reps = min_reps if min_reps is not None else _get_loop_threshold()
        if self.min_reps < 1:
            raise ValueError(f"min_reps must be >= 1, got {self.min_reps}")
        self._buffer: deque[str] = deque(maxlen=window)

    def signature_of(self, event: AgentEvent) -> str:
        if event.tool_call:
            return (
                f"{event.tool_call.tool_name}:"
                + repr(sorted(event.tool_call.arguments.items()))[:120]
            )
        return event.event_type.value

    def observe(self, event: AgentEvent) -> LoopReport:
        if event.event_type not in (EventType.TOOL_CALL, EventType.PLANNER_OUTPUT):
            return LoopReport(False, 0, 0, [])
        sig = self.signature_of(event)
        self._buffer.append(sig)
        return self._scan()

    def _scan(self) -> LoopReport:
        buf = list(self._buffer)
        n = len(buf)
        for cycle in range(self.min_cycle, min(n // self.min_reps + 1, 10)):
            tail = buf[-cycle * self.min_reps :]
            if len(tail) < cycle * self.min_reps:
                continue
            chunks = [tuple(tail[i : i + cycle]) for i in range(0, len(tail), cycle)]
            if all(c == chunks[0] for c in chunks):
                return LoopReport(
                    detected=True,
                    cycle_length=cycle,
                    repetitions=len(chunks),
                    sample=list(chunks[0]),
                )
        return LoopReport(False, 0, 0, [])

    def reset(self) -> None:
        self._buffer.clear()


__all__ = ["LoopDetector", "LoopReport"]
