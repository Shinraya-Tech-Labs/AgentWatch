from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from panoptis.api.server import app, reset_rate_limiter_for_tests


@pytest.fixture
def client():
    reset_rate_limiter_for_tests()
    return TestClient(app)


def test_health_check_reports_db_status(client):
    response = client.get("/health")
    data = response.json()

    assert "database_connected" in data
    assert isinstance(data["database_connected"], bool)

    assert data["status"] in {"ok", "degraded"}
    expected_http = 200 if data["status"] == "ok" else 503
    assert response.status_code == expected_http


def test_system_status_endpoint(client):
    # Protect with API key if needed, but in local dev it's usually None
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    data = response.json()
    assert "database" in data
    assert "connected" in data["database"]
    assert "mode" in data["database"]
    assert data["database"]["mode"] in ["persistent", "in-memory"]
