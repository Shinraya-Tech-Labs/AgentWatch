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


# ── TO-008: trust model of the dry-run endpoint ──────────────────────────────────────

PRIVATE_RULE_ID = "ACME_INTERNAL_RULE_7"
PRIVATE_RULE_REASON = "acme internal: blocks the payroll export tool"


@pytest.fixture
def custom_policy(monkeypatch):
    import dataclasses

    from panoptis.api import server
    from panoptis.core.safety import RiskPattern

    pol = dataclasses.replace(
        server._safety_engine.policy,
        custom_patterns=[
            RiskPattern(
                pattern=r"payroll[-_]export",
                risk_level=RiskLevel.HIGH,
                reason=PRIVATE_RULE_REASON,
                policy_id=PRIVATE_RULE_ID,
            )
        ],
    )
    monkeypatch.setattr(server._safety_engine, "_policy", pol, raising=False)
    monkeypatch.setattr(type(server._safety_engine), "policy", property(lambda self: pol))
    return pol


@pytest.fixture
def cloud_keys(monkeypatch):
    from panoptis.api import server, tenant_auth

    store = tenant_auth.TenantStore()
    monkeypatch.setattr(tenant_auth, "_tenant_store", store)
    monkeypatch.setattr(server, "_CLOUD_MODE", True)
    monkeypatch.setenv("AGENTWATCH_ADMIN_KEY", "operator-secret-key")
    t = store.create_tenant("acme")
    _, raw = store.create_api_key(t.tenant_id)
    return {"tenant": {"X-Api-Key": raw}, "operator": {"X-Api-Key": "operator-secret-key"}}


def _check(client, headers=None, **body):
    return client.post(
        "/api/v1/safety/check",
        json={"command": "run payroll-export now", "tool_name": "bash", **body},
        headers=headers or {},
    )


def test_requires_a_credential_when_a_key_is_configured(client, monkeypatch):
    from panoptis.api import server

    monkeypatch.setattr(server, "_API_KEY", "configured-key")
    assert _check(client).status_code == 401
    assert _check(client, {"X-Api-Key": "configured-key"}).status_code == 200


def test_cloud_tenant_does_not_see_the_policy_rules(client, custom_policy, cloud_keys):
    r = _check(client, cloud_keys["tenant"])
    assert r.status_code == 200
    body = r.text
    assert PRIVATE_RULE_ID not in body and PRIVATE_RULE_REASON not in body
    data = r.json()
    assert data["threat_path"] == [] and "custom-policy" in data["matched_policies"]
    assert data["risk_level"] in ("high", "critical")


def test_policy_owner_gets_the_full_threat_path(client, custom_policy, cloud_keys, monkeypatch):
    from panoptis.api import server

    r = _check(client, cloud_keys["operator"])
    assert r.status_code == 200 and PRIVATE_RULE_ID in r.text
    assert any(n["policy_id"] == PRIVATE_RULE_ID and n["matched"] for n in r.json()["threat_path"])
    monkeypatch.setattr(server, "_CLOUD_MODE", False)  # self-hosted: the key holder owns the policy
    assert PRIVATE_RULE_ID in _check(client).text


@pytest.mark.parametrize(
    "body",
    [
        {"command": "x" * 9000},
        {"arguments": {"blob": "y" * 20000}},
        {"arguments": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}},
        {"affected_resources": [str(i) for i in range(101)]},
        {"tool_name": "t" * 200},
    ],
)
def test_oversized_or_deep_input_is_rejected_before_matching(client, body, monkeypatch):
    from panoptis.core.safety import RiskScorer

    called = []
    monkeypatch.setattr(
        RiskScorer, "score", lambda self, tc: called.append(1) or ("safe", 0.0, [], [])
    )
    assert _check(client, **body).status_code == 422
    assert called == [], "the policy must not be evaluated on rejected input"


def test_rate_limited(client, monkeypatch):
    from panoptis.api import server
    from panoptis.api.middleware.rate_limiter import InMemoryBackend

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    monkeypatch.setattr(server, "RATE_WRITE", 3)
    codes = [_check(client).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]
