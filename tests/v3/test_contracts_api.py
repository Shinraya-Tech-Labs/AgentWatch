"""API tests for v3 Contracts and Asynchronous Contract Assessment (Issue #731).

Covers:
- POST /api/v3/contracts (validation, registration, tenant scoping)
- POST /api/v3/runs/{run_id}/assess (async job start, run/contract validation, tenant scoping)
- GET /api/v3/assessments/{assessment_id} (status, verdict, witness IDs, missing facts, tenant isolation)
- GET /api/v3/assessments/{assessment_id}/witness (streaming minimal evidence witnesses, tenant isolation)
- WebSocket /ws/assessments/{assessment_id} (progress streaming, tenant isolation)
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from agentwatch import instrument as aw
from agentwatch.api import v3 as v3api
from agentwatch.runtime.engine import Engine


@pytest.fixture
def client(store, monkeypatch):
    from agentwatch.api import server
    from agentwatch.api.middleware.rate_limiter import InMemoryBackend
    from agentwatch.api.server import app

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    engine = Engine(store)
    v3api.set_engine(engine)
    monkeypatch.delenv("AGENTWATCH_ALLOW_REEXECUTION", raising=False)
    yield TestClient(app), engine
    v3api.set_engine(None)


def _seed_run_with_transfer(client_tuple, tenant: str = "default") -> str:
    c, engine = client_tuple
    sink = aw.configure(None)
    from agentwatch.sensors.base import ListSink

    local_sink = ListSink()
    aw.configure(local_sink)

    with aw.run(f"transfer-run-{tenant}"):
        with aw.span("TOOL_INVOCATION", "approval.granted") as s1:
            s1.input({"transaction_id": "tx-123"}).output({"status": "ok"})
        with aw.span("TOOL_INVOCATION", "payments.transfer") as s2:
            s2.input({"transaction_id": "tx-123", "amount": 500}).output({"transferred": True})

    aw.configure(None)
    drafts = [d.to_dict() for d in local_sink.drafts]
    for d in drafts:
        d["tenant_id"] = tenant

    engine.ingest([type(local_sink.drafts[0]).from_dict(d) for d in drafts])
    engine.process(tenant)
    runs = engine.store.runs(engine.interp_id_for(tenant))
    assert len(runs) >= 1
    return runs[0]["run_id"]


def test_contract_registration_and_validation(client):
    c, _ = client

    # Valid contract
    payload = {
        "name": "payment-approval-contract",
        "version": "1.0",
        "description": "Payments must be preceded by approval",
        "rule": {
            "operator": "preceding_event",
            "target_operation": "payments.transfer",
            "required_operation": "approval.granted",
            "max_window_seconds": 300.0,
            "join_on": ["transaction_id"],
        },
        "require_authoritative_log_for_refutation": True,
    }
    r = c.post("/api/v3/contracts", json=payload)
    assert r.status_code in (200, 201)
    data = r.json()
    assert data["name"] == "payment-approval-contract"
    assert data["version"] == "1.0"
    assert data["rule"]["operator"] == "preceding_event"

    # Invalid contract: unknown operator
    bad_op = dict(payload)
    bad_op["rule"] = dict(payload["rule"], operator="unknown_operator")
    r2 = c.post("/api/v3/contracts", json=bad_op)
    assert r2.status_code == 400

    # Invalid contract: empty target operation
    bad_target = dict(payload)
    bad_target["rule"] = dict(payload["rule"], target_operation="")
    r3 = c.post("/api/v3/contracts", json=bad_target)
    assert r3.status_code == 400

    # Invalid contract: non-positive window
    bad_win = dict(payload)
    bad_win["rule"] = dict(payload["rule"], max_window_seconds=-10.0)
    r4 = c.post("/api/v3/contracts", json=bad_win)
    assert r4.status_code == 400


def test_asynchronous_assessment_lifecycle(client):
    c, engine = client
    run_id = _seed_run_with_transfer(client)

    # Register contract
    contract_payload = {
        "name": "payment-approval-contract",
        "version": "1.0",
        "rule": {
            "operator": "preceding_event",
            "target_operation": "payments.transfer",
            "required_operation": "approval.granted",
            "max_window_seconds": 600.0,
            "join_on": ["transaction_id"],
        },
    }
    c.post("/api/v3/contracts", json=contract_payload)

    # Start assessment
    assess_req = {
        "contract_name": "payment-approval-contract",
        "contract_version": "1.0",
        "coverage": [
            {
                "sensor_id": "audit_sensor",
                "authoritative_for": ["approval.granted"],
                "active": True,
                "dropped_count": 0,
            }
        ],
    }
    r = c.post(f"/api/v3/runs/{run_id}/assess", json=assess_req)
    assert r.status_code in (200, 202)
    resp = r.json()
    assert "assessment_id" in resp
    assessment_id = resp["assessment_id"]
    assert resp["status"] in ("pending", "running", "completed")

    # Poll status
    time.sleep(0.1)
    status_r = c.get(f"/api/v3/assessments/{assessment_id}")
    assert status_r.status_code == 200
    res = status_r.json()
    assert res["assessment_id"] == assessment_id
    assert res["status"] in ("completed", "running")
    if res["status"] == "completed":
        assert res["verdict"] in ("supported", "refuted", "inconclusive", "conflicting_evidence")
        assert "events_evaluated" in res

    # Stream witness
    witness_r = c.get(f"/api/v3/assessments/{assessment_id}/witness")
    assert witness_r.status_code == 200
    assert (
        witness_r.headers["content-type"].startswith("application/x-ndjson")
        or "stream" in witness_r.headers["content-type"]
    )


def test_assessment_unknown_run_is_404(client):
    c, _ = client
    c.post(
        "/api/v3/contracts",
        json={
            "name": "test-c",
            "rule": {
                "operator": "preceding_event",
                "target_operation": "a",
                "required_operation": "b",
            },
        },
    )
    r = c.post("/api/v3/runs/doesnotexist/assess", json={"contract_name": "test-c"})
    assert r.status_code == 404


def test_assessment_unknown_contract_is_404(client):
    c, _ = client
    run_id = _seed_run_with_transfer(client)
    r = c.post(f"/api/v3/runs/{run_id}/assess", json={"contract_name": "nonexistent-contract"})
    assert r.status_code == 404


def test_assessment_unknown_assessment_is_404(client):
    c, _ = client
    assert c.get("/api/v3/assessments/asm-doesnotexist").status_code == 404
    assert c.get("/api/v3/assessments/asm-doesnotexist/witness").status_code == 404


def test_tenant_isolation_on_contracts_and_assessments(client, monkeypatch):
    c, engine = client
    from agentwatch.api import server
    from agentwatch.api.server import app

    orig_tenant_fn = server._v3_tenant
    try:
        # Register contract under tenant A
        app.dependency_overrides[orig_tenant_fn] = lambda: "tenant-A"

        c_payload = {
            "name": "tenant-A-contract",
            "rule": {
                "operator": "preceding_event",
                "target_operation": "t.op",
                "required_operation": "r.op",
            },
        }
        r = c.post("/api/v3/contracts", json=c_payload)
        assert r.status_code in (200, 201)

        run_a = _seed_run_with_transfer(client, tenant="tenant-A")

        # Assess run A with contract under tenant A
        assess_res = c.post(
            f"/api/v3/runs/{run_a}/assess", json={"contract_name": "tenant-A-contract"}
        ).json()
        asm_id = assess_res["assessment_id"]

        # Now switch to tenant B
        app.dependency_overrides[orig_tenant_fn] = lambda: "tenant-B"

        # Tenant B cannot access tenant A's contract or run
        assert (
            c.post(
                f"/api/v3/runs/{run_a}/assess", json={"contract_name": "tenant-A-contract"}
            ).status_code
            == 404
        )

        # Tenant B cannot access tenant A's assessment
        assert c.get(f"/api/v3/assessments/{asm_id}").status_code == 404

        # Tenant B cannot access tenant A's witness stream
        assert c.get(f"/api/v3/assessments/{asm_id}/witness").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_assessment_websocket_progress(client, monkeypatch):
    c, engine = client
    from agentwatch.api import server
    from agentwatch.api.server import app

    run_id = _seed_run_with_transfer(client)
    c.post(
        "/api/v3/contracts",
        json={
            "name": "ws-contract",
            "rule": {
                "operator": "preceding_event",
                "target_operation": "payments.transfer",
                "required_operation": "approval.granted",
                "join_key": "input.transaction_id",
            },
        },
    )

    r = c.post(f"/api/v3/runs/{run_id}/assess", json={"contract_name": "ws-contract"})
    assert r.status_code == 202
    asm_id = r.json()["assessment_id"]

    # Test WebSocket connection to valid assessment
    with c.websocket_connect(f"/ws/assessments/{asm_id}") as ws:
        msg = ws.receive_json()
        assert msg["assessment_id"] == asm_id
        assert "status" in msg

    # Test WebSocket connection to non-existent assessment (should close with 4004)
    import starlette.websockets

    with pytest.raises(starlette.websockets.WebSocketDisconnect) as exc_info:
        with c.websocket_connect("/ws/assessments/asm-doesnotexist") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4004
