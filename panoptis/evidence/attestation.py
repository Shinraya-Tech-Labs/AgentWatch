"""Self-contained audit tokens and hash commitments for witness records.

Two parts, both standard library only:

**Audit token.** One witness leaf, the Merkle inclusion proof for it, the root of the sealed
segment it sits in, a timestamp and a signature, packaged as one JSON-serializable object an
auditor can check without access to the evidence store. The signature covers every other field.

The default signature is HMAC-SHA256, the same scheme as ``panopti/attest.py`` (an HMAC over the
signed text with ``hashlib.sha256``, compared in constant time). HMAC is symmetric: anyone who can
verify a token with the shared key can also forge one. For a third-party auditor use the optional
Ed25519 signer, which needs the ``cryptography`` package (``pip install 'panoptis[crypto]'``)
and lets the auditor hold only a public key.

**Hash commitments.** A commitment is ``SHA-256(domain || nonce || canonical_json(value))`` with a
random 256-bit nonce. It is hiding (without the nonce the digest reveals nothing useful about the
value, even for guessable values such as ``true``) and binding (opening it to a different value
needs a SHA-256 collision). These are plain hash commitments with selective disclosure, not
zero-knowledge proofs: an auditor learns exactly the fields the owner chooses to open, and the
names of all fields.

A record is committed field by field. Its digest (over the field commitments only) is the witness
leaf hash that goes into the Merkle tree. To show that a rule held (for example
``{"human_approval": true}``) the owner opens only that field. The prompt and parameters stay behind
their commitments.

The timestamp in a token is the issuer's claim, covered by the signature. It is not a trusted
timestamp: nothing here involves an external time-stamping authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from panoptis.evidence.canonical import canonical_json
from panoptis.evidence.crypto import require_cryptography
from panoptis.evidence.merkle import MerkleProof, MerkleTree, verify_inclusion

FORMAT_ID = "panoptis-audit-token/1"
COMMITMENT_DOMAIN = b"panoptis/commitment/v1\x00"
RECORD_DOMAIN = b"panoptis/record/v1\x00"
MIN_NONCE_BYTES = 16
MIN_HMAC_KEY_BYTES = 16
HMAC_ALG = "HMAC-SHA256"
ED25519_ALG = "Ed25519"


# ---------------------------------------------------------------------------------------------
# hash commitments
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Opening:
    """The private half of a commitment: the value and the nonce that blinds it."""

    value: Any
    nonce: str  # lowercase hex

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "nonce": self.nonce}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Opening:
        try:
            return cls(value=data["value"], nonce=data["nonce"])
        except (KeyError, TypeError) as exc:
            raise ValueError("opening must have value and nonce") from exc


def _commitment_digest(value: Any, nonce: bytes) -> str:
    body = canonical_json(value).encode("utf-8")
    return hashlib.sha256(COMMITMENT_DOMAIN + nonce + body).hexdigest()


def commit(value: Any, nonce: bytes | None = None) -> tuple[str, Opening]:
    """Commit to ``value``. Returns ``(digest, opening)``.

    Publish the digest, keep the opening private. ``nonce`` is random unless given (a fixed nonce
    is for tests; it must be at least 16 bytes, and reusing one across values weakens hiding).
    """
    if nonce is None:
        nonce = secrets.token_bytes(32)
    elif len(nonce) < MIN_NONCE_BYTES:
        raise ValueError(f"nonce must be at least {MIN_NONCE_BYTES} bytes")
    return _commitment_digest(value, nonce), Opening(value=value, nonce=nonce.hex())


def verify_opening(digest: str, opening: Opening) -> bool:
    """True when ``opening`` opens ``digest``."""
    try:
        nonce = bytes.fromhex(opening.nonce)
        if len(nonce) < MIN_NONCE_BYTES:
            return False
        return hmac.compare_digest(_commitment_digest(opening.value, nonce), digest)
    except (ValueError, TypeError):
        return False


def record_digest(fields: Mapping[str, str]) -> str:
    """Digest of a committed record: SHA-256 over the field names and their commitments only."""
    body = canonical_json(dict(fields)).encode("utf-8")
    return hashlib.sha256(RECORD_DOMAIN + body).hexdigest()


class CommittedRecord:
    """A record committed field by field. Holds the openings, so keep it private.

    ``fields`` (field name to commitment digest) is the public part; ``digest`` is the witness
    leaf hash to put in the Merkle tree.
    """

    def __init__(self, record: Mapping[str, Any]) -> None:
        if not record:
            raise ValueError("cannot commit an empty record")
        fields: dict[str, str] = {}
        openings: dict[str, Opening] = {}
        for name, value in record.items():
            if not isinstance(name, str):
                raise TypeError("record field names must be strings")
            fields[name], openings[name] = commit(value)
        self._fields = fields
        self._openings = openings

    @property
    def fields(self) -> dict[str, str]:
        return dict(self._fields)

    @property
    def digest(self) -> str:
        return record_digest(self._fields)

    def disclose(self, *names: str) -> dict[str, Opening]:
        """Openings for the named fields only."""
        missing = [n for n in names if n not in self._openings]
        if missing:
            raise KeyError(f"unknown fields: {', '.join(missing)}")
        return {n: self._openings[n] for n in names}


def verify_disclosure(
    fields: Mapping[str, str],
    disclosure: Mapping[str, Opening],
    required: Mapping[str, Any],
) -> bool:
    """True when every ``required`` field is opened to exactly the required value.

    Values are compared in canonical JSON form, so ``True`` does not match ``1``. Fields that are
    disclosed but not required are checked against their commitment too.
    """
    if not required:
        return False
    for name, opening in disclosure.items():
        commitment = fields.get(name)
        if commitment is None or not verify_opening(commitment, opening):
            return False
    for name, expected in required.items():
        opened = disclosure.get(name)
        if opened is None:
            return False
        try:
            if canonical_json(opened.value) != canonical_json(expected):
                return False
        except ValueError:
            return False
    return True


# ---------------------------------------------------------------------------------------------
# signatures
# ---------------------------------------------------------------------------------------------


class Signer(Protocol):
    alg: str
    key_id: str

    def sign(self, message: bytes) -> str: ...


class Verifier(Protocol):
    alg: str
    key_id: str

    def verify(self, message: bytes, signature: str) -> bool: ...


class HmacSha256Signer:
    """HMAC-SHA256 over the message, hex encoded (the ``panopti/attest.py`` scheme).

    The key is shared: this object both signs and verifies, and whoever can verify can forge.
    Generate keys with ``secrets.token_bytes(32)``; never hard-code one.
    """

    alg = HMAC_ALG

    def __init__(self, key: bytes | str, key_id: str = "hmac-1") -> None:
        raw = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        if len(raw) < MIN_HMAC_KEY_BYTES:
            raise ValueError(f"HMAC key must be at least {MIN_HMAC_KEY_BYTES} bytes")
        self._key = raw
        self.key_id = key_id

    def sign(self, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(self, message: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message), signature)


class Ed25519Signer:
    """Optional Ed25519 signer. Needs the ``cryptography`` package."""

    alg = ED25519_ALG

    def __init__(self, private_key: bytes, key_id: str = "ed25519-1") -> None:
        require_cryptography("Ed25519 signing")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self._key = Ed25519PrivateKey.from_private_bytes(private_key)
        self.key_id = key_id

    @classmethod
    def generate(cls, key_id: str = "ed25519-1") -> Ed25519Signer:
        return cls(secrets.token_bytes(32), key_id)

    def sign(self, message: bytes) -> str:
        return self._key.sign(message).hex()

    def public_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        return self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def verifier(self) -> Ed25519Verifier:
        return Ed25519Verifier(self.public_key_bytes(), self.key_id)


class Ed25519Verifier:
    """Verifies Ed25519 signatures with a public key only."""

    alg = ED25519_ALG

    def __init__(self, public_key: bytes, key_id: str = "ed25519-1") -> None:
        require_cryptography("Ed25519 verification")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        self._key = Ed25519PublicKey.from_public_bytes(public_key)
        self.key_id = key_id

    def verify(self, message: bytes, signature: str) -> bool:
        from cryptography.exceptions import InvalidSignature

        try:
            self._key.verify(bytes.fromhex(signature), message)
        except (InvalidSignature, ValueError):
            return False
        return True


# ---------------------------------------------------------------------------------------------
# audit token
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of a verification. Truthy when ``ok``; ``reason`` says why not otherwise."""

    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


@dataclass(frozen=True, slots=True)
class AuditToken:
    """Self-contained evidence that one witness leaf is in a sealed segment.

    ``fields`` is present when the leaf is a committed record (see ``CommittedRecord``); then
    ``leaf_hash`` must equal ``record_digest(fields)``.
    """

    segment_id: str
    segment_root: str
    leaf_hash: str
    proof: MerkleProof
    timestamp: str
    alg: str
    key_id: str
    signature: str
    fields: Mapping[str, str] | None = None
    version: str = FORMAT_ID

    def _body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "version": self.version,
            "segment_id": self.segment_id,
            "segment_root": self.segment_root,
            "leaf_hash": self.leaf_hash,
            "proof": self.proof.to_dict(),
            "timestamp": self.timestamp,
            "alg": self.alg,
            "key_id": self.key_id,
        }
        if self.fields is not None:
            body["fields"] = dict(self.fields)
        return body

    def signing_input(self) -> bytes:
        """The exact bytes that are signed: version line plus canonical JSON of every field."""
        return f"{self.version}\n{canonical_json(self._body())}".encode()

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "signature": self.signature}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuditToken:
        if not isinstance(data, Mapping):
            raise ValueError("malformed audit token: expected an object")
        try:
            fields = data.get("fields")
            token = cls(
                segment_id=data["segment_id"],
                segment_root=data["segment_root"],
                leaf_hash=data["leaf_hash"],
                proof=MerkleProof.from_dict(data["proof"]),
                timestamp=data["timestamp"],
                alg=data["alg"],
                key_id=data["key_id"],
                signature=data["signature"],
                fields=None if fields is None else dict(fields),
                version=data["version"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed audit token: {exc!r}") from exc
        return token

    @classmethod
    def from_json(cls, text: str) -> AuditToken:
        return cls.from_dict(json.loads(text))


def issue_token(
    tree: MerkleTree,
    index: int,
    *,
    segment_id: str,
    signer: Signer,
    timestamp: datetime | None = None,
    record: CommittedRecord | None = None,
) -> AuditToken:
    """Issue a signed audit token for leaf ``index`` of a sealed ``tree``.

    ``timestamp`` is when the segment was sealed (default: now, UTC). It is the issuer's claim.
    Pass ``record`` to carry the public field commitments in the token; its digest must be the
    leaf hash at ``index``.
    """
    leaf = tree.leaf_hash(index)
    if record is not None and record.digest != leaf:
        raise ValueError("record digest does not match the leaf hash at this index")
    when = timestamp if timestamp is not None else datetime.now(UTC)
    if when.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    unsigned = AuditToken(
        segment_id=segment_id,
        segment_root=tree.root,
        leaf_hash=leaf,
        proof=tree.prove(index),
        timestamp=when.astimezone(UTC).isoformat(),
        alg=signer.alg,
        key_id=signer.key_id,
        signature="",
        fields=None if record is None else record.fields,
    )
    signature = signer.sign(unsigned.signing_input())
    return AuditToken(
        segment_id=unsigned.segment_id,
        segment_root=unsigned.segment_root,
        leaf_hash=unsigned.leaf_hash,
        proof=unsigned.proof,
        timestamp=unsigned.timestamp,
        alg=unsigned.alg,
        key_id=unsigned.key_id,
        signature=signature,
        fields=unsigned.fields,
    )


def verify_token(
    token: AuditToken,
    verifier: Verifier,
    *,
    expected_root: str | None = None,
) -> VerificationResult:
    """Check a token: format, signature, Merkle proof, timestamp, and field commitments.

    The verification method comes from ``verifier``, never from the token's own ``alg`` field, and
    the two must agree. Pass ``expected_root`` when the auditor already holds the segment root
    from another channel; otherwise the root is only as trustworthy as the signature on it.
    """
    if token.version != FORMAT_ID:
        return VerificationResult(False, f"unsupported token version {token.version!r}")
    if token.alg != verifier.alg:
        return VerificationResult(False, "token algorithm does not match the verifier")
    if token.key_id != verifier.key_id:
        return VerificationResult(False, "token key id does not match the verifier")
    try:
        signature_ok = verifier.verify(token.signing_input(), token.signature)
    except (ValueError, TypeError):
        signature_ok = False
    if not signature_ok:
        return VerificationResult(False, "signature does not verify")
    try:
        _parse_timestamp(token.timestamp)
    except (ValueError, TypeError):
        return VerificationResult(False, "timestamp is not an ISO-8601 time with an offset")
    if expected_root is not None and token.segment_root != expected_root:
        return VerificationResult(False, "segment root differs from the expected root")
    if not verify_inclusion(token.leaf_hash, token.proof, token.segment_root):
        return VerificationResult(False, "inclusion proof does not verify against the root")
    if token.fields is not None and record_digest(token.fields) != token.leaf_hash:
        return VerificationResult(False, "field commitments do not match the leaf hash")
    return VerificationResult(True)


def verify_rule(
    token: AuditToken,
    verifier: Verifier,
    disclosure: Mapping[str, Opening],
    required: Mapping[str, Any],
    *,
    expected_root: str | None = None,
) -> VerificationResult:
    """Verify the token, then that the opened fields satisfy ``required``.

    Example: ``required={"human_approval": True}`` with only that field opened. Other fields of
    the record are not revealed and are not needed.
    """
    result = verify_token(token, verifier, expected_root=expected_root)
    if not result:
        return result
    if token.fields is None:
        return VerificationResult(False, "token carries no field commitments")
    if not verify_disclosure(token.fields, disclosure, required):
        return VerificationResult(False, "disclosed fields do not satisfy the required rule")
    return VerificationResult(True)
