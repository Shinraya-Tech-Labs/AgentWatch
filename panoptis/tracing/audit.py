"""
OBS-006 — Tool Call Audit Log.

Records every tool call with:
    name, args, response, duration, retry count.
Surfaces:
    - hallucinated arguments (refer to nonexistent things)
    - silent retry storms (3+ retries for the same args)
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from panoptis.core.schema import AgentEvent, EventType

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    timestamp: str
    session_id: str
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
    raw_command: str | None
    response: Any = None
    error: str | None = None
    duration_ms: float | None = None
    retry_count: int = 0
    hallucination_flags: list[str] = field(default_factory=list)


# Heuristics for "obviously invented" arguments
_INVENTED_PATTERNS = (
    re.compile(r"/(tmp|private)/very_likely_to_not_exist", re.I),
    re.compile(r"\b(my_secret|fake_key|placeholder|TODO_REPLACE)\b", re.I),
    re.compile(r"\$\{[A-Z_]+\}"),
)


def detect_hallucinated_arguments(arguments: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    blob = repr(arguments)
    for pat in _INVENTED_PATTERNS:
        if pat.search(blob):
            flags.append(f"matches_pattern:{pat.pattern}")
    # Empty mandatory-looking args
    for key, val in arguments.items():
        if val in (None, "", []):
            flags.append(f"empty:{key}")
    return flags


class ToolAuditLog:
    """Append-only audit log of every tool call observed."""

    def __init__(self, retry_storm_threshold: int = 3) -> None:
        self._entries: list[AuditEntry] = []
        self._pending: dict[str, AuditEntry] = {}  # tool_id → entry awaiting result
        self._retry_counter: dict[tuple[str, str], int] = defaultdict(int)
        self.retry_storm_threshold = retry_storm_threshold

    def ingest(self, event: AgentEvent) -> AuditEntry | None:
        if event.event_type == EventType.TOOL_CALL and event.tool_call:
            tc = event.tool_call
            entry = AuditEntry(
                timestamp=datetime.now(UTC).isoformat(),
                session_id=event.session_id,
                tool_id=tc.tool_id or event.event_id,
                tool_name=tc.tool_name,
                arguments=tc.arguments,
                raw_command=tc.raw_command,
                hallucination_flags=detect_hallucinated_arguments(tc.arguments),
            )
            self._entries.append(entry)
            self._pending[entry.tool_id] = entry

            # retry counter — same (tool, args-signature)
            sig = (tc.tool_name, repr(sorted(tc.arguments.items())))
            self._retry_counter[sig] += 1
            entry.retry_count = self._retry_counter[sig]
            return entry

        if event.event_type in (EventType.TOOL_RESULT, EventType.TOOL_ERROR):
            tr = event.tool_result
            if tr is None:
                return None
            tool_id = tr.tool_id or ""
            matched = self._pending.pop(tool_id) if tool_id in self._pending else None
            if matched is None:
                # Unmatched — create a standalone record
                matched = AuditEntry(
                    timestamp=datetime.now(UTC).isoformat(),
                    session_id=event.session_id,
                    tool_id=tool_id,
                    tool_name=tr.tool_name,
                    arguments={},
                    raw_command=None,
                )
                self._entries.append(matched)
            entry = matched
            entry.response = tr.output
            entry.error = tr.error
            entry.duration_ms = tr.execution_time_ms
            return entry

        return None

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def retry_storms(self) -> list[tuple[str, str, int]]:
        """Return (tool_name, args_signature, count) for any storm."""
        return [
            (tool, sig, count)
            for (tool, sig), count in self._retry_counter.items()
            if count >= self.retry_storm_threshold
        ]

    def hallucinated_calls(self) -> list[AuditEntry]:
        return [e for e in self._entries if e.hallucination_flags]

    def to_jsonl(self) -> str:
        import json

        return "\n".join(json.dumps(asdict(e), default=str) for e in self._entries)


__all__ = ["AuditEntry", "ToolAuditLog", "detect_hallucinated_arguments"]
