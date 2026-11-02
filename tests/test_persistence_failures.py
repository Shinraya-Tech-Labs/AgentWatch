"""TO-007: a configured database that fails never turns into a silent in-memory deployment."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("aiosqlite")

from panoptis.api import server  # noqa: E402

UNREACHABLE = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from panoptis.api.middleware.rate_limiter import InMemoryBackend

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    monkeypatch.delenv("AGENTWATCH_ALLOW_MEMORY_FALLBACK", raising=False)
    monkeypatch.setattr(server, "_db_session_factory", None)
    monkeypatch.setitem(server._persistence, "state", "not_configured")
    yield


def _event(sid: str) -> dict:
    return {"session_id": sid, "agent_id": "bot", "event_type": "tool.call"}


def test_unreachable_database_stops_startup_by_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    with pytest.raises(RuntimeError, match="refusing to start in memory"):
        with TestClient(server.app):
            pass


def test_explicit_fallback_starts_degraded_and_refuses_to_claim_persistence(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    monkeypatch.setenv("AGENTWATCH_ALLOW_MEMORY_FALLBACK", "1")
    with TestClient(server.app) as c:
        h = c.get("/health")
        assert h.status_code == 503
        assert h.json()["persistence"]["state"] == "fallback"
        assert h.json()["database"]["mode"] == "memory_fallback"
        r = c.post("/api/v1/events", json=_event("fb-1"))
        assert r.status_code == 503 and "not persisted" in r.json()["detail"]


def test_fallback_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    monkeypatch.setenv("AGENTWATCH_ALLOW_MEMORY_FALLBACK", "1")
    monkeypatch.setattr(server, "_IS_PROD", True)
    monkeypatch.setattr(server, "_API_KEY", "prod-key")
    with pytest.raises(RuntimeError, match="refusing to start in memory"):
        with TestClient(server.app):
            pass


def test_no_database_configured_is_in_memory_by_design_and_healthy(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(server.app) as c:
        h = c.get("/health").json()
        assert (
            h["persistence"]["state"] == "not_configured" and h["database"]["status"] == "in_memory"
        )
        assert c.post("/api/v1/events", json=_event("mem-1")).status_code == 200


def test_write_failures_return_503_and_recover_after_reconnect(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'v1.db').as_posix()}")
    with TestClient(server.app) as c:
        assert c.get("/health").json()["persistence"]["state"] == "connected"
        assert c.post("/api/v1/events", json=_event("db-ok")).status_code == 200
        good = server._db_session_factory

        def broken_factory():
            raise ConnectionError("database went away")

        monkeypatch.setattr(server, "_db_session_factory", broken_factory)
        r = c.post("/api/v1/events", json=_event("db-down"))
        assert r.status_code == 503 and "not persisted" in r.json()["detail"]
        s = c.post(
            "/api/v1/sessions",
            json={"session_id": "db-down-session", "agent_id": "bot", "framework": "custom"},
        )
        assert s.status_code == 503
        assert c.get("/health").status_code == 503
        monkeypatch.setattr(server, "_db_session_factory", good)  # the database is back
        assert c.post("/api/v1/events", json=_event("db-back")).status_code == 200
        assert c.get("/health").json()["database"]["status"] == "ok"


def test_retrying_an_event_after_an_outage_stores_it_exactly_once(monkeypatch, tmp_path):
    import asyncio

    from sqlalchemy import func, select

    from panoptis.core.models import EventRecord

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'v1.db').as_posix()}")
    ev = {**_event("retry-session"), "event_id": "11111111-2222-3333-4444-555555555555"}
    with TestClient(server.app) as c:
        good = server._db_session_factory

        def broken_factory():
            raise ConnectionError("down")

        monkeypatch.setattr(server, "_db_session_factory", broken_factory)
        assert c.post("/api/v1/events", json=ev).status_code == 503
        monkeypatch.setattr(server, "_db_session_factory", good)
        assert c.post("/api/v1/events", json=ev).status_code == 200  # the retry persists it
        assert (
            c.post("/api/v1/events", json=ev).status_code == 200
        )  # a second retry is not an error

        async def count() -> int:
            async with good() as db:
                return (
                    await db.execute(
                        select(func.count())
                        .select_from(EventRecord)
                        .where(EventRecord.event_id == ev["event_id"])
                    )
                ).scalar_one()

        assert asyncio.run(count()) == 1
        assert server._collector.get_trace("retry-session").event_count == 1


@pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="needs DATABASE_URL pointing at PostgreSQL, which enforces the foreign key (CI sets it)",
)
async def test_an_event_for_a_session_the_collector_has_not_seen_yet_persists(monkeypatch):
    # the collector and the database writer are separate subscribers of one event, so the
    # writer can run first: the session row must then come from the event itself
    import os
    import uuid

    from panoptis.core.models import init_db
    from panoptis.core.schema import AgentEvent, EventType

    factory = await init_db(os.environ["DATABASE_URL"])
    monkeypatch.setattr(server, "_db_session_factory", factory)
    sid = f"unseen-{uuid.uuid4().hex[:8]}"
    assert server._collector.get_trace(sid) is None
    failures: list[str] = []
    token = server._write_failures.set(failures)
    try:
        await server._pg_write_event(
            AgentEvent(session_id=sid, agent_id="bot", event_type=EventType.TOOL_CALL)
        )
    finally:
        server._write_failures.reset(token)
    assert failures == []
    async with factory() as db:
        from panoptis.core.models import Repository

        assert (await Repository(db).get_session(sid)) is not None
        assert len(await Repository(db).get_events(sid)) == 1
    await factory.kw["bind"].dispose()
