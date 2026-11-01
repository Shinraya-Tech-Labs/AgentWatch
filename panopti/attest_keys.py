"""Signing keys for attestation seals: a keyring with ids, rotation and revocation.

Configuration (environment)::

    AGENTWATCH_ATTEST_KEYS        kid=<base64 or hex key>,kid2=...   (one per line also works)
    AGENTWATCH_ATTEST_ACTIVE_KID  the kid new seals are signed with (default: the last listed)
    AGENTWATCH_ATTEST_REVOKED     kids that must no longer verify (comma separated)
    AGENTWATCH_ATTEST_DEV_KEY=1   development only: an ephemeral random key for this process

There is no built-in key. A key must be at least 32 bytes and must not be a known public
default; the dev key is refused in production and cloud mode. Key material is never
logged or included in exceptions.

Generate a key::

    python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
"""

from __future__ import annotations

import base64
import binascii
import hmac
import logging
import os
import re
import secrets
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MIN_KEY_BYTES = 32
# Keys that shipped in source code; anyone can forge seals with them.
KNOWN_PUBLIC_KEYS = frozenset({b"panopti-seal-default-key", b"panopti-demo-hmac-key"})
_KID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class AttestationKeyError(ValueError):
    """The signing-key configuration is missing or unsafe. The message never contains key material."""


def decode_key(text: str) -> bytes:
    """A key given as base64 (preferred) or hex."""
    t = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]+", t) and len(t) % 2 == 0 and len(t) >= 2 * MIN_KEY_BYTES:
        return bytes.fromhex(t)
    try:
        return base64.b64decode(t, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttestationKeyError("attestation key is neither valid base64 nor hex") from exc


def check_key(kid: str, key: bytes) -> None:
    if not _KID.match(kid):
        raise AttestationKeyError(f"invalid key id {kid!r}")
    if any(hmac.compare_digest(key, k) for k in KNOWN_PUBLIC_KEYS):
        raise AttestationKeyError(
            f"key {kid!r} is a known public default key; anyone could forge seals with it"
        )
    if len(key) < MIN_KEY_BYTES:
        raise AttestationKeyError(
            f"key {kid!r} is {len(key)} bytes; at least {MIN_KEY_BYTES} are required"
        )


def is_production() -> bool:
    env = (os.environ.get("AGENTWATCH_ENV") or "").strip().lower()
    cloud = (os.environ.get("AGENTWATCH_CLOUD_MODE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return env in ("production", "prod") or cloud


@dataclass
class Keyring:
    keys: dict[str, bytes] = field(default_factory=dict)
    active_kid: str | None = None
    revoked: frozenset[str] = frozenset()
    ephemeral: bool = False  # the development-only random key

    def add(self, kid: str, key: bytes, *, activate: bool = True) -> None:
        check_key(kid, key)
        self.keys[kid] = key
        if activate or self.active_kid is None:
            self.active_kid = kid

    def signing_key(self) -> tuple[str, bytes]:
        if not self.active_kid or self.active_kid not in self.keys:
            raise AttestationKeyError("no active attestation signing key is configured")
        if self.active_kid in self.revoked:
            raise AttestationKeyError(
                f"the active key {self.active_kid!r} is revoked; activate another key"
            )
        return self.active_kid, self.keys[self.active_kid]

    def verification_key(self, kid: str) -> bytes | None:
        """The key for ``kid``, or None if it is unknown or revoked (verification then fails)."""
        if kid in self.revoked:
            return None
        return self.keys.get(kid)

    def describe(self) -> dict[str, object]:
        """Safe summary for diagnostics (no key material)."""
        return {
            "kids": sorted(self.keys),
            "active_kid": self.active_kid,
            "revoked": sorted(self.revoked),
            "ephemeral": self.ephemeral,
        }


def parse_keys(raw: str | None) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if not raw:
        return out
    for part in re.split(r"[,\n]", raw):
        part = part.strip()
        if not part:
            continue
        kid, sep, value = part.partition("=")
        kid = kid.strip()
        if not sep or not value.strip():
            raise AttestationKeyError(
                f"AGENTWATCH_ATTEST_KEYS: entry for {kid!r} must be kid=<key>"
            )
        if kid in out:
            raise AttestationKeyError(f"AGENTWATCH_ATTEST_KEYS: key id {kid!r} is listed twice")
        key = decode_key(value)
        check_key(kid, key)
        out[kid] = key
    return out


def keyring_from_env(environ: dict[str, str] | None = None) -> Keyring:
    """The configured keyring. Raises :class:`AttestationKeyError` if it is missing or unsafe."""
    env = os.environ if environ is None else environ
    keys = parse_keys(env.get("AGENTWATCH_ATTEST_KEYS"))
    revoked = frozenset(
        k.strip() for k in (env.get("AGENTWATCH_ATTEST_REVOKED") or "").split(",") if k.strip()
    )
    ring = Keyring(keys=keys, revoked=revoked)
    if keys:
        active = (env.get("AGENTWATCH_ATTEST_ACTIVE_KID") or "").strip() or list(keys)[-1]
        if active not in keys:
            raise AttestationKeyError(
                f"AGENTWATCH_ATTEST_ACTIVE_KID {active!r} is not in AGENTWATCH_ATTEST_KEYS"
            )
        ring.active_kid = active
        return ring
    if (env.get("AGENTWATCH_ATTEST_DEV_KEY") or "").strip().lower() in ("1", "true", "yes", "on"):
        prod = (env.get("AGENTWATCH_ENV") or "").strip().lower() in ("production", "prod") or (
            env.get("AGENTWATCH_CLOUD_MODE") or ""
        ).strip().lower() in ("1", "true", "yes", "on")
        if prod:
            raise AttestationKeyError(
                "AGENTWATCH_ATTEST_DEV_KEY is development-only and is refused in production/cloud mode"
            )
        logger.warning(
            "attestation: using an ephemeral development key; seals will not verify in another process"
        )
        ring.keys["dev-ephemeral"] = secrets.token_bytes(MIN_KEY_BYTES)
        ring.active_kid = "dev-ephemeral"
        ring.ephemeral = True
        return ring
    raise AttestationKeyError(
        "no attestation signing key configured: set AGENTWATCH_ATTEST_KEYS (see docs/ATTESTATION_KEYS.md); "
        "for local development only, AGENTWATCH_ATTEST_DEV_KEY=1"
    )
