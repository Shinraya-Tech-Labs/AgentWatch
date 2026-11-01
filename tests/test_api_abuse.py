"""API tests for the entitlement usage / abuse endpoint (issue #463)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import panoptis.api.server as server

    monkeypatch.setattr(server, "_API_KEY", None)
    server._usage_tracker.reset()
    return TestClient(server.app)


def test_usage_requires_machine_id(client):
    resp = client.post("/api/v1/entitlement/usage", json={"subject": "user"})
    assert resp.status_code == 400


def test_single_device_not_flagged(client):
    resp = client.post(
        "/api/v1/entitlement/usage", json={"subject": "user", "machine_id": "device-a"}
    )
    assert resp.status_code == 200
    assert resp.json()["abuse_detected"] is False


def test_second_device_flagged(client):
    client.post("/api/v1/entitlement/usage", json={"subject": "user", "machine_id": "device-a"})
    resp = client.post(
        "/api/v1/entitlement/usage",
        json={"subject": "user"},
        headers={"X-Machine-Id": "device-b"},
    )
    assert resp.status_code == 200
    assert resp.json()["abuse_detected"] is True
    assert resp.json()["active_devices"] == 2
