"""TO-031: the GDPR erasure endpoint really erases memory, per tenant, visibly on failure.

Before: the memory target imported ``list_memory_entries`` / ``drop_memory_entries`` from
``panoptis.memory.governance``, which defined neither, so every erasure reported a failed
target (HTTP 207) and no memory was ever erased; its count also matched by substring.
"""

from __future__ import annotations

import gc
import os
import uuid

import pytest

from panoptis.memory.engine import MemoryEngine
from panoptis.memory.governance import (
    drop_memory_entries,
    list_memory_entries,
    memory_stores_registered,
    register_memory_store,
)
from panoptis.protect.ledger import KIND_DECISION, ProtectLedger


class _NoEmbed:
    async def embed(self, texts):
        return [None for _ in texts]


async def _engine(tenant: str = "default") -> MemoryEngine:
    return MemoryEngine(embedding_provider=_NoEmbed(), tenant_id=tenant)


@pytest.fixture(autouse=True)
def _no_leftover_stores():
    gc.collect()  # stores are weakly held: engines of earlier tests drop out
    yield
    gc.collect()


async def test_list_and_drop_match_exactly_by_scope():
    eng = await _engine()
    await eng.store("agent-1", "likes tea", session_id="s1", metadata={"user_id": "u-1"})
    await eng.store("agent-1", "likes coffee", session_id="s2", metadata={"user_id": "u-10"})
    await eng.store("agent-2", "other", session_id="s1", metadata={"user_id": "u-2"})
    assert len(await list_memory_entries("u-1", scope="user_id")) == 1  # not u-10 (exact match)
    assert len(await list_memory_entries("agent-1", scope="agent_id")) == 2
    assert len(await list_memory_entries("s1", scope="session_id")) == 2
    assert await drop_memory_entries("u-1", scope="user_id") == 1
    assert [e["user_id"] for e in await list_memory_entries()] == ["u-10", "u-2"]
    with pytest.raises(ValueError):
        await drop_memory_entries("  ")
    with pytest.raises(ValueError):
        await list_memory_entries("x", scope="nope")


async def test_stores_are_tenant_scoped():
    a, b = await _engine("acme"), await _engine("globex")
    await a.store("bot", "acme memory", metadata={"user_id": "u-1"})
    await b.store("bot", "globex memory", metadata={"user_id": "u-1"})
    assert await drop_memory_entries("u-1", scope="user_id", tenant_id="acme") == 1
    assert [e["content"] for e in await list_memory_entries(tenant_id="globex")] == [
        "globex memory"
    ]
    assert await drop_memory_entries("acme", scope="tenant_id", tenant_id="globex") == 0


async def test_discarded_engines_drop_out_of_the_registry():
    eng = await _engine()
    assert memory_stores_registered() >= 1
    del eng
    gc.collect()
    assert memory_stores_registered() == 0


# ── the endpoint ────────────────────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from panoptis.api import server

    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    with TestClient(server.app) as c:
        yield c


def _erase(c, key: str | None, identifier: str, scope: str = "user_id"):
    headers = {"X-Api-Key": key} if key else {}
    return c.post(
        "/api/v1/gdpr/erase", json={"identifier": identifier, "scope": scope}, headers=headers
    )


async def test_endpoint_erases_the_subjects_memories_with_a_clean_receipt(api):
    eng = await _engine("acme")
    await eng.store("bot", "u-7 lives in Paris", metadata={"user_id": "u-7"})
    await eng.store("bot", "u-7 prefers email", metadata={"user_id": "u-7"})
    await eng.store("bot", "u-8 note", metadata={"user_id": "u-8"})
    r = _erase(api, "key-acme", "u-7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failure_count"] == 0 and body["failed_targets"] == [] and body["items_erased"] >= 2
    assert body["audit_signature"]
    remaining = await list_memory_entries(tenant_id="acme")
    assert [e["user_id"] for e in remaining] == ["u-8"]


async def test_endpoint_never_reaches_another_tenants_memories(api):
    acme, globex = await _engine("acme"), await _engine("globex")
    await acme.store("bot", "acme fact", metadata={"user_id": "u-1"})
    await globex.store("bot", "globex fact", metadata={"user_id": "u-1"})
    assert _erase(api, "key-acme", "u-1").status_code == 200
    assert await list_memory_entries(tenant_id="acme") == []
    assert [e["content"] for e in await list_memory_entries(tenant_id="globex")] == ["globex fact"]
    # erasing a whole tenant: only one's own
    r = _erase(api, "key-acme", "globex", scope="tenant_id")
    assert r.status_code == 200 and len(await list_memory_entries(tenant_id="globex")) == 1


async def test_without_memory_stores_the_target_is_not_claimed(api):
    gc.collect()
    assert memory_stores_registered() == 0
    r = _erase(api, "key-acme", "u-9")
    assert r.status_code == 200 and r.json()["failed_targets"] == []


async def test_a_failing_memory_store_is_a_visible_partial_failure(api):
    class Broken:
        def all_entries(self):
            raise OSError("memory backend down")

    broken = Broken()
    register_memory_store(broken, tenant_id="acme")
    r = _erase(api, "key-acme", "u-1")
    assert r.status_code == 207 and "memory" in r.json()["failed_targets"]
    del broken


def test_erasure_requires_authentication(api):
    assert _erase(api, None, "u-1").status_code == 401
    assert _erase(api, "wrong", "u-1").status_code == 401


async def test_erasure_does_not_bypass_evidence_integrity(api, tmp_path):
    lg = ProtectLedger(f"sqlite:///{(tmp_path / 'ev.db').as_posix()}", seal_every=0)
    lg.record(
        KIND_DECISION, {"verdict": "allow", "user": "u-5"}, {"verdict": "allow"}, tenant_id="acme"
    )
    lg.store.seal_all("acme")
    eng = await _engine("acme")
    await eng.store("bot", "u-5 memory", metadata={"user_id": "u-5"})
    assert _erase(api, "key-acme", "u-5").status_code == 200
    assert await list_memory_entries(tenant_id="acme") == []
    # the append-only evidence chain is untouched and still verifies (its erasure path is
    # crypto-shredding, docs/EVIDENCE_INTEGRITY.md)
    assert lg.verify(tenant_id="acme", anchor=False)["ok"]
    assert len(lg.query((KIND_DECISION,), tenant_id="acme")) == 1
    lg.store.close()


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="needs DATABASE_URL pointing at PostgreSQL (CI sets it)",
)
def test_sql_erasure_in_a_self_hosted_multi_tenant_deployment_stays_in_the_callers_tenant(api):
    agent = f"pg-bot-{uuid.uuid4().hex[:8]}"
    for key, sid in (("key-acme", f"{agent}-a"), ("key-globex", f"{agent}-g")):
        r = api.post(
            "/api/v1/sessions",
            json={"session_id": sid, "agent_id": agent, "framework": "custom"},
            headers={"X-Api-Key": key},
        )
        assert r.status_code == 200, r.text
    first = _erase(api, "key-acme", agent, scope="agent_id").json()
    # acme's session from the trace cache and from SQL, never globex's SQL row
    assert first["failed_targets"] == [] and first["items_erased"] == 2, first
    second = _erase(api, "key-globex", agent, scope="agent_id").json()
    assert second["failed_targets"] == [] and second["items_erased"] == 2, second
