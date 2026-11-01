"""Tests for the AgentWatch API server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import agentwatch.api.server as _server_module
from agentwatch._version import __version__
from agentwatch.api.server import app, reset_rate_limiter_for_tests


@pytest.fixture
def client():
    reset_rate_limiter_for_tests
    return TestClient(app)


@pytest.fixture
def mock_db_session():
    with patch("agentwatch.api.server._db_session_factory") as mock_factory:
        mock_session_context = AsyncMock()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        mock_session_context.__aenter__.return_value = mock_db
        mock_factory.return_value = mock_session_context

        yield mock_factory


@pytest.fixture
def mock_redis_healthy():
    with (
        patch("agentwatch.api.server.os.getenv") as mock_getenv,
        patch("agentwatch.api.server.aioredis.from_url") as mock_redis,
    ):
        mock_getenv.return_value = "redis://localhost:6379"

        mock_client = MagicMock()

        mock_client.ping = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock()

        mock_redis.return_value = mock_client

        yield mock_getenv, mock_redis


def test_health_healthy_db_and_redis(client, mock_db_session, mock_redis_healthy):
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()

    assert data["database"]["status"] == "ok"
    assert data["redis"]["status"] == "ok"
    assert data["database_connected"] is True
    assert data["version"] == __version__


def test_health_db_unavailable(client, mock_redis_healthy):
    with patch(
        "agentwatch.api.server._db_session_factory",
        side_effect=Exception("Database is Unavailable"),
    ):
        response = client.get("/health")

    assert response.status_code == 503
    data = response.json()

    assert data["database"]["status"] == "degraded"
    assert "Database is Unavailable" in data["database"]["error"]


def test_health_redis_unavailable(client, mock_db_session):
    with (
        patch("agentwatch.api.server.os.getenv") as mock_getenv,
        patch("agentwatch.api.server.aioredis.from_url") as mock_redis,
    ):
        mock_getenv.return_value = "redis://localhost:6379"
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=Exception("Redis is Unavailable"))
        mock_client.aclose = AsyncMock()

        mock_redis.return_value = mock_client

        response = client.get("/health")

        assert response.status_code == 503
        data = response.json()

        assert data["redis"]["status"] == "degraded"


def test_health_redis_not_configured(client, mock_db_session):
    with patch("agentwatch.api.server.os.getenv") as mock_getenv:
        mock_getenv.return_value = None

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert data["database"]["status"] == "ok"
        assert data["redis"]["status"] == "not_configured"


def test_health_both_degraded(client):
    with (
        patch("agentwatch.api.server._db_session_factory") as mock_factory,
        patch("agentwatch.api.server.os.getenv") as mock_getenv,
        patch("agentwatch.api.server.aioredis.from_url") as mock_redis,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("DB connection failed")
        )
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_getenv.return_value = "redis://localhost:6379"

        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=Exception("Redis connection failed"))
        mock_client.aclose = AsyncMock()
        mock_redis.from_url = AsyncMock(return_value=mock_client)

        response = client.get("/health")

        assert response.status_code == 503
        data = response.json()

        assert data["status"] == "degraded"
        assert data["database"]["status"] == "degraded"
        assert data["redis"]["status"] == "degraded"


def test_health_response_structure(client, mock_db_session, mock_redis_healthy):
    response = client.get("/health")

    data = response.json()

    expected_keys = {
        "version",
        "timestamp",
        "database_connected",
        "traces",
        "event_bus",
        "safety",
        "cost",
        "database",
        "redis",
    }
    assert expected_keys.issubset(data.keys())

    assert isinstance(data["version"], str)
    assert isinstance(data["timestamp"], str)
    assert isinstance(data["database_connected"], bool)
    assert isinstance(data["status"], str)
    assert data["status"] in ["ok", "degraded"]

    assert isinstance(data["database"], dict)
    assert "status" in data["database"]
    assert data["database"]["status"] in ["ok", "degraded", "in_memory"]

    assert isinstance(data["redis"], dict)
    assert "status" in data["redis"]
    assert data["redis"]["status"] in ["ok", "degraded", "not_configured"]


def test_health_check(client, mock_db_session):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == __version__


def test_get_sessions_empty(client):
    response = client.get("/api/v1/sessions")
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)


def test_publish_event_malformed(client):
    response = client.post("/api/v1/events", json={"invalid": "data"})
    assert response.status_code == 422  # Validation error


def test_get_governance_report(client):
    response = client.get("/api/v1/governance/compliance-report")
    assert response.status_code == 200
    assert "generated_at" in response.json()


# ---------------------------------------------------------------------------
# WebSocket /ws/events authentication tests (issue #120)
# ---------------------------------------------------------------------------


class TestWebSocketAuth:
    """Verify that /ws/events enforces API key authentication consistently
    with the REST layer, covering both the header and query-param paths as
    well as the no-key-configured (development) and key-configured cases.
    """

    def test_anonymous_connection_rejected_when_key_configured(self, monkeypatch):
        """An anonymous WebSocket connection must be rejected when
        AGENTWATCH_API_KEY is set, regardless of whether a key is supplied.
        The server closes before accepting, so the client library raises
        WebSocketDisconnect at connection entry time.
        """
        monkeypatch.setattr(_server_module, "_API_KEY", "test-secret")
        monkeypatch.setattr(_server_module, "_IS_PROD", False)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events"):
                pass

    def test_wrong_key_rejected_via_header(self, monkeypatch):
        """A connection supplying an incorrect key in X-Api-Key is rejected."""
        monkeypatch.setattr(_server_module, "_API_KEY", "correct-secret")
        monkeypatch.setattr(_server_module, "_IS_PROD", False)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events", headers={"x-api-key": "wrong-secret"}):
                pass

    def test_wrong_key_rejected_via_query_param(self, monkeypatch):
        """A connection supplying an incorrect key as ?api_key=... is rejected."""
        monkeypatch.setattr(_server_module, "_API_KEY", "correct-secret")
        monkeypatch.setattr(_server_module, "_IS_PROD", False)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events?api_key=wrong-secret"):
                pass

    def test_valid_key_accepted_via_header(self, monkeypatch):
        """A connection with the correct key in X-Api-Key is accepted."""
        monkeypatch.setattr(_server_module, "_API_KEY", "correct-secret")
        monkeypatch.setattr(_server_module, "_IS_PROD", False)
        client = TestClient(app)
        with client.websocket_connect("/ws/events", headers={"x-api-key": "correct-secret"}) as ws:
            # Connection accepted; send a keepalive ping and verify no error.
            ws.send_text("ping")

    def test_valid_key_accepted_via_query_param(self, monkeypatch):
        """A connection with the correct key as ?api_key=... is accepted."""
        monkeypatch.setattr(_server_module, "_API_KEY", "correct-secret")
        monkeypatch.setattr(_server_module, "_IS_PROD", False)
        client = TestClient(app)
        with client.websocket_connect("/ws/events?api_key=correct-secret") as ws:
            ws.send_text("ping")

    def test_no_key_configured_development_allows_connection(self, monkeypatch):
        """When AGENTWATCH_API_KEY is not set and the environment is
        development, WebSocket connections are accepted without a key,
        matching the existing REST endpoint behaviour.
        """
        monkeypatch.setattr(_server_module, "_API_KEY", None)
        monkeypatch.setattr(_server_module, "_IS_PROD", False)
        client = TestClient(app)
        with client.websocket_connect("/ws/events") as ws:
            ws.send_text("ping")

    def test_no_key_configured_production_rejects_connection(self, monkeypatch):
        """When AGENTWATCH_API_KEY is not set in production the server must
        fail-closed and reject the WebSocket connection (code 4500).
        """
        monkeypatch.setattr(_server_module, "_API_KEY", None)
        monkeypatch.setattr(_server_module, "_IS_PROD", True)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events"):
                pass


def test_websocket_payload_sanitization(client):
    from agentwatch.core.event_bus import get_event_bus
    from agentwatch.core.schema import AgentEvent, AgentFramework, EventType

    # Connect to websocket
    with client.websocket_connect("/ws/events") as ws:
        # Publish an event to the bus containing HTML
        event = AgentEvent(
            session_id="ws-test-session",
            agent_id="test-agent",
            framework=AgentFramework.CUSTOM,
            event_type=EventType.PLANNER_OUTPUT,
            planner_output_preview="<script>alert('xss')</script>",
        )

        # Publish synchronously
        get_event_bus().publish_sync(event)

        # Read from websocket
        received = ws.receive_json()
        assert (
            received["planner_output_preview"]
            == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
        )


def test_update_safety_policy_validation(client):
    # Valid payload
    resp = client.put(
        "/api/v1/safety/policy",
        json={
            "block_on_high": True,
            "block_on_critical": True,
            "require_approval_on_high": True,
            "require_approval_on_medium": False,
            "approval_timeout_seconds": 60,
        },
    )
    assert resp.status_code == 200

    # Invalid payload (timeout too low)
    resp = client.put(
        "/api/v1/safety/policy",
        json={
            "approval_timeout_seconds": 4,
        },
    )
    assert resp.status_code == 422

    # Invalid payload (timeout too high)
    resp = client.put(
        "/api/v1/safety/policy",
        json={
            "approval_timeout_seconds": 99999,
        },
    )
    assert resp.status_code == 422
