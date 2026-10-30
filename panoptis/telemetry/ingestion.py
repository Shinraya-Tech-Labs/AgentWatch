"""Tenant-aware ingestion pipeline for AgentWatch Cloud.

Provides batched, rate-limited event ingestion with per-tenant isolation.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from panoptis.api.tenant_auth import TenantStore
from panoptis.core.schema import AgentEvent
from panoptis.models.tenant import TenantStatus

logger = logging.getLogger(__name__)


@dataclass
class IngestionMetrics:
    """Per-tenant ingestion metrics."""

    events_received: int = 0
    events_accepted: int = 0
    events_rejected: int = 0
    bytes_ingested: int = 0
    last_event_at: datetime | None = None
    rate_limit_hits: int = 0


class RateLimiter:
    """Simple token bucket rate limiter per tenant."""

    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, Any]] = {}

    def allow(self, tenant_id: str, rate_limit: int) -> bool:
        """Check if a request is allowed under the rate limit."""
        now = time.monotonic()
        bucket = self._buckets.get(tenant_id)

        if bucket is None:
            self._buckets[tenant_id] = {
                "tokens": rate_limit - 1,
                "last_refill": now,
                "rate": rate_limit,
            }
            return True

        # Always update rate from current plan to handle plan changes
        bucket["rate"] = rate_limit

        # Refill tokens
        elapsed = now - bucket["last_refill"]
        refill = elapsed * bucket["rate"]
        bucket["tokens"] = min(bucket["rate"], bucket["tokens"] + refill)
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True
        return False


class TenantIngestionPipeline:
    """Multi-tenant event ingestion with batching, rate limiting, and tenant isolation."""

    def __init__(
        self,
        tenant_store: TenantStore,
        batch_size: int = 100,
        flush_interval: float = 5.0,
    ):
        self._tenant_store = tenant_store
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._rate_limiter = RateLimiter()
        self._metrics: dict[str, IngestionMetrics] = defaultdict(IngestionMetrics)
        self._batches: dict[str, list[AgentEvent]] = defaultdict(list)
        self._handlers: list[Any] = []
        self._flush_task: asyncio.Task | None = None

    def add_handler(self, handler: Any) -> None:
        """Register an event handler to receive batched events."""
        self._handlers.append(handler)

    async def start(self) -> None:
        """Start the background flush loop."""
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop the background flush and flush remaining events."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush_all()

    async def ingest(self, tenant_id: str, event: AgentEvent) -> bool:
        """Ingest a single event for a tenant.

        Returns True if accepted, False if rejected (rate limit, quota, etc.).
        """
        metrics = self._metrics[tenant_id]
        metrics.events_received += 1

        # Validate tenant exists and is active
        tenant = self._tenant_store.get_tenant(tenant_id)
        if not tenant or tenant.status != TenantStatus.ACTIVE:
            metrics.events_rejected += 1
            logger.warning("Event rejected: tenant %s not found or inactive", tenant_id)
            return False

        # Rate limit check
        if not self._rate_limiter.allow(tenant_id, tenant.config.ingestion_rate_limit):
            metrics.rate_limit_hits += 1
            metrics.events_rejected += 1
            logger.warning("Event rejected: rate limit for tenant %s", tenant_id)
            return False

        # Quota check
        ok, reason = self._tenant_store.check_usage_limits(tenant_id)
        if not ok:
            metrics.events_rejected += 1
            logger.warning("Event rejected: %s for tenant %s", reason, tenant_id)
            return False

        # Accept event
        self._batches[tenant_id].append(event)
        metrics.events_accepted += 1
        metrics.bytes_ingested += len(event.model_dump_json())
        metrics.last_event_at = datetime.now(UTC)

        # Flush if batch is full
        if len(self._batches[tenant_id]) >= self._batch_size:
            await self._flush_tenant(tenant_id)

        return True

    async def _flush_loop(self) -> None:
        """Periodically flush all tenant batches."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                await self.flush_all()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in ingestion flush loop")

    async def flush_all(self) -> dict[str, int]:
        """Flush all pending batches. Returns {tenant_id: events_flushed}."""
        results = {}
        tenant_ids = list(self._batches.keys())
        for tenant_id in tenant_ids:
            count = await self._flush_tenant(tenant_id)
            if count > 0:
                results[tenant_id] = count
        return results

    async def _flush_tenant(self, tenant_id: str) -> int:
        """Flush a single tenant's batch.

        Events are popped from the batch BEFORE delivery so that a handler
        crash does not cause an infinite retry loop.  However, usage is only
        recorded AFTER handlers succeed, avoiding phantom billing when delivery
        fails.
        """
        batch = self._batches.pop(tenant_id, [])
        if not batch:
            return 0

        # Dispatch to handlers first — only record usage on success
        delivered = False
        for handler in self._handlers:
            try:
                result = handler(tenant_id, batch)
                if inspect.isawaitable(result):
                    await result
                delivered = True
            except Exception:
                logger.exception("Handler error flushing batch for tenant %s", tenant_id)

        if not delivered:
            # Re-queue events if all handlers failed
            self._batches[tenant_id] = batch + self._batches.get(tenant_id, [])
            logger.warning(
                "Re-queued %d events for tenant %s (handlers failed)", len(batch), tenant_id
            )
            return 0

        # Record usage only after successful delivery
        total_tokens = sum(e.token_usage.total_tokens for e in batch if e.token_usage)
        total_cost = sum(e.token_usage.estimated_cost_usd or 0.0 for e in batch if e.token_usage)
        self._tenant_store.record_usage(
            tenant_id=tenant_id,
            tokens=total_tokens,
            usd_cost=total_cost,
            events=len(batch),
        )

        logger.debug("Flushed %d events for tenant %s", len(batch), tenant_id)
        return len(batch)

    def get_metrics(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Get ingestion metrics, optionally filtered by tenant."""
        if tenant_id:
            m = self._metrics.get(tenant_id)
            return m.__dict__ if m else {}
        return {tid: m.__dict__ for tid, m in self._metrics.items()}
