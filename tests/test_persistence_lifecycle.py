"""What happens to events when the process around them goes wrong: a deaf event loop, a shutdown
with writes in flight, a cancelled write, a failure inside a transaction, a killed process.

The loop tests run on SQLite because aiosqlite hands results back from a thread, the only path
that needs the loop's self-pipe. PostgreSQL runs too when DATABASE_URL points at it, as in CI.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from panoptis.api import server
from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.models import EventRecord, Repository, SessionRecord, init_db
from panoptis.core.schema import AgentEvent, EventType

BURST = 20
BOUND = 90  # seconds a stuck burst gets before the test fails instead of hanging
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


def _events(session_id: str, count: int = BURST) -> list[AgentEvent]:
    return [
        AgentEvent(
            session_id=session_id,
            agent_id="bot",
            event_type=EventType.TOOL_CALL,
            step_number=step,
        )
        for step in range(count)
    ]


def _sid() -> str:
    return f"life-{uuid.uuid4().hex[:12]}"


@pytest.fixture(params=BACKENDS)
def database(request, monkeypatch, tmp_path):
    if request.param == "sqlite":
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'l.db').as_posix()}")
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    monkeypatch.delenv("AGENTWATCH_TENANT_KEYS", raising=False)
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    server.reset_rate_limiter_for_tests()
    return os.environ["DATABASE_URL"]


@pytest.fixture
def sqlite_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'l.db').as_posix()}")
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    monkeypatch.delenv("AGENTWATCH_TENANT_KEYS", raising=False)
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    return os.environ["DATABASE_URL"]


async def _count(session_id: str) -> int:
    async with server._db_session_factory() as db:
        q = select(func.count()).select_from(EventRecord)
        return (await db.execute(q.where(EventRecord.session_id == session_id))).scalar_one()


async def _session_exists(session_id: str) -> bool:
    async with server._db_session_factory() as db:
        return await db.get(SessionRecord, session_id) is not None


def _stored_after_restart(session_id: str) -> int:
    with TestClient(server.app) as client:
        return client.portal.call(_count, session_id)


# ── a loop that threads cannot wake ──────────────────────────────────────────────────────────


def test_a_burst_finishes_when_threads_can_no_longer_wake_the_loop(sqlite_database):
    """Seen on Windows: the self-pipe's TCP connection was reset, asyncio swallows that error,
    and every result handed back from a thread sat in the ready queue while the loop slept."""
    sid = _sid()
    with TestClient(server.app) as client:

        async def deafen():
            asyncio.get_running_loop()._write_to_self = lambda: None

        async def burst():
            await asyncio.gather(*[server._pg_write_event(e) for e in _events(sid)])

        before = server._persistence["events_not_stored"]
        loop = _portal_loop(client)  # asked now: this call needs a loop that can still wake
        client.portal.call(deafen)
        finished = client.portal.start_task_soon(burst)
        try:
            finished.result(timeout=BOUND)
        finally:
            # from this thread, then through the working pipe: a failure must not hang teardown
            del loop._write_to_self
            loop.call_soon_threadsafe(lambda: None)
        assert client.portal.call(_count, sid) == BURST
        assert server._persistence["events_not_stored"] == before


def _portal_loop(client: TestClient) -> asyncio.AbstractEventLoop:
    async def loop():
        return asyncio.get_running_loop()

    return client.portal.call(loop)


async def test_the_heartbeat_outlives_every_shutdown_step_then_stops(sqlite_database, monkeypatch):
    """Draining and closing the pool are thread hand-offs too, so they need it as well."""
    alive = {}
    drain, dispose = EventBus.drain, AsyncEngine.dispose

    async def watched_drain(self, timeout):
        alive["drain"] = not beat.done()
        return await drain(self, timeout)

    async def watched_dispose(self, close=True):
        alive["dispose"] = not beat.done()
        return await dispose(self, close)

    monkeypatch.setattr(EventBus, "drain", watched_drain)
    monkeypatch.setattr(AsyncEngine, "dispose", watched_dispose)
    async with server.lifespan(server.app):
        (beat,) = [t for t in asyncio.all_tasks() if t.get_name() == "api.loop-heartbeat"]
    await asyncio.sleep(0)
    assert alive == {"drain": True, "dispose": True}
    assert beat.cancelled()


# ── shutdown with writes in flight ───────────────────────────────────────────────────────────


def test_shutdown_waits_for_events_already_published(database):
    sid = _sid()
    with TestClient(server.app) as client:

        async def publish():
            for event in _events(sid):
                get_event_bus().publish_sync(event)

        client.portal.call(publish)
    assert _stored_after_restart(sid) == BURST


def test_shutdown_reports_what_it_could_not_finish(sqlite_database, monkeypatch, caplog):
    monkeypatch.setattr(server, "_SHUTDOWN_DRAIN_SECONDS", 0.05)
    with TestClient(server.app) as client:

        async def stuck(event):
            await asyncio.Event().wait()

        async def publish():
            bus = get_event_bus()
            await bus.drain(30)  # the startup seed, so that only this event can be unfinished
            bus.subscribe_fn(stuck, handler_id="test.stuck")
            bus.publish_sync(_events(_sid(), 1)[0])
            await asyncio.sleep(0)
            return server._persistence["events_not_stored"]

        before = client.portal.call(publish)
    get_event_bus().unsubscribe("test.stuck")
    assert "still in flight" in caplog.text
    assert server._persistence["events_not_stored"] == before + 1


async def test_a_drained_bus_has_nothing_left():
    bus = EventBus()
    seen = []

    async def slow(event):
        await asyncio.sleep(0.01)
        seen.append(event.event_id)

    bus.subscribe_fn(slow, handler_id="slow")
    events = _events(_sid(), 5)
    for event in events:
        bus.publish_sync(event)
    assert await bus.drain(5) == 0
    assert sorted(seen) == sorted(e.event_id for e in events)
    assert await bus.drain(5) == 0
    await asyncio.sleep(0)  # done-callbacks run on the next pass of the loop
    assert not bus._background  # finished work is not kept


async def test_draining_gives_up_at_its_limit_and_leaves_the_work_running():
    bus = EventBus()
    release = asyncio.Event()
    finished = []

    async def blocked(event):
        await release.wait()
        finished.append(event.event_id)

    bus.subscribe_fn(blocked, handler_id="blocked")
    event = _events(_sid(), 1)[0]
    bus.publish_sync(event)
    assert await bus.drain(0.01) == 1
    release.set()
    assert await bus.drain(5) == 0
    assert finished == [event.event_id]


def _task_on_another_loop():
    other = asyncio.new_event_loop()
    return other, other.create_task(asyncio.sleep(0))


def _finish(other, task):
    task.cancel()
    other.run_until_complete(asyncio.gather(task, return_exceptions=True))


def test_draining_ignores_work_that_belongs_to_another_loop():
    """The bus is global: a task of another live loop cannot be awaited from this one."""
    bus = EventBus()
    other, stranger = _task_on_another_loop()
    bus._background.add(stranger)
    try:
        assert asyncio.run(bus.drain(0.01)) == 0
        assert stranger in bus._background  # not this loop's to forget
    finally:
        _finish(other, stranger)
        other.close()


def test_draining_forgets_work_of_a_closed_loop():
    bus = EventBus()
    other, stranger = _task_on_another_loop()
    _finish(other, stranger)
    other.close()
    bus._background.add(stranger)
    assert asyncio.run(bus.drain(0.01)) == 0
    assert stranger not in bus._background


# ── a write interrupted part way ─────────────────────────────────────────────────────────────


def test_a_write_cancelled_inside_its_transaction_leaves_nothing(database, monkeypatch):
    """Cancelled after the session row is written and before the event: all or nothing."""
    sid = _sid()
    event = _events(sid, 1)[0]
    real = Repository.insert_event
    with TestClient(server.app) as client:

        async def cancelled():
            reached = asyncio.Event()

            async def stalls(self, data):
                reached.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(Repository, "insert_event", stalls)
            task = asyncio.create_task(server._pg_write_event(event))
            await reached.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            monkeypatch.setattr(Repository, "insert_event", real)

        client.portal.call(cancelled)
        assert client.portal.call(_session_exists, sid) is False
        client.portal.call(server._pg_write_event, event)
        client.portal.call(server._pg_write_event, event)
        assert client.portal.call(_count, sid) == 1


def test_a_failure_after_the_session_row_leaves_nothing_behind(database, monkeypatch, caplog):
    """The session row and its first event are one transaction."""
    sid = _sid()
    event = _events(sid, 1)[0]
    with TestClient(server.app) as client:
        real = Repository.insert_event

        async def fails(self, data):
            raise RuntimeError("disk full")

        monkeypatch.setattr(Repository, "insert_event", fails)
        before = server._persistence["events_not_stored"]
        client.portal.call(server._pg_write_event, event)
        monkeypatch.setattr(Repository, "insert_event", real)

        assert server._persistence["events_not_stored"] == before + 1
        assert "PG event write failed" in caplog.text
        assert client.portal.call(_session_exists, sid) is False
        client.portal.call(server._pg_write_event, event)
        assert client.portal.call(_session_exists, sid) is True
        assert client.portal.call(_count, sid) == 1


# ── a killed process ─────────────────────────────────────────────────────────────────────────

WRITER = """
    import asyncio, sys, uuid
    from panoptis.api import server
    from panoptis.core.models import init_db
    from panoptis.core.schema import AgentEvent, EventType

    url, session_id, count = sys.argv[1], sys.argv[2], int(sys.argv[3])

    def event(step):
        return AgentEvent(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_OID, f"{session_id}-{step}")),
            session_id=session_id, agent_id="bot", event_type=EventType.TOOL_CALL,
            step_number=step,
        )

    async def main():
        server._db_session_factory = await init_db(url)
        for start in range(0, count, 10):
            await asyncio.gather(*[server._pg_write_event(event(s))
                                   for s in range(start, min(start + 10, count))])
            print(start + 10, flush=True)

    asyncio.run(main())
"""


def _writer(tmp_path: Path, url: str, sid: str, count: int) -> subprocess.Popen:
    script = tmp_path / "writer.py"
    script.write_text(textwrap.dedent(WRITER), encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    return subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, str(script), url, sid, str(count)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def test_a_killed_writer_leaves_whole_rows_and_a_rerun_completes(database, tmp_path):
    """Kill the process between batches of concurrent commits, then run the same writes again:
    nothing half written, nothing duplicated, nothing missing."""
    sid, total = _sid(), 200
    url = os.environ["DATABASE_URL"]
    writer = _writer(tmp_path, url, sid, total)
    try:
        assert int(writer.stdout.readline()) >= 10  # at least one batch committed
        writer.kill()
    finally:
        writer.wait(timeout=30)

    with TestClient(server.app) as client:
        survived = client.portal.call(_count, sid)
    assert 10 <= survived <= total

    rerun = _writer(tmp_path, url, sid, total)
    rerun.communicate(timeout=120)
    assert rerun.returncode == 0

    with TestClient(server.app) as client:
        assert client.portal.call(_count, sid) == total

    if url.startswith("sqlite"):
        path = url.split("///", 1)[1]
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            orphans = """SELECT count(*) FROM agent_events e LEFT JOIN agent_sessions s
                         ON e.session_id = s.session_id WHERE s.session_id IS NULL"""
            assert db.execute(orphans).fetchone() == (0,)


async def test_init_db_opens_a_database_a_killed_process_left(tmp_path):
    """Guards are installed on every start, so a restart must not trip over its own objects."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'k.db').as_posix()}"
    for _ in range(2):
        factory = await init_db(url)
        await factory.kw["bind"].dispose()
    started = time.perf_counter()
    factory = await init_db(url)
    await factory.kw["bind"].dispose()
    assert time.perf_counter() - started < 30
