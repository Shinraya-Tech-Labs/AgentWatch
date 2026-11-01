"""
MAG-002 — Deadlock Detector.

Detect agents waiting on each other. Build a wait-for graph and look for
cycles. Alert with the cycle members.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeadlockReport:
    deadlocked: bool
    cycle: list[str]
    detail: str


class DeadlockDetector:
    """
    Track 'agent A is waiting on agent B' relations and report cycles.
    """

    def __init__(self) -> None:
        # agent -> agent it is waiting on
        self._waiting: dict[str, str] = {}

    def set_wait(self, who: str, on: str) -> None:
        self._waiting[who] = on

    def clear_wait(self, who: str) -> None:
        self._waiting.pop(who, None)

    def scan(self) -> DeadlockReport:
        for start in self._waiting:
            seen: list[str] = []
            cur = start
            while cur in self._waiting:
                if cur in seen:
                    cycle = seen[seen.index(cur) :]
                    return DeadlockReport(
                        deadlocked=True,
                        cycle=cycle,
                        detail="wait-for cycle: " + " -> ".join(cycle + [cur]),
                    )
                seen.append(cur)
                cur = self._waiting[cur]
        return DeadlockReport(False, [], "no cycle detected")


__all__ = ["DeadlockDetector", "DeadlockReport"]
