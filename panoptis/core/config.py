"""Configuration for AgentWatch Cloud multi-tenant deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CloudConfig:
    """AgentWatch Cloud configuration loaded from environment variables."""

    # Deployment
    environment: str = field(default_factory=lambda: os.getenv("AGENTWATCH_ENV", "development"))
    base_url: str = field(
        default_factory=lambda: os.getenv("AGENTWATCH_BASE_URL", "http://localhost:8000")
    )

    # Database
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "AGENTWATCH_DATABASE_URL",
            "postgresql+asyncpg://panoptis:panoptis@localhost:5432/panoptis",
        )
    )

    # Redis (for ingestion pipeline, caching, rate limiting)
    redis_url: str = field(
        default_factory=lambda: os.getenv("AGENTWATCH_REDIS_URL", "redis://localhost:6379/0")
    )

    # Audit log persistence
    audit_log_path: Path = field(
        default_factory=lambda: Path(os.getenv("AGENTWATCH_AUDIT_LOG_PATH", "data/audit-log.jsonl"))
    )

    # Tenant settings
    default_plan: str = field(default_factory=lambda: os.getenv("AGENTWATCH_DEFAULT_PLAN", "free"))

    # Ingestion pipeline
    ingestion_batch_size: int = field(
        default_factory=lambda: int(os.getenv("AGENTWATCH_INGESTION_BATCH_SIZE", "100"))
    )
    ingestion_flush_interval: float = field(
        default_factory=lambda: float(os.getenv("AGENTWATCH_INGESTION_FLUSH_INTERVAL", "5.0"))
    )

    # Billing / Stripe
    stripe_secret_key: str = field(default_factory=lambda: os.getenv("STRIPE_SECRET_KEY", ""))
    stripe_webhook_secret: str = field(
        default_factory=lambda: os.getenv("STRIPE_WEBHOOK_SECRET", "")
    )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_cloud(self) -> bool:
        return os.getenv("AGENTWATCH_CLOUD_MODE", "false").lower() == "true"


_cloud_config: CloudConfig | None = None


def get_cloud_config() -> CloudConfig:
    """Get or create the global CloudConfig singleton."""
    global _cloud_config
    if _cloud_config is None:
        _cloud_config = CloudConfig()
    return _cloud_config
