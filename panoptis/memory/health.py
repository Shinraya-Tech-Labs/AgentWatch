"""
MEM-006 — Memory Health Monitor.

Detect stale, conflicting, corrupted memories. Alerts before they affect
agent behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class HealthIssue:
    key: str
    kind: str  # stale | conflict | corrupt | duplicate
    detail: str


@dataclass
class HealthReport:
    total_memories: int
    issues: list[HealthIssue] = field(default_factory=list)

    @property
    def score(self) -> float:
        if self.total_memories == 0:
            return 1.0
        bad = len(self.issues)
        return max(0.0, 1.0 - bad / self.total_memories)


class MemoryHealthMonitor:
    """Inspect a memory dict and surface anomalies."""

    def __init__(
        self,
        stale_after: timedelta = timedelta(days=90),
    ):
        self.stale_after = stale_after

    def inspect(
        self,
        memories: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> HealthReport:
        now = now or datetime.now(UTC)
        issues: list[HealthIssue] = []

        # Stale check
        for m in memories:
            ts = m.get("timestamp") or m.get("last_accessed")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    ts = None
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if now - ts > self.stale_after:
                    issues.append(
                        HealthIssue(
                            key=m.get("key", "?"),
                            kind="stale",
                            detail=f"older than {self.stale_after.days} days",
                        )
                    )

        # Conflict check — same key, different value
        by_key: dict[str, set[Any]] = {}
        for m in memories:
            k = m.get("key")
            if k is None:
                continue
            v = repr(m.get("value"))
            by_key.setdefault(k, set()).add(v)
        for k, vs in by_key.items():
            if len(vs) > 1:
                issues.append(
                    HealthIssue(key=k, kind="conflict", detail=f"{len(vs)} divergent values")
                )

        # Corrupt check — schema-required field missing
        for m in memories:
            if "key" not in m or "value" not in m:
                issues.append(
                    HealthIssue(
                        key=str(m.get("key", "?")),
                        kind="corrupt",
                        detail="missing key or value field",
                    )
                )

        # Duplicate check — exact-repr duplicates of full record
        seen_repr: dict[str, int] = {}
        for m in memories:
            r = repr(sorted(m.items()))
            seen_repr[r] = seen_repr.get(r, 0) + 1
        for r, count in seen_repr.items():
            if count > 1:
                # find the key
                first = next((m for m in memories if repr(sorted(m.items())) == r), {})
                issues.append(
                    HealthIssue(
                        key=str(first.get("key", "?")),
                        kind="duplicate",
                        detail=f"{count} identical entries",
                    )
                )

        return HealthReport(total_memories=len(memories), issues=issues)


__all__ = ["HealthIssue", "HealthReport", "MemoryHealthMonitor"]
