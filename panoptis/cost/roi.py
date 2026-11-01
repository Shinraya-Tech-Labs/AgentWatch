"""
CST-006 — ROI Calculator.

Track:
  - cost saved by blocking dangerous actions (estimated damage avoided)
  - cost of failures caught vs. missed
  - net ROI = value created - cost incurred
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ROILedgerEntry:
    when: datetime = field(default_factory=lambda: datetime.now(UTC))
    category: str = ""  # block_saved | failure_caught | failure_missed | cost_incurred
    usd: float = 0.0
    notes: str = ""


@dataclass
class ROISummary:
    total_saved_usd: float
    total_cost_usd: float
    failures_caught: int
    failures_missed: int
    net_roi_usd: float
    roi_ratio: float  # saved/cost; 0 if cost is 0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "total_saved_usd": self.total_saved_usd,
            "total_cost_usd": self.total_cost_usd,
            "failures_caught": self.failures_caught,
            "failures_missed": self.failures_missed,
            "net_roi_usd": self.net_roi_usd,
            "roi_ratio": self.roi_ratio,
        }


# Severity → estimated $ damage averted
DAMAGE_BY_SEVERITY = {
    "low": 50.0,
    "medium": 500.0,
    "high": 5_000.0,
    "critical": 50_000.0,
}


class ROILedger:
    """Append-only ledger of value-impacting events."""

    def __init__(self) -> None:
        self._entries: list[ROILedgerEntry] = []

    def record_block(self, severity: str, notes: str = "") -> ROILedgerEntry:
        usd = DAMAGE_BY_SEVERITY.get(severity, 50.0)
        entry = ROILedgerEntry(category="block_saved", usd=usd, notes=notes)
        self._entries.append(entry)
        return entry

    def record_failure_caught(self, usd: float, notes: str = "") -> ROILedgerEntry:
        entry = ROILedgerEntry(category="failure_caught", usd=usd, notes=notes)
        self._entries.append(entry)
        return entry

    def record_failure_missed(self, usd: float, notes: str = "") -> ROILedgerEntry:
        entry = ROILedgerEntry(category="failure_missed", usd=usd, notes=notes)
        self._entries.append(entry)
        return entry

    def record_cost(self, usd: float, notes: str = "") -> ROILedgerEntry:
        entry = ROILedgerEntry(category="cost_incurred", usd=usd, notes=notes)
        self._entries.append(entry)
        return entry

    def summary(self) -> ROISummary:
        saved = sum(e.usd for e in self._entries if e.category in ("block_saved", "failure_caught"))
        cost = sum(e.usd for e in self._entries if e.category == "cost_incurred")
        caught = sum(1 for e in self._entries if e.category == "failure_caught")
        missed = sum(1 for e in self._entries if e.category == "failure_missed")
        net = saved - cost
        ratio = (saved / cost) if cost > 0 else 0.0
        return ROISummary(
            total_saved_usd=saved,
            total_cost_usd=cost,
            failures_caught=caught,
            failures_missed=missed,
            net_roi_usd=net,
            roi_ratio=ratio,
        )

    def entries(self) -> list[ROILedgerEntry]:
        return list(self._entries)


__all__ = ["ROILedger", "ROILedgerEntry", "ROISummary", "DAMAGE_BY_SEVERITY"]
