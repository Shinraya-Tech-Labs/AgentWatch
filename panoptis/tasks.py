"""Production Celery application and background task runners."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

try:
    from celery import Celery

    has_celery = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    Celery = None  # type: ignore
    has_celery = False

if has_celery and Celery is not None:
    celery_app = Celery(
        "panoptis",
        broker=os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/1")),
        backend=os.getenv(
            "CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/1")
        ),
    )

    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Multi-AZ Redis failover resilience and worker reconnection policies
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=None,
        broker_transport_options={
            "visibility_timeout": 3600,
            "retry_policy": {
                "timeout": 5.0,
                "interval_start": 0.2,
                "interval_step": 0.5,
                "interval_max": 3.0,
                "max_retries": 10,
            },
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
    )
else:
    celery_app = None  # type: ignore


def _task(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if celery_app is not None:
        return celery_app.task(name=name)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        return fn

    return decorator


@_task(name="panoptis.tasks.ping")
def ping() -> str:
    return "pong"


@_task(name="panoptis.tasks.run_redteam")
def run_redteam(payloads_path: str | None = None) -> dict:
    """Scheduled red-team pass (SAF-008 automation).

    Runs the attack corpus through the safety detectors and returns a
    vulnerability report. ``bypassed`` lists the attacks whose guardrails
    failed. Schedule it with Celery beat to continuously pen-test agents; the
    JSON-serializable return value is stored in the Celery result backend.
    ``payloads_path`` overrides the bundled corpus.
    """
    from panoptis.security.redteam import RedTeamHarness, load_corpus

    scenarios = load_corpus(payloads_path) if payloads_path else None
    return RedTeamHarness(scenarios).run().to_dict()


@_task(name="panoptis.tasks.seal_segments")
def seal_segments(tenant_id: str = "default") -> dict[str, Any]:
    """Automated periodic segment sealing task (data integrity & retention compaction)."""
    from panoptis.storage.store import Store

    store = Store()
    sealed = store.seal_all(tenant_id=tenant_id)
    return {
        "tenant_id": tenant_id,
        "segments_sealed": len(sealed),
        "segment_ids": [s.segment_id for s in sealed],
    }


@_task(name="panoptis.tasks.purge_segments")
def purge_segments(tenant_id: str = "default", older_than_days: int = 30) -> dict[str, Any]:
    """Automated retention purge for segments older than specified threshold."""
    from datetime import UTC, datetime, timedelta

    from panoptis.storage.store import Store

    store = Store()
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    segs = store.segments_sealed_before(tenant_id, cutoff)
    purged_count = 0
    for seg in segs:
        purged_count += store.purge_segment(
            seg.segment_id,
            reason=f"retention: automated cleanup older than {older_than_days} days",
            actor="celery-worker",
            tenant_id=tenant_id,
        )
    return {
        "tenant_id": tenant_id,
        "segments_purged": len(segs),
        "observations_purged": purged_count,
        "verified": store.verify(tenant_id).ok,
    }
