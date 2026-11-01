"""Retention purge removes every copy of the purged payloads, not only the observation rows.

Regression: purge deleted the observation rows but left the blob files of large payloads on
disk, and derived copies (events, artifacts and their blobs) until an unrelated rebuild."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from panoptis import instrument as aw
from panoptis.runtime.engine import Engine
from panoptis.storage.store import Store

MARKER = "purge-me-" + "x" * 100_000  # over the 64 KB inline limit: stored as a blob


@pytest.fixture
def engine(tmp_path):
    store = Store(f"sqlite:///{(tmp_path / 'aw.db').as_posix()}")
    yield Engine(store)
    store.close()


def record(sink, name: str, output: str) -> None:
    sink.drafts.clear()
    with aw.run(name):
        with aw.span("TOOL_INVOCATION", "fetch", actor="agent:a") as s:
            s.output(output)


def blob_files(store: Store) -> set[str]:
    return {p.name for p in store.blobs.root.rglob("*") if p.is_file()}


def test_purge_removes_blobs_and_derived_copies(engine, sink):
    st = engine.store
    record(sink, "old-run", MARKER)
    engine.ingest(sink.drafts)
    old = st.seal()
    record(sink, "new-run", "small output")
    engine.ingest(sink.drafts)
    st.seal()
    engine.process("default", force=True)
    assert blob_files(st), "the large payload should be stored as a blob"
    with st.engine.connect() as conn:
        artifacts_before = conn.execute(text("SELECT count(*) FROM aw3_artifacts")).scalar()

    report = engine.purge_segments([old.segment_id], reason="retention test", actor="test")

    assert report["observations"] > 0
    assert report["blobs_deleted"] >= 1
    assert blob_files(st) == set(), "blob files of purged payloads must be deleted"
    with st.engine.connect() as conn:
        for table, column in (
            ("aw3_artifacts", "content_json"),
            ("aw3_events", "doc"),
        ):
            leaked = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} LIKE '%purge-me-%'")  # noqa: S608 - fixed names
            ).scalar()
            assert leaked == 0, f"{table} still holds a copy of a purged payload"
        artifacts_after = conn.execute(text("SELECT count(*) FROM aw3_artifacts")).scalar()
    assert artifacts_after < artifacts_before
    names = {r["name"] for r in st.runs(st.active_interpretation("default")["interp_id"])}
    assert names == {"new-run"}
    assert st.verify("default").ok


def test_blob_shared_with_a_kept_observation_survives(engine, sink):
    st = engine.store
    record(sink, "old-run", MARKER)
    engine.ingest(sink.drafts)
    old = st.seal()
    record(sink, "new-run", MARKER)  # byte-identical payload: same content-addressed blob
    engine.ingest(sink.drafts)
    st.seal()
    engine.purge_segments([old.segment_id], reason="retention test")
    assert blob_files(st), "a blob still referenced by a kept observation must stay"
    assert any(MARKER in o.payload_json for o in st.observations())
    assert st.verify("default").ok


def test_erasure_deletes_plaintext_artifact_blobs(tmp_path, sink):
    store = Store(f"sqlite:///{(tmp_path / 'enc.db').as_posix()}", encrypt_payloads=True)
    try:
        engine = Engine(store)
        sink.drafts.clear()
        with aw.run("intake", subject_id="alice"):
            with aw.span("TOOL_INVOCATION", "fetch", actor="agent:a") as s:
                s.output(MARKER)
        engine.ingest(sink.drafts)
        engine.process("default", force=True)
        engine.erase_subject("default", "alice", reason="test")
        for name in blob_files(store):
            data = next(store.blobs.root.rglob(name)).read_bytes()
            assert b"purge-me-" not in data, "a plaintext copy outlived the erasure"
    finally:
        store.close()
