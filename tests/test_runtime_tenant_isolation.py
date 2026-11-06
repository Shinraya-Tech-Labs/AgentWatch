"""Tenant isolation of what the API keeps per session outside the database.

Cost budgets and checkpoints are found by session id, and a session id can pass to another
tenant, for example after an erasure. Every test runs on SQLite and on PostgreSQL, which runs
when DATABASE_URL points at it, as in CI. Checkpoints are real filesystem snapshots, so a
refused rollback is checked on disk.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from panoptis.api import server
from panoptis.api.tenancy import parse_tenant_keys
from panoptis.core.models import EventRecord, MemoryEntryRecord, SessionRecord, init_db
from panoptis.core.schema import AgentEvent, EventType, TokenUsage
from panoptis.cost.tracker import CostTracker
from panoptis.rollback.engine import Checkpoint, CheckpointType, RollbackEngine

POSTGRES = os.environ.get("DATABASE_URL", "").startswith("postgresql")
BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(
            not POSTGRES, reason="needs DATABASE_URL pointing at PostgreSQL (CI sets it)"
        ),
    ),
]
ACME = {"X-Api-Key": "key-acme"}
GLOBEX = {"X-Api-Key": "key-globex"}
SPENT = {"total_tokens": 4242, "estimated_cost_usd": 12.5}


def _id(name: str = "s") -> str:
    return f"{name}-{uuid.uuid4().hex[:12]}"


def _restart() -> None:
    """A restart empties everything held in memory, not only the collector."""
    server._collector._traces.clear()
    server._collector._session_index.clear()
    server._cost_tracker._budgets.clear()
    server._rollback_engine._checkpoints.clear()
    server._rollback_engine._session_checkpoints.clear()


def _evict(session_id: str, tenant_key: dict, deployment: Deployment, monkeypatch) -> None:
    """Push a session out of the collector with ordinary traffic, as any tenant can."""
    monkeypatch.setattr(server._collector, "_max_traces", len(server._collector._traces))
    for _ in range(30):
        if server._collector.get_trace(session_id) is None:
            return
        assert deployment.event(_id(), tenant_key).status_code == 200
    raise AssertionError("the session was not evicted")


@dataclass
class Deployment:
    client: TestClient
    acme: str
    globex: str
    work: Path

    def session(self, session_id: str, key: dict = ACME, **fields):
        body = {"session_id": session_id, "agent_id": "bot", "framework": "custom"} | fields
        return self.client.post("/api/v1/sessions", json=body, headers=key)

    def event(self, session_id: str, key: dict = ACME, **fields):
        body = {"session_id": session_id, "agent_id": "bot", "event_type": "tool.call"} | fields
        return self.client.post("/api/v1/events", json=body, headers=key)

    def cost(self, session_id: str, key: dict = ACME):
        return self.client.get(f"/api/v1/sessions/{session_id}/cost", headers=key)

    def checkpoints(self, session_id: str, key: dict = ACME):
        return self.client.get(f"/api/v1/sessions/{session_id}/checkpoints", headers=key)

    def rollback(self, session_id: str, key: dict = ACME, **fields):
        body = {"restore_git": False} | fields
        return self.client.post(f"/api/v1/sessions/{session_id}/rollback", json=body, headers=key)

    def erase(self, identifier: str, key: dict = ACME, scope: str = "session_id"):
        return self.client.post(
            "/api/v1/gdpr/erase", json={"identifier": identifier, "scope": scope}, headers=key
        )

    def snapshot(
        self, session_id: str, tenant: str | None, content: str = "original"
    ) -> Checkpoint:
        """A real filesystem checkpoint of a directory that holds one file."""
        directory = self.work / _id("dir")
        directory.mkdir()
        (directory / "state.txt").write_text(content)

        async def create():
            engine = server._rollback_engine
            checkpoint = await engine.create_checkpoint(
                session_id,
                1,
                working_dir=str(directory),
                checkpoint_type=CheckpointType.FILESYSTEM,
            )
            checkpoint.tenant_id = tenant
            await engine._save_checkpoint_meta(checkpoint)
            return checkpoint

        return self.client.portal.call(create)


def _state(checkpoint: Checkpoint) -> Path:
    return Path(checkpoint.working_dir) / "state.txt"


def _files_of(checkpoint: Checkpoint) -> list[Path]:
    root = server._rollback_engine._checkpoints_dir
    paths = [root / "meta" / f"{checkpoint.checkpoint_id}.json"]
    return paths + [root / "snapshots" / checkpoint.checkpoint_id]


def _no_cost_of_acme(response) -> None:
    assert response.status_code == 404, response.text


def _tokens(response) -> int:
    assert response.status_code == 200, response.text
    return response.json().get("tokens_used")


def _stored_budget(deployment: Deployment, session_id: str, key: dict = ACME):
    """Read a budget with the trace gone, so /cost cannot rebuild a lost one and hide the loss."""
    server._collector._traces.pop(session_id, None)
    return deployment.cost(session_id, key)


def _configure(monkeypatch, tmp_path, keys: str) -> None:
    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", keys)
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    monkeypatch.setattr(server._rollback_engine, "_checkpoints_dir", tmp_path / "checkpoints")
    monkeypatch.setattr(server._rollback_engine, "_checkpoints", {})
    monkeypatch.setattr(server._rollback_engine, "_session_checkpoints", {})
    monkeypatch.setattr(server._collector, "_traces", {})  # an eviction needs few events
    monkeypatch.setattr(server._collector, "_session_index", {})
    monkeypatch.setattr(server._cost_tracker, "_budgets", {})
    server.reset_rate_limiter_for_tests()


async def _clean(tenants: list[str]) -> None:
    async with server._db_session_factory() as db:
        await db.execute(delete(EventRecord).where(EventRecord.tenant_id.in_(tenants)))
        await db.execute(delete(SessionRecord).where(SessionRecord.tenant_id.in_(tenants)))
        await db.commit()


@pytest.fixture(params=BACKENDS)
def deployment(request, monkeypatch, tmp_path):
    tag = uuid.uuid4().hex[:12]
    acme, globex = f"acme-{tag}", f"globex-{tag}"
    if request.param == "sqlite":
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'r.db').as_posix()}")
    _configure(monkeypatch, tmp_path, f"{acme}=key-acme,{globex}=key-globex")
    with TestClient(server.app) as client:
        yield Deployment(client, acme, globex, tmp_path)
        client.portal.call(_clean, [acme, globex])


@pytest.fixture
def no_database(monkeypatch, tmp_path):
    tag = uuid.uuid4().hex[:12]
    acme, globex = f"acme-{tag}", f"globex-{tag}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _configure(monkeypatch, tmp_path, f"{acme}=key-acme,{globex}=key-globex")
    with TestClient(server.app) as client:
        yield Deployment(client, acme, globex, tmp_path)


# ── cost ───────────────────────────────────────────────────────────────────────────────────


def test_cost_does_not_pass_to_the_next_holder_of_an_erased_id(deployment):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)
    assert _tokens(deployment.cost(sid)) == 4242
    assert deployment.erase(sid).status_code == 200

    assert deployment.session(sid, GLOBEX).status_code == 200

    _no_cost_of_acme(deployment.cost(sid, GLOBEX))


@pytest.mark.parametrize("evicted", [False, True])
@pytest.mark.parametrize("scope", ["session_id", "agent_id", "user_id", "tenant_id"])
def test_every_erasure_scope_removes_the_budget(deployment, scope, evicted):
    sid, agent, user = _id(), _id("agent"), _id("user")
    deployment.session(sid, agent_id=agent, metadata={"user_id": user})
    deployment.event(sid, agent_id=agent, token_usage=SPENT)
    identifier = {
        "session_id": sid,
        "agent_id": agent,
        "user_id": user,
        "tenant_id": deployment.acme,
    }[scope]
    if evicted:  # then only the database or the budget itself can name the session
        server._collector._traces.pop(sid)

    receipt = deployment.erase(identifier, scope=scope).json()

    assert deployment.session(sid).status_code == 200  # the owner starts again from nothing
    _no_cost_of_acme(deployment.cost(sid))
    assert "runtime_state" in receipt["targets_reached"]


def test_an_erased_budget_is_counted_on_the_receipt(deployment):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)
    counted = deployment.erase(sid).json()["items_erased"]

    without_budget = _id()
    deployment.event(without_budget)
    server._cost_tracker._budgets.clear()
    uncounted = deployment.erase(without_budget).json()["items_erased"]

    assert counted == uncounted + 1


def test_erasing_another_tenants_session_leaves_its_budget(deployment):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)

    assert deployment.erase(sid, GLOBEX).json()["items_erased"] == 0

    assert _tokens(_stored_budget(deployment, sid)) == 4242


def test_erasing_a_tenant_by_name_stops_at_that_tenant(deployment):
    ours, theirs = _id(), _id()
    deployment.event(ours, token_usage=SPENT)
    deployment.event(theirs, GLOBEX, token_usage=SPENT)
    checkpoint = deployment.snapshot(ours, deployment.acme)

    deployment.erase(deployment.globex, scope="tenant_id")

    # naming another tenant must not quietly erase the caller's own state either
    assert _tokens(_stored_budget(deployment, ours)) == 4242
    assert all(path.exists() for path in _files_of(checkpoint))
    assert _tokens(_stored_budget(deployment, theirs, GLOBEX)) == 4242


def test_eviction_hands_no_budget_to_another_tenant(deployment, monkeypatch):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)

    _evict(sid, GLOBEX, deployment, monkeypatch)

    assert deployment.cost(sid, GLOBEX).status_code == 404
    assert deployment.checkpoints(sid, GLOBEX).status_code == 404  # refused by the owner check
    assert _tokens(deployment.cost(sid)) == 4242  # the owner still reads it


def test_a_refused_takeover_reads_no_budget(deployment, monkeypatch):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)
    _evict(sid, GLOBEX, deployment, monkeypatch)

    assert deployment.session(sid, GLOBEX).status_code == 409
    assert deployment.event(sid, GLOBEX, token_usage={"total_tokens": 1}).status_code == 404

    assert server._collector.get_trace(sid) is None  # a refused write leaves nothing in memory
    assert deployment.cost(sid, GLOBEX).status_code == 404
    assert deployment.checkpoints(sid, GLOBEX).status_code == 404
    assert _tokens(deployment.cost(sid)) == 4242


def test_nobody_reads_cost_after_a_restart_except_what_is_rebuilt(deployment):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)
    _restart()

    assert deployment.cost(sid, GLOBEX).status_code == 404
    assert deployment.cost(sid).status_code == 404  # budgets are not persisted: nothing to read


def test_a_lost_budget_is_rebuilt_once_for_the_owner_from_its_trace(deployment):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)
    server._cost_tracker._budgets.clear()

    assert _tokens(deployment.cost(sid)) == 4242
    assert _tokens(deployment.cost(sid)) == 4242  # not counted again


def test_a_rebuilt_budget_is_counted_for_the_caller_not_the_events_tag(deployment):
    sid = _id()
    deployment.session(sid)
    untagged = AgentEvent(
        session_id=sid,
        agent_id="bot",
        event_type=EventType.TOOL_CALL,
        token_usage=TokenUsage(total_tokens=10),
    )
    deployment.client.portal.call(server._collector.ingest, untagged)  # an in-process publisher

    assert _tokens(deployment.cost(sid)) == 10
    assert _tokens(deployment.cost(sid)) == 10  # found again, not added to a budget nobody reads


def test_without_a_database_eviction_and_reregistration_hand_over_no_budget(
    no_database, monkeypatch
):
    sid = _id()
    no_database.event(sid, token_usage=SPENT)
    _evict(sid, GLOBEX, no_database, monkeypatch)

    assert no_database.session(sid, GLOBEX).status_code == 200  # nothing durable knows the id

    _no_cost_of_acme(no_database.cost(sid, GLOBEX))


def test_without_a_database_an_unknown_owner_fails_closed(no_database, monkeypatch):
    sid = _id()
    no_database.event(sid, token_usage=SPENT)
    _evict(sid, GLOBEX, no_database, monkeypatch)

    assert no_database.cost(sid).status_code == 404
    assert no_database.cost(sid, GLOBEX).status_code == 404
    assert no_database.checkpoints(sid, GLOBEX).status_code == 404


def test_without_a_database_erasure_finds_a_budget_the_trace_cache_lost(no_database):
    sid = _id()
    no_database.event(sid, token_usage=SPENT)
    server._collector._traces.pop(sid)

    assert no_database.erase(sid).status_code == 200

    assert no_database.session(sid).status_code == 200
    _no_cost_of_acme(no_database.cost(sid))


def test_without_a_database_operator_pruning_removes_the_budget(no_database):
    sid = _id()
    no_database.session(sid)
    no_database.event(sid, token_usage=SPENT)
    checkpoint = no_database.snapshot(sid, no_database.acme)
    os.utime(_files_of(checkpoint)[0], (1_000_000_000, 1_000_000_000))

    response = no_database.client.delete(
        "/api/v1/sessions/prune?older_than_hours=1", headers={"X-Api-Key": "operator-key"}
    )

    assert response.status_code == 200, response.text
    assert no_database.session(sid).status_code == 200
    _no_cost_of_acme(no_database.cost(sid))


def test_operator_pruning_removes_the_budget(deployment, monkeypatch):
    sid = _id()
    deployment.session(sid, started_at="2020-01-01T00:00:00+00:00")
    deployment.event(sid, token_usage=SPENT)
    response = deployment.client.delete(
        "/api/v1/sessions/prune?older_than_hours=1", headers={"X-Api-Key": "operator-key"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["pruned_db_sessions"] >= 1
    assert deployment.session(sid).status_code == 200
    _no_cost_of_acme(deployment.cost(sid))


def test_two_tenants_racing_for_an_id_keep_their_cost_apart(deployment, monkeypatch):
    for _ in range(5):
        sid = _id()
        both_past_the_collector = asyncio.Barrier(2)

        async def meet(event, barrier=both_past_the_collector):
            await barrier.wait()  # neither request is ahead when the database decides
            return False

        monkeypatch.setattr(server, "_event_id_taken", meet)
        body = {
            "session_id": sid,
            "agent_id": "bot",
            "event_type": "tool.call",
            "token_usage": SPENT,
        }

        async def send():
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                return await asyncio.gather(
                    http.post("/api/v1/events", json=body, headers=ACME),
                    http.post("/api/v1/events", json=body, headers=GLOBEX),
                )

        first, second = deployment.client.portal.call(send)

        assert sorted((first.status_code, second.status_code)) == [200, 404]
        winner, loser = (ACME, GLOBEX) if first.status_code == 200 else (GLOBEX, ACME)
        assert _tokens(deployment.cost(sid, winner)) == 4242
        assert deployment.cost(sid, loser).status_code == 404


def _spend(session_id: str, tenant: str | None) -> AgentEvent:
    return AgentEvent(
        session_id=session_id,
        agent_id="bot",
        event_type=EventType.TOOL_CALL,
        token_usage=TokenUsage(total_tokens=10, estimated_cost_usd=1.0),
        metadata={"tenant_id": tenant} if tenant else {},
    )


def test_one_session_id_in_two_tenants_is_two_budgets():
    tracker = CostTracker()
    tracker.ingest_event(_spend("shared", "acme"))
    tracker.ingest_event(_spend("shared", "acme"))
    tracker.ingest_event(_spend("shared", "globex"))

    assert tracker.get_session("shared", tenant_id="acme").tokens_used == 20
    assert tracker.get_session("shared", tenant_id="globex").tokens_used == 10
    assert tracker.get_session("shared") is None  # a session id alone finds nothing


def test_an_untagged_budget_belongs_to_default():
    tracker = CostTracker()
    tracker.ingest_event(_spend("mine", None))

    assert tracker.get_session("mine").tokens_used == 10
    assert tracker.get_session("mine", tenant_id="default").tokens_used == 10
    assert tracker.get_session("mine", tenant_id="acme") is None


@pytest.mark.parametrize("tag", [5, ["x"], {"a": 1}])
def test_a_tenant_tag_of_the_wrong_type_is_a_plain_key(tag):
    tracker = CostTracker()
    event = _spend("mine", None)
    event.metadata["tenant_id"] = tag

    tracker.ingest_event(event)

    budget = tracker.get_session("mine", tenant_id=str(tag))
    assert budget is not None
    assert budget.tokens_used == 10
    assert tracker.get_session("mine") is None


def test_the_caller_can_name_the_budget_for_an_event():
    tracker = CostTracker()

    tracker.ingest_event(_spend("mine", "acme"), tenant_id="default")

    assert tracker.get_session("mine").tokens_used == 10
    assert tracker.get_session("mine", tenant_id="acme") is None


def test_forgetting_stays_in_the_tenant():
    tracker = CostTracker()
    tracker.ingest_event(_spend("shared", "acme"))
    tracker.ingest_event(_spend("shared", "globex"))

    assert tracker.forget(["shared"], "acme") == 1

    assert tracker.get_session("shared", tenant_id="acme") is None
    assert tracker.get_session("shared", tenant_id="globex") is not None


# ── checkpoints ────────────────────────────────────────────────────────────────────────────


def test_the_owner_can_restore_its_checkpoint(deployment):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.acme)
    _state(checkpoint).write_text("changed")

    result = deployment.rollback(sid, checkpoint_id=checkpoint.checkpoint_id).json()

    assert result["status"] == "completed"
    assert _state(checkpoint).read_text() == "original"


def test_a_checkpoint_id_cannot_be_used_through_another_tenants_session(deployment):
    theirs, ours = _id(), _id()
    deployment.session(theirs)
    deployment.session(ours, GLOBEX)
    checkpoint = deployment.snapshot(theirs, deployment.acme)
    _state(checkpoint).write_text("changed")

    result = deployment.rollback(ours, GLOBEX, checkpoint_id=checkpoint.checkpoint_id).json()

    assert result["status"] == "failed"
    assert _state(checkpoint).read_text() == "changed"  # nothing was restored


def test_a_foreign_checkpoint_answers_as_an_unknown_one(deployment):
    theirs, ours = _id(), _id()
    deployment.session(theirs)
    deployment.session(ours, GLOBEX)
    checkpoint = deployment.snapshot(theirs, deployment.acme)

    foreign = deployment.rollback(ours, GLOBEX, checkpoint_id=checkpoint.checkpoint_id)
    unknown = deployment.rollback(ours, GLOBEX, checkpoint_id="ckpt-does-not-exist")

    assert foreign.status_code == unknown.status_code == 200
    assert unknown.json()["error"] == "Checkpoint not found"

    def shape(response):
        body = response.json()
        return {k: v for k, v in body.items() if k not in ("checkpoint_id", "duration_seconds")}

    assert shape(foreign) == shape(unknown)


def test_a_checkpoint_of_another_session_of_the_same_tenant_is_refused(deployment):
    first, second = _id(), _id()
    deployment.session(first)
    deployment.session(second)
    checkpoint = deployment.snapshot(first, deployment.acme)
    _state(checkpoint).write_text("changed")

    result = deployment.rollback(second, checkpoint_id=checkpoint.checkpoint_id).json()

    assert result["status"] == "failed"
    assert _state(checkpoint).read_text() == "changed"


def test_a_checkpoint_stamped_for_another_tenant_is_refused_on_the_owners_session(deployment):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.globex)
    _state(checkpoint).write_text("changed")

    by_id = deployment.rollback(sid, checkpoint_id=checkpoint.checkpoint_id).json()
    latest = deployment.rollback(sid).json()

    assert (by_id["status"], latest["status"]) == ("failed", "failed")
    assert _state(checkpoint).read_text() == "changed"
    assert deployment.checkpoints(sid).json()["checkpoints"] == []


def test_another_tenants_checkpoints_are_not_listed(deployment):
    sid = _id()
    deployment.session(sid)
    deployment.snapshot(sid, deployment.acme)

    assert deployment.checkpoints(sid, GLOBEX).status_code == 404
    assert len(deployment.checkpoints(sid).json()["checkpoints"]) == 1


def test_erasure_invalidates_a_checkpoint_and_deletes_its_files(deployment):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.acme)
    _state(checkpoint).write_text("changed")
    assert all(path.exists() for path in _files_of(checkpoint))

    receipt = deployment.erase(sid).json()

    assert not any(path.exists() for path in _files_of(checkpoint))
    assert "runtime_state" in receipt["targets_reached"]
    deployment.session(sid)
    assert deployment.checkpoints(sid).json()["checkpoints"] == []
    result = deployment.rollback(sid, checkpoint_id=checkpoint.checkpoint_id).json()
    assert result["status"] == "failed"
    assert _state(checkpoint).read_text() == "changed"


def test_erased_checkpoints_do_not_reach_the_next_holder_of_the_id(deployment):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.acme)
    _state(checkpoint).write_text("changed")
    deployment.erase(sid)
    assert deployment.session(sid, GLOBEX).status_code == 200

    assert deployment.checkpoints(sid, GLOBEX).json()["checkpoints"] == []
    assert deployment.rollback(sid, GLOBEX).json()["status"] == "failed"
    assert _state(checkpoint).read_text() == "changed"


def test_a_tenant_cannot_erase_a_checkpoint_with_no_tenant_and_is_told(deployment):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, None)

    response = deployment.erase(sid)

    assert all(path.exists() for path in _files_of(checkpoint))
    assert response.status_code == 207, response.text
    assert "runtime_state" in response.json()["failed_targets"]


def test_default_owns_a_checkpoint_with_no_tenant(deployment):
    sid = _id()
    checkpoint = deployment.snapshot(sid, None)
    server._rollback_engine._checkpoints.clear()
    server._rollback_engine._session_checkpoints.clear()

    response = deployment.erase(sid, {"X-Api-Key": "operator-key"})

    assert response.status_code == 200, response.text
    assert not any(path.exists() for path in _files_of(checkpoint))


def test_erasing_another_tenants_session_leaves_its_checkpoints(deployment):
    sid = _id()
    deployment.session(sid)
    stamped = deployment.snapshot(sid, deployment.acme)
    unstamped = deployment.snapshot(sid, None)

    assert deployment.erase(sid, GLOBEX).json()["items_erased"] == 0

    assert all(path.exists() for path in _files_of(stamped) + _files_of(unstamped))
    assert len(deployment.checkpoints(sid).json()["checkpoints"]) == 1  # the stamped one


def test_erasing_a_session_leaves_a_checkpoint_stamped_for_another_tenant(deployment):
    sid = _id()
    deployment.session(sid)
    ours = deployment.snapshot(sid, deployment.acme)
    theirs = deployment.snapshot(sid, deployment.globex)

    deployment.erase(sid)

    assert not any(path.exists() for path in _files_of(ours))
    assert all(path.exists() for path in _files_of(theirs))
    assert server._rollback_engine.get_checkpoint(theirs.checkpoint_id) is not None
    assert server._rollback_engine._session_checkpoints.get(sid) == [theirs.checkpoint_id]


def test_a_checkpoint_left_on_disk_by_a_restart_is_still_erased(deployment):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.acme)
    server._rollback_engine._checkpoints.clear()
    server._rollback_engine._session_checkpoints.clear()
    _restart()

    deployment.erase(sid)

    assert not any(path.exists() for path in _files_of(checkpoint))


def test_a_tenants_stale_checkpoint_is_found_without_its_session_row(deployment):
    sid = _id()
    checkpoint = deployment.snapshot(sid, deployment.acme)
    server._rollback_engine._checkpoints.clear()  # only the metadata file can name it now
    server._rollback_engine._session_checkpoints.clear()

    deployment.erase(deployment.acme, scope="tenant_id")

    assert not any(path.exists() for path in _files_of(checkpoint))


def test_a_failed_checkpoint_delete_is_reported_not_claimed(deployment, monkeypatch):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.acme)

    def refuse(self, *args, **kwargs):
        raise PermissionError("in use")

    with monkeypatch.context() as broken:
        broken.setattr(Path, "unlink", refuse)
        response = deployment.erase(sid)

    body = response.json()
    assert response.status_code == 207, body
    assert "runtime_state" in body["failed_targets"]
    assert "runtime_state" not in body["targets_reached"]
    assert _files_of(checkpoint)[0].exists()


def test_a_snapshot_that_will_not_delete_is_reported_and_stays_findable(deployment, monkeypatch):
    sid = _id()
    deployment.session(sid)
    checkpoint = deployment.snapshot(sid, deployment.acme)

    def refuse(*args, **kwargs):
        raise PermissionError("in use")

    with monkeypatch.context() as broken:
        broken.setattr(shutil, "rmtree", refuse)
        response = deployment.erase(sid)

    body = response.json()
    assert response.status_code == 207, body
    assert "runtime_state" in body["failed_targets"]
    assert all(path.exists() for path in _files_of(checkpoint))  # a retry can still find it

    assert deployment.erase(sid).status_code == 200
    assert not any(path.exists() for path in _files_of(checkpoint))


def test_cost_is_erased_even_when_the_database_cannot_list_sessions(deployment, monkeypatch):
    sid = _id()
    deployment.event(sid, token_usage=SPENT)
    server._collector._traces.pop(sid)  # only the budget itself can name the session now

    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("database down")

        async def __aexit__(self, *exc):
            return False

    real = server._db_session_factory
    monkeypatch.setattr(server, "_db_session_factory", lambda: _Broken())
    body = deployment.erase(sid).json()
    monkeypatch.setattr(server, "_db_session_factory", real)

    assert deployment.session(sid).status_code == 200
    _no_cost_of_acme(deployment.cost(sid))
    assert "runtime_state" in body["failed_targets"]


async def test_rollback_checks_ownership_itself_before_restoring(tmp_path):
    engine = RollbackEngine(checkpoints_dir=tmp_path / "checkpoints")
    work = tmp_path / "work"
    work.mkdir()
    (work / "state.txt").write_text("original")
    checkpoint = await engine.create_checkpoint(
        "s1",
        1,
        working_dir=str(work),
        checkpoint_type=CheckpointType.FILESYSTEM,
        tenant_id="acme",
    )
    (work / "state.txt").write_text("changed")

    for owner in ({"tenant_id": "globex"}, {"session_id": "s2"}, {"tenant_id": "default"}):
        result = await engine.rollback(checkpoint.checkpoint_id, **owner)
        assert result.error == "Checkpoint not found"
        assert (work / "state.txt").read_text() == "changed"

    result = await engine.rollback(checkpoint.checkpoint_id, session_id="s1", tenant_id="acme")
    assert result.status.value == "completed"
    assert (work / "state.txt").read_text() == "original"


async def test_the_tenant_stamp_survives_the_metadata_file(tmp_path):
    engine = RollbackEngine(checkpoints_dir=tmp_path / "checkpoints")
    checkpoint = await engine.create_checkpoint(
        "s1", 1, working_dir=str(tmp_path), checkpoint_type=CheckpointType.MEMORY, tenant_id="acme"
    )

    loaded = await RollbackEngine(tmp_path / "checkpoints").load_checkpoint_meta(
        checkpoint.checkpoint_id
    )

    assert loaded.tenant_id == "acme"


async def test_rolling_back_a_session_only_uses_the_tenants_checkpoints(tmp_path):
    engine = RollbackEngine(checkpoints_dir=tmp_path / "checkpoints")
    await engine.create_checkpoint(
        "s1", 1, working_dir=str(tmp_path), checkpoint_type=CheckpointType.MEMORY, tenant_id="acme"
    )

    refused = await engine.rollback_session("s1", tenant_id="globex")

    assert refused.status.value == "failed"
    assert (await engine.rollback_session("s1", tenant_id="acme")).status.value == "completed"


# ── tenant id length ───────────────────────────────────────────────────────────────────────


def _tenant_of_length(length: int) -> str:
    return secrets.token_hex(32)[:length]


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, tmp_path):
    if request.param == "sqlite":
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    return tmp_path


@pytest.mark.parametrize("length", [1, 36, 37, 63, 64])
def test_a_tenant_id_of_any_allowed_length_is_stored_exactly(backend, monkeypatch, length):
    tenant = _tenant_of_length(length)
    other = f"z{_tenant_of_length(20)}"
    _configure(monkeypatch, backend, f"{tenant}=key-acme,{other}=key-globex")
    with TestClient(server.app) as client:
        deployment = Deployment(client, tenant, other, backend)
        sid = _id()
        assert deployment.session(sid).status_code == 200
        assert deployment.event(sid, token_usage=SPENT).status_code == 200

        async def read():
            async with server._db_session_factory() as db:
                row = await db.get(SessionRecord, sid)
                events = await db.execute(
                    select(EventRecord.tenant_id).where(EventRecord.session_id == sid)
                )
                return row.tenant_id, list(events.scalars())

        assert client.portal.call(read) == (tenant, [tenant])
        assert _tokens(deployment.cost(sid)) == 4242
        client.portal.call(_clean, [tenant, other])


def test_tenants_sharing_a_long_prefix_stay_separate(backend, monkeypatch):
    prefix = _tenant_of_length(36)
    first, second = f"{prefix}aaaa", f"{prefix}bbbb"
    _configure(monkeypatch, backend, f"{first}=key-acme,{second}=key-globex")
    with TestClient(server.app) as client:
        deployment = Deployment(client, first, second, backend)
        sid = _id()
        assert deployment.session(sid).status_code == 200

        assert deployment.session(sid, GLOBEX).status_code == 409

        _restart()
        assert deployment.session(sid, GLOBEX).status_code == 409
        client.portal.call(_clean, [first, second])


def test_a_tenant_id_past_the_limit_is_refused_at_configuration():
    with pytest.raises(ValueError):
        parse_tenant_keys(f"{'a' * 65}=key")
    assert parse_tenant_keys(f"{'a' * 64}=key") == {"key": "a" * 64}


def test_the_model_matches_the_longest_tenant_id():
    for model in (SessionRecord, EventRecord, MemoryEntryRecord):
        assert model.__table__.c.tenant_id.type.length == 64


def _migration_sql() -> str:
    doc = (Path(__file__).parent.parent / "docs" / "PERSISTENCE.md").read_text(encoding="utf-8")
    section = doc.split("### Widening tenant columns", 1)[1]
    return re.search(r"```sql\n(.*?)```", section, re.DOTALL).group(1)


@pytest.mark.skipif(not POSTGRES, reason="only PostgreSQL enforces the width")
async def test_an_existing_database_is_warned_not_migrated_and_the_documented_sql_works(caplog):
    import asyncpg

    url = os.environ["DATABASE_URL"]
    admin_dsn = url.replace("postgresql+asyncpg", "postgresql")
    name = f"widen_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = url.rpartition("/")
    target = f"{base}/{name}"
    engine = create_async_engine(target)
    factory = None
    try:
        factory = await init_db(target)
        async with engine.begin() as conn:  # what a database from before this change looks like
            await conn.execute(text("DROP TRIGGER agent_sessions_tenant_fixed ON agent_sessions"))
            for table in ("agent_sessions", "agent_events", "memory_entries"):
                await conn.execute(
                    text(f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE VARCHAR(36)")
                )
            await conn.execute(
                text(
                    "INSERT INTO agent_sessions (session_id, tenant_id, agent_id, framework, "
                    "status, started_at) VALUES ('old', 'old-tenant', 'bot', 'custom', "
                    "'running', now())"
                )
            )
        await factory.kw["bind"].dispose()

        with caplog.at_level("WARNING", logger="panoptis.core.models"):
            factory = await init_db(target)

        assert "tenant_id is narrower" in caplog.text

        async def widths():
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text(
                        "SELECT character_maximum_length FROM information_schema.columns "
                        "WHERE column_name = 'tenant_id' AND table_name IN "
                        "('agent_sessions', 'agent_events', 'memory_entries')"
                    )
                )
                return set(rows.scalars())

        assert await widths() == {36}  # warned, and left alone

        insert_long = text(
            "INSERT INTO agent_sessions (session_id, tenant_id, agent_id, framework, status, "
            "started_at) VALUES ('new', :tenant, 'bot', 'custom', 'running', now())"
        )
        with pytest.raises(Exception, match="too long"):
            async with engine.begin() as conn:
                await conn.execute(insert_long, {"tenant": "t" * 64})

        direct = await asyncpg.connect(admin_dsn.rpartition("/")[0] + f"/{name}")
        await direct.execute(_migration_sql())
        await direct.close()

        assert await widths() == {64}
        async with engine.begin() as conn:
            await conn.execute(insert_long, {"tenant": "t" * 64})
            old = await conn.execute(
                text("SELECT tenant_id FROM agent_sessions WHERE session_id = 'old'")
            )
            assert old.scalar_one() == "old-tenant"
        with pytest.raises(Exception, match="cannot change once set"):  # the guard is back
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE agent_sessions SET tenant_id = 'x' WHERE session_id = 'old'")
                )
    finally:
        await engine.dispose()
        if factory is not None:
            await factory.kw["bind"].dispose()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def test_every_session_route_refuses_another_tenant(deployment):
    """A new route under /sessions/{session_id} is checked here without anyone adding it."""
    sid = _id()
    assert deployment.session(sid).status_code == 200
    assert deployment.event(sid).status_code == 200
    bodies = {"rollback": {"restore_git": False}, "simulate": {"rewind_to_step": 0}}
    routes = {
        (method, route.path)
        for route in server.app.routes
        if "{session_id}" in getattr(route, "path", "")
        for method in getattr(route, "methods", ())
        if method != "HEAD"
    }
    assert len(routes) >= 10

    for method, path in sorted(routes):
        answer = deployment.client.request(
            method,
            path.replace("{session_id}", sid),
            json=bodies.get(path.rsplit("/", 1)[-1]),
            headers=GLOBEX,
        )
        assert answer.status_code == 404, f"{method} {path}: {answer.status_code}"
