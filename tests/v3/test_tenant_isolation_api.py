"""TO-003: cross-tenant access through the API fails.

Cloud mode with real tenant-store keys, and self-hosted mode with forged headers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from panoptis import instrument as aw
from panoptis.api import server, tenant_auth
from panoptis.api import v3 as v3api
from panoptis.runtime.engine import Engine
from panoptis.storage.store import Store


def _sid(name: str) -> str:
    """Session ids are global in the collector: make each test's ids unique."""
    import uuid

    return f"{name}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from panoptis.api.middleware.rate_limiter import InMemoryBackend

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    store = Store(f"sqlite:///{(tmp_path / 'aw.db').as_posix()}")
    v3api.set_engine(Engine(store))
    with TestClient(server.app) as c:  # runs the lifespan: the collector subscribes to the bus
        yield c
    v3api.set_engine(None)
    store.close()


@pytest.fixture
def cloud(monkeypatch):
    """Cloud mode with a fresh tenant store and two tenants."""
    store = tenant_auth.TenantStore()
    monkeypatch.setattr(tenant_auth, "_tenant_store", store)
    monkeypatch.setattr(server, "_CLOUD_MODE", True)
    a = store.create_tenant("acme")
    b = store.create_tenant("globex")
    ka, raw_a = store.create_api_key(a.tenant_id, "a")
    kb, raw_b = store.create_api_key(b.tenant_id, "b")
    return {
        "store": store,
        "a": a,
        "b": b,
        "key_a": ka,
        "raw_a": raw_a,
        "key_b": kb,
        "raw_b": raw_b,
    }


def _drafts(sink) -> list[dict]:
    sink.drafts.clear()
    with aw.run("tenant-run"):
        with aw.span("TOOL_INVOCATION", "lookup", actor="agent:a") as s:
            s.output({"ok": True})
    return [d.to_dict() for d in sink.drafts]


def test_cloud_tenant_cannot_read_another_tenants_observation(client, cloud, sink):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    r = client.post("/api/v3/observations", json={"observations": _drafts(sink)}, headers=a)
    assert r.status_code == 200, r.text
    obs_id = r.json()["obs_ids"][0]
    assert client.get(f"/api/v3/observations/{obs_id}", headers=a).status_code == 200
    assert client.get(f"/api/v3/observations/{obs_id}", headers=b).status_code == 404
    assert client.get("/api/v3/status", headers=b).json()["observations"] == 0


def test_cloud_payload_tenant_is_ignored(client, cloud, sink):
    a = {"X-Api-Key": cloud["raw_a"]}
    drafts = _drafts(sink)
    for d in drafts:
        d["tenant_id"] = cloud["b"].tenant_id  # attempt to write into tenant B
    client.post("/api/v3/observations", json={"observations": drafts}, headers=a)
    assert (
        client.get("/api/v3/status", headers={"X-Api-Key": cloud["raw_b"]}).json()["observations"]
        == 0
    )
    assert client.get("/api/v3/status", headers=a).json()["observations"] == len(drafts)


def test_cloud_header_cannot_switch_tenant(client, cloud):
    forged = {"X-Api-Key": cloud["raw_a"], "X-Tenant-ID": cloud["b"].tenant_id}
    assert client.get("/api/v3/status", headers=forged).status_code == 403
    same = {"X-Api-Key": cloud["raw_a"], "X-Tenant-ID": cloud["a"].tenant_id}
    assert client.get("/api/v3/status", headers=same).status_code == 200


def test_cloud_missing_revoked_and_expired_keys_are_rejected(client, cloud):
    assert client.get("/api/v3/status").status_code == 401
    assert (
        client.get("/api/v3/status", headers={"X-Tenant-ID": cloud["a"].tenant_id}).status_code
        == 401
    )
    assert (
        client.get("/api/v3/status", headers={"X-Api-Key": "aw_not_a_real_key_000000"}).status_code
        == 401
    )
    cloud["store"].revoke_api_key(cloud["key_a"].key_id)
    assert client.get("/api/v3/status", headers={"X-Api-Key": cloud["raw_a"]}).status_code == 401
    cloud["key_b"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get("/api/v3/status", headers={"X-Api-Key": cloud["raw_b"]}).status_code == 401


def test_suspended_tenant_keys_are_rejected(client, cloud):
    cloud["store"].suspend_tenant(cloud["a"].tenant_id)
    assert client.get("/api/v3/status", headers={"X-Api-Key": cloud["raw_a"]}).status_code == 401
    assert client.get("/api/v3/status", headers={"X-Api-Key": cloud["raw_b"]}).status_code == 200


def test_self_hosted_header_cannot_select_a_tenant(client):
    assert client.get("/api/v3/status").status_code == 200
    assert client.get("/api/v3/status", headers={"X-Tenant-ID": "default"}).status_code == 200
    assert client.get("/api/v3/status", headers={"X-Tenant-ID": "victim-tenant"}).status_code == 403


def _event(session_id: str, **meta) -> dict:
    return {
        "session_id": session_id,
        "agent_id": "bot",
        "event_type": "tool.call",
        "metadata": meta,
    }


def test_v1_events_are_tagged_with_the_authenticated_tenant(client, cloud):
    a = {"X-Api-Key": cloud["raw_a"]}
    r = client.post("/api/v1/events", json=_event("sess-a1", tenant_id="spoofed"), headers=a)
    assert r.status_code == 200, r.text
    trace = server._collector.get_trace("sess-a1")
    assert trace is not None
    assert trace.session.metadata["tenant_id"] == cloud["a"].tenant_id
    events = server._collector.get_events("sess-a1")
    assert events and all(e.metadata["tenant_id"] == cloud["a"].tenant_id for e in events)


def test_v1_tenant_cannot_append_to_or_claim_another_tenants_session(client, cloud):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    client.post("/api/v1/events", json=_event("sess-owned-by-a"), headers=a)
    before = server._collector.get_trace("sess-owned-by-a").event_count
    assert (
        client.post("/api/v1/events", json=_event("sess-owned-by-a"), headers=b).status_code == 404
    )
    session = {"session_id": "sess-owned-by-a", "agent_id": "bot", "framework": "custom"}
    assert client.post("/api/v1/sessions", json=session, headers=b).status_code == 409
    assert server._collector.get_trace("sess-owned-by-a").event_count == before


async def test_collector_drops_a_racing_event_from_another_tenant():
    from panoptis.core.schema import AgentEvent, EventType
    from panoptis.tracing.collector import TraceCollector

    c = TraceCollector()
    await c.ingest(
        AgentEvent(
            session_id="s",
            agent_id="a",
            event_type=EventType.TOOL_CALL,
            metadata={"tenant_id": "t1"},
        )
    )
    await c.ingest(
        AgentEvent(
            session_id="s",
            agent_id="b",
            event_type=EventType.TOOL_CALL,
            metadata={"tenant_id": "t2"},
        )
    )
    assert c.get_trace("s").event_count == 1


def test_v1_events_are_teed_into_the_tenants_v3_store(client, cloud):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    client.post("/api/v1/events", json=_event("sess-tee"), headers=a)
    assert client.get("/api/v3/status", headers=a).json()["observations"] >= 1
    assert client.get("/api/v3/status", headers=b).json()["observations"] == 0


SESSION_READS = [
    "",
    "/events",
    "/trace",
    "/confidence",
    "/reasoning",
    "/cost",
    "/replay",
    "/checkpoints",
]


@pytest.mark.parametrize("suffix", SESSION_READS)
def test_v1_session_reads_are_tenant_scoped(client, cloud, suffix):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    sid = _sid("sess-private")
    client.post("/api/v1/events", json=_event(sid), headers=a)
    other = client.get(f"/api/v1/sessions/{sid}{suffix}", headers=b)
    assert other.status_code == 404, f"{suffix}: {other.status_code} {other.text[:200]}"
    assert client.get(f"/api/v1/sessions/{sid}{suffix}", headers=a).status_code != 404


@pytest.mark.parametrize("suffix", ["/simulate", "/rollback"])
def test_v1_session_actions_are_tenant_scoped(client, cloud, suffix):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    sid = _sid("sess-act")
    client.post("/api/v1/events", json=_event(sid), headers=a)
    body = {"rewind_to_step": 0} if suffix == "/simulate" else {}
    assert client.post(f"/api/v1/sessions/{sid}{suffix}", json=body, headers=b).status_code == 404


def test_v1_session_list_shows_only_the_callers_sessions(client, cloud):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    client.post("/api/v1/events", json=_event("sess-list-a"), headers=a)
    client.post("/api/v1/events", json=_event("sess-list-b"), headers=b)
    ids_a = {x["session_id"] for x in client.get("/api/v1/sessions", headers=a).json()["sessions"]}
    ids_b = {x["session_id"] for x in client.get("/api/v1/sessions", headers=b).json()["sessions"]}
    assert "sess-list-a" in ids_a and "sess-list-b" not in ids_a
    assert "sess-list-b" in ids_b and "sess-list-a" not in ids_b


OPERATOR_GETS = [
    "/api/v1/system/status",
    "/api/v1/dashboard/summary",
    "/api/v1/dashboard/top",
    "/api/v1/safety/blocked",
    "/api/v1/safety/policy",
    "/api/v1/compliance/audit-log",
    "/api/v1/governance/compliance-report",
    "/api/v1/ingestion/metrics",
    "/api/v1/tenants",
]


@pytest.mark.parametrize("path", OPERATOR_GETS)
def test_cloud_tenant_keys_cannot_reach_operator_routes(client, cloud, path, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_ADMIN_KEY", "operator-secret-key")
    assert client.get(path, headers={"X-Api-Key": cloud["raw_a"]}).status_code == 403
    assert client.get(path, headers={"X-Api-Key": "operator-secret-key"}).status_code not in (
        401,
        403,
    )


def test_cloud_operator_writes_need_the_operator_key(client, cloud, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_ADMIN_KEY", "operator-secret-key")
    tenant_key = {"X-Api-Key": cloud["raw_a"]}
    assert (
        client.post("/api/v1/tenants", json={"name": "rogue"}, headers=tenant_key).status_code
        == 403
    )
    assert (
        client.delete("/api/v1/sessions/prune?older_than_hours=1", headers=tenant_key).status_code
        == 403
    )
    assert client.post("/api/v1/demo/seed", headers=tenant_key).status_code == 403


def test_cloud_operator_routes_are_closed_when_no_operator_key_is_configured(
    client, cloud, monkeypatch
):
    monkeypatch.delenv("AGENTWATCH_ADMIN_KEY", raising=False)
    for key in (cloud["raw_a"], "", "anything"):
        assert client.get("/api/v1/tenants", headers={"X-Api-Key": key}).status_code == 403


def test_cloud_tenant_can_read_only_its_own_tenant_record(client, cloud, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_ADMIN_KEY", "operator-secret-key")
    a = {"X-Api-Key": cloud["raw_a"]}
    assert client.get(f"/api/v1/tenants/{cloud['a'].tenant_id}", headers=a).status_code == 200
    assert client.get(f"/api/v1/tenants/{cloud['b'].tenant_id}", headers=a).status_code == 403


def test_self_hosted_operator_routes_still_work_with_the_api_key(client):
    assert client.get("/api/v1/dashboard/summary").status_code == 200


def test_cloud_websocket_requires_a_tenant_key(client, cloud):
    from starlette.websockets import WebSocketDisconnect

    for headers in ({}, {"x-api-key": "aw_not_a_real_key_000000"}):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/events", headers=headers) as ws:
                ws.receive_text()
        assert exc.value.code == 4001
    with pytest.raises(WebSocketDisconnect) as exc:
        forged = {"x-api-key": cloud["raw_a"], "x-tenant-id": cloud["b"].tenant_id}
        with client.websocket_connect("/ws/events", headers=forged) as ws:
            ws.receive_text()
    assert exc.value.code == 4003


def test_cloud_websocket_delivers_only_the_connections_tenant_events(client, cloud):
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    sid_a, sid_b = _sid("ws-a"), _sid("ws-b")
    with client.websocket_connect("/ws/events", headers={"x-api-key": cloud["raw_a"]}) as ws:
        assert client.post("/api/v1/events", json=_event(sid_b), headers=b).status_code == 200
        assert client.post("/api/v1/events", json=_event(sid_a), headers=a).status_code == 200
        first = ws.receive_json()
    assert first["session_id"] == sid_a, "tenant A's socket received another tenant's event"


def _erase(client, headers, identifier: str, scope: str = "session_id"):
    return client.post(
        "/api/v1/gdpr/erase", json={"identifier": identifier, "scope": scope}, headers=headers
    )


def test_gdpr_erase_removes_the_owners_cached_session_and_spares_other_tenants(
    client, cloud, monkeypatch
):
    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    sid_a, sid_b = _sid("gdpr-a"), _sid("gdpr-b")
    client.post("/api/v1/events", json=_event(sid_a), headers=a)
    client.post("/api/v1/events", json=_event(sid_b), headers=b)
    r = _erase(client, a, sid_a)
    assert r.status_code in (200, 207), r.text
    assert r.json()["items_erased"] >= 1
    assert client.get(f"/api/v1/sessions/{sid_a}", headers=a).status_code == 404, (
        "erased session still served"
    )
    assert client.get(f"/api/v1/sessions/{sid_b}", headers=b).status_code == 200


def test_gdpr_erase_of_a_foreign_session_looks_like_an_unknown_one(client, cloud, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    a, b = {"X-Api-Key": cloud["raw_a"]}, {"X-Api-Key": cloud["raw_b"]}
    sid_b = _sid("gdpr-foreign")
    client.post("/api/v1/events", json=_event(sid_b), headers=b)
    foreign = _erase(client, a, sid_b).json()
    unknown = _erase(client, a, _sid("gdpr-never-existed")).json()
    for key in ("items_erased", "failure_count", "failed_targets", "scope"):
        assert foreign[key] == unknown[key], key
    assert foreign["items_erased"] == 0
    assert client.get(f"/api/v1/sessions/{sid_b}", headers=b).status_code == 200
