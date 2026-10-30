"""Crypto-shredding: per-subject encryption, erasure, and what survives it (ADR-0011)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from panoptis import instrument as aw
from panoptis.runtime.engine import Engine
from panoptis.storage.store import Store

SUBJECT_TEXT = "Alice Example's medical history mentions a rare condition"


@pytest.fixture
def enc_engine(tmp_path):
    store = Store(f"sqlite:///{(tmp_path / 'enc.db').as_posix()}", encrypt_payloads=True)
    yield Engine(store)
    store.close()


def record(sink, subject: str, text_value: str) -> None:
    @aw.model("stub/m")
    def summarize(p: str) -> str:
        return "summary of " + p

    with aw.run("intake", subject_id=subject):
        with aw.span("OPERATION", "handle", actor="agent:a"):
            summarize(text_value)


def test_payloads_are_encrypted_at_rest_and_readable(enc_engine, sink):
    record(sink, "alice", SUBJECT_TEXT)
    enc_engine.ingest(sink.drafts)
    with enc_engine.store.engine.connect() as conn:
        stored = [r[0] for r in conn.execute(text("SELECT payload_json FROM aw3_observations"))]
    assert stored and all(v.startswith("enc:v1:") for v in stored)
    assert not any("Alice" in v for v in stored)
    obs = enc_engine.store.observations()
    assert any("Alice" in o.payload_json for o in obs)  # decrypted on read
    assert all(o.encoding == "aes-gcm" for o in obs)
    assert all(o.declared("subject_id") == "alice" for o in obs)
    enc_engine.process()
    assert enc_engine.store.verify().ok or enc_engine.store.seal() is not None


def test_idempotency_survives_encryption(enc_engine, sink):
    record(sink, "alice", SUBJECT_TEXT)
    first = enc_engine.ingest(sink.drafts)
    again = enc_engine.ingest(sink.drafts)
    assert len(first.accepted) == len(sink.drafts)
    assert len(again.accepted) == 0 and len(again.duplicates) == len(sink.drafts)


def test_erasure_shreds_one_subject_and_keeps_the_chain(enc_engine, sink):
    record(sink, "alice", SUBJECT_TEXT)
    record(sink, "bob", "Bob's harmless note about the weather in Lisbon today")
    enc_engine.ingest(sink.drafts)
    enc_engine.process()
    enc_engine.store.seal_all()
    iid = enc_engine.interp_id_for("default")
    with enc_engine.store.engine.connect() as conn:
        before = " ".join(
            str(r) for r in conn.execute(text("SELECT content_json FROM aw3_artifacts"))
        )
    assert "Alice" in before  # derived copies exist before erasure

    report = enc_engine.erase_subject(
        "default", "alice", reason="GDPR art. 17 request", actor="test"
    )
    assert report["key_destroyed"] and report["observations_affected"] > 0

    # evidence chain still verifies: rows were not modified, only the key is gone
    assert enc_engine.store.verify().ok
    obs = enc_engine.store.observations()
    alice = [o for o in obs if o.declared("subject_id") == "alice"]
    bob = [o for o in obs if o.declared("subject_id") == "bob"]
    assert alice and all(o.erased and "Alice" not in o.payload_json for o in alice)
    assert bob and all(o.encoding == "aes-gcm" for o in bob)

    # no derived row still contains the erased subject's plaintext
    with enc_engine.store.engine.connect() as conn:
        for table in (
            "aw3_events",
            "aw3_artifacts",
            "aw3_relations",
            "aw3_derived",
            "aw3_runs",
            "aw3_experiments",
        ):
            dump = " ".join(str(r) for r in conn.execute(text(f"SELECT * FROM {table}")))  # noqa: S608 - fixed table names
            assert "Alice" not in dump, table
    events = enc_engine.store.events(iid)
    assert events  # rebuilt from the remaining readable evidence
    with enc_engine.store.engine.connect() as conn:
        after = " ".join(
            str(r) for r in conn.execute(text("SELECT content_json FROM aw3_artifacts"))
        )
    assert "Lisbon" in after  # bob's data survives
    diags = enc_engine.store.diagnostics(iid)
    assert sum(1 for d in diags if d["code"] == "payload_erased") == len(alice)
    assert enc_engine.store.erasures()[0]["subject"] == "alice"


def test_erased_subject_cannot_receive_new_payloads(enc_engine, sink):
    record(sink, "carol", "first")
    enc_engine.ingest(sink.drafts)
    enc_engine.erase_subject("default", "carol", reason="request")
    sink.drafts.clear()
    record(sink, "carol", "second")
    with pytest.raises(ValueError, match="destroyed"):
        enc_engine.ingest(sink.drafts)


def test_plain_store_is_unchanged(store, sink):
    record(sink, "dave", "plain text")
    Engine(store).ingest(sink.drafts)
    obs = store.observations()
    assert all(o.encoding == "plain" and o.verify_hash() for o in obs)
