"""
MEM-003 — Cross-Session Identity.

Stable identity for a (user, project) across sessions. Tracks:
    - preferences
    - active constraints
    - decisions made
No staleness, no collision between users (identity = composite key).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class IdentityRecord:
    user_id: str
    project_id: str
    preferences: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def identity_key(self) -> str:
        raw = f"{self.user_id}::{self.project_id}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]


class IdentityStore:
    """In-memory identity store. Production backends plug in via subclass."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IdentityRecord] = {}

    def get_or_create(self, user_id: str, project_id: str) -> IdentityRecord:
        key = (user_id, project_id)
        if key not in self._records:
            self._records[key] = IdentityRecord(user_id=user_id, project_id=project_id)
        rec = self._records[key]
        rec.last_seen = datetime.now(UTC)
        return rec

    def set_preference(self, user_id: str, project_id: str, name: str, value: Any) -> None:
        rec = self.get_or_create(user_id, project_id)
        rec.preferences[name] = value

    def add_constraint(self, user_id: str, project_id: str, constraint: str) -> None:
        rec = self.get_or_create(user_id, project_id)
        if constraint not in rec.constraints:
            rec.constraints.append(constraint)

    def remove_constraint(self, user_id: str, project_id: str, constraint: str) -> None:
        rec = self.get_or_create(user_id, project_id)
        if constraint in rec.constraints:
            rec.constraints.remove(constraint)

    def add_decision(
        self,
        user_id: str,
        project_id: str,
        title: str,
        decided: str,
        rationale: str = "",
    ) -> None:
        rec = self.get_or_create(user_id, project_id)
        rec.decisions.append(
            {
                "title": title,
                "decided": decided,
                "rationale": rationale,
                "when": datetime.now(UTC).isoformat(),
            }
        )

    def all(self) -> list[IdentityRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)


__all__ = ["IdentityRecord", "IdentityStore"]
