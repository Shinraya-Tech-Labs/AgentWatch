"""The ``memory_entries`` erasure target, through the API, on SQLite and on PostgreSQL.

Every scope seeds rows that must survive: other subjects in the same tenant, and the same
subject in another tenant. The PostgreSQL variant runs when DATABASE_URL points at PostgreSQL,
as it does in CI. Tenants are unique per test, so rows never collide on a shared database.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.dml import Delete

from panoptis.api import server
from panoptis.core.models import MemoryEntryRecord as Entry
from panoptis.memory.engine import MemoryEntry, MemoryType
from panoptis.memory.governance import _STORES, register_memory_store

BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(
            not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
            reason="needs DATABASE_URL pointing at PostgreSQL (CI sets it)",
        ),
    ),
]
UNREACHABLE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/absent"


@dataclass
class Deployment:
    client: TestClient
    acme: str
    globex: str

    def seed(self, *rows: dict) -> None:
        async def run():
            async with server._db_session_factory() as db:
                for row in rows:
                    defaults = {
                        "tenant_id": self.acme,
                        "agent_id": "bot",
                        "memory_type": "episodic",
                        "content": "a fact",
                        "entry_metadata": {},
                    }
                    db.add(Entry(**(defaults | row)))
                await db.commit()

        self.client.portal.call(run)

    def remaining(self) -> list[str]:
        async def run():
            async with server._db_session_factory() as db:
                q = select(Entry.entry_id).where(Entry.tenant_id.in_([self.acme, self.globex]))
                return sorted((await db.execute(q)).scalars())

        return self.client.portal.call(run)

    def erase(self, identifier: str, scope: str, key: str = "key-acme"):
        return self.client.post(
            "/api/v1/gdpr/erase",
            json={"identifier": identifier, "scope": scope},
            headers={"X-Api-Key": key},
        )


@pytest.fixture(params=BACKENDS)
def deployment(request, monkeypatch, tmp_path):
    tag = uuid.uuid4().hex[:12]
    acme, globex = f"acme-{tag}", f"globex-{tag}"
    if request.param == "sqlite":
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", f"{acme}=key-acme,{globex}=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    server.reset_rate_limiter_for_tests()
    with TestClient(server.app) as client:
        yield Deployment(client, acme, globex)

        async def clean():
            async with server._db_session_factory() as db:
                await db.execute(delete(Entry).where(Entry.tenant_id.in_([acme, globex])))
                await db.commit()

        client.portal.call(clean)


def _id(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:12]}"


def _assert_clean(response, erased: int) -> None:
    body = response.json()
    assert response.status_code == 200, body
    assert body["failed_targets"] == []
    assert "persisted_memory" in body["targets_reached"]
    assert body["items_erased"] == erased


def test_user_scope_erases_only_that_users_rows(deployment):
    user = _id("user")
    target, also = _id("target"), _id("also")
    other_user, no_user, same_agent_name = _id("other"), _id("none"), _id("agent")
    elsewhere = _id("globex")
    deployment.seed(
        {"entry_id": target, "entry_metadata": {"user_id": user}},
        {"entry_id": also, "agent_id": "other-bot", "entry_metadata": {"user_id": user}},
        {"entry_id": other_user, "entry_metadata": {"user_id": _id("someone")}},
        {"entry_id": no_user},
        {"entry_id": same_agent_name, "agent_id": user, "entry_metadata": {"user_id": "x"}},
        {
            "entry_id": elsewhere,
            "tenant_id": deployment.globex,
            "entry_metadata": {"user_id": user},
        },
    )

    response = deployment.erase(user, "user_id")

    _assert_clean(response, 2)
    assert deployment.remaining() == sorted([other_user, no_user, same_agent_name, elsewhere])


def test_agent_scope_erases_only_that_agents_rows(deployment):
    agent = _id("agent")
    target, also = _id("target"), _id("also")
    other_agent, same_user = _id("other"), _id("user")
    elsewhere = _id("globex")
    deployment.seed(
        {"entry_id": target, "agent_id": agent},
        {"entry_id": also, "agent_id": agent, "session_id": _id("s")},
        {"entry_id": other_agent, "agent_id": _id("bot")},
        {"entry_id": same_user, "agent_id": _id("bot"), "entry_metadata": {"user_id": agent}},
        {"entry_id": elsewhere, "agent_id": agent, "tenant_id": deployment.globex},
    )

    response = deployment.erase(agent, "agent_id")

    _assert_clean(response, 2)
    assert deployment.remaining() == sorted([other_agent, same_user, elsewhere])


def test_session_scope_erases_only_that_sessions_rows(deployment):
    session = _id("s")
    target, also = _id("target"), _id("also")
    other_session, no_session = _id("other"), _id("none")
    elsewhere = _id("globex")
    deployment.seed(
        {"entry_id": target, "session_id": session},
        {"entry_id": also, "session_id": session, "agent_id": "other-bot"},
        {"entry_id": other_session, "session_id": _id("s")},
        {"entry_id": no_session},
        {"entry_id": elsewhere, "session_id": session, "tenant_id": deployment.globex},
    )

    response = deployment.erase(session, "session_id")

    _assert_clean(response, 2)
    assert deployment.remaining() == sorted([other_session, no_session, elsewhere])


def test_a_tenant_cannot_erase_another_tenants_rows(deployment):
    theirs = _id("globex")
    deployment.seed({"entry_id": theirs, "tenant_id": deployment.globex})

    response = deployment.erase(deployment.globex, "tenant_id")

    assert response.json()["items_erased"] == 0
    assert deployment.remaining() == [theirs]


def test_a_subject_with_more_rows_than_one_delete_batch_is_fully_erased(deployment, monkeypatch):
    monkeypatch.setattr(server, "_ERASURE_DELETE_BATCH", 3)
    user = _id("user")
    keep = _id("keep")
    rows = [{"entry_id": _id("m"), "entry_metadata": {"user_id": user}} for _ in range(10)]
    deployment.seed(*rows, {"entry_id": keep})

    response = deployment.erase(user, "user_id")

    _assert_clean(response, 10)
    assert deployment.remaining() == [keep]


def _unreachable_factory():
    return async_sessionmaker(create_async_engine(UNREACHABLE), expire_on_commit=False)


def test_unreachable_sql_storage_is_reported_and_nothing_is_claimed(deployment, monkeypatch):
    user = _id("user")
    row = _id("row")
    deployment.seed({"entry_id": row, "entry_metadata": {"user_id": user}})

    with monkeypatch.context() as outage:
        outage.setattr(server, "_db_session_factory", _unreachable_factory())
        response = deployment.erase(user, "user_id")

    body = response.json()
    assert response.status_code == 207, body
    assert {"persisted_memory", "sessions_and_events"} <= set(body["failed_targets"])
    assert "persisted_memory" not in body["targets_reached"]
    assert "sessions_and_events" not in body["targets_reached"]
    assert body["items_erased"] == 0
    assert body["failure_count"] >= 2
    assert body["audit_signature"]
    assert deployment.remaining() == [row]


def test_a_failed_delete_leaves_the_rows_and_is_reported(deployment, monkeypatch):
    user = _id("user")
    row = _id("row")
    deployment.seed({"entry_id": row, "entry_metadata": {"user_id": user}})
    real = AsyncSession.execute

    async def refuse_memory_deletes(self, statement, *args, **kwargs):
        if isinstance(statement, Delete) and statement.table.name == "memory_entries":
            raise RuntimeError("storage refused the delete")
        return await real(self, statement, *args, **kwargs)

    with monkeypatch.context() as broken:
        broken.setattr(AsyncSession, "execute", refuse_memory_deletes)
        response = deployment.erase(user, "user_id")

    body = response.json()
    assert response.status_code == 207, body
    assert body["failed_targets"] == ["persisted_memory"]
    assert "persisted_memory" not in body["targets_reached"]
    assert "sessions_and_events" in body["targets_reached"]
    assert body["items_erased"] == 0
    assert deployment.remaining() == [row]


def test_a_second_batch_failing_rolls_back_the_first(deployment, monkeypatch):
    monkeypatch.setattr(server, "_ERASURE_DELETE_BATCH", 1)
    user = _id("user")
    rows = [_id("a"), _id("b")]
    deployment.seed(*[{"entry_id": r, "entry_metadata": {"user_id": user}} for r in rows])
    real = AsyncSession.execute
    deletes = []

    async def fail_on_second_delete(self, statement, *args, **kwargs):
        if isinstance(statement, Delete) and statement.table.name == "memory_entries":
            deletes.append(statement)
            if len(deletes) == 2:
                raise RuntimeError("connection lost")
        return await real(self, statement, *args, **kwargs)

    with monkeypatch.context() as broken:
        broken.setattr(AsyncSession, "execute", fail_on_second_delete)
        response = deployment.erase(user, "user_id")

    assert len(deletes) == 2
    assert response.status_code == 207
    assert response.json()["items_erased"] == 0
    assert deployment.remaining() == sorted(rows)


class _Store:
    def __init__(self, *entries):
        self.entries = list(entries)

    def all_entries(self):
        return list(self.entries)

    def remove(self, entry_id):
        self.entries = [e for e in self.entries if e.entry_id != entry_id]
        return True


def test_a_partial_failure_counts_only_what_was_really_erased(deployment, monkeypatch):
    user = _id("user")
    row = _id("row")
    deployment.seed({"entry_id": row, "entry_metadata": {"user_id": user}})
    store = _Store(
        MemoryEntry(
            entry_id=_id("mem"),
            agent_id="bot",
            memory_type=MemoryType.EPISODIC,
            content="a fact",
            metadata={"user_id": user},
        )
    )
    register_memory_store(store, tenant_id=deployment.acme)

    try:
        with monkeypatch.context() as outage:
            outage.setattr(server, "_db_session_factory", _unreachable_factory())
            response = deployment.erase(user, "user_id")
    finally:
        _STORES.pop(store, None)  # the registry is process-wide

    body = response.json()
    assert response.status_code == 207, body
    assert "memory" in body["targets_reached"]
    assert "persisted_memory" in body["failed_targets"]
    assert body["items_erased"] == 1
    assert store.entries == []
    assert deployment.remaining() == [row]
