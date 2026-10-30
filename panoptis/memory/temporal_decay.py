"""
MEM-005 — Temporal Decay Curve Manager (backward-compatibility shim).

This module is now a thin wrapper around :class:`ForgettingEngine` which
lives in :mod:`panoptis.memory.decay`.  The ``TemporalDecayManager`` class
delegates directly to ``ForgettingEngine``'s memory-entry methods.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from panoptis.memory.decay import ForgettingEngine


class TemporalDecayManager(ForgettingEngine):
    """Backward-compatible alias — delegates to ForgettingEngine.

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
        *,
        prune_threshold: float = 0.05,
        decay_episodic_only: bool = True,
    ) -> None:
        super().__init__(
            prune_threshold=prune_threshold,
            decay_episodic_only=decay_episodic_only,
        )

    def strength(self, entry: object, now: datetime | None = None) -> float:  # type: ignore[override]
        return self.entry_strength(entry, now)

    def refresh(self, entry: object, now: datetime | None = None) -> float:
        return self.refresh_entry(entry, now)

    def is_prunable(self, entry: object, now: datetime | None = None) -> bool:
        return self.is_entry_prunable(entry, now)

    def select_prunable(
        self,
        entries: Iterable[object],
        now: datetime | None = None,
    ) -> list[object]:
        return super().select_prunable(entries, now)


__all__ = ["TemporalDecayManager"]
