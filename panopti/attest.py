"""Panopti Cryptographic Policy Attestation & Verifiable Audit Seals.

Generates tamper-evident cryptographic seals for agent execution traces, proving
formal invariant compliance and absence of runaway loops.
Pure standard library (Ponytail: Ultra).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from panopti.attest_keys import AttestationKeyError, Keyring, keyring_from_env


def _canonical_json(data: Any) -> str:
    """Serialize object to canonical, deterministic JSON."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


SEAL_VERSION = "2"


@dataclass(slots=True)
class PanoptiAttestationSeal:
    """Cryptographic attestation certificate proving verified execution.

    Version 2 seals name the key that signed them (``key_id``) and sign every field,
    including the version and key id, as canonical JSON.
    """

    seal_id: str
    merkle_root: str
    policy_verdict: str
    confidence_floor: float
    loop_status: str
    tool_count: int
    timestamp_iso: str
    signature: str
    key_id: str | None = None
    version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def signed_fields(self) -> dict[str, Any]:
        d = self.to_dict()
        d.pop("signature")
        d["confidence_floor"] = f"{float(d['confidence_floor']):.4f}"
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PanoptiAttestationSeal:
        return cls(
            seal_id=data["seal_id"],
            merkle_root=data["merkle_root"],
            policy_verdict=data["policy_verdict"],
            confidence_floor=float(data["confidence_floor"]),
            loop_status=data["loop_status"],
            tool_count=int(data["tool_count"]),
            timestamp_iso=data["timestamp_iso"],
            signature=data["signature"],
            key_id=data.get("key_id"),
            version=str(data.get("version", "1")),
        )

    @classmethod
    def from_json(cls, json_str: str) -> PanoptiAttestationSeal:
        return cls.from_dict(json.loads(json_str))


def _keyring_for(keyring: Keyring | None, secret_key: str | bytes | None) -> Keyring:
    """The keyring to use: explicit, else a one-key ring from ``secret_key``, else the env."""
    if keyring is not None:
        return keyring
    if secret_key is not None:
        ring = Keyring()
        key = secret_key.encode() if isinstance(secret_key, str) else secret_key
        ring.add("explicit", key)  # validated: strength, not a known public default
        return ring
    return keyring_from_env()


class AttestationEngine:
    """Issues and cryptographically validates Panopti attestation seals."""

    @staticmethod
    def _v1_payload(seal: PanoptiAttestationSeal) -> str:
        return (
            f"{seal.seal_id}|{seal.merkle_root}|{seal.policy_verdict}|"
            f"{seal.confidence_floor:.4f}|{seal.loop_status}|{seal.tool_count}|{seal.timestamp_iso}"
        )

    @staticmethod
    def _sign(key: bytes, seal: PanoptiAttestationSeal) -> str:
        return hmac.new(
            key, _canonical_json(seal.signed_fields()).encode(), hashlib.sha256
        ).hexdigest()

    @classmethod
    def create_seal(
        cls,
        merkle_root: str,
        tool_count: int,
        confidence_floor: float = 1.0,
        is_compliant: bool = True,
        loop_detected: bool = False,
        secret_key: str | bytes | None = None,
        *,
        keyring: Keyring | None = None,
    ) -> PanoptiAttestationSeal:
        """Issue a signed (version 2) attestation seal.

        The key comes from ``keyring``, else ``secret_key`` (checked: at least 32 bytes,
        not a known public key), else ``AGENTWATCH_ATTEST_KEYS``. There is no default key:
        with none configured this raises :class:`AttestationKeyError`.
        """
        kid, key = _keyring_for(keyring, secret_key).signing_key()
        seal = PanoptiAttestationSeal(
            seal_id=f"seal_{uuid.uuid4().hex[:16]}",
            merkle_root=merkle_root,
            policy_verdict="COMPLIANT" if is_compliant else "VIOLATION",
            confidence_floor=confidence_floor,
            loop_status="LOOP_DETECTED" if loop_detected else "CLEAN",
            tool_count=tool_count,
            timestamp_iso=datetime.now(UTC).isoformat(),
            signature="",
            key_id=kid,
            version=SEAL_VERSION,
        )
        seal.signature = cls._sign(key, seal)
        return seal

    @classmethod
    def verify_seal(
        cls,
        seal: PanoptiAttestationSeal,
        secret_key: str | bytes | None = None,
        *,
        keyring: Keyring | None = None,
        allow_legacy: bool = False,
    ) -> bool:
        """True only if the seal was signed by a known, unrevoked key and is unmodified.

        Fails closed: an unknown or revoked key id, a missing key or any modified field
        returns False. Version 1 seals (no key id) verify only with ``allow_legacy=True``
        and an explicit, non-public ``secret_key``.
        """
        try:
            ring = _keyring_for(keyring, secret_key)
        except AttestationKeyError:
            return False
        if seal.version == "1" or seal.key_id is None:
            if not allow_legacy or secret_key is None:
                return False
            key = ring.verification_key("explicit")
            if key is None:
                return False
            expected = hmac.new(key, cls._v1_payload(seal).encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(seal.signature, expected)
        if seal.version != SEAL_VERSION:
            return False
        kid = seal.key_id if keyring is not None or secret_key is None else "explicit"
        key = ring.verification_key(kid)
        if key is None:
            return False
        return hmac.compare_digest(seal.signature, cls._sign(key, seal))
