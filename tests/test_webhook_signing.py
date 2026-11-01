from __future__ import annotations

import json

import httpx
import pytest

from panoptis.alerting.engine import AlertingConfig, AlertingEngine
from panoptis.core.schema import AgentEvent, AgentFramework, EventType
from panoptis.security.webhook_signing import generate_webhook_signature


def test_signature_generation():
    secret = "my-secret-key"  # noqa: S105
    payload_dict = {"event": "test"}
    payload_bytes = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")

    sig1 = generate_webhook_signature(payload_bytes, secret)
    sig2 = generate_webhook_signature(payload_bytes, secret)

    # Same payload + same secret = identical
    assert sig1 == sig2
    assert sig1.startswith("sha256=")

    # Different payload
    payload2 = json.dumps({"event": "other"}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig3 = generate_webhook_signature(payload2, secret)
    assert sig1 != sig3

    # Empty payload
    sig_empty = generate_webhook_signature(b"", secret)
    assert sig_empty.startswith("sha256=")
    assert sig_empty != sig1


@pytest.mark.asyncio
async def test_alerting_engine_sends_signature(monkeypatch):
    secret = "test-secret"  # noqa: S105
    engine = AlertingEngine(
        AlertingConfig(
            slack_webhook_url="https://hooks.slack.com/services/TTEST1234/BTEST1234/abcdefghijklmn",
            webhook_signing_secret=secret,
        )
    )

    captured_request = {}

    class MockResponse:
        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        captured_request["url"] = args[1] if len(args) > 1 else kwargs.get("url")
        captured_request["content"] = kwargs.get("content")
        captured_request["headers"] = kwargs.get("headers")
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    event = AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.SAFETY_BLOCK,
    )

    sent = await engine.alert_event(event)
    assert sent["slack"] is True

    headers = captured_request.get("headers", {})
    assert "X-AgentWatch-Signature" in headers

    sig = headers["X-AgentWatch-Signature"]
    assert sig.startswith("sha256=")

    # Verify signature matches the content sent
    content = captured_request.get("content")
    expected_sig = generate_webhook_signature(content, secret)
    assert sig == expected_sig


@pytest.mark.asyncio
async def test_alerting_engine_no_signature_without_secret(monkeypatch):
    engine = AlertingEngine(
        AlertingConfig(
            slack_webhook_url="https://hooks.slack.com/services/TTEST1234/BTEST1234/abcdefghijklmn",
            webhook_signing_secret=None,
        )
    )

    captured_request = {}

    class MockResponse:
        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        captured_request["url"] = args[1] if len(args) > 1 else kwargs.get("url")
        captured_request["content"] = kwargs.get("content")
        captured_request["headers"] = kwargs.get("headers")
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    event = AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.SAFETY_BLOCK,
    )

    sent = await engine.alert_event(event)
    assert sent["slack"] is True

    headers = captured_request.get("headers", {})
    assert "X-AgentWatch-Signature" not in headers
