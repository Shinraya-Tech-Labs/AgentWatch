"""
MAG-009 — Async Multi-Agent Race Condition Detector.

When multiple agents read and write the same resource (a memory key,
a file, a DB row, an external API's state) concurrently, overlapping
access windows can corrupt shared state. This module tracks per-resource
access intervals across agents and flags overlapping windows where at
least one side is a write.

What this catches:
    Two different agents touching the same resource in overlapping
    time windows, where at least one of them writes.

What this does NOT catch:
    - Races between calls from the SAME agent (sequential by definition
      in this model — not a cross-agent race).
    - Missing or incomplete trace data: this only sees what's recorded
      via record_access(); it cannot infer accesses that were never
      logged.
    - True low-level races (e.g. sub-millisecond interleavings inside a
      single reported span) — detection is bounded by the precision of
      the start_ts/end_ts the caller supplies.

Tie-breaking: when two windows share an exact boundary (a.end_ts ==
b.start_ts), they are NOT considered overlapping — one access must have
fully finished before the other is considered safe to start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AccessType(str, Enum):
    READ = "read"
    WRITE = "write"


@dataclass
class ResourceAccess:
    agent_id: str
    resource: str
    access_type: AccessType
    start_ts: datetime
    end_ts: datetime

    def __post_init__(self) -> None:
        if self.end_ts < self.start_ts:
            raise ValueError(
                f"end_ts ({self.end_ts}) precedes start_ts ({self.start_ts}) "
                f"for agent {self.agent_id} on resource {self.resource!r}"
            )


@dataclass
class RaceConflict:
    resource: str
    agent_a: str
    agent_b: str
    access_a: AccessType
    access_b: AccessType
    detail: str


@dataclass
class RaceReport:
    has_race: bool
    conflicts: list[RaceConflict] = field(default_factory=list)


class RaceConditionDetector:
    """
    Track resource accesses across agents and detect overlapping
    read/write or write/write windows from different agents.
    """

    def __init__(self) -> None:
        self._accesses: dict[str, list[ResourceAccess]] = {}

    def record_access(
        self,
        agent_id: str,
        resource: str,
        access_type: AccessType,
        start_ts: datetime,
        end_ts: datetime,
    ) -> None:
        self._accesses.setdefault(resource, []).append(
            ResourceAccess(agent_id, resource, access_type, start_ts, end_ts)
        )

    @staticmethod
    def _overlaps(a: ResourceAccess, b: ResourceAccess) -> bool:
        # Strict inequality: an access that ends exactly when another
        # starts is treated as sequential, not overlapping.
        return a.start_ts < b.end_ts and b.start_ts < a.end_ts

    def scan(self) -> RaceReport:
        conflicts: list[RaceConflict] = []
        for resource, accesses in self._accesses.items():
            for i in range(len(accesses)):
                for j in range(i + 1, len(accesses)):
                    a, b = accesses[i], accesses[j]

                    # Same agent: sequential by construction in this
                    # model, not a cross-agent race.
                    if a.agent_id == b.agent_id:
                        continue

                    # Two reads can never corrupt state.
                    if a.access_type == AccessType.READ and b.access_type == AccessType.READ:
                        continue

                    if self._overlaps(a, b):
                        conflicts.append(
                            RaceConflict(
                                resource=resource,
                                agent_a=a.agent_id,
                                agent_b=b.agent_id,
                                access_a=a.access_type,
                                access_b=b.access_type,
                                detail=(
                                    f"{a.agent_id} ({a.access_type.value}) and "
                                    f"{b.agent_id} ({b.access_type.value}) overlap on "
                                    f"'{resource}'"
                                ),
                            )
                        )
        return RaceReport(has_race=bool(conflicts), conflicts=conflicts)


__all__ = ["AccessType", "RaceConditionDetector", "RaceConflict", "RaceReport", "ResourceAccess"]
