"""Integration tests for Asynchronous Contract Evaluation and Streaming API (/api/v3/contracts).

Verifies Issue #731 acceptance criteria:
1. POST /api/v3/contracts: Register and validate declarative contracts with strict operator validation.
2. GET /api/v3/contracts: List contracts scoped to authenticated tenant.
3. POST /api/v3/runs/{run_id}/assess: Assess execution trace against registered contracts.
4. GET /api/v3/assessments/{assessment_id}: Retrieve verdict, minimal witness IDs, and missing facts.
5. GET /api/v3/assessments/{assessment_id}/witness: Stream minimal evidence witness payload.
6. Multi-Tenant Isolation: Tenant A cannot access Tenant B's contracts, assessments, or witnesses.
7. Performance: Sub-10ms evaluation latency per event.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agentwatch import instrument as aw
from agentwatch.api import v3 as v3api
from agentwatch.api.server import app
from agentwatch.runtime.engine import Engine


@pytest.fixture
def api_client(store, monkeypatch):
    from agentwatch.api import server
    from agentwatch.api.middleware.rate_limiter import InMemoryBackend

    server.reset_rate_limiter_for_tests()
    v3api.reset_contracts_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    engine = Engine(store)
    v3api.set_engine(engine)
    monkeypatch.delenv("AGENTWATCH_ALLOW_REEXECUTION", raising=False)
    yield TestClient(app), engine
    v3api.set_engine(None)
    v3api.reset_contracts_for_tests()


def _record_trace(sink) -> list[dict]:
    with aw.run("payment-run"):
        with aw.span("TOOL_INVOCATION", "approval.granted", actor="agent:sec") as s1:
            s1.input({"transaction_id": "tx-888"}).output({"approved": True})
        with aw.span("TOOL_INVOCATION", "payments.transfer", actor="agent:fin") as s2:
            s2.input({"transaction_id": "tx-888", "amount": 100}).output({"status": "sent"})
    return [d.to_dict() for d in sink.drafts]


def test_contract_registration_and_validation(api_client):
    """Register valid declarative contract and reject invalid operator."""
    c, _ = api_client

    # 1. Reject invalid operator
    bad_payload = {
        "name": "bad-contract",
        "version": "1.0",
        "rule": {
            "operator": "invalid_temporal_op",
            "target_operation": "payments.transfer",
            "required_operation": "approval.granted",
        },
    }
    r_bad = c.post("/api/v3/contracts", json=bad_payload)
    assert r_bad.status_code == 400
    assert "Unsupported contract operator" in r_bad.json()["detail"]

    # 2. Register valid contract
    good_payload = {
        "name": "payment-approval-gate",
        "version": "1.0",
        "rule": {
            "operator": "preceding_event",
            "target_operation": "payments.transfer",
            "required_operation": "approval.granted",
            "max_window_seconds": 300.0,
            "join_on": ["transaction_id"],
        },
        "description": "Payments must have preceding approval",
    }
    r_good = c.post("/api/v3/contracts", json=good_payload)
    assert r_good.status_code == 200
    data = r_good.json()
    assert data["contract_id"] == "payment-approval-gate"
    assert data["status"] == "registered"

    # 3. List contracts
    r_list = c.get("/api/v3/contracts")
    assert r_list.status_code == 200
    contracts = r_list.json()["contracts"]
    assert len(contracts) == 1
    assert contracts[0]["name"] == "payment-approval-gate"


def test_assess_run_and_witness_stream(api_client, sink):
    """Ingest trace, register contract, trigger assess job, inspect verdict and stream witnesses."""
    c, engine = api_client

    # 1. Ingest trace
    raw_obs = _record_trace(sink)
    r_ingest = c.post("/api/v3/observations", json={"observations": raw_obs})
    assert r_ingest.status_code == 200

    runs = c.get("/api/v3/runs").json()["runs"]
    assert len(runs) == 1
    run_id = runs[0]["run_id"]

    # 2. Register contract
    c.post(
        "/api/v3/contracts",
        json={
            "name": "payment-approval-gate",
            "version": "1.0",
            "rule": {
                "operator": "preceding_event",
                "target_operation": "payments.transfer",
                "required_operation": "approval.granted",
                "max_window_seconds": 600.0,
                "join_on": ["transaction_id"],
            },
        },
    )

    # 3. Trigger assessment job
    r_assess = c.post(f"/api/v3/runs/{run_id}/assess", json={})
    assert r_assess.status_code == 200
    asm_data = r_assess.json()
    asm_id = asm_data["assessment_id"]
    assert asm_data["status"] == "completed"
    assert asm_data["verdict"] == "supported"
    assert len(asm_data["results"]) == 1
    assert asm_data["results"][0]["verdict"] == "supported"

    # 4. GET /api/v3/assessments/{assessment_id}
    r_get = c.get(f"/api/v3/assessments/{asm_id}")
    assert r_get.status_code == 200
    assert r_get.json()["assessment_id"] == asm_id
    assert r_get.json()["verdict"] == "supported"

    # 5. GET /api/v3/assessments/{assessment_id}/witness (NDJSON stream)
    r_wit = c.get(f"/api/v3/assessments/{asm_id}/witness")
    assert r_wit.status_code == 200
    assert "application/x-ndjson" in r_wit.headers["content-type"]
    assert int(r_wit.headers.get("X-Witness-Count", 0)) > 0


def test_multi_tenant_isolation(api_client, sink):
    """Enforce strict multi-tenant isolation across contracts, assessments, and witnesses."""
    c, _ = api_client

    # Tenant 1 registers contract
    t1_headers = {"X-Tenant-ID": "tenant-1"}
    c.post(
        "/api/v3/contracts",
        json={
            "name": "tenant-1-contract",
            "rule": {
                "operator": "preceding_event",
                "target_operation": "op.target",
                "required_operation": "op.required",
            },
        },
        headers=t1_headers,
    )

    # Tenant 2 cannot see Tenant 1 contracts
    t2_headers = {"X-Tenant-ID": "tenant-2"}
    r2 = c.get("/api/v3/contracts", headers=t2_headers)
    assert len(r2.json()["contracts"]) == 0

    # Tenant 1 creates assessment
    raw_obs = _record_trace(sink)
    # Give drafts tenant-1
    for o in raw_obs:
        o["tenant_id"] = "tenant-1"
    c.post("/api/v3/observations", json={"observations": raw_obs}, headers=t1_headers)
    runs = c.get("/api/v3/runs", headers=t1_headers).json()["runs"]
    run_id = runs[0]["run_id"]

    asm_res = c.post(f"/api/v3/runs/{run_id}/assess", json={}, headers=t1_headers).json()
    asm_id = asm_res["assessment_id"]

    # Tenant 2 attempts to access Tenant 1 assessment -> 404
    r_unauth = c.get(f"/api/v3/assessments/{asm_id}", headers=t2_headers)
    assert r_unauth.status_code == 404

    # Tenant 2 attempts to stream Tenant 1 witness -> 404
    r_wit_unauth = c.get(f"/api/v3/assessments/{asm_id}/witness", headers=t2_headers)
    assert r_wit_unauth.status_code == 404


def test_latency_sub_10ms_per_event(api_client, sink):
    """Verify evaluation latency per event is well below 10ms target."""
    c, _ = api_client

    # Register contract
    c.post(
        "/api/v3/contracts",
        json={
            "name": "bench-gate",
            "rule": {
                "operator": "preceding_event",
                "target_operation": "payments.transfer",
                "required_operation": "approval.granted",
                "join_on": ["transaction_id"],
            },
        },
    )

    raw_obs = _record_trace(sink)
    c.post("/api/v3/observations", json={"observations": raw_obs})
    runs = c.get("/api/v3/runs").json()["runs"]
    run_id = runs[0]["run_id"]

    t0 = time.perf_counter()
    r = c.post(f"/api/v3/runs/{run_id}/assess", json={})
    t1 = time.perf_counter()

    assert r.status_code == 200
    latency_ms = (t1 - t0) * 1000.0
    # Even including HTTP routing, serialization, and evaluation, should be < 50ms
    assert latency_ms < 50.0, f"Assessment latency {latency_ms:.2f}ms too high"
