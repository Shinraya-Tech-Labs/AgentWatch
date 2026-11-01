"""PostgreSQL store behaviour (runs when AGENTWATCH_TEST_PG_URL is set, e.g. in CI)."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from panoptis.evidence.model import ObservationDraft, SensorRef
from panoptis.runtime.engine import Engine
from panoptis.sensors.base import ListSink
from panoptis.sensors.native.recorder import Recorder
from panoptis.storage.store import Store

PG_URL = os.environ.get("AGENTWATCH_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="AGENTWATCH_TEST_PG_URL not set")


@pytest.fixture
def pg_store(tmp_path):
    store = Store(PG_URL, blob_dir=tmp_path / "blobs")
    yield store
    store.close()


def _tenant() -> str:
    return f"t-{uuid.uuid4().hex[:10]}"


def _drafts(tenant: str, n: int) -> list[ObservationDraft]:
    ref = SensorRef("test", "1", f"pg-{tenant}")
    return [
        ObservationDraft(
            sensor=ref,
            source_kind="test.kind",
            payload={"i": i},
            source_seq=i,
            observed_at=datetime.now(UTC),
            tenant_id=tenant,
        )
        for i in range(n)
    ]


def test_pg_append_idempotent_and_immutable(pg_store):
    t = _tenant()
    assert len(pg_store.append(_drafts(t, 5)).accepted) == 5
    assert len(pg_store.append(_drafts(t, 5)).duplicates) == 5
    with pytest.raises(DBAPIError):
        with pg_store.engine.begin() as conn:
            conn.execute(
                text("UPDATE aw3_observations SET payload_json = '{}' WHERE tenant_id = :t"),
                {"t": t},
            )
    with pytest.raises(DBAPIError):
        with pg_store.engine.begin() as conn:
            conn.execute(text("DELETE FROM aw3_observations WHERE tenant_id = :t"), {"t": t})
    pg_store.seal_all(t)
    assert pg_store.verify(t).ok


def test_pg_engine_shared_between_processes(pg_store):
    """Two engines (as two API/worker processes would) see the same interpretation."""
    t = _tenant()
    sink = ListSink()
    rec = Recorder(sink, tenant_id=t)
    with rec.run("pg-run"):
        with rec.span("TOOL_INVOCATION", "t", object="tool:t") as s:
            s.input({"a": 1}).output("ok")
    e1, e2 = Engine(PG_URL), Engine(PG_URL)
    e1.ingest(sink.drafts)
    errors: list[BaseException] = []

    def run(e: Engine) -> None:
        try:
            e.process(t, force=True)
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(e,)) for e in (e1, e2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    iid = e2.interp_id_for(t)
    assert len(e2.store.runs(iid)) == 1
    assert {e["kind"] for e in e1.store.events(iid)} == {"LIFECYCLE", "TOOL_INVOCATION"}


def test_pg_crypto_shredding(tmp_path):
    t = _tenant()
    store = Store(PG_URL, blob_dir=tmp_path / "blobs", encrypt_payloads=True)
    try:
        sink = ListSink()
        rec = Recorder(sink, tenant_id=t)
        with rec.run("pg-erase", subject_id="subj-1"):
            with rec.span("TOOL_INVOCATION", "t", object="tool:t") as s:
                s.input({"name": "Private Person"}).output("ok")
        engine = Engine(store)
        engine.ingest(sink.drafts)
        engine.process(t)
        store.seal_all(t)
        report = engine.erase_subject(t, "subj-1", reason="test")
        assert report["key_destroyed"]
        assert store.verify(t).ok
        assert all(o.erased for o in store.observations(t))
    finally:
        store.close()
