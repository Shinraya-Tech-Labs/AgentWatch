"""Aggregate agent sessions into a grouped cost report (issue #339).

This module is deliberately pure and side-effect free: :func:`build_cost_report`
takes a list of already-time-filtered :class:`AgentSession` summaries and returns
a :class:`CostReport`. Fetching sessions (from the API) and rendering (Rich table
or JSON) live in the CLI layer, which keeps the aggregation trivially testable.

Design choices (the issue left these open):
* USD comes from the pre-computed ``session.estimated_cost_usd`` — no per-model
  pricing lookup is needed at report time.
* A "successful goal" is a session whose stored ``status`` is
  ``ExecutionStatus.SUCCESS``. Session summaries do not carry their events, so an
  events-based (dual-eval) definition is intentionally out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from panoptis.core.schema import AgentSession, ExecutionStatus

#: Session dimensions the report can group by.
VALID_GROUP_BY: tuple[str, ...] = ("framework", "agent", "status")


def parse_sessions(items: list[dict[str, object]]) -> tuple[list[AgentSession], int]:
    """Validate raw session dicts into :class:`AgentSession`, skipping bad rows.

    The sessions API returns already-serialized sessions, but legacy or partial
    records can still fail validation. Rather than crashing the whole report on a
    single bad row, invalid records are skipped and counted so the caller can
    surface how many were dropped.

    Args:
        items: Raw session dictionaries (e.g. from the sessions API response).

    Returns:
        A ``(sessions, skipped)`` tuple: the successfully parsed sessions and the
        number of records that failed validation.
    """
    sessions: list[AgentSession] = []
    skipped = 0
    for item in items:
        try:
            sessions.append(AgentSession.model_validate(item))
        except ValidationError:
            skipped += 1
    return sessions, skipped


def _group_key(session: AgentSession, group_by: str) -> str:
    if group_by == "framework":
        return session.framework.value
    if group_by == "agent":
        return session.agent_name or session.agent_id
    # "status" is the only remaining option; build_cost_report validates group_by
    # against VALID_GROUP_BY before this helper is ever reached.
    return session.status.value


@dataclass
class CostReportRow:
    """Aggregated spend for a single group (e.g. one framework)."""

    group: str
    sessions: int = 0
    total_tokens: int = 0
    total_usd: float = 0.0
    successful: int = 0

    @property
    def cost_per_successful_goal(self) -> float | None:
        """USD spent per successful session, or ``None`` if none succeeded."""
        if self.successful <= 0:
            return None
        return self.total_usd / self.successful

    def to_dict(self) -> dict[str, Any]:
        cps = self.cost_per_successful_goal
        return {
            "group": self.group,
            "sessions": self.sessions,
            "total_tokens": self.total_tokens,
            "total_usd": round(self.total_usd, 6),
            "successful": self.successful,
            "cost_per_successful_goal": None if cps is None else round(cps, 6),
        }


@dataclass
class CostReport:
    """A grouped cost report over a reporting window."""

    group_by: str
    days: int
    rows: list[CostReportRow] = field(default_factory=list)

    @property
    def total_sessions(self) -> int:
        return sum(r.sessions for r in self.rows)

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.rows)

    @property
    def total_usd(self) -> float:
        return sum(r.total_usd for r in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_by": self.group_by,
            "days": self.days,
            "totals": {
                "sessions": self.total_sessions,
                "total_tokens": self.total_tokens,
                "total_usd": round(self.total_usd, 6),
            },
            "rows": [r.to_dict() for r in self.rows],
        }


def build_cost_report(
    sessions: list[AgentSession],
    *,
    group_by: str = "framework",
    days: int = 30,
) -> CostReport:
    """Aggregate sessions into a grouped cost report.

    The function does not filter by time — the caller is expected to have already
    restricted ``sessions`` to the reporting window. ``days`` is recorded on the
    report purely for display. Rows are returned sorted by total USD descending.

    Args:
        sessions: Session summaries to aggregate.
        group_by: One of :data:`VALID_GROUP_BY` (``framework``/``agent``/``status``).
        days: Reporting window in days, carried through for display.

    Returns:
        The aggregated :class:`CostReport`.

    Raises:
        ValueError: If ``group_by`` is not one of :data:`VALID_GROUP_BY`.
    """
    if group_by not in VALID_GROUP_BY:
        raise ValueError(f"unsupported group_by: {group_by!r} (expected one of {VALID_GROUP_BY})")

    rows: dict[str, CostReportRow] = {}
    for session in sessions:
        key = _group_key(session, group_by)
        row = rows.get(key)
        if row is None:
            row = CostReportRow(group=key)
            rows[key] = row
        row.sessions += 1
        row.total_tokens += session.total_tokens
        row.total_usd += session.estimated_cost_usd
        if session.status is ExecutionStatus.SUCCESS:
            row.successful += 1

    ordered = sorted(rows.values(), key=lambda r: r.total_usd, reverse=True)
    return CostReport(group_by=group_by, days=days, rows=ordered)
