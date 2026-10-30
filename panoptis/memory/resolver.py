"""
MEM-004 — Memory Conflict Resolver.

When two memories contradict, score them by trust and recency.
Returns the authoritative assertion plus the rejected entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class MemoryEntry:
    key: str
    value: Any
    trust: float = 0.5  # 0..1
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Resolution:
    winner: MemoryEntry
    rejected: list[MemoryEntry]
    rationale: str


def resolve(entries: list[MemoryEntry], *, recency_weight: float = 0.6) -> Resolution:
    if not entries:
        raise ValueError("no entries to resolve")
    if len(entries) == 1:
        return Resolution(winner=entries[0], rejected=[], rationale="only_one_entry")

    # Normalize recency on [0, 1]
    now = datetime.now(UTC).timestamp()
    timestamps = [e.timestamp.timestamp() for e in entries]
    span = max(timestamps) - min(timestamps) or 1.0

    def composite(e: MemoryEntry) -> float:
        recency = 1.0 - ((now - e.timestamp.timestamp()) / max(span, 1.0))
        return e.trust * (1 - recency_weight) + recency * recency_weight

    ranked = sorted(entries, key=composite, reverse=True)
    winner, *rest = ranked
    return Resolution(
        winner=winner,
        rejected=rest,
        rationale=f"trust×{1 - recency_weight:.2f} + recency×{recency_weight:.2f}",
    )


class MemoryConflictResolver:
    """Statefully accumulate entries by key and resolve on read."""

    def __init__(self, recency_weight: float = 0.6):
        self.recency_weight = recency_weight
        self._entries: dict[str, list[MemoryEntry]] = {}

    def add(self, entry: MemoryEntry) -> None:
        self._entries.setdefault(entry.key, []).append(entry)

    def get(self, key: str) -> Resolution | None:
        entries = self._entries.get(key)
        if not entries:
            return None
        return resolve(entries, recency_weight=self.recency_weight)

    def keys(self) -> list[str]:
        return list(self._entries.keys())


__all__ = ["MemoryEntry", "Resolution", "MemoryConflictResolver", "resolve"]
