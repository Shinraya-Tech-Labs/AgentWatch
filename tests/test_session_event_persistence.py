"""Session ownership and first-event persistence, on SQLite and on PostgreSQL.

The session id is a global primary key, so one tenant owns it for good. These tests prove that
the database decides that, after a restart and under concurrency, and that a burst of first
events for a new session all arrive. PostgreSQL runs when DATABASE_URL points at it, as in CI.
``AGENTWATCH_BURST_REPEATS`` sets how many times each burst repeats.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from panoptis.api import server
from panoptis.core.event_bus import get_event_bus
from panoptis.core.models import (
    EventConflictError,
    EventRecord,
    Repository,
    SessionOwnershipError,
    SessionRecord,
    TenantRepository,
    init_db,
)
from panoptis.core.schema import AgentEvent, EventType

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
BURST = 20
REPEATS = int(os.environ.get("AGENTWATCH_BURST_REPEATS", "25"))
ACME = {"X-Api-Key": "key-acme"}
GLOBEX = {"X-Api-Key": "key-globex"}


def _id(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:12]}"


def _restart() -> None:
    """A restart empties the collector, and with it the in-memory ownership guard."""
    server._collector._traces.clear()
    server._collector._session_index.clear()


@dataclass
class Deployment:
    client: TestClient
    acme: str
    globex: str

    def run(self, coroutine_function, *args):
        return self.client.portal.call(coroutine_function, *args)

    def session(self, session_id: str, key: dict = ACME, **fields):
        body = {"session_id": session_id, "agent_id": "bot", "framework": "custom"} | fields
        return self.client.post("/api/v1/sessions", json=body, headers=key)

    def event(self, session_id: str, key: dict = ACME, **fields):
        body = {"session_id": session_id, "agent_id": "bot", "event_type": "tool.call"} | fields
        return self.client.post("/api/v1/events", json=body, headers=key)

    def row(self, session_id: str):
        async def read():
            async with server._db_session_factory() as db:
                return await db.get(SessionRecord, session_id)

        return self.run(read)

    def event_tenants(self, session_id: str) -> list:
        async def read():
            async with server._db_session_factory() as db:
                q = select(EventRecord.tenant_id).where(EventRecord.session_id == session_id)
                return list((await db.execute(q)).scalars())

        return self.run(read)

    def event_count(self, session_id: str) -> int:
        return len(self.event_tenants(session_id))

    def concurrently(self, *requests):
        """Send requests at once through the whole HTTP stack, on the app's own loop."""

        async def send():
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                return await asyncio.gather(
                    *[http.post(path, json=body, headers=key) for path, body, key in requests]
                )

        return self.run(send)

    def burst(self, session_id: str, tenant: str, count: int = BURST) -> None:
        """A burst straight at the write path, as an in-process publisher would cause."""
        events = [
            AgentEvent(
                session_id=session_id,
                agent_id="bot",
                event_type=EventType.TOOL_CALL,
                step_number=step,
            )
            for step in range(count)
        ]

        async def write():
            await asyncio.gather(*[server._pg_write_event(e, tenant_id=tenant) for e in events])

        self.run(write)


def _start(request, monkeypatch, tmp_path):
    if request.param == "sqlite":
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'p.db').as_posix()}")
    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    server.reset_rate_limiter_for_tests()


async def _clean(tenants) -> None:
    async with server._db_session_factory() as db:
        await db.execute(delete(EventRecord).where(EventRecord.tenant_id.in_(tenants)))
        await db.execute(delete(SessionRecord).where(SessionRecord.tenant_id.in_(tenants)))
        await db.commit()


@pytest.fixture(params=BACKENDS)
def deployment(request, monkeypatch, tmp_path):
    tag = uuid.uuid4().hex[:12]
    acme, globex = f"acme-{tag}", f"globex-{tag}"
    _start(request, monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", f"{acme}=key-acme,{globex}=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    with TestClient(server.app) as client:
        yield Deployment(client, acme, globex)
        client.portal.call(_clean, [acme, globex])


class _Key:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id
        self.scopes = ["*"]


class _Store:
    """The tenant store of a cloud deployment, reduced to what these requests touch."""

    def __init__(self, keys):
        self.keys = keys

    def validate_api_key(self, key):
        return _Key(self.keys[key]) if key in self.keys else None

    def __getattr__(self, name):
        return lambda *args, **kwargs: True


@pytest.fixture(params=BACKENDS)
def cloud(request, monkeypatch, tmp_path):
    tag = uuid.uuid4().hex[:12]
    acme, globex = f"acme-{tag}", f"globex-{tag}"
    _start(request, monkeypatch, tmp_path)
    monkeypatch.delenv("AGENTWATCH_TENANT_KEYS", raising=False)
    monkeypatch.setattr(server, "_CLOUD_MODE", True)
    store = _Store({"key-acme": acme, "key-globex": globex})
    monkeypatch.setattr(server, "get_tenant_store", lambda: store)
    with TestClient(server.app) as client:
        yield Deployment(client, acme, globex)
        client.portal.call(_clean, [acme, globex])


# ── ownership ──────────────────────────────────────────────────────────────────────────────


def test_a_second_tenant_cannot_take_a_session_after_a_restart(deployment):
    sid = _id("s")
    assert deployment.session(sid, agent_id="acme-bot", goal="acme goal").status_code == 200
    _restart()

    refused = deployment.session(sid, GLOBEX, agent_id="globex-bot")

    assert refused.status_code == 409
    row = deployment.row(sid)
    assert (row.tenant_id, row.agent_id, row.goal) == (deployment.acme, "acme-bot", "acme goal")
    assert server._collector.get_trace(sid) is None  # a refused write leaves nothing in memory


def test_a_second_tenants_event_cannot_take_a_session_after_a_restart(deployment):
    sid = _id("s")
    deployment.session(sid, agent_id="acme-bot", goal="acme goal")
    deployment.event(sid)
    _restart()

    refused = deployment.event(sid, GLOBEX, agent_id="globex-bot")

    assert refused.status_code == 404
    row = deployment.row(sid)
    assert (row.tenant_id, row.agent_id, row.goal) == (deployment.acme, "acme-bot", "acme goal")
    assert deployment.event_tenants(sid) == [deployment.acme]
    assert server._collector.get_trace(sid) is None


def test_an_owner_is_not_locked_out_by_a_refused_tenant(deployment):
    sid = _id("s")
    deployment.session(sid)
    _restart()
    assert deployment.event(sid, GLOBEX).status_code == 404

    assert deployment.event(sid).status_code == 200

    assert deployment.event_tenants(sid) == [deployment.acme]


def test_the_owner_keeps_working_after_a_restart(deployment):
    sid = _id("s")
    deployment.session(sid, goal="first")
    _restart()

    assert deployment.session(sid, goal="second").status_code == 200
    assert deployment.event(sid).status_code == 200

    assert deployment.row(sid).goal == "second"
    assert deployment.event_count(sid) == 1


def test_a_write_through_the_bus_cannot_take_a_session(deployment, caplog):
    sid = _id("s")
    deployment.session(sid, goal="acme goal")
    _restart()
    before = server._persistence["events_not_stored"]

    with caplog.at_level(logging.ERROR, logger="panoptis.api.server"):
        deployment.burst(sid, deployment.globex, count=1)

    assert deployment.row(sid).tenant_id == deployment.acme
    assert deployment.event_count(sid) == 0
    assert "SessionOwnershipError" in caplog.text
    assert server._persistence["events_not_stored"] == before + 1


@pytest.mark.parametrize("rounds", [10])
def test_two_tenants_creating_one_session_id_leave_exactly_one_owner(deployment, rounds):
    for _ in range(rounds):
        sid = _id("s")
        body = {"session_id": sid, "agent_id": "bot", "framework": "custom"}
        first, second = deployment.concurrently(
            ("/api/v1/sessions", body, ACME), ("/api/v1/sessions", body, GLOBEX)
        )
        assert sorted((first.status_code, second.status_code)) == [200, 409]
        winner = deployment.acme if first.status_code == 200 else deployment.globex
        assert deployment.row(sid).tenant_id == winner
        assert server._collector.get_trace(sid).session.metadata["tenant_id"] == winner


@pytest.mark.parametrize("rounds", [10])
def test_two_tenants_sending_first_events_leave_exactly_one_owner(deployment, rounds):
    for _ in range(rounds):
        sid = _id("s")
        body = {"session_id": sid, "agent_id": "bot", "event_type": "tool.call"}
        requests = [("/api/v1/events", body, key) for key in (ACME, GLOBEX) * 5]
        responses = deployment.concurrently(*requests)

        accepted = {
            request[2]["X-Api-Key"]
            for request, response in zip(requests, responses, strict=True)
            if response.status_code == 200
        }
        assert {r.status_code for r in responses} <= {200, 404}
        assert len(accepted) == 1
        winner = deployment.acme if accepted == {"key-acme"} else deployment.globex
        assert deployment.row(sid).tenant_id == winner
        assert set(deployment.event_tenants(sid)) == {winner}
        assert deployment.event_count(sid) == sum(r.status_code == 200 for r in responses)


def test_same_tenant_updates_still_apply(deployment):
    sid = _id("s")
    deployment.session(sid, agent_id="first", goal="first goal", status="running")

    deployment.session(sid, agent_id="second", goal="second goal", status="success")

    row = deployment.row(sid)
    assert (row.agent_id, row.goal, row.status) == ("second", "second goal", "success")


def test_the_first_start_time_is_kept(deployment):
    sid = _id("s")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    deployment.session(sid, started_at=start.isoformat())
    deployment.session(sid, started_at=(start + timedelta(days=1)).isoformat())
    assert deployment.row(sid).started_at.replace(tzinfo=UTC) == start


def test_a_stale_write_cannot_move_a_session_backwards(deployment):
    sid = _id("s")
    ended = datetime(2026, 1, 1, tzinfo=UTC)

    async def write(**fields):
        async with server._db_session_factory() as db:
            base = {
                "session_id": sid,
                "tenant_id": deployment.acme,
                "agent_id": "bot",
                "framework": "custom",
            }
            await Repository(db).upsert_session(base | fields)
            await db.commit()

    deployment.run(
        lambda: write(
            total_events=5,
            total_tokens=50,
            estimated_cost_usd=0.5,
            status="success",
            ended_at=ended,
            goal="the goal",
        )
    )
    deployment.run(
        lambda: write(
            total_events=3,
            total_tokens=30,
            estimated_cost_usd=0.3,
            status="running",
            ended_at=None,
            goal=None,
        )
    )

    row = deployment.row(sid)
    assert (row.total_events, row.total_tokens) == (5, 50)
    assert row.estimated_cost_usd == 0.5
    assert (row.status, row.goal) == ("success", "the goal")
    assert row.ended_at.replace(tzinfo=UTC) == ended


def test_a_status_still_changes_once_a_session_has_ended(deployment):
    sid = _id("s")
    ended = datetime(2026, 1, 1, tzinfo=UTC)

    async def write(status):
        async with server._db_session_factory() as db:
            await Repository(db).upsert_session(
                {
                    "session_id": sid,
                    "tenant_id": deployment.acme,
                    "agent_id": "bot",
                    "framework": "custom",
                    "status": status,
                    "ended_at": ended,
                }
            )
            await db.commit()

    deployment.run(lambda: write("success"))
    deployment.run(lambda: write("failure"))
    assert deployment.row(sid).status == "failure"


@pytest.mark.parametrize("method", ["upsert_session", "claim_session"])
def test_the_repository_refuses_another_tenants_session(deployment, method):
    sid = _id("s")
    deployment.session(sid, goal="acme goal")

    async def write():
        async with server._db_session_factory() as db:
            repo = TenantRepository(Repository(db), tenant_id=deployment.globex)
            await getattr(repo, method)(
                {"session_id": sid, "agent_id": "x", "framework": "custom", "goal": "stolen"}
            )

    with pytest.raises(SessionOwnershipError):
        deployment.run(write)
    assert deployment.row(sid).goal == "acme goal"


def test_claiming_a_session_never_changes_it(deployment):
    sid = _id("s")
    deployment.session(sid, goal="the goal", agent_id="real")

    async def claim():
        async with server._db_session_factory() as db:
            repo = TenantRepository(Repository(db), tenant_id=deployment.acme)
            await repo.claim_session(
                {"session_id": sid, "agent_id": "other", "framework": "custom", "goal": None}
            )
            await db.commit()

    deployment.run(claim)
    row = deployment.row(sid)
    assert (row.agent_id, row.goal) == ("real", "the goal")


def test_the_database_refuses_changing_a_sessions_tenant(deployment):
    sid = _id("s")
    deployment.session(sid)

    async def reassign():
        async with server._db_session_factory() as db:
            await db.execute(
                text("UPDATE agent_sessions SET tenant_id = :t WHERE session_id = :s"),
                {"t": deployment.globex, "s": sid},
            )

    with pytest.raises(IntegrityError):
        deployment.run(reassign)
    assert deployment.row(sid).tenant_id == deployment.acme


def test_the_database_refuses_an_event_from_another_tenant(deployment):
    sid = _id("s")
    deployment.session(sid)

    async def insert():
        async with server._db_session_factory() as db:
            db.add(
                EventRecord(
                    event_id=_id("e"),
                    tenant_id=deployment.globex,
                    session_id=sid,
                    agent_id="x",
                    framework="custom",
                    event_type="tool.call",
                    timestamp=datetime.now(UTC),
                )
            )
            await db.commit()

    with pytest.raises(IntegrityError):
        deployment.run(insert)
    assert deployment.event_count(sid) == 0


def test_a_row_with_no_tenant_can_be_given_one_once(deployment):
    sid = _id("s")

    async def seed():
        async with server._db_session_factory() as db:
            await Repository(db).upsert_session(
                {"session_id": sid, "agent_id": "old", "framework": "custom"}
            )
            await db.commit()

    async def tag(tenant):
        async with server._db_session_factory() as db:
            await db.execute(
                text("UPDATE agent_sessions SET tenant_id = :t WHERE session_id = :s"),
                {"t": tenant, "s": sid},
            )
            await db.commit()

    deployment.run(seed)
    deployment.run(lambda: tag(deployment.acme))
    assert deployment.row(sid).tenant_id == deployment.acme
    with pytest.raises(IntegrityError):
        deployment.run(lambda: tag(deployment.globex))


def test_a_row_with_no_tenant_is_not_any_tenants_to_take(deployment):
    sid = _id("s")

    async def seed():
        async with server._db_session_factory() as db:
            await Repository(db).upsert_session(
                {"session_id": sid, "agent_id": "old", "framework": "custom", "goal": "old goal"}
            )
            await db.commit()

    deployment.run(seed)

    assert deployment.session(sid, goal="taken").status_code == 409

    assert deployment.row(sid).goal == "old goal"

    async def clean():
        async with server._db_session_factory() as db:
            await db.execute(delete(SessionRecord).where(SessionRecord.session_id == sid))
            await db.commit()

    deployment.run(clean)


def test_a_row_with_no_tenant_belongs_to_default(deployment):
    sid = _id("s")

    async def write(tenant):
        async with server._db_session_factory() as db:
            data = {"session_id": sid, "agent_id": "bot", "framework": "custom"}
            await Repository(db).upsert_session(data | ({"tenant_id": tenant} if tenant else {}))
            await db.commit()

    async def clean():
        async with server._db_session_factory() as db:
            await db.execute(delete(SessionRecord).where(SessionRecord.session_id == sid))
            await db.commit()

    deployment.run(lambda: write(None))
    deployment.run(lambda: write("default"))
    assert deployment.row(sid).tenant_id is None
    deployment.run(clean)


async def test_installing_the_guards_twice_is_harmless(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'twice.db').as_posix()}"
    for _ in range(2):
        factory = await init_db(url)
        await factory.kw["bind"].dispose()


# ── cloud mode ─────────────────────────────────────────────────────────────────────────────


def test_cloud_rows_carry_their_tenant(cloud):
    sid = _id("s")
    assert cloud.session(sid).status_code == 200
    assert cloud.event(sid).status_code == 200
    assert cloud.row(sid).tenant_id == cloud.acme
    assert cloud.event_tenants(sid) == [cloud.acme]


def test_cloud_tenants_cannot_take_each_others_sessions(cloud):
    sid = _id("s")
    cloud.session(sid, goal="acme goal")
    _restart()

    assert cloud.session(sid, GLOBEX).status_code == 409
    assert cloud.event(sid, GLOBEX).status_code == 404

    assert (cloud.row(sid).tenant_id, cloud.row(sid).goal) == (cloud.acme, "acme goal")


def test_cloud_erasure_removes_what_the_api_stored(cloud):
    sid = _id("s")
    cloud.session(sid)
    cloud.event(sid)

    receipt = cloud.client.post(
        "/api/v1/gdpr/erase", json={"identifier": sid, "scope": "session_id"}, headers=ACME
    ).json()

    assert "sessions_and_events" in receipt["targets_reached"]
    assert cloud.row(sid) is None
    assert cloud.event_count(sid) == 0


def test_erasing_another_tenant_by_name_erases_nothing(cloud):
    ours, theirs = _id("s"), _id("s")
    cloud.session(ours)
    cloud.event(ours)
    cloud.session(theirs, GLOBEX)
    cloud.event(theirs, GLOBEX)

    receipt = cloud.client.post(
        "/api/v1/gdpr/erase",
        json={"identifier": cloud.globex, "scope": "tenant_id"},
        headers=ACME,
    )

    assert receipt.status_code == 200
    assert cloud.row(ours) is not None
    assert cloud.event_count(ours) == 1
    assert server._collector.get_trace(ours) is not None
    assert cloud.row(theirs) is not None
    assert server._collector.get_trace(theirs) is not None


def test_after_a_restart_the_database_decides_who_reads_a_session(deployment):
    sid = _id("s")
    deployment.event(sid, token_usage={"total_tokens": 5, "estimated_cost_usd": 1.5})
    _restart()
    url = f"/api/v1/sessions/{sid}/cost"
    assert deployment.client.get(url, headers=GLOBEX).status_code == 404
    assert deployment.client.get(url, headers=ACME).status_code == 200


def test_an_evicted_cloud_session_is_not_readable_by_another_tenant(cloud):
    sid = _id("s")
    cloud.event(sid, token_usage={"total_tokens": 5, "estimated_cost_usd": 1.5})
    server._collector._traces.pop(sid)
    assert cloud.client.get(f"/api/v1/sessions/{sid}/cost", headers=GLOBEX).status_code == 404


def test_erasing_an_agent_reaches_its_events_in_another_agents_session(cloud):
    sid = _id("s")
    cloud.session(sid)
    cloud.event(sid)
    agent = _id("agent")
    cloud.event(sid, agent_id=agent)
    assert cloud.event_count(sid) == 2

    receipt = cloud.client.post(
        "/api/v1/gdpr/erase", json={"identifier": agent, "scope": "agent_id"}, headers=ACME
    )

    assert receipt.status_code == 200
    assert cloud.event_count(sid) == 1  # the other agent's event and the session stay
    assert cloud.row(sid) is not None


def test_erasing_an_agent_stays_in_the_callers_tenant(cloud):
    ours, theirs = _id("s"), _id("s")
    agent = _id("agent")
    cloud.event(ours, agent_id=agent)
    cloud.event(theirs, GLOBEX, agent_id=agent)

    cloud.client.post(
        "/api/v1/gdpr/erase", json={"identifier": agent, "scope": "agent_id"}, headers=ACME
    )

    assert cloud.event_count(ours) == 0
    assert cloud.event_count(theirs) == 1


def test_a_failed_owner_lookup_answers_503(cloud, monkeypatch):
    sid = _id("s")
    cloud.event(sid)
    server._collector._traces.pop(sid)

    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("database down")

        async def __aexit__(self, *exc):
            return False

    real = server._db_session_factory
    monkeypatch.setattr(server, "_db_session_factory", lambda: _Broken())
    response = cloud.client.get(f"/api/v1/sessions/{sid}/cost", headers=ACME)
    monkeypatch.setattr(server, "_db_session_factory", real)
    assert response.status_code == 503


def test_a_tenant_can_erase_itself_by_name(cloud):
    ours = _id("s")
    cloud.session(ours)
    cloud.event(ours)
    cloud.client.post(
        "/api/v1/gdpr/erase",
        json={"identifier": cloud.acme, "scope": "tenant_id"},
        headers=ACME,
    )
    assert cloud.row(ours) is None


def test_cloud_erasure_stays_in_the_callers_tenant(cloud):
    theirs, ours = _id("s"), _id("s")
    cloud.session(theirs, GLOBEX, agent_id="shared-agent")
    cloud.session(ours, agent_id="shared-agent")

    cloud.client.post(
        "/api/v1/gdpr/erase",
        json={"identifier": "shared-agent", "scope": "agent_id"},
        headers=ACME,
    )

    assert cloud.row(ours) is None
    assert cloud.row(theirs).tenant_id == cloud.globex


def test_a_cloud_receipt_admits_older_rows_it_cannot_reach(cloud):
    receipt = cloud.client.post(
        "/api/v1/gdpr/erase", json={"identifier": _id("s"), "scope": "session_id"}, headers=ACME
    ).json()
    assert any("before rows carried a tenant" in gap for gap in receipt["not_reached"])


# ── concurrent first events ────────────────────────────────────────────────────────────────


def test_repeated_bursts_of_first_events_all_persist(deployment, caplog):
    before = server._persistence["events_not_stored"]
    with caplog.at_level(logging.ERROR, logger="panoptis.api.server"):
        for _ in range(REPEATS):
            sid = _id("s")
            deployment.burst(sid, deployment.acme)
            assert deployment.event_count(sid) == BURST
            assert deployment.row(sid).tenant_id == deployment.acme
    assert "PG event write failed" not in caplog.text
    assert server._persistence["events_not_stored"] == before


def test_repeated_http_bursts_are_all_accepted_and_stored(deployment, caplog):
    with caplog.at_level(logging.ERROR, logger="panoptis.api.server"):
        for _ in range(max(1, REPEATS // 5)):
            server.reset_rate_limiter_for_tests()
            sid = _id("s")
            body = {"session_id": sid, "agent_id": "bot", "event_type": "tool.call"}
            responses = deployment.concurrently(*[("/api/v1/events", body, ACME)] * BURST)
            assert [r.status_code for r in responses] == [200] * BURST
            assert deployment.event_count(sid) == BURST
    assert "write failed" not in caplog.text


def test_bursts_for_many_new_sessions_at_once_all_persist(deployment):
    sessions = [_id("s") for _ in range(5)]

    async def write():
        events = [
            AgentEvent(session_id=sid, agent_id="bot", event_type=EventType.TOOL_CALL)
            for sid in sessions
            for _ in range(BURST)
        ]
        await asyncio.gather(*[server._pg_write_event(e, deployment.acme) for e in events])

    deployment.run(write)
    assert [deployment.event_count(sid) for sid in sessions] == [BURST] * 5


def test_the_demo_seed_persists_every_event(deployment):
    async def count():
        async with server._db_session_factory() as db:
            q = select(func.count()).select_from(EventRecord)
            return (await db.execute(q.where(EventRecord.session_id == "demo-session"))).scalar()

    async def seed_and_settle():
        before = await count()
        server._seed_demo_data()
        for _ in range(100):
            if await count() >= before + 6:
                break
            await asyncio.sleep(0.05)
        return await count() - before

    _restart()
    assert deployment.run(seed_and_settle) == 6


def test_the_same_event_sent_at_once_is_stored_once(deployment):
    sid = _id("s")
    event = AgentEvent(session_id=sid, agent_id="bot", event_type=EventType.TOOL_CALL)

    async def write():
        await asyncio.gather(
            *[server._pg_write_event(event, tenant_id=deployment.acme) for _ in range(BURST)]
        )

    deployment.run(write)
    assert deployment.event_count(sid) == 1


def test_a_retried_request_is_accepted_and_stored_once(deployment):
    sid = _id("s")
    event_id = str(uuid.uuid4())
    first = deployment.event(sid, event_id=event_id)
    retried = deployment.event(sid, event_id=event_id)
    assert (first.status_code, retried.status_code) == (200, 200)
    assert deployment.event_count(sid) == 1


def test_the_first_write_of_an_event_wins(deployment):
    sid = _id("s")
    event_id = str(uuid.uuid4())
    deployment.event(sid, event_id=event_id, goal="original")
    deployment.event(sid, event_id=event_id, goal="changed")

    async def read():
        async with server._db_session_factory() as db:
            return (await db.get(EventRecord, event_id)).goal

    assert deployment.run(read) == "original"


def test_an_event_id_from_another_tenant_is_refused_not_lost(deployment):
    theirs, ours = _id("s"), _id("s")
    event_id = str(uuid.uuid4())
    assert deployment.event(theirs, GLOBEX, event_id=event_id).status_code == 200

    refused = deployment.event(ours, event_id=event_id)

    assert refused.status_code == 409
    assert deployment.event_count(ours) == 0
    assert deployment.event_tenants(theirs) == [deployment.globex]


def test_an_event_id_from_another_session_is_refused(deployment):
    first, second = _id("s"), _id("s")
    event_id = str(uuid.uuid4())
    deployment.event(first, event_id=event_id)

    assert deployment.event(second, event_id=event_id).status_code == 409

    assert deployment.event_count(first) == 1
    assert deployment.event_count(second) == 0


def test_a_refused_event_reaches_nothing(deployment):
    first, second = _id("s"), _id("s")
    event_id = str(uuid.uuid4())
    deployment.event(first, event_id=event_id)
    seen = []
    get_event_bus().subscribe_fn(lambda e: seen.append(e), handler_id="test.seen")
    try:
        assert deployment.event(second, event_id=event_id).status_code == 409
    finally:
        get_event_bus().unsubscribe("test.seen")

    assert seen == []
    assert server._collector.get_trace(second) is None
    assert deployment.row(second) is None


def test_the_atomic_check_still_refuses_when_the_early_check_misses(deployment, monkeypatch):
    """Two sessions racing for one id both pass the early look; insert_event decides."""
    first, second = _id("s"), _id("s")
    event_id = str(uuid.uuid4())
    deployment.event(first, event_id=event_id)

    async def missed(event):
        return False

    monkeypatch.setattr(server, "_event_id_taken", missed)
    before = server._persistence["events_not_stored"]
    assert deployment.event(second, event_id=event_id).status_code == 409
    assert server._persistence["events_not_stored"] == before
    assert deployment.event_count(second) == 0


def test_a_failed_early_check_is_reported_by_the_write(deployment, monkeypatch):
    sid = _id("s")
    real = server._db_session_factory

    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("database down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(server, "_db_session_factory", lambda: _Broken())
    assert deployment.event(sid).status_code == 503
    monkeypatch.setattr(server, "_db_session_factory", real)


def test_a_failed_claim_publishes_nothing(deployment, monkeypatch):
    sid = _id("s")

    async def claim_session(self, data):
        raise RuntimeError("database down")

    monkeypatch.setattr(Repository, "claim_session", claim_session)
    assert deployment.event(sid).status_code == 503
    assert server._collector.get_trace(sid) is None


def test_the_repository_reports_an_event_id_conflict(deployment):
    first, second = _id("s"), _id("s")
    event_id = str(uuid.uuid4())
    deployment.event(first, event_id=event_id)
    deployment.session(second)

    async def write():
        async with server._db_session_factory() as db:
            await Repository(db).insert_event(
                {
                    "event_id": event_id,
                    "tenant_id": deployment.acme,
                    "session_id": second,
                    "agent_id": "bot",
                    "framework": "custom",
                    "event_type": "tool.call",
                    "timestamp": datetime.now(UTC),
                }
            )

    with pytest.raises(EventConflictError):
        deployment.run(write)


def _failing(monkeypatch, error, times):
    real = Repository.insert_event
    calls = []

    async def insert_event(self, event_data):
        calls.append(event_data["event_id"])
        if len(calls) <= times:
            raise error
        await real(self, event_data)

    monkeypatch.setattr(Repository, "insert_event", insert_event)
    return calls


LOCKED = OperationalError("INSERT", {}, Exception("database is locked"))


class _DeadlockError(Exception):
    sqlstate = "40P01"


def test_contention_is_retried_and_stores_the_event_once(deployment, monkeypatch):
    sid = _id("s")
    calls = _failing(monkeypatch, LOCKED, times=2)
    assert deployment.event(sid).status_code == 200
    assert len(calls) == 3
    assert deployment.event_count(sid) == 1


def test_a_deadlock_counts_as_contention(deployment, monkeypatch):
    sid = _id("s")
    calls = _failing(monkeypatch, OperationalError("INSERT", {}, _DeadlockError()), times=1)
    assert deployment.event(sid).status_code == 200
    assert len(calls) == 2


def test_contention_that_never_clears_is_reported_to_the_sender(deployment, monkeypatch, caplog):
    sid = _id("s")
    before = server._persistence["events_not_stored"]
    calls = _failing(monkeypatch, LOCKED, times=99)

    with caplog.at_level(logging.ERROR, logger="panoptis.api.server"):
        response = deployment.event(sid)

    assert response.status_code == 503
    assert len(calls) == 3
    assert "PG event write failed" in caplog.text
    assert server._persistence["events_not_stored"] == before  # the sender was told


def test_a_client_cannot_raise_the_lost_event_count(deployment):
    first, second = _id("s"), _id("s")
    event_id = str(uuid.uuid4())
    deployment.event(first, event_id=event_id)
    before = server._persistence["events_not_stored"]
    assert deployment.event(second, event_id=event_id).status_code == 409
    assert server._persistence["events_not_stored"] == before


def test_a_failure_that_is_not_contention_is_not_retried(deployment, monkeypatch):
    calls = _failing(monkeypatch, RuntimeError("disk full"), times=99)
    assert deployment.event(_id("s")).status_code == 503
    assert len(calls) == 1


def test_a_lost_background_event_shows_in_health(deployment, monkeypatch, caplog):
    before = deployment.client.get("/health").json()["persistence"]["events_not_stored"]
    _failing(monkeypatch, RuntimeError("disk full"), times=99)

    with caplog.at_level(logging.ERROR, logger="panoptis.api.server"):
        deployment.burst(_id("s"), deployment.acme, count=1)

    after = deployment.client.get("/health").json()["persistence"]["events_not_stored"]
    assert after == before + 1
    assert "PG event write failed" in caplog.text


def test_a_client_retry_after_a_failure_stores_the_event_once(deployment, monkeypatch):
    sid = _id("s")
    event_id = str(uuid.uuid4())
    with monkeypatch.context() as outage:
        _failing(outage, RuntimeError("disk full"), times=99)
        assert deployment.event(sid, event_id=event_id).status_code == 503
    assert deployment.event(sid, event_id=event_id).status_code == 200
    assert deployment.event_count(sid) == 1


def test_events_read_back_in_a_defined_order(deployment):
    sid = _id("s")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    arrival = [(3, 0), (1, 2), (2, 0), (1, 1), (1, 0)]

    async def write():
        await asyncio.gather(
            *[
                server._pg_write_event(
                    AgentEvent(
                        session_id=sid,
                        agent_id="bot",
                        event_type=EventType.TOOL_CALL,
                        step_number=step,
                        timestamp=base + timedelta(seconds=second),
                    ),
                    tenant_id=deployment.acme,
                )
                for step, second in arrival
            ]
        )

    async def read():
        async with server._db_session_factory() as db:
            repo = TenantRepository(Repository(db), tenant_id=deployment.acme)
            events = await repo.get_events(sid)
            return [(e.step_number, e.timestamp.replace(tzinfo=UTC) - base) for e in events]

    deployment.run(write)
    assert deployment.run(read) == [
        (step, timedelta(seconds=second)) for step, second in sorted(arrival)
    ]
