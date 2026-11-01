"""TO-003: cross-tenant access through the API fails.

Cloud mode with real tenant-store keys, and self-hosted mode with forged headers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentwatch import instrument as aw
from agentwatch.api import server, tenant_auth
from agentwatch.api import v3 as v3api
from agentwatch.runtime.engine import Engine
from agentwatch.storage.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    from agentwatch.api.middleware.rate_limiter import InMemoryBackend

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    store = Store(f"sqlite:///{(tmp_path / 'aw.db').as_posix()}")
    v3api.set_engine(Engine(store))
    yield TestClient(server.app)
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
