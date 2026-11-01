"""Tenant, API key, and usage tracking models for AgentWatch Cloud.

Architecture
~~~~~~~~~~~~
Every data record (``SessionRecord``, ``EventRecord``, ``MemoryEntryRecord``)
carries a nullable ``tenant_id`` column.  In cloud mode the ``TenantRepository``
wrapper **enforces** that all reads/writes are scoped to the authenticated
tenant — unscoped queries are never executed.

API keys are stored as PBKDF2-HMAC-SHA256 hashes with a random 16-byte salt
(``salt:hex_digest``).  Raw keys are returned once at creation time and never
persisted or logged.

Migration Strategy
~~~~~~~~~~~~~~~~~~
1. Add ``tenant_id`` column (nullable ``VARCHAR(36)``) to ``agent_sessions``,
   ``agent_events``, ``agent_checkpoints``, and ``memory_entries`` tables.
2. Run a backfill script: assign all existing rows to a default migration
   tenant (``tenant_id = '00000000-0000-0000-0000-000000000001'``).
3. After backfill, set the column to ``NOT NULL`` with a server default.
4. Add composite indexes: ``(tenant_id, started_at)``, ``(tenant_id, session_id)``.
5. The ``TenantRepository`` guard ensures new writes always include
   ``tenant_id``; legacy single-tenant deployments set ``tenant_id = 'default'``.

Usage Tracking & Future Billing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``UsageRecord`` aggregates per-tenant monthly metrics:

- ``tokens_used`` — total LLM tokens consumed
- ``requests_count`` — API call count
- ``usd_cost`` — estimated cost (from ``TokenUsage.estimated_cost_usd``)
- ``sessions_count`` / ``events_count`` — volume tracking

These fields map directly to Stripe metered billing line items:
``tokens_used`` → per-token pricing, ``usd_cost`` → cost-plus markup,
``requests_count`` → flat-rate tiers.  The ``TenantPlan`` enum defines
plan-level quotas; exceeding them triggers HTTP 429 responses via
``check_usage_limits()``.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class TenantPlan(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


@dataclass
class TenantConfig:
    """Per-tenant resource limits and configuration."""

    max_sessions: int = 100
    max_events_per_day: int = 10_000
    max_tokens_per_month: int = 1_000_000
    max_usd_per_month: float = 100.0
    ingestion_rate_limit: int = 100  # events per second
    retention_days: int = 30


PLAN_DEFAULTS: dict[TenantPlan, TenantConfig] = {
    TenantPlan.FREE: TenantConfig(
        max_sessions=50,
        max_events_per_day=5_000,
        max_tokens_per_month=500_000,
        max_usd_per_month=10.0,
        ingestion_rate_limit=50,
        retention_days=7,
    ),
    TenantPlan.STARTER: TenantConfig(
        max_sessions=200,
        max_events_per_day=50_000,
        max_tokens_per_month=5_000_000,
        max_usd_per_month=100.0,
        ingestion_rate_limit=200,
        retention_days=30,
    ),
    TenantPlan.PROFESSIONAL: TenantConfig(
        max_sessions=1_000,
        max_events_per_day=500_000,
        max_tokens_per_month=50_000_000,
        max_usd_per_month=1_000.0,
        ingestion_rate_limit=1_000,
        retention_days=90,
    ),
    TenantPlan.ENTERPRISE: TenantConfig(
        max_sessions=10_000,
        max_events_per_day=10_000_000,
        max_tokens_per_month=500_000_000,
        max_usd_per_month=10_000.0,
        ingestion_rate_limit=10_000,
        retention_days=365,
    ),
}


@dataclass
class Tenant:
    """Represents a cloud tenant (organization/team)."""

    tenant_id: str
    name: str
    plan: TenantPlan = TenantPlan.FREE
    status: TenantStatus = TenantStatus.ACTIVE
    config: TenantConfig = field(default_factory=TenantConfig)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "plan": self.plan.value,
            "status": self.status.value,
            "config": {
                "max_sessions": self.config.max_sessions,
                "max_events_per_day": self.config.max_events_per_day,
                "max_tokens_per_month": self.config.max_tokens_per_month,
                "max_usd_per_month": self.config.max_usd_per_month,
                "ingestion_rate_limit": self.config.ingestion_rate_limit,
                "retention_days": self.config.retention_days,
            },
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


def _hash_api_key(raw_key: str, salt: str | None = None) -> str:
    """PBKDF2-HMAC-SHA256 hash of an API key with random salt.

    Returns ``salt:hash`` so the salt is recoverable during validation.
    If *salt* is ``None`` a fresh 16-byte hex salt is generated.
    """
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", raw_key.encode(), salt.encode(), iterations=100_000)
    return f"{salt}:{dk.hex()}"


def _verify_api_key(raw_key: str, stored: str) -> bool:
    """Verify a raw key against a ``salt:hash`` value."""
    salt, expected_hash = stored.split(":", 1)
    dk = hashlib.pbkdf2_hmac("sha256", raw_key.encode(), salt.encode(), iterations=100_000)
    return secrets.compare_digest(dk.hex(), expected_hash)


def generate_api_key(tenant_id: str) -> str:
    """Generate a prefixed API key: `aw_{tenant_id_short}_{random}`."""
    short_id = tenant_id[:8].replace("-", "")
    random_part = secrets.token_urlsafe(32)
    return f"aw_{short_id}_{random_part}"


@dataclass
class ApiKey:
    """An API key bound to a specific tenant."""

    key_id: str
    tenant_id: str
    key_hash: str  # SHA-256 of the raw key
    name: str = ""
    scopes: list[str] = field(default_factory=lambda: ["*"])
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked: bool = False

    def is_valid(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at and datetime.now(UTC) > self.expires_at:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "scopes": self.scopes,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked": self.revoked,
        }


@dataclass
class UsageRecord:
    """Tracks token and request usage for billing."""

    tenant_id: str
    period: str  # YYYY-MM
    tokens_used: int = 0
    requests_count: int = 0
    usd_cost: float = 0.0
    sessions_count: int = 0
    events_count: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "period": self.period,
            "tokens_used": self.tokens_used,
            "requests_count": self.requests_count,
            "usd_cost": self.usd_cost,
            "sessions_count": self.sessions_count,
            "events_count": self.events_count,
            "updated_at": self.updated_at.isoformat(),
        }
