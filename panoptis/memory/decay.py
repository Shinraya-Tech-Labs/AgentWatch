"""
MEM-005 — Forgetting Curve Engine.

Importance-weighted decay. Critical memories never decay. Routine memories
fade following an Ebbinghaus-style exponential.

ForgettingEngine provides both a standalone key-value decay store and
memory-entry integration (strength, refresh, prunable selection) that
replaces the former TemporalDecayManager.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class Importance(str, Enum):
    CRITICAL = "critical"  # never decays
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


_IMPORTANCE_HALFLIFE_DAYS = {
    Importance.CRITICAL: float("inf"),
    Importance.HIGH: 365.0,
    Importance.NORMAL: 30.0,
    Importance.LOW: 7.0,
}

# Map engine importance levels onto the forgetting-curve half-lives.
_ENGINE_IMPORTANCE_MAP: dict[str, Importance] = {}  # populated lazily


def _engine_importance_map() -> dict[str, Importance]:
    """Build the ImportanceLevel -> Importance mapping (lazy to avoid circular import)."""
    if _ENGINE_IMPORTANCE_MAP:
        return _ENGINE_IMPORTANCE_MAP
    from panoptis.memory.engine import ImportanceLevel

    _ENGINE_IMPORTANCE_MAP.update(
        {
            ImportanceLevel.LOW: Importance.LOW,
            ImportanceLevel.MEDIUM: Importance.NORMAL,
            ImportanceLevel.HIGH: Importance.HIGH,
            ImportanceLevel.CRITICAL: Importance.CRITICAL,
        }
    )
    return _ENGINE_IMPORTANCE_MAP


def strength_at(
    importance: Importance,
    last_accessed: datetime,
    access_count: int = 0,
    now: datetime | None = None,
) -> float:
    """Exponential forgetting-curve strength in ``[0.0, 1.0]``.

    Importance sets the half-life; CRITICAL memories never decay. Each prior
    access adds a small rehearsal boost so frequently-used memories resist
    forgetting. This is the single source of truth for the decay curve.
    """
    now = now or datetime.now(UTC)
    halflife = _IMPORTANCE_HALFLIFE_DAYS[importance]
    if halflife == float("inf"):
        return 1.0
    age_days = (now - last_accessed).total_seconds() / 86400
    base = math.exp(-math.log(2) * age_days / halflife)
    # Each access slightly boosts strength (rehearsal effect)
    boost = min(0.5, 0.05 * access_count)
    return min(1.0, base + boost)


@dataclass
class DecayingMemory:
    key: str
    value: object
    importance: Importance = Importance.NORMAL
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(UTC))
    access_count: int = 0

    def strength(self, now: datetime | None = None) -> float:
        return strength_at(self.importance, self.last_accessed, self.access_count, now)


class ForgettingEngine:
    """Sliding-window importance-weighted memory store.

    Provides both a standalone key-value decay store (``put``/``access``/``prune``)
    and memory-entry integration (``entry_strength``/``refresh_entry``/``select_prunable``)
    that replaces the former TemporalDecayManager.

    Parameters
    ----------
    prune_threshold:
        Entries whose decayed strength falls below this value become eligible
        for cleanup. CRITICAL memories are always exempt.
    decay_episodic_only:
        When ``True`` (default) only episodic memories decay; semantic and
        procedural knowledge is treated as durable and keeps full strength.
    """

    def __init__(
        self,
        prune_threshold: float = 0.05,
        decay_episodic_only: bool = True,
    ):
        self.prune_threshold = prune_threshold
        self.decay_episodic_only = decay_episodic_only
        self._store: dict[str, DecayingMemory] = {}

    def put(
        self,
        key: str,
        value: object,
        importance: Importance = Importance.NORMAL,
    ) -> DecayingMemory:
        mem = DecayingMemory(key=key, value=value, importance=importance)
        self._store[key] = mem
        return mem

    def access(self, key: str) -> DecayingMemory | None:
        mem = self._store.get(key)
        if mem is None:
            return None
        mem.last_accessed = datetime.now(UTC)
        mem.access_count += 1
        return mem

    def prune(self, *, now: datetime | None = None) -> list[str]:
        """Remove memories below the strength threshold. Returns removed keys."""
        removed: list[str] = []
        for k, m in list(self._store.items()):
            if m.strength(now) < self.prune_threshold and m.importance != Importance.CRITICAL:
                removed.append(k)
                del self._store[k]
        return removed

    def snapshot(self, *, now: datetime | None = None) -> list[tuple[str, float, Importance]]:
        return [(k, m.strength(now), m.importance) for k, m in self._store.items()]

    # ── memory-entry integration (replaces TemporalDecayManager) ──────────

    def _entry_decays(self, entry: Any) -> bool:
        """Whether the forgetting curve applies to this memory entry."""
        from panoptis.memory.engine import ImportanceLevel, MemoryType

        if entry.importance == ImportanceLevel.CRITICAL:
            return False
        if self.decay_episodic_only:
            return entry.memory_type == MemoryType.EPISODIC
        return True

    def entry_strength(self, entry: Any, now: datetime | None = None) -> float:
        """Current forgetting-curve strength for a MemoryEntry in ``[0.0, 1.0]``."""
        if not self._entry_decays(entry):
            return 1.0
        imp_map = _engine_importance_map()
        importance = imp_map[entry.importance]
        return strength_at(importance, entry.last_accessed, entry.access_count, now)

    def refresh_entry(self, entry: Any, now: datetime | None = None) -> float:
        """Recompute and store ``entry.decay_factor``; return the new value."""
        entry.decay_factor = self.entry_strength(entry, now)
        return entry.decay_factor

    def is_entry_prunable(self, entry: Any, now: datetime | None = None) -> bool:
        """Whether a MemoryEntry has decayed below the cleanup threshold."""
        if not self._entry_decays(entry):
            return False
        return self.entry_strength(entry, now) < self.prune_threshold

    def select_prunable(
        self,
        entries: Iterable[Any],
        now: datetime | None = None,
    ) -> list[Any]:
        """Return the subset of MemoryEntry objects eligible for cleanup."""
        return [e for e in entries if self.is_entry_prunable(e, now)]


__all__ = ["DecayingMemory", "Importance", "ForgettingEngine", "strength_at"]
