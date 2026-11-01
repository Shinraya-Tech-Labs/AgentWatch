"""
MEM-008 — Memory Governance.

Retention policies, deletion schedules, and GDPR right-to-erasure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from panoptis.governance.gdpr import ErasureStore


@dataclass
class RetentionPolicy:
    name: str
    applies_to: str  # "all" | "type:episodic" | "user:X" | "project:Y"
    retain_for: timedelta
    require_consent: bool = False


@dataclass
class ErasureRequest:
    user_id: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    scope: str = "all"  # all | sessions | memories | exports


@dataclass
class ErasureReceipt:
    request: ErasureRequest
    completed_at: datetime
    items_deleted: int


class MemoryGovernance:
    """Apply policies and process erasure requests."""

    def __init__(self) -> None:
        self.policies: list[RetentionPolicy] = []

    def add_policy(self, policy: RetentionPolicy) -> None:
        self.policies.append(policy)

    def policy_for(self, memory: dict[str, Any]) -> RetentionPolicy | None:
        type_str = f"type:{memory.get('type')}"
        user_str = f"user:{memory.get('user_id')}"
        project_str = f"project:{memory.get('project_id')}"
        for p in self.policies:
            if p.applies_to in ("all", type_str, user_str, project_str):
                return p
        return None

    def apply_retention(
        self,
        memories: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return the subset of memories that survive policy enforcement."""
        now = now or datetime.now(UTC)
        kept: list[dict[str, Any]] = []
        for m in memories:
            policy = self.policy_for(m)
            if policy is None:
                kept.append(m)
                continue
            ts = m.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    ts = None
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if now - ts > policy.retain_for:
                    continue
            kept.append(m)
        return kept

    def erase(
        self,
        request: ErasureRequest,
        memories: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], ErasureReceipt]:
        """Process a GDPR Article 17 erasure request over an in-memory list."""
        keep = [m for m in memories if m.get("user_id") != request.user_id]
        deleted = len(memories) - len(keep)
        receipt = ErasureReceipt(
            request=request,
            completed_at=datetime.now(UTC),
            items_deleted=deleted,
        )
        return keep, receipt

    async def erase_persisted(
        self,
        request: ErasureRequest,
        store: ErasureStore,
    ) -> ErasureReceipt:
        """Process an erasure request against the persistence layer."""
        deleted = await store.erase_user_data(request.user_id, scope=request.scope)
        return ErasureReceipt(
            request=request,
            completed_at=datetime.now(UTC),
            items_deleted=deleted,
        )


__all__ = [
    "RetentionPolicy",
    "ErasureRequest",
    "ErasureReceipt",
    "ErasureStore",
    "MemoryGovernance",
]
