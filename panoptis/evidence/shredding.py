"""Crypto-shredding for witness payloads: per-subject keys, leaf hashes over ciphertext.

Each subject (a person or entity with a right to erasure) gets its own random 256-bit key. A
payload is encrypted with AES-256-GCM under that key and stored as the envelope string
``enc:v1:<key_id>:<base64(nonce | ciphertext)>`` (the existing format of
:mod:`panoptis.evidence.crypto`; the key id is bound as associated data). The witness leaf hash
is the SHA-256 of that *envelope*, so it is computed over ciphertext, never plaintext.

Erasing a subject destroys its key. The envelope, its leaf hash, the Merkle root of every segment
that contains it, and every inclusion proof stay exactly as they were, so segment verification
still passes. The payload can no longer be decrypted.

Dependency note: Merkle trees, audit tokens, commitments and HMAC signing are standard library
only. AES-256-GCM is not in the standard library, so this module needs the optional
``cryptography`` package (``pip install 'panoptis[crypto]'``). It is imported lazily, when a
:class:`ShreddingVault` is created, and a missing package raises
:class:`~panoptis.evidence.crypto.CryptoImportError` (an ``ImportError``) with the install
command. No cipher is implemented here.

Limits: :class:`InMemoryKeyStore` keeps keys in process memory and is a reference implementation.
For production plug in a store backed by a KMS or HSM with the same interface. Python cannot
guarantee that every copy of a key is wiped from memory, and plaintext that left the vault before
erasure (logs, exports, backups, caches) is out of reach.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from panoptis.evidence import crypto
from panoptis.evidence.canonical import canonical_json, sha256_hex
from panoptis.evidence.merkle import MerkleTree


class SubjectErasedError(LookupError):
    """The key for this subject or envelope was destroyed; the payload is unrecoverable."""


class UnknownKeyError(LookupError):
    """No key with this id was ever issued by this key store."""


class KeyStore(Protocol):
    """What the vault needs from a key store. Back it with a KMS or HSM in production."""

    def get_or_create(self, subject: str) -> tuple[str, bytes]:
        """``(key_id, key)`` for the subject. Raises ``SubjectErasedError`` after erasure."""
        ...

    def key(self, key_id: str) -> bytes:
        """The key for ``key_id``. Raises ``SubjectErasedError`` or ``UnknownKeyError``."""
        ...

    def erase(self, subject: str) -> str | None:
        """Destroy the subject's key. Returns its key id, or ``None`` if there was none."""
        ...


class InMemoryKeyStore:
    """Process-memory key store: a reference implementation, not a production key manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subject_key_id: dict[str, str] = {}
        self._keys: dict[str, bytearray] = {}
        self._erased_key_ids: set[str] = set()
        self._erased_subjects: set[str] = set()  # SHA-256 of the subject id, not the id

    @staticmethod
    def _subject_digest(subject: str) -> str:
        return hashlib.sha256(subject.encode("utf-8")).hexdigest()

    def get_or_create(self, subject: str) -> tuple[str, bytes]:
        with self._lock:
            if self._subject_digest(subject) in self._erased_subjects:
                raise SubjectErasedError("subject was erased; refusing to create a new key")
            key_id = self._subject_key_id.get(subject)
            if key_id is None:
                key_id = "k" + secrets.token_hex(8)  # random, carries no subject information
                self._subject_key_id[subject] = key_id
                self._keys[key_id] = bytearray(crypto.new_key())
            return key_id, bytes(self._keys[key_id])

    def key(self, key_id: str) -> bytes:
        with self._lock:
            if key_id in self._erased_key_ids:
                raise SubjectErasedError("the key for this payload was destroyed")
            material = self._keys.get(key_id)
            if material is None:
                raise UnknownKeyError(f"unknown key id {key_id!r}")
            return bytes(material)

    def erase(self, subject: str) -> str | None:
        with self._lock:
            key_id = self._subject_key_id.pop(subject, None)
            self._erased_subjects.add(self._subject_digest(subject))
            if key_id is None:
                return None
            material = self._keys.pop(key_id)
            for i in range(len(material)):
                material[i] = 0  # best effort: overwrite the stored copy
            self._erased_key_ids.add(key_id)
            return key_id


@dataclass(frozen=True, slots=True)
class EncryptedWitness:
    """An encrypted payload and the leaf hash to put in a Merkle tree.

    ``leaf_hash`` is the SHA-256 of ``envelope`` (ciphertext), so it does not change when the
    subject's key is destroyed.
    """

    envelope: str
    leaf_hash: str
    key_id: str


@dataclass(frozen=True, slots=True)
class ErasureReceipt:
    """Record that a subject's key was destroyed. Carries the key id, not the subject."""

    key_id: str
    erased_at: str


class ShreddingVault:
    """Encrypts payloads per subject, and destroys a subject's key on erasure."""

    def __init__(self, keystore: KeyStore | None = None) -> None:
        crypto.require_cryptography("AES-256-GCM crypto-shredding")
        self.keystore: KeyStore = keystore if keystore is not None else InMemoryKeyStore()

    def encrypt(self, subject: str, payload: Any) -> EncryptedWitness:
        """Encrypt ``payload`` (any JSON-serializable value) under the subject's key."""
        key_id, key = self.keystore.get_or_create(subject)
        envelope = crypto.encrypt(canonical_json(payload), key, key_id)
        return EncryptedWitness(envelope=envelope, leaf_hash=sha256_hex(envelope), key_id=key_id)

    def decrypt(self, envelope: str) -> Any:
        """Decrypt an envelope. Raises ``SubjectErasedError`` once its key is destroyed."""
        if not crypto.is_encrypted(envelope):
            raise ValueError("not an enc:v1 envelope")
        from cryptography.exceptions import InvalidTag

        key = self.keystore.key(crypto.key_id_of(envelope))
        try:
            return json.loads(crypto.decrypt(envelope, key))
        except InvalidTag as exc:
            raise ValueError("envelope failed authentication (wrong key or tampered)") from exc

    def erase(self, subject: str) -> ErasureReceipt | None:
        """GDPR erasure: destroy the subject's key. Envelopes, hashes and proofs are untouched."""
        key_id = self.keystore.erase(subject)
        if key_id is None:
            return None
        return ErasureReceipt(key_id=key_id, erased_at=datetime.now(UTC).isoformat())


def seal_segment(witnesses: Sequence[EncryptedWitness]) -> MerkleTree:
    """Merkle tree over the leaf hashes (ciphertext hashes) of ``witnesses``, in order."""
    return MerkleTree([w.leaf_hash for w in witnesses])
