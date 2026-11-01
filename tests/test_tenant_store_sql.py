"""TO-006: durable tenant and API-key store.

Runs on SQLite and, when AGENTWATCH_TEST_PG_URL is set (CI), on PostgreSQL.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from panoptis.api.tenant_store_sql import SqlTenantStore, schema_meta
from panoptis.models.tenant import TenantPlan, TenantStatus

URLS = ["sqlite"] + (["postgresql"] if os.environ.get("AGENTWATCH_TEST_PG_URL") else [])


@pytest.fixture(params=URLS)
def url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{(tmp_path / 'tenants.db').as_posix()}"
        return
    # isolated schema per test on the CI PostgreSQL service
    from sqlalchemy import create_engine, text

    base = os.environ["AGENTWATCH_TEST_PG_URL"]
    schema = f"t_{uuid.uuid4().hex[:10]}"
    eng = create_engine(base)
    with eng.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield f"{base}{'&' if '?' in base else '?'}options=-csearch_path%3D{schema}"
    with eng.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    eng.dispose()


def reopen(store: SqlTenantStore) -> SqlTenantStore:
    """Simulate a process restart: a brand-new store object on the same database."""
    store.close()
    return SqlTenantStore(store.url)


def test_tenants_keys_scopes_and_revocations_survive_a_restart(url):
    s = SqlTenantStore(url)
    acme = s.create_tenant("acme", plan=TenantPlan.PROFESSIONAL, metadata={"region": "eu"})
    k_ro, raw_ro = s.create_api_key(acme.tenant_id, "readonly", scopes=["session:read"])
    k_rw, raw_rw = s.create_api_key(acme.tenant_id, "writer")
    s.revoke_api_key(k_rw.key_id)
    s = reopen(s)
    t = s.get_tenant(acme.tenant_id)
    assert (
        t is not None
        and t.name == "acme"
        and t.metadata == {"region": "eu"}
        and t.status is TenantStatus.ACTIVE
    )
    got = s.validate_api_key(raw_ro)
    assert got is not None and got.scopes == ["session:read"] and got.tenant_id == acme.tenant_id
    assert s.validate_api_key(raw_rw) is None, "revocation must survive the restart"
    assert {k.name for k in s.list_api_keys(acme.tenant_id)} == {"readonly", "writer"}
    s.close()


def test_suspension_revokes_every_key_atomically_and_persists(url):
    s = SqlTenantStore(url)
    t = s.create_tenant("globex")
    raws = [s.create_api_key(t.tenant_id, f"k{i}")[1] for i in range(3)]
    other = s.create_tenant("other")
    _, raw_other = s.create_api_key(other.tenant_id)
    assert s.suspend_tenant(t.tenant_id)
    s = reopen(s)
    assert all(s.validate_api_key(r) is None for r in raws)
    assert s.get_tenant(t.tenant_id).status is TenantStatus.SUSPENDED
    assert s.validate_api_key(raw_other) is not None
    s.close()


def test_only_the_hash_is_stored(tmp_path):
    db = tmp_path / "tenants.db"
    s = SqlTenantStore(f"sqlite:///{db.as_posix()}")
    t = s.create_tenant("acme")
    _, raw = s.create_api_key(t.tenant_id, "ci")
    s.close()
    con = sqlite3.connect(db)
    dumped = "\n".join(
        str(row)
        for table in ("aw_api_keys", "aw_tenant_audit", "aw_tenants")
        for row in con.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed names
    )
    con.close()
    assert raw not in dumped and raw[20:] not in dumped
    con = sqlite3.connect(db)
    (stored,) = con.execute("SELECT key_hash FROM aw_api_keys").fetchone()
    con.close()
    salt, digest = stored.split(":")
    assert len(salt) == 32 and len(digest) == 64, "expected a PBKDF2 salt:hash, not the key"


def test_expired_unknown_and_malformed_keys_are_rejected(url):
    s = SqlTenantStore(url)
    t = s.create_tenant("acme")
    _, raw = s.create_api_key(
        t.tenant_id, "old", expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    assert s.validate_api_key(raw) is None
    for bad in ("", "aw_nope", raw[:20] + "tampered-suffix"):
        assert s.validate_api_key(bad) is None
    s.close()


def test_key_creation_requires_an_existing_tenant(url):
    s = SqlTenantStore(url)
    with pytest.raises(KeyError):
        s.create_api_key("no-such-tenant")
    s.close()


def test_audit_trail_records_lifecycle_events(url):
    s = SqlTenantStore(url)
    t = s.create_tenant("acme")
    k, _ = s.create_api_key(t.tenant_id, "ci")
    s.revoke_api_key(k.key_id)
    s.suspend_tenant(t.tenant_id)
    s = reopen(s)
    actions = [e["action"] for e in reversed(s.audit_log(t.tenant_id))]
    assert actions == ["tenant.create", "key.create", "key.revoke", "tenant.suspend"]
    s.close()


def test_usage_persists_and_limits_apply(url):
    s = SqlTenantStore(url)
    t = s.create_tenant("acme")
    s.record_usage(t.tenant_id, tokens=10, usd_cost=0.5, events=2)
    s.record_usage(t.tenant_id, tokens=5, events=1)
    s = reopen(s)
    u = s.get_usage(t.tenant_id)
    assert (u.tokens_used, u.events_count, u.requests_count) == (15, 3, 2)
    assert s.check_usage_limits(t.tenant_id) == (True, "OK")
    s.update_tenant(
        t.tenant_id,
        config={
            **{
                k: getattr(t.config, k)
                for k in (
                    "max_sessions",
                    "max_tokens_per_month",
                    "max_usd_per_month",
                    "ingestion_rate_limit",
                    "retention_days",
                )
            },
            "max_events_per_day": 3,
        },
    )
    assert s.check_usage_limits(t.tenant_id) == (False, "Daily event limit exceeded")
    s.close()


def test_concurrent_create_and_revoke_are_consistent(url):
    s = SqlTenantStore(url)
    t = s.create_tenant("acme")
    created: list[tuple[str, str]] = []
    lock = threading.Lock()

    def make() -> None:
        k, raw = s.create_api_key(t.tenant_id)
        with lock:
            created.append((k.key_id, raw))

    threads = [threading.Thread(target=make) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len({kid for kid, _ in created}) == 8
    revoke = [threading.Thread(target=s.revoke_api_key, args=(kid,)) for kid, _ in created[:4]]
    for th in revoke:
        th.start()
    for th in revoke:
        th.join()
    valid = [s.validate_api_key(raw) is not None for _, raw in created]
    assert valid == [False] * 4 + [True] * 4
    s.close()


def test_database_failure_denies_validation_and_fails_writes(url):
    s = SqlTenantStore(url)
    t = s.create_tenant("acme")
    _, raw = s.create_api_key(t.tenant_id)
    broken = SqlTenantStore.__new__(SqlTenantStore)
    broken.url = "sqlite:////nonexistent-dir/x/y.db"
    from sqlalchemy import create_engine

    broken.engine = create_engine(broken.url)
    broken._lock = threading.Lock()
    assert broken.validate_api_key(raw) is None, "a DB error must deny, not allow"
    with pytest.raises(Exception):  # noqa: B017 - any DB error: the write must not look successful
        broken.create_tenant("ghost")
    s.close()


def test_a_newer_schema_is_refused(url):
    s = SqlTenantStore(url)
    from sqlalchemy import update

    with s.engine.begin() as c:
        c.execute(update(schema_meta).where(schema_meta.c.key == "version").values(value="99"))
    s.close()
    with pytest.raises(RuntimeError, match="newer"):
        SqlTenantStore(url)


@pytest.fixture
def fresh_singleton(monkeypatch, tmp_path):
    from panoptis.api import tenant_auth

    for var in ("AGENTWATCH_TENANT_STORE", "AGENTWATCH_STORE", "AGENTWATCH_CLOUD_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENTWATCH_HOME", str(tmp_path / "home"))
    tenant_auth.reset_tenant_store_for_tests()
    yield tenant_auth
    tenant_auth.reset_tenant_store_for_tests()


def test_self_hosted_default_is_in_memory(fresh_singleton):
    assert type(fresh_singleton.get_tenant_store()).__name__ == "TenantStore"


def test_cloud_mode_never_uses_memory(fresh_singleton, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_CLOUD_MODE", "true")
    store = fresh_singleton.get_tenant_store()
    assert isinstance(store, SqlTenantStore)
    assert store.url.endswith("home/tenants.db")
    t = store.create_tenant("acme")
    _, raw = store.create_api_key(t.tenant_id)
    fresh_singleton.reset_tenant_store_for_tests()  # "restart"
    assert fresh_singleton.get_tenant_store().validate_api_key(raw) is not None


def test_explicit_tenant_store_url_wins(fresh_singleton, tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'explicit.db').as_posix()}"
    monkeypatch.setenv("AGENTWATCH_TENANT_STORE", url)
    assert fresh_singleton.get_tenant_store().url == url


def test_cloud_api_keys_survive_an_api_restart(fresh_singleton, monkeypatch):
    """End to end through the API: a key created over HTTP still authenticates after the
    tenant store is reloaded (process restart), and a revocation made before it holds."""
    from fastapi.testclient import TestClient

    from panoptis.api import server

    monkeypatch.setenv("AGENTWATCH_CLOUD_MODE", "true")
    monkeypatch.setattr(server, "_CLOUD_MODE", True)
    monkeypatch.setenv("AGENTWATCH_ADMIN_KEY", "operator-secret-key")
    with TestClient(server.app) as c:
        op = {"X-Api-Key": "operator-secret-key"}
        tid = c.post("/api/v1/tenants?name=acme", headers=op).json()["tenant_id"]
        first = c.post(f"/api/v1/tenants/{tid}/api-keys?name=k0", headers=op)
        assert first.status_code == 200, first.text  # the operator issues the first key
        own = {"X-Api-Key": first.json()["key"]}
        second = c.post(f"/api/v1/tenants/{tid}/api-keys?name=k1", headers=own)
        assert second.status_code == 200, second.text  # the tenant manages its own keys
        keys = [first.json(), second.json()]
        other = c.post("/api/v1/tenants?name=globex", headers=op).json()["tenant_id"]
        assert c.post(f"/api/v1/tenants/{other}/api-keys", headers=own).status_code == 403
        assert c.get(f"/api/v1/tenants/{tid}/api-keys", headers=own).status_code == 200
    raws = [k["key"] for k in keys]
    assert all(raws), keys
    store = fresh_singleton.get_tenant_store()
    store.revoke_api_key(store.validate_api_key(raws[1]).key_id)
    fresh_singleton.reset_tenant_store_for_tests()  # restart
    with TestClient(server.app) as c:
        assert c.get("/api/v3/status", headers={"X-Api-Key": raws[0]}).status_code == 200
        assert c.get("/api/v3/status", headers={"X-Api-Key": raws[1]}).status_code == 401
