"""Audit tokens (HMAC-SHA256 and optional Ed25519) and hash commitments."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from panoptis.evidence.attestation import (
    FORMAT_ID,
    AuditToken,
    CommittedRecord,
    Ed25519Signer,
    Ed25519Verifier,
    HmacSha256Signer,
    Opening,
    commit,
    issue_token,
    record_digest,
    verify_disclosure,
    verify_opening,
    verify_rule,
    verify_token,
)
from panoptis.evidence.crypto import CryptoImportError, CryptoUnavailableError
from panoptis.evidence.merkle import MerkleProof, MerkleTree

SEALED_AT = datetime(2026, 10, 27, 9, 30, tzinfo=UTC)


def leaves(n: int) -> list[str]:
    return [hashlib.sha256(f"witness-{i}".encode()).hexdigest() for i in range(n)]


@pytest.fixture
def signer() -> HmacSha256Signer:
    return HmacSha256Signer(secrets.token_bytes(32), key_id="audit-key-1")


@pytest.fixture
def tree() -> MerkleTree:
    return MerkleTree(leaves(9))


@pytest.fixture
def token(tree: MerkleTree, signer: HmacSha256Signer) -> AuditToken:
    return issue_token(tree, 4, segment_id="seg-0007", signer=signer, timestamp=SEALED_AT)


# ---- HMAC audit token -----------------------------------------------------------------------


def test_token_verifies_and_carries_what_the_issue_asked_for(
    token: AuditToken, tree: MerkleTree, signer: HmacSha256Signer
) -> None:
    assert token.segment_root == tree.root
    assert token.leaf_hash == leaves(9)[4]
    assert token.timestamp == "2026-10-27T09:30:00+00:00"
    assert token.alg == "HMAC-SHA256"
    assert token.version == FORMAT_ID
    result = verify_token(token, signer)
    assert result and result.ok and result.reason == ""


def test_signature_is_the_panopti_hmac_scheme(token: AuditToken, signer: HmacSha256Signer) -> None:
    """HMAC-SHA256 hex digest over the signed text, as in panopti/attest.py."""
    key = signer._key
    expected = hmac.new(key, token.signing_input(), hashlib.sha256).hexdigest()
    assert token.signature == expected


def test_token_is_self_contained_json(token: AuditToken, signer: HmacSha256Signer) -> None:
    text = token.to_json()
    restored = AuditToken.from_json(text)
    assert restored == token
    assert verify_token(restored, signer)
    data = json.loads(text)
    assert set(data) >= {"segment_root", "proof", "timestamp", "signature", "leaf_hash"}


def test_every_token_field_is_covered_by_the_signature(
    token: AuditToken, signer: HmacSha256Signer
) -> None:
    other_root = MerkleTree(leaves(10)).root
    forged_proof = MerkleProof(token.proof.leaf_index, token.proof.tree_size + 1, token.proof.path)
    changes = {
        "segment_id": "seg-0008",
        "segment_root": other_root,
        "leaf_hash": leaves(10)[9],
        "proof": forged_proof,
        "timestamp": "2026-10-27T09:31:00+00:00",
        "key_id": "audit-key-2",
        "signature": "0" * 64,
    }
    for name, value in changes.items():
        tampered = dataclasses.replace(token, **{name: value})
        assert not verify_token(tampered, signer), name


def test_wrong_key_rejected(token: AuditToken) -> None:
    other = HmacSha256Signer(secrets.token_bytes(32), key_id="audit-key-1")
    result = verify_token(token, other)
    assert not result
    assert "signature" in result.reason


def test_validly_signed_token_with_wrong_leaf_or_root_fails_the_proof(
    tree: MerkleTree, signer: HmacSha256Signer
) -> None:
    good = issue_token(tree, 2, segment_id="s", signer=signer, timestamp=SEALED_AT)
    for bad in (
        dataclasses.replace(good, leaf_hash=leaves(9)[3]),
        dataclasses.replace(good, segment_root=MerkleTree(leaves(5)).root),
        dataclasses.replace(good, proof=tree.prove(3)),
    ):
        resigned = dataclasses.replace(bad, signature=signer.sign(bad.signing_input()))
        result = verify_token(resigned, signer)
        assert not result and "proof" in result.reason


def test_algorithm_and_key_id_must_match_the_verifier(
    token: AuditToken, signer: HmacSha256Signer
) -> None:
    for tampered in (
        dataclasses.replace(token, alg="none"),
        dataclasses.replace(token, alg="Ed25519"),
        dataclasses.replace(token, key_id="someone-else"),
    ):
        assert not verify_token(tampered, signer)


def test_unsupported_version_rejected(token: AuditToken, signer: HmacSha256Signer) -> None:
    result = verify_token(dataclasses.replace(token, version="panoptis-audit-token/2"), signer)
    assert not result and "version" in result.reason


def test_expected_root_pins_the_segment(token: AuditToken, signer: HmacSha256Signer) -> None:
    assert verify_token(token, signer, expected_root=token.segment_root)
    result = verify_token(token, signer, expected_root=MerkleTree(leaves(3)).root)
    assert not result and "root" in result.reason


def test_garbage_signature_does_not_raise(token: AuditToken, signer: HmacSha256Signer) -> None:
    for sig in ("", "zz", "éé", "0" * 200):
        assert not verify_token(dataclasses.replace(token, signature=sig), signer)


def test_bad_timestamp_rejected_even_when_signed(
    token: AuditToken, signer: HmacSha256Signer
) -> None:
    for stamp in ("yesterday", "2026-10-27T09:30:00"):  # second one has no UTC offset
        bad = dataclasses.replace(token, timestamp=stamp)
        bad = dataclasses.replace(bad, signature=signer.sign(bad.signing_input()))
        result = verify_token(bad, signer)
        assert not result and "timestamp" in result.reason


def test_issue_rejects_naive_timestamp_and_normalizes_offsets(
    tree: MerkleTree, signer: HmacSha256Signer
) -> None:
    with pytest.raises(ValueError):
        issue_token(tree, 0, segment_id="s", signer=signer, timestamp=datetime(2026, 1, 1))
    ist = timezone(timedelta(hours=5, minutes=30))
    tok = issue_token(
        tree, 0, segment_id="s", signer=signer, timestamp=datetime(2026, 1, 1, 5, 30, tzinfo=ist)
    )
    assert tok.timestamp == "2026-01-01T00:00:00+00:00"
    assert verify_token(issue_token(tree, 0, segment_id="s", signer=signer), signer)  # now()


def test_every_leaf_gets_a_verifiable_token(tree: MerkleTree, signer: HmacSha256Signer) -> None:
    for i in range(tree.size):
        tok = issue_token(tree, i, segment_id="s", signer=signer, timestamp=SEALED_AT)
        assert verify_token(tok, signer)


def test_from_dict_rejects_malformed_tokens(token: AuditToken) -> None:
    good = token.to_dict()
    for key in ("segment_root", "proof", "signature", "version"):
        broken = {k: v for k, v in good.items() if k != key}
        with pytest.raises(ValueError):
            AuditToken.from_dict(broken)
    with pytest.raises(ValueError):
        AuditToken.from_dict({**good, "proof": {"leaf_index": 1}})
    with pytest.raises(ValueError):
        AuditToken.from_dict(None)  # type: ignore[arg-type]


def test_hmac_key_requirements() -> None:
    with pytest.raises(ValueError):
        HmacSha256Signer(b"short")
    with pytest.raises(ValueError):
        HmacSha256Signer("")
    s = HmacSha256Signer("a-sixteen-byte-key!")  # str keys accepted, like panopti/attest.py
    assert s.verify(b"m", s.sign(b"m"))
    assert not s.verify(b"m2", s.sign(b"m"))


# ---- optional Ed25519 -----------------------------------------------------------------------


def test_ed25519_token_verified_with_public_key_only(tree: MerkleTree) -> None:
    signer = Ed25519Signer.generate(key_id="ed-1")
    tok = issue_token(tree, 5, segment_id="seg", signer=signer, timestamp=SEALED_AT)
    assert tok.alg == "Ed25519"
    verifier = Ed25519Verifier(signer.public_key_bytes(), key_id="ed-1")
    assert verify_token(AuditToken.from_json(tok.to_json()), verifier)
    assert verify_token(tok, signer.verifier())
    tampered = dataclasses.replace(tok, segment_id="other")
    assert not verify_token(tampered, verifier)
    assert not verify_token(dataclasses.replace(tok, signature="zz"), verifier)
    assert not verify_token(dataclasses.replace(tok, signature="00" * 64), verifier)
    stranger = Ed25519Signer.generate(key_id="ed-1").verifier()
    assert not verify_token(tok, stranger)


def test_hmac_token_is_not_accepted_by_an_ed25519_verifier_and_vice_versa(
    tree: MerkleTree, signer: HmacSha256Signer
) -> None:
    ed = Ed25519Signer.generate(key_id=signer.key_id)
    hmac_tok = issue_token(tree, 0, segment_id="s", signer=signer, timestamp=SEALED_AT)
    ed_tok = issue_token(tree, 0, segment_id="s", signer=ed, timestamp=SEALED_AT)
    assert not verify_token(hmac_tok, ed.verifier())
    assert not verify_token(ed_tok, signer)


def test_missing_cryptography_gives_an_actionable_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "cryptography", None)  # makes `import cryptography` fail
    with pytest.raises(ImportError) as info:
        Ed25519Signer.generate()
    assert isinstance(info.value, CryptoImportError)
    assert isinstance(info.value, CryptoUnavailableError)  # existing handlers still catch it
    assert "pip install 'panoptis[crypto]'" in str(info.value)
    with pytest.raises(ImportError):
        Ed25519Verifier(b"\x00" * 32)


def test_core_works_with_cryptography_blocked() -> None:
    """Merkle, HMAC tokens and commitments must not need the optional package."""
    code = (
        "import sys; sys.modules['cryptography'] = None\n"
        "import hashlib, secrets\n"
        "from panoptis.evidence.attestation import (HmacSha256Signer, issue_token, verify_token,"
        " CommittedRecord, verify_rule)\n"
        "from panoptis.evidence.merkle import MerkleTree\n"
        "rec = CommittedRecord({'human_approval': True, 'prompt': 'secret text'})\n"
        "tree = MerkleTree([hashlib.sha256(b'x').hexdigest(), rec.digest])\n"
        "s = HmacSha256Signer(secrets.token_bytes(32))\n"
        "t = issue_token(tree, 1, segment_id='s', signer=s, record=rec)\n"
        "assert verify_rule(t, s, rec.disclose('human_approval'), {'human_approval': True})\n"
        "print('stdlib-ok')\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert "stdlib-ok" in out.stdout


# ---- commitments ----------------------------------------------------------------------------


def test_commitment_opens_to_the_committed_value() -> None:
    digest, opening = commit({"approved": True, "by": "j.doe"})
    assert len(digest) == 64
    assert verify_opening(digest, opening)
    assert Opening.from_dict(opening.to_dict()) == opening


def test_commitment_is_hiding() -> None:
    """Same value, fresh nonce: unrelated digests. The digest does not contain the value."""
    d1, o1 = commit(True)
    d2, o2 = commit(True)
    assert d1 != d2 and o1.nonce != o2.nonce
    private_text = "the customer's diagnosis is X"
    digest, _ = commit(private_text)
    assert private_text not in digest
    # a guesser without the nonce cannot confirm a guess by hashing the plain value
    assert hashlib.sha256(json.dumps(private_text).encode()).hexdigest() != digest
    assert hashlib.sha256(b"true").hexdigest() != d1


def test_commitment_is_binding() -> None:
    digest, opening = commit({"approved": True})
    assert not verify_opening(digest, Opening({"approved": False}, opening.nonce))
    assert not verify_opening(digest, Opening({"approved": True}, secrets.token_hex(32)))
    assert not verify_opening(digest, Opening(opening.value, opening.nonce[:-2]))
    assert not verify_opening(digest, Opening(opening.value, "not-hex"))
    assert not verify_opening(digest, Opening(opening.value, "07" * 8))  # nonce below the minimum
    assert not verify_opening("0" * 64, opening)
    assert not verify_opening(digest, Opening(float("nan"), opening.nonce))


def test_commitment_nonce_rules_and_determinism_with_a_fixed_nonce() -> None:
    nonce = b"\x07" * 16
    assert commit("x", nonce)[0] == commit("x", nonce)[0]
    assert commit("x", nonce)[0] != commit("y", nonce)[0]
    with pytest.raises(ValueError):
        commit("x", b"short")
    with pytest.raises(ValueError):
        commit(float("inf"))


def test_opening_from_dict_validation() -> None:
    with pytest.raises(ValueError):
        Opening.from_dict({"value": 1})
    with pytest.raises(ValueError):
        Opening.from_dict(None)  # type: ignore[arg-type]


# ---- committed records and rule verification ------------------------------------------------

PROMPT = "Please wire 40,000 EUR to account DE00 0000 0000 0000 0000 00 for Acme GmbH"


@pytest.fixture
def record() -> CommittedRecord:
    return CommittedRecord(
        {
            "event": "payment_proposed",
            "human_approval": True,
            "approver": "j.doe",
            "prompt": PROMPT,
            "parameters": {"amount": 40000, "iban": "DE00 0000 0000 0000 0000 00"},
        }
    )


@pytest.fixture
def rec_token(record: CommittedRecord, signer: HmacSha256Signer) -> AuditToken:
    ls = leaves(5)
    ls[3] = record.digest
    return issue_token(
        MerkleTree(ls), 3, segment_id="seg-9", signer=signer, timestamp=SEALED_AT, record=record
    )


def test_rule_verified_without_revealing_prompt_or_parameters(
    record: CommittedRecord, rec_token: AuditToken, signer: HmacSha256Signer
) -> None:
    disclosure = record.disclose("human_approval")
    wire = rec_token.to_json() + json.dumps({k: v.to_dict() for k, v in disclosure.items()})
    assert "wire 40,000" not in wire and "DE00" not in wire and "j.doe" not in wire
    assert set(rec_token.fields or {}) == {
        "event",
        "human_approval",
        "approver",
        "prompt",
        "parameters",
    }
    restored = AuditToken.from_json(rec_token.to_json())
    result = verify_rule(restored, signer, disclosure, {"human_approval": True})
    assert result and result.ok


def test_rule_with_two_fields_and_disclosure_of_extra_field(
    record: CommittedRecord, rec_token: AuditToken, signer: HmacSha256Signer
) -> None:
    disclosure = record.disclose("human_approval", "event")
    required = {"human_approval": True, "event": "payment_proposed"}
    assert verify_rule(rec_token, signer, disclosure, required)


def test_rule_not_satisfied_is_rejected(
    record: CommittedRecord, rec_token: AuditToken, signer: HmacSha256Signer
) -> None:
    disclosure = record.disclose("human_approval")
    assert not verify_rule(rec_token, signer, disclosure, {"human_approval": False})
    assert not verify_rule(rec_token, signer, disclosure, {"human_approval": 1})  # True is not 1
    assert not verify_rule(rec_token, signer, disclosure, {"human_approval": "true"})
    assert not verify_rule(rec_token, signer, disclosure, {})
    assert not verify_rule(rec_token, signer, disclosure, {"approver": "j.doe"})  # not disclosed


def test_forged_disclosure_is_rejected(
    record: CommittedRecord, rec_token: AuditToken, signer: HmacSha256Signer
) -> None:
    """An owner who never got approval cannot open the approval field to True afterwards."""
    no_approval = CommittedRecord({"human_approval": False, "prompt": PROMPT})
    lying = no_approval.disclose("human_approval")
    assert not verify_rule(rec_token, signer, lying, {"human_approval": True})
    forged = {
        "human_approval": Opening(
            True, no_approval.disclose("human_approval")["human_approval"].nonce
        )
    }
    assert not verify_rule(rec_token, signer, forged, {"human_approval": True})
    unknown_field = {"other": record.disclose("approver")["approver"]}
    assert not verify_rule(rec_token, signer, unknown_field, {"other": "j.doe"})


def test_tampering_with_field_commitments_is_rejected(
    record: CommittedRecord, rec_token: AuditToken, signer: HmacSha256Signer
) -> None:
    fake_digest, fake_open = commit(True)
    swapped = dict(rec_token.fields or {})
    swapped["human_approval"] = fake_digest
    tampered = dataclasses.replace(rec_token, fields=swapped)
    assert not verify_rule(
        tampered, signer, {"human_approval": fake_open}, {"human_approval": True}
    )


def test_signed_token_whose_fields_do_not_match_its_leaf_is_rejected(
    rec_token: AuditToken, signer: HmacSha256Signer
) -> None:
    other = CommittedRecord({"human_approval": True})
    bad = dataclasses.replace(rec_token, fields=other.fields)
    bad = dataclasses.replace(bad, signature=signer.sign(bad.signing_input()))
    result = verify_token(bad, signer)
    assert not result and "commitments" in result.reason


def test_rule_check_needs_field_commitments_and_a_valid_token(
    tree: MerkleTree, signer: HmacSha256Signer, record: CommittedRecord, rec_token: AuditToken
) -> None:
    plain = issue_token(tree, 1, segment_id="s", signer=signer, timestamp=SEALED_AT)
    result = verify_rule(plain, signer, record.disclose("human_approval"), {"human_approval": True})
    assert not result and "no field commitments" in result.reason
    wrong_key = HmacSha256Signer(secrets.token_bytes(32), key_id=signer.key_id)
    assert not verify_rule(
        rec_token, wrong_key, record.disclose("human_approval"), {"human_approval": True}
    )


def test_issue_token_rejects_a_record_that_is_not_the_leaf(
    record: CommittedRecord, tree: MerkleTree, signer: HmacSha256Signer
) -> None:
    with pytest.raises(ValueError):
        issue_token(tree, 0, segment_id="s", signer=signer, record=record)


def test_record_api_edges(record: CommittedRecord) -> None:
    with pytest.raises(ValueError):
        CommittedRecord({})
    with pytest.raises(TypeError):
        CommittedRecord({1: "x"})  # type: ignore[dict-item]
    with pytest.raises(KeyError):
        record.disclose("missing")
    assert record.disclose() == {}
    assert record.digest == record_digest(record.fields)
    mutated = record.fields
    mutated["event"] = "x"
    assert record.fields["event"] != "x"  # fields returns a copy
    assert record_digest({"a": "1"}) != record_digest({"a": "2"})
    assert not verify_disclosure({}, {}, {})
    assert not verify_disclosure(record.fields, record.disclose("event"), {"event": float("nan")})
