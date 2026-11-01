"""TO-004: session erasure is tenant-safe (real database, two tenants).

Uses SQLite through aiosqlite with the real v1 schema. Every test seeds tenant A and
tenant B and proves that erasing as A never modifies or deletes B's rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

pytest.importorskip("aiosqlite")

from panoptis.core.models import (  # noqa: E402
    CheckpointRecord,
    EventRecord,
    Repository,
    SessionRecord,
    TaskRecord,
    init_db,
)

A, B = "tenant-a", "tenant-b"


@pytest.fixture
async def factory(tmp_path):
    return await init_db(f"sqlite+aiosqlite:///{(tmp_path / 'v1.db').as_posix()}")


async def seed(
    factory, tenant: str, session_id: str, *, events: int = 3, event_tenant: str | None = "same"
) -> None:
    async with factory() as db:
        db.add(
            SessionRecord(
                session_id=session_id,
                tenant_id=tenant,
                agent_id="bot",
                framework="custom",
                status="running",
            )
        )
        await db.flush()
        for i in range(events):
            db.add(
                EventRecord(
                    event_id=str(uuid.uuid4()),
                    tenant_id=tenant if event_tenant == "same" else event_tenant,
                    session_id=session_id,
                    agent_id="bot",
                    framework="custom",
                    event_type="tool.call",
                    status="running",
                    step_number=i,
                    timestamp=datetime.now(UTC),
                )
            )
        db.add(TaskRecord(task_id=str(uuid.uuid4()), session_id=session_id, title="t"))
        db.add(
            CheckpointRecord(
                checkpoint_id=str(uuid.uuid4()),
                session_id=session_id,
                step_number=0,
                checkpoint_type="manual",
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()


async def counts(factory, tenant: str | None = None) -> dict[str, int]:
    async with factory() as db:
        out = {}
        for name, model in (("sessions", SessionRecord), ("events", EventRecord)):
            q = select(func.count()).select_from(model)
            if tenant is not None:
                q = q.where(model.tenant_id == tenant)
            out[name] = (await db.execute(q)).scalar_one()
        for name, model in (("tasks", TaskRecord), ("checkpoints", CheckpointRecord)):
            q = select(func.count()).select_from(model)
            if tenant is not None:
                q = q.where(
                    model.session_id.in_(
                        select(SessionRecord.session_id).where(SessionRecord.tenant_id == tenant)
                    )
                )
            out[name] = (await db.execute(q)).scalar_one()
        return out


async def test_tenant_a_cannot_erase_tenant_b_session(factory):
    await seed(factory, A, "sess-a")
    await seed(factory, B, "sess-b")
    before_b = await counts(factory, B)
    async with factory() as db:
        removed = await Repository(db).erase_session("sess-b", tenant_id=A)
        await db.commit()
    assert removed == 0
    assert await counts(factory, B) == before_b, "tenant B's rows were modified"


async def test_owner_erases_its_session_and_all_related_rows(factory):
    await seed(factory, A, "sess-a", events=4)
    await seed(factory, B, "sess-b")
    before_b = await counts(factory, B)
    async with factory() as db:
        removed = await Repository(db).erase_session("sess-a", tenant_id=A)
        await db.commit()
    assert removed == 1 + 4 + 1 + 1  # session, events, task, checkpoint
    assert await counts(factory, A) == {"sessions": 0, "events": 0, "tasks": 0, "checkpoints": 0}
    assert await counts(factory, B) == before_b


async def test_unknown_and_foreign_sessions_are_indistinguishable(factory):
    await seed(factory, B, "sess-b")
    async with factory() as db:
        repo = Repository(db)
        foreign = await repo.erase_session("sess-b", tenant_id=A)
        unknown = await repo.erase_session("sess-does-not-exist", tenant_id=A)
        await db.commit()
    assert foreign == unknown == 0


async def test_another_tenants_events_reusing_the_session_id_survive(factory):
    await seed(factory, A, "shared-id", events=2)
    async with factory() as db:  # tenant B's events recorded under the same session id
        for i in range(2):
            db.add(
                EventRecord(
                    event_id=str(uuid.uuid4()),
                    tenant_id=B,
                    session_id="shared-id",
                    agent_id="b",
                    framework="custom",
                    event_type="tool.call",
                    status="running",
                    step_number=i,
                    timestamp=datetime.now(UTC),
                )
            )
        await db.commit()
    async with factory() as db:
        await Repository(db).erase_session("shared-id", tenant_id=A)
        await db.commit()
    assert (await counts(factory, B))["events"] == 2


async def test_api_erasure_target_is_tenant_scoped_for_every_scope(factory, monkeypatch):
    from panoptis.api import server
    from panoptis.governance.gdpr import ErasureScope

    monkeypatch.setattr(server, "_db_session_factory", factory)
    monkeypatch.setattr(server, "_CLOUD_MODE", True)
    await seed(factory, A, "sess-a")
    await seed(factory, B, "sess-b")
    before_b = await counts(factory, B)
    target = server._SessionErasureTarget(tenant_id=A)
    for scope, ident in (
        (ErasureScope.SESSION_ID, "sess-b"),
        (ErasureScope.AGENT_ID, "bot"),
        (ErasureScope.TENANT_ID, B),
    ):
        await target.erase_matching(ident, scope)
    assert await counts(factory, B) == before_b, "an erasure scope reached tenant B's rows"
    assert (await counts(factory, A))["sessions"] == 0, (
        "the agent-scope erase did remove A's own session"
    )


async def _seed_user_sessions(factory) -> None:
    async with factory() as db:
        for sid, user in (("u-alice-1", "alice"), ("u-alice-2", "alice"), ("u-bob", "bob")):
            db.add(
                SessionRecord(
                    session_id=sid,
                    tenant_id=A,
                    agent_id="bot",
                    framework="custom",
                    status="running",
                    session_metadata={"user_id": user},
                )
            )
        db.add(
            SessionRecord(
                session_id="u-alice-other-tenant",
                tenant_id=B,
                agent_id="bot",
                framework="custom",
                status="running",
                session_metadata={"user_id": "alice"},
            )
        )
        await db.commit()


async def _session_ids(factory) -> set[str]:
    async with factory() as db:
        return set((await db.execute(select(SessionRecord.session_id))).scalars())


async def test_user_scope_erases_only_that_users_sessions_in_the_tenant(factory, monkeypatch):
    from panoptis.api import server
    from panoptis.governance.gdpr import ErasureScope

    monkeypatch.setattr(server, "_db_session_factory", factory)
    monkeypatch.setattr(server, "_CLOUD_MODE", True)
    await _seed_user_sessions(factory)
    target = server._SessionErasureTarget(tenant_id=A)
    assert await target.count_matching("alice", ErasureScope.USER_ID) == 2
    await target.erase_matching("alice", ErasureScope.USER_ID)
    assert await _session_ids(factory) == {"u-bob", "u-alice-other-tenant"}


async def test_unknown_filter_keys_match_nothing_instead_of_everything(factory):
    await _seed_user_sessions(factory)
    async with factory() as db:
        repo = Repository(db)
        assert await repo.get_session_ids({"tenant_id": A, "no_such_column": "x"}) == []
        assert await repo.count_sessions({"tenant_id": A, "no_such_column": "x"}) == 0
