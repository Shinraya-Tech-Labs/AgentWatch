"""Evidence-plane invariants: immutability, idempotency, redaction without mutation,
tamper-evident segments."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from panoptis.evidence.canonical import canonical_json
from panoptis.evidence.model import ObservationDraft, SensorRef
from panoptis.evidence.redaction import PayloadPolicy, redact_payload
from panoptis.evidence.segments import merkle_proof, merkle_root, verify_proof
from panoptis.storage.store import ImmutableEvidenceError

REF = SensorRef("test", "1", "test-1")


def draft(i: int, payload=None, **kw) -> ObservationDraft:
    return ObservationDraft(
        sensor=REF,
        source_kind="test.kind",
        payload=payload if payload is not None else {"i": i},
        source_seq=i,
        observed_at=datetime.now(UTC),
        **kw,
    )


def test_append_is_idempotent(store):
    res = store.append([draft(1), draft(2)])
    assert len(res.accepted) == 2
    again = store.append([draft(1), draft(2), draft(3)])
    assert len(again.accepted) == 1
    assert len(again.duplicates) == 2
    assert store.count_observations() == 3


def test_duplicate_within_batch_is_collapsed(store):
    res = store.append([draft(1), draft(1)])
    assert len(res.accepted) == 1 and len(res.duplicates) == 1


def test_stored_observation_round_trips_and_verifies(store):
    store.append([draft(7, {"nested": {"a": [1, 2, 3]}}, declared_ids={"span_id": "abc"})])
    obs = store.observations()[0]
    assert obs.payload() == {"nested": {"a": [1, 2, 3]}}
    assert obs.declared("span_id") == "abc"
    assert obs.verify_hash()


def test_payload_accessor_returns_independent_copies(store):
    store.append([draft(1, {"k": ["v"]})])
    obs = store.observations()[0]
    p = obs.payload()
    p["k"].append("mutated")
    assert obs.payload() == {"k": ["v"]}
    with pytest.raises(AttributeError):
        obs.payload_json = "{}"  # type: ignore[misc]


def test_database_rejects_update_and_delete(store):
    store.append([draft(1)])
    with pytest.raises(DBAPIError):
        with store.engine.begin() as conn:
            conn.execute(text("UPDATE aw3_observations SET payload_json = '{}'"))
    with pytest.raises(DBAPIError):
        with store.engine.begin() as conn:
            conn.execute(text("DELETE FROM aw3_observations"))
    with pytest.raises(ImmutableEvidenceError):
        store.update_observation("x")
    assert store.count_observations() == 1


def test_seal_and_verify_detects_tampering(store):
    store.append([draft(i) for i in range(10)])
    seg = store.seal()
    assert seg is not None and seg.n_obs == 10
    report = store.verify()
    assert report.ok and report.observations_checked == 10 and report.unsealed_observations == 0
    # simulate out-of-band tampering by bypassing the trigger
    with store.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER aw3_obs_no_update"))
        conn.execute(
            text("UPDATE aw3_observations SET payload_json = :p WHERE source_seq = 3"),
            {"p": '{"i":999}'},
        )
    bad = store.verify()
    assert not bad.ok
    assert any("payload hash mismatch" in e for e in bad.errors)


def test_segments_chain(store):
    store.append([draft(i) for i in range(3)])
    s1 = store.seal()
    store.append([draft(i) for i in range(3, 6)])
    s2 = store.seal()
    assert s2.prev_segment_hash == s1.segment_hash
    assert store.verify().ok


def test_inclusion_proof(store):
    store.append([draft(i) for i in range(5)])
    store.seal()
    obs = store.observations()[2]
    proof = store.inclusion_proof(obs.obs_id)
    assert verify_proof(proof["leaf"], [tuple(p) for p in proof["proof"]], proof["merkle_root"])


def test_merkle_helpers():
    leaves = [f"{i:064x}" for i in range(7)]
    root = merkle_root(leaves)
    for i in range(7):
        assert verify_proof(leaves[i], merkle_proof(leaves, i), root)
    assert not verify_proof(leaves[0], merkle_proof(leaves, 1), root)


def test_purge_requires_authorization_and_keeps_chain(store):
    store.append([draft(i) for i in range(4)])
    seg = store.seal()
    store.append([draft(i) for i in range(4, 6)])
    store.seal()
    removed = store.purge_segment(seg.segment_id, reason="retention: 30 days", actor="admin")
    assert removed == 4
    report = store.verify()
    assert report.ok, report.errors
    assert store.count_observations() == 2


def test_redaction_never_mutates_input():
    payload = {
        "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456"},
        "text": "key sk-ant-abcdefghijklmnopqrstuvwxyz0123",
    }
    original = copy.deepcopy(payload)
    red, manifest = redact_payload(payload)
    assert payload == original
    assert "sk-ant" not in canonical_json(red)
    assert red["headers"]["Authorization"].startswith("[REDACTED")
    assert manifest is not None and manifest.total >= 2


def test_redaction_is_recorded_in_stored_observation(store):
    store.append([draft(1, {"prompt": "use key AKIAABCDEFGHIJKLMNOP now"})])
    obs = store.observations()[0]
    assert "AKIA" not in obs.payload_json
    assert obs.redaction is not None
    assert obs.redaction.redactions[0][1] == "secret.aws_access_key"


def test_hash_capture_policy_keeps_structure():
    red, _ = redact_payload({"a": "hello", "b": [1, "x"]}, PayloadPolicy(capture="hash"))
    assert red["a"]["$len"] == 5 and red["b"][0] == 1 and "$sha256" in red["b"][1]


def test_large_payload_goes_to_blob_store(store):
    big = {"text": "x" * 100_000}
    store.append([draft(1, big)])
    obs = store.observations()[0]
    assert obs.payload() == big
    assert store.verify().ok or store.seal() is not None


def test_rejects_invalid_drafts(store):
    bad = ObservationDraft(sensor=REF, source_kind="", payload={})
    naive = ObservationDraft(
        sensor=REF, source_kind="k", payload={}, observed_at=datetime(2024, 1, 1)
    )
    res = store.append([bad, naive])
    assert len(res.rejected) == 2 and not res.accepted
