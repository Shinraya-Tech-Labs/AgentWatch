"""
SAF-0XX — Cognitive Recursion Depth Detector.

Detects excessive self-referential reasoning patterns that may indicate
the agent is stuck in a cognitive loop.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, EventType

DEFAULT_BUFFER_SIZE = 10
DEFAULT_THRESHOLD = 5

DEFAULT_PATTERNS = [
    r"i think i should",
    r"let me reconsider",
    r"i was wrong(?: about)?",
    r"maybe i should",
    r"let me rethink",
]


@dataclass
class RecursionDepthReport:
    detected: bool
    matches: int
    matched_patterns: list[str]


class RecursionDepthDetector:
    def __init__(
        self,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        threshold: int = DEFAULT_THRESHOLD,
        patterns: list[str] | None = None,
    ):
        self.threshold = threshold
        self.patterns = [re.compile(p, re.IGNORECASE) for p in (patterns or DEFAULT_PATTERNS)]
        self.buffer = deque(maxlen=buffer_size)

    def observe(self, event: AgentEvent) -> RecursionDepthReport:
        if event.event_type != EventType.PLANNER_OUTPUT:
            return RecursionDepthReport(False, 0, [])

        text = event.planner_output_preview or ""
        self.buffer.append(text)

        return self._scan()

    def _scan(self) -> RecursionDepthReport:
        count = 0
        matched = []

        for text in self.buffer:
            for pattern in self.patterns:
                if pattern.search(text):
                    count += 1
                    matched.append(pattern.pattern)

        return RecursionDepthReport(
            detected=count > self.threshold,
            matches=count,
            matched_patterns=matched,
        )

    def reset(self):
        self.buffer.clear()


__all__ = [
    "RecursionDepthDetector",
    "RecursionDepthReport",
]
