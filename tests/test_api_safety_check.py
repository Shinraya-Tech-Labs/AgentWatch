import pytest
from fastapi.testclient import TestClient

from panoptis.api.server import create_app
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import RiskLevel, SafetyCheckData


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_safety_check_blocks_critical(client):
    res = client.post("/api/v1/safety/check", json={"command": "rm -rf /", "tool_name": "bash"})
    assert res.status_code == 200
    data = res.json()
    assert data["blocked"] is True
    assert data["risk_level"] in ("critical", "high")


def test_safety_check_allows_safe(client):
    res = client.post("/api/v1/safety/check", json={"command": "echo hello", "tool_name": "bash"})
    assert res.status_code == 200
    data = res.json()
    assert data["blocked"] is False
    assert data["risk_level"] in ("safe", "low")


def test_safety_check_keeps_command_argument_in_sync(client, monkeypatch):
    captured = {}

    class DummyCheckedEvent:
        safety = SafetyCheckData(
            risk_level=RiskLevel.SAFE,
            risk_score=0.0,
            blocked=False,
            reasons=[],
            matched_policies=[],
            requires_approval=False,
        )

    async def fake_check_event(self, event):
        captured["tool_call"] = event.tool_call
        return DummyCheckedEvent()

    monkeypatch.setattr(SafetyEngine, "check_event", fake_check_event)

    res = client.post(
        "/api/v1/safety/check",
        json={
            "command": "echo hello",
            "tool_name": "bash",
            "arguments": {"command": "rm -rf /", "other": "value"},
        },
    )

    assert res.status_code == 200
    assert captured["tool_call"].raw_command == "echo hello"
    assert captured["tool_call"].arguments["command"] == "echo hello"
    assert captured["tool_call"].arguments["other"] == "value"
