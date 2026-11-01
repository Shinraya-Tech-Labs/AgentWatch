"""Minimal Celery app for Docker compose."""

from __future__ import annotations

import os

from celery import Celery

celery_app = Celery(
    "panoptis",
    broker=os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/1")),
    backend=os.getenv("CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/1")),
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="panoptis.tasks.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="panoptis.tasks.run_redteam")
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
