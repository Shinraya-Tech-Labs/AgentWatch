"""Crypto-shredding of witness payloads: leaf hashes over ciphertext, proofs survive erasure.

These tests need the ``cryptography`` package (AES-256-GCM), which ships with the ``crypto``
extra. ``tests/v3/test_erasure.py`` relies on it the same way.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidTag

from panoptis.evidence import crypto
from panoptis.evidence.attestation import (
    AuditToken,
    HmacSha256Signer,
    issue_token,
    verify_token,
)
from panoptis.evidence.crypto import CryptoImportError
from panoptis.evidence.merkle import MerkleTree, verify_inclusion
from panoptis.evidence.shredding import (
    InMemoryKeyStore,
    ShreddingVault,
    SubjectErasedError,
    UnknownKeyError,
    seal_segment,
)

ALICE_1 = {"prompt": "Alice Example, DOB 1980-02-03, reports chest pain", "tool": "triage"}
ALICE_2 = {"prompt": "Alice Example asks for her lab results", "tool": "lookup"}
BOB_1 = {"prompt": "Bob Sample asks to change his address", "tool": "crm.update"}


@pytest.fixture
def vault() -> ShreddingVault:
    return ShreddingVault()


def external_verify(leaf_hash: str, proof_json: str, root: str) -> bool:
    """Standard-library-only verifier, same idea as in test_merkle.py (RFC 6962 recursion)."""
    proof = json.loads(proof_json)
    index, size, path = proof["leaf_index"], proof["tree_size"], proof["path"]

    def fold(node: bytes, idx: int, n: int, siblings: list[str]) -> bytes:
        if n == 1:
            assert not siblings
            return node
        k = 1
        while k * 2 < n:
            k *= 2
        top = bytes.fromhex(siblings[-1])
        if idx < k:
            return hashlib.sha256(b"\x01" + fold(node, idx, k, siblings[:-1]) + top).digest()
        return hashlib.sha256(b"\x01" + top + fold(node, idx - k, n - k, siblings[:-1])).digest()

    start = hashlib.sha256(b"\x00" + bytes.fromhex(leaf_hash)).digest()
    return fold(start, index, size, path).hex() == root


# ---- encryption -----------------------------------------------------------------------------


def test_payload_is_encrypted_and_leaf_hash_is_over_the_ciphertext(vault: ShreddingVault) -> None:
    w = vault.encrypt("alice", ALICE_1)
    assert w.envelope.startswith("enc:v1:" + w.key_id + ":")
    assert "Alice" not in w.envelope and "chest pain" not in w.envelope
    raw = base64.b64decode(w.envelope.split(":", 3)[3])
    assert b"Alice" not in raw
    assert w.leaf_hash == hashlib.sha256(w.envelope.encode()).hexdigest()
    assert vault.decrypt(w.envelope) == ALICE_1


def test_each_encryption_uses_a_fresh_nonce(vault: ShreddingVault) -> None:
    a = vault.encrypt("alice", ALICE_1)
    b = vault.encrypt("alice", ALICE_1)
    assert a.envelope != b.envelope and a.leaf_hash != b.leaf_hash
    assert a.key_id == b.key_id  # same subject, same key
    assert vault.decrypt(a.envelope) == vault.decrypt(b.envelope) == ALICE_1


def test_subjects_get_independent_keys_and_ids_do_not_leak_the_subject(
    vault: ShreddingVault,
) -> None:
    a = vault.encrypt("alice", ALICE_1)
    b = vault.encrypt("bob", BOB_1)
    assert a.key_id != b.key_id
    assert "alice" not in a.key_id and "alice" not in a.envelope


def test_tampered_envelope_fails_authentication(vault: ShreddingVault) -> None:
    w = vault.encrypt("alice", ALICE_1)
    head, _, blob = w.envelope.rpartition(":")
    raw = bytearray(base64.b64decode(blob))
    raw[-1] ^= 1
    with pytest.raises(ValueError, match="authentication"):
        vault.decrypt(head + ":" + base64.b64encode(bytes(raw)).decode())


def test_envelope_cannot_be_moved_under_another_subjects_key(vault: ShreddingVault) -> None:
    """The key id is associated data; relabelling an envelope with Bob's id must not decrypt."""
    a = vault.encrypt("alice", ALICE_1)
    b = vault.encrypt("bob", BOB_1)
    relabelled = a.envelope.replace(a.key_id, b.key_id, 1)
    with pytest.raises(ValueError, match="authentication"):
        vault.decrypt(relabelled)


def test_decrypt_input_validation(vault: ShreddingVault) -> None:
    with pytest.raises(ValueError, match="enc:v1"):
        vault.decrypt("plain text")
    with pytest.raises(UnknownKeyError):
        vault.decrypt("enc:v1:knever:AAAA")


# ---- the acceptance scenario ----------------------------------------------------------------


def test_erasure_destroys_the_payload_but_not_the_proofs(vault: ShreddingVault) -> None:
    """Encrypt, seal, record the root, erase Alice, show proofs and root still verify."""
    signer = HmacSha256Signer(secrets.token_bytes(32), key_id="audit-1")
    items = [
        vault.encrypt("alice", ALICE_1),
        vault.encrypt("bob", BOB_1),
        vault.encrypt("alice", ALICE_2),
    ]
    tree = seal_segment(items)
    root_before = tree.root
    proofs = [tree.prove(i) for i in range(len(items))]
    token = issue_token(
        tree, 0, segment_id="seg-1", signer=signer, timestamp=datetime(2026, 10, 27, tzinfo=UTC)
    )
    token_json = token.to_json()
    alice_key = vault.keystore.key(items[0].key_id)  # what an attacker would need
    assert vault.decrypt(items[0].envelope) == ALICE_1  # readable before erasure

    receipt = vault.erase("alice")

    # the plaintext is gone for every one of Alice's payloads
    assert receipt is not None and receipt.key_id == items[0].key_id
    for w in (items[0], items[2]):
        with pytest.raises(SubjectErasedError):
            vault.decrypt(w.envelope)
        with pytest.raises(SubjectErasedError):
            vault.keystore.key(w.key_id)
    # no other key opens it: a random key fails GCM authentication
    with pytest.raises(InvalidTag):
        crypto.decrypt(items[0].envelope, crypto.new_key())
    # the ciphertext holds no plaintext, so nothing is recoverable from the stored evidence
    for w in items:
        assert "Alice" not in w.envelope and "Bob" not in w.envelope
    assert all(bytes(k) != alice_key for k in vault.keystore._keys.values())  # type: ignore[attr-defined]

    # Merkle validation still passes: same leaf hashes, same root, same proofs
    assert seal_segment(items).root == root_before
    for i, w in enumerate(items):
        assert w.leaf_hash == hashlib.sha256(w.envelope.encode()).hexdigest()
        assert verify_inclusion(w.leaf_hash, proofs[i], root_before)
        assert external_verify(w.leaf_hash, json.dumps(proofs[i].to_dict()), root_before)
    # an audit token issued before erasure still verifies afterwards
    assert verify_token(AuditToken.from_json(token_json), signer)
    # Bob is unaffected
    assert vault.decrypt(items[1].envelope) == BOB_1


def test_erasing_one_subject_does_not_affect_another(vault: ShreddingVault) -> None:
    alice = vault.encrypt("alice", ALICE_1)
    bob = vault.encrypt("bob", BOB_1)
    carol = vault.encrypt("carol", {"prompt": "Carol asks about billing"})
    vault.erase("bob")
    assert vault.decrypt(alice.envelope) == ALICE_1
    assert vault.decrypt(carol.envelope) == {"prompt": "Carol asks about billing"}
    with pytest.raises(SubjectErasedError):
        vault.decrypt(bob.envelope)
    vault.erase("alice")
    assert vault.decrypt(carol.envelope) == {"prompt": "Carol asks about billing"}
    # new data for a subject who was never erased still works
    assert vault.decrypt(vault.encrypt("carol", {"n": 2}).envelope) == {"n": 2}


def test_stored_key_bytes_are_overwritten_on_erase(vault: ShreddingVault) -> None:
    w = vault.encrypt("alice", ALICE_1)
    store = vault.keystore
    material = store._keys[w.key_id]  # type: ignore[attr-defined]
    assert any(material)
    vault.erase("alice")
    assert not any(material)  # best effort wipe of the stored copy
    assert w.key_id not in store._keys  # type: ignore[attr-defined]


def test_erase_semantics(vault: ShreddingVault) -> None:
    assert vault.erase("nobody") is None  # nothing to destroy
    vault.encrypt("alice", ALICE_1)
    assert vault.erase("alice") is not None
    assert vault.erase("alice") is None  # already gone
    with pytest.raises(SubjectErasedError):
        vault.encrypt("alice", ALICE_2)  # refuses to start a new key for an erased subject


def test_erased_subject_list_holds_digests_not_names(vault: ShreddingVault) -> None:
    vault.encrypt("alice", ALICE_1)
    vault.erase("alice")
    store = vault.keystore
    assert "alice" not in store._erased_subjects  # type: ignore[attr-defined]
    assert hashlib.sha256(b"alice").hexdigest() in store._erased_subjects  # type: ignore[attr-defined]


def test_custom_keystore_is_pluggable() -> None:
    class CountingStore(InMemoryKeyStore):
        calls = 0

        def get_or_create(self, subject: str) -> tuple[str, bytes]:
            CountingStore.calls += 1
            return super().get_or_create(subject)

    v = ShreddingVault(CountingStore())
    v.encrypt("alice", ALICE_1)
    assert CountingStore.calls == 1


def test_seal_segment_of_nothing_is_the_empty_tree() -> None:
    assert seal_segment([]).size == 0
    assert isinstance(seal_segment([]), MerkleTree)


# ---- optional dependency --------------------------------------------------------------------


def test_missing_cryptography_gives_an_actionable_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(ImportError) as info:
        ShreddingVault()
    assert isinstance(info.value, CryptoImportError)
    message = str(info.value)
    assert "AES-256-GCM" in message and "pip install 'panoptis[crypto]'" in message
