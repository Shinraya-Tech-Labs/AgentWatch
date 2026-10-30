"""Unit tests for the AlertingEngine webhook delivery retry mechanism."""

from __future__ import annotations

import asyncio

import httpx

from panoptis.alerting.engine import AlertingConfig, AlertingEngine
from panoptis.core.schema import AgentEvent, AgentFramework, EventType


def test_alerting_engine_retries_and_succeeds(monkeypatch):
    calls = 0

    class MockResponse:
        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("Transient connection failure")
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    # Shorten delay for fast tests
    engine = AlertingEngine(
        AlertingConfig(
            slack_webhook_url="https://hooks.slack.com/services/TTEST1234/BTEST1234/abcdefghijklmn"
        )
    )

    event = AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.SAFETY_BLOCK,
    )

    sent = asyncio.run(engine.alert_event(event))
    assert sent["slack"] is True
    assert calls == 3


def test_alerting_engine_fails_after_max_retries(monkeypatch):
    calls = 0

    async def mock_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("Permanent connection failure")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    engine = AlertingEngine(
        AlertingConfig(
            slack_webhook_url="https://hooks.slack.com/services/TTEST1234/BTEST1234/abcdefghijklmn"
        )
    )

    event = AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.SAFETY_BLOCK,
    )

    sent = asyncio.run(engine.alert_event(event))
    assert sent["slack"] is False
    assert calls == 3
