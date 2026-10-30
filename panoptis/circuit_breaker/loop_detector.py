"""Sliding-window loop and parameter oscillation detector for agent tool calls.

Catches runaway agents trapped in correction death spirals, repeating identical
tool calls or cycling between alternating parameters (e.g. A -> B -> A -> B).
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any


class AgentLoopError(Exception):
    """Raised when an agent is caught in a repetitive tool-call or oscillation loop."""

    def __init__(self, message: str, pattern: str, occurrences: int) -> None:
        super().__init__(message)
        self.pattern = pattern
        self.occurrences = occurrences


# Backward-compatible alias
AgentLoopException = AgentLoopError


@dataclass(frozen=True)
class ToolCallFingerprint:
    """Canonical representation of a single tool call."""

    tool_name: str
    args_hash: str
    raw_hash: str


class LoopDetector:
    """Sliding-window detector for repetitive tool calls and parameter oscillations."""

    def __init__(
        self,
        window_size: int = 10,
        max_repeats: int = 3,
        max_oscillations: int = 2,
    ) -> None:
        if window_size < 2:
            raise ValueError("window_size must be >= 2")
        if max_repeats < 2:
            raise ValueError("max_repeats must be >= 2")
        if max_oscillations < 2:
            raise ValueError("max_oscillations must be >= 2")

        self.window_size = window_size
        self.max_repeats = max_repeats
        self.max_oscillations = max_oscillations
        self._history: deque[ToolCallFingerprint] = deque(maxlen=window_size)

    @staticmethod
    def canonical_fingerprint(
        tool_name: str, arguments: dict[str, Any] | Any
    ) -> ToolCallFingerprint:
        """Produce a canonical SHA-256 fingerprint for a tool name and argument dictionary."""
        try:
            canonical_json = json.dumps(arguments, sort_keys=True, default=str)
        except Exception:
            canonical_json = str(arguments)

        args_hash = hashlib.sha256(canonical_json.encode()).hexdigest()[:16]
        raw_hash = hashlib.sha256(f"{tool_name}:{canonical_json}".encode()).hexdigest()[:16]
        return ToolCallFingerprint(
            tool_name=tool_name,
            args_hash=args_hash,
            raw_hash=raw_hash,
        )

    def record(self, tool_name: str, arguments: dict[str, Any] | Any) -> str | None:
        """Record a tool invocation and evaluate for loops.

        Returns:
            A descriptive warning string if a loop is detected, or None if healthy.
        """
        fp = self.canonical_fingerprint(tool_name, arguments)
        self._history.append(fp)
        return self.check_traps()

    def check_traps(self) -> str | None:
        """Evaluate history for identical repetitions or periodic parameter oscillations."""
        min_required = min(self.max_repeats, 2 * self.max_oscillations)
        if len(self._history) < min_required:
            return None

        recent = list(self._history)
        last_fp = recent[-1]

        # 1. Repetition Trap: Check for consecutive identical invocations
        consecutive = 0
        for item in reversed(recent):
            if item.raw_hash == last_fp.raw_hash:
                consecutive += 1
            else:
                break

        if consecutive >= self.max_repeats:
            return (
                f"Repetitive loop detected: tool '{last_fp.tool_name}' invoked "
                f"{consecutive} times with identical parameters"
            )

        # 2. Oscillation Trap: Check for periodic cycles (period >= 2)
        n = len(recent)
        max_period = min(n // self.max_oscillations, 5)
        for period in range(2, max_period + 1):
            slice_len = period * self.max_oscillations
            if n >= slice_len:
                sub = recent[-slice_len:]
                pattern = sub[:period]
                matches = True
                for i in range(period, slice_len):
                    if sub[i].raw_hash != pattern[i % period].raw_hash:
                        matches = False
                        break
                if matches:
                    pattern_tools = " -> ".join(p.tool_name for p in pattern)
                    return (
                        f"Parameter oscillation detected: periodic cycle ({pattern_tools}) "
                        f"repeated {self.max_oscillations} times"
                    )

        return None

    def reset(self) -> None:
        """Clear the observation window."""
        self._history.clear()
