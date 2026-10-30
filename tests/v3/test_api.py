"""/api/v3 endpoints, the OTLP receiver and the legacy tee."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from otlp_fixture import OTLP

from panoptis import instrument as aw
from panoptis.api import v3 as v3api
from panoptis.runtime.engine import Engine


@pytest.fixture
def client(store, monkeypatch):
    from panoptis.api import server
    from panoptis.api.middleware.rate_limiter import InMemoryBackend
    from panoptis.api.server import app

    # the legacy per-user limit (100/hour) is process-global; give this module a fresh quota
    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    engine = Engine(store)
    v3api.set_engine(engine)
    monkeypatch.delenv("AGENTWATCH_ALLOW_REEXECUTION", raising=False)
    yield TestClient(app), engine
    v3api.set_engine(None)


def _record(sink) -> list[dict]:
    with aw.run("api-demo"):
        with aw.span("OPERATION", "plan", actor="agent:p"):
            with aw.span("TOOL_INVOCATION", "search", object="tool:search") as s:
                s.input({"q": "x"}).output(["a", "b"])
    return [d.to_dict() for d in sink.drafts]


def test_ingest_then_read_everything(client, sink):
    c, _ = client
    body = {"observations": _record(sink)}
    r = c.post("/api/v3/observations", json=body)
    assert r.status_code == 200 and r.json()["accepted"] == len(body["observations"])
    assert c.post("/api/v3/observations", json=body).json()["duplicates"] == len(
        body["observations"]
    )
    runs = c.get("/api/v3/runs").json()["runs"]
    assert len(runs) == 1
    rid = runs[0]["run_id"]
    detail = c.get(f"/api/v3/runs/{rid[:8]}").json()
    assert detail["run"]["run_id"] == rid and detail["tree"]
    events = c.get(f"/api/v3/runs/{rid}/events").json()["events"]
    tool = next(e for e in events if e["kind"] == "TOOL_INVOCATION")
    ev = c.get(f"/api/v3/events/{tool['event_id'][:10]}").json()
    assert ev["evidence"] and ev["evidence"][0]["payload"]
    assert any(i["type"] == "CONTAINS" for i in ev["incoming"])
    graph = c.get(f"/api/v3/runs/{rid}/graph?view=execution").json()
    assert graph["relations"] and all(r["view"] == "EXECUTION" for r in graph["relations"])
    prov = c.get(f"/api/v3/provenance/event:{tool['event_id']}").json()
    assert prov["text"]
    obs_id = ev["evidence"][0]["obs_id"]
    assert c.get(f"/api/v3/observations/{obs_id}").json()["observation"]["obs_id"] == obs_id
    assert c.post("/api/v3/evidence/verify").json()["ok"] is True
    assert c.get("/api/v3/status").json()["capabilities"]
    assert c.get("/api/v3/live").json()["recent_events"]
    assert c.get("/api/v3/motifs").status_code == 200
    q = c.post("/api/v3/query", json={"text": f"events {rid[:8]} kind=TOOL_INVOCATION"}).json()
    assert q["answered"] and q["evidence"] == [tool["event_id"]]


def test_unknown_run_is_404(client):
    c, _ = client
    assert c.get("/api/v3/runs/doesnotexist").status_code == 404


def test_tenant_cannot_be_chosen_by_payload(client, sink):
    c, engine = client
    obs = _record(sink)
    for o in obs:
        o["tenant_id"] = "someone-else"
    c.post("/api/v3/observations", json={"observations": obs})
    assert engine.store.count_observations("someone-else") == 0
    assert engine.store.count_observations("default") == len(obs)


def test_invalid_envelopes_are_rejected_individually(client, sink):
    c, _ = client
    obs = _record(sink)
    r = c.post("/api/v3/observations", json={"observations": [{"nope": 1}, *obs]}).json()
    assert r["accepted"] == len(obs) and r["rejected"][0]["index"] == 0


def test_otlp_receiver(client):
    c, _ = client
    r = c.post("/v1/traces", json=OTLP)
    assert r.status_code == 200
    events = c.get(
        f"/api/v3/runs/{c.get('/api/v3/runs').json()['runs'][0]['run_id']}/events"
    ).json()["events"]
    assert {e["kind"] for e in events} >= {"MODEL_INVOCATION", "RETRIEVAL"}


def test_reexecution_is_disabled_by_default(client, sink):
    c, _ = client
    c.post("/api/v3/observations", json={"observations": _record(sink)})
    rid = c.get("/api/v3/runs").json()["runs"][0]["run_id"]
    assert c.post("/api/v3/replay", json={"run": rid, "level": "L2"}).status_code == 403
    r = c.post("/api/v3/replay", json={"run": rid, "level": "L1"}).json()
    assert r["replay_level"] == "L1" and r["consistent_with_stored_interpretation"] is True


def test_hypothesis_lifecycle_llm_cannot_self_verify(client, sink):
    c, _ = client
    c.post("/api/v3/observations", json={"observations": _record(sink)})
    h = c.post(
        "/api/v3/hypotheses",
        json={
            "cause": "event:a",
            "effect": "event:b",
            "statement": "a causes b",
            "proposer": "llm",
        },
    ).json()
    assert h["status"] == "PROPOSED" and h["evidence_class"] is None
    # correlational evidence never settles a causal claim
    h2 = c.post(
        f"/api/v3/hypotheses/{h['hypothesis_id']}/evidence",
        json={"kind": "CORRELATION", "direction": "supports", "summary": "co-occur"},
    ).json()
    assert h2["status"] == "PROPOSED" and h2["evidence_class"] == "CORRELATIONAL"
    # interventional evidence must reference a recorded experiment
    bad = c.post(
        f"/api/v3/hypotheses/{h['hypothesis_id']}/evidence",
        json={"kind": "INTERVENTION", "direction": "supports", "summary": "trust me"},
    )
    assert bad.status_code == 400


def test_legacy_v1_events_are_teed_into_v3(client):
    c, engine = client
    r = c.post(
        "/api/v1/events",
        json={
            "session_id": "s-tee",
            "agent_id": "a",
            "event_type": "goal.set",
            "goal": "write docs",
        },
    )
    assert r.status_code == 200
    obs = engine.store.observations()
    assert (
        obs
        and obs[0].source_kind == "legacy.agent_event"
        and obs[0].declared("session_id") == "s-tee"
    )


def test_status_names_the_store_backend_without_credentials(client):
    c, engine = client
    body = c.get("/api/v3/status").json()
    assert body["store_backend"] == "sqlite"
    assert engine.store.url not in str(body)  # the URL (may hold credentials) is not exposed
