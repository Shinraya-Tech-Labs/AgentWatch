"""Tests for production Celery failover configuration and diagnostic test alerting."""

from __future__ import annotations

import httpx
import pytest

from panoptis.alerting.engine import AlertingConfig, AlertingEngine
from panoptis.evidence.model import ObservationDraft, SensorRef
from panoptis.storage.store import Store
from panoptis.tasks import celery_app, seal_segments


@pytest.mark.skipif(celery_app is None, reason="Celery not installed in current environment")
def test_celery_failover_resilience_configuration():
    """Verify Celery is configured for multi-AZ Redis failover resilience."""
    conf = celery_app.conf
    assert conf.broker_connection_retry_on_startup is True
    assert conf.broker_connection_max_retries is None
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True

    transport_opts = conf.broker_transport_options
    assert transport_opts["visibility_timeout"] == 3600
    retry_policy = transport_opts["retry_policy"]
    assert retry_policy["max_retries"] == 10
    assert retry_policy["timeout"] == 5.0


def test_seal_segments_task(tmp_path, monkeypatch):
    """Verify background segment sealing task executes cleanly."""
    db_file = tmp_path / "test_tasks.db"
    store = Store(f"sqlite:///{db_file}")

    # Point tasks Store to this temp database
    monkeypatch.setattr("panoptis.storage.store.Store", lambda: store)

    ref = SensorRef("test", "1.0", "w-1")
    draft = ObservationDraft(
        sensor=ref,
        source_kind="task.test",
        payload={"msg": "hello"},
        source_seq=1,
    )
    store.append([draft])

    res = seal_segments(tenant_id="default")
    assert res["tenant_id"] == "default"
    assert res["segments_sealed"] == 1
    assert len(res["segment_ids"]) == 1
    assert store.verify("default").ok is True


@pytest.mark.asyncio
async def test_send_test_alert(monkeypatch):
    """Verify diagnostic test alert dispatches to configured webhook channels."""
    captured: list[dict] = []

    class MockResponse:
        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        captured.append({"url": kwargs.get("url") or args[1], "content": kwargs.get("content")})
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    config = AlertingConfig(
        slack_webhook_url="https://hooks.slack.com/services/TTEST1234/BTEST1234/abcdefghijklmn",
        pagerduty_webhook_url="https://events.pagerduty.com/v2/enqueue",
        pagerduty_routing_key="0123456789abcdef0123456789abcdef",
    )
    engine = AlertingEngine(config)

    res = await engine.send_test_alert("Staging verification")
    assert res["slack"] is True
    assert res["pagerduty"] is True
    assert len(captured) == 2
    assert "AgentWatch Channel Test" in str(captured[0])
    assert "panoptis-doctor" in str(captured[1])
