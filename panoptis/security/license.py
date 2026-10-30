"""
CMP — Premium entitlement verification and license enforcement.

Premium features are unlocked by an *entitlement token*: a JWT signed by the
AgentWatch backend with an RS256 private key and verified here against the
matching public key. Verification is asymmetric, so the CLI only ever holds the
public key and cannot mint tokens of its own.

This module is the client-side verification primitive. It does **not** decide
*which* features are premium, nor does it replace server-side enforcement —
the most robust gate is to have the backend perform premium work only for a
valid token (see issue #405). What it provides is a single, cryptographically
checked entry point (:func:`verify_entitlement`) so that premium gating is a
signature check rather than a patchable ``if is_premium()`` boolean.

Security Model:
This system uses a bearer-token entitlement model. The entitlement token is a JWT
which acts as a bearer credential. The machine_id (passed in client headers) is used purely as
a metadata consistency and basic sanity check for concurrent use tracking/abuse detection,
rather than secure cryptographic proof-of-possession or hardware device binding. Since the
client supplies the machine ID value, anyone possessing a valid entitlement token can also
send the matching machine ID.

PyJWT is an optional dependency (the ``crypto`` extra). Verification raises a
clear :class:`LicenseError` if it is missing rather than failing open.
"""

from __future__ import annotations

import hashlib
import platform
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Tokens are signed asymmetrically; only RS256 is accepted on verify so a token
# cannot be downgraded to an algorithm the CLI could forge (e.g. HS256 using the
# public key as the shared secret).
_ALGORITHM = "RS256"


class LicenseError(Exception):
    """Base class for all entitlement-verification failures."""


class LicenseUnavailableError(LicenseError):
    """The verification backend (PyJWT) is not installed."""


class LicenseInvalidError(LicenseError):
    """The token is malformed, unsigned, or fails signature verification."""


class LicenseExpiredError(LicenseError):
    """The token signature is valid but the entitlement has expired."""


class MachineMismatchError(LicenseError):
    """The token is bound to a different machine than the current one."""


@dataclass(frozen=True)
class Entitlement:
    """A verified premium entitlement decoded from a signed token."""

    subject: str
    tier: str
    expires_at: datetime
    machine_id: str | None = None
    features: frozenset[str] = field(default_factory=frozenset)

    def grants(self, feature: str) -> bool:
        """Return whether this entitlement unlocks ``feature``."""
        return feature in self.features


def current_machine_id() -> str:
    """Return a stable, non-reversible fingerprint for this machine.

    Derived from the hostname and the primary network interface's MAC address.
    Used as client-side metadata check for license consistency. Note that this
    fingerprint is supplied by the client and does not serve as a cryptographic
    proof-of-possession or secure device binding; the backend uses it for
    concurrency tracking and abuse detection (see issue #406 acceptance criteria).
    """
    raw = f"{platform.node()}:{uuid.getnode():012x}".encode()
    return hashlib.sha256(raw).hexdigest()


def verify_entitlement(
    token: str,
    public_key: str,
    *,
    machine_id: str | None = None,
) -> Entitlement:
    """Verify a signed entitlement token and return the decoded entitlement.

    Note that the entitlement model is a bearer-token model. The machine_id
    check serves as a client-side consistency sanity check rather than a
    secure, cryptographically-enforced device binding.

    Args:
        token: The compact JWS entitlement token issued by the backend.
        public_key: PEM-encoded RSA public key used to verify the signature.
        machine_id: Expected machine fingerprint. When the token carries a
            ``machine_id`` claim it must equal this value; defaults to
            :func:`current_machine_id`.

    Returns:
        The verified :class:`Entitlement`.

    Raises:
        LicenseUnavailableError: PyJWT is not installed.
        LicenseInvalidError: Signature verification failed or token malformed.
        LicenseExpiredError: The signature is valid but the entitlement expired.
        MachineMismatchError: The token is bound to a different machine.
    """
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised via extras-less envs
        raise LicenseUnavailableError(
            "PyJWT is required to verify premium entitlements. "
            "Install it with: pip install 'panoptis[crypto]'"
        ) from exc

    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp", "tier"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise LicenseExpiredError("Premium entitlement has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise LicenseInvalidError(f"Invalid entitlement token: {exc}") from exc

    bound_machine = claims.get("machine_id")
    if bound_machine is not None:
        expected = machine_id if machine_id is not None else current_machine_id()
        if bound_machine != expected:
            raise MachineMismatchError("Entitlement is bound to a different machine than this one.")

    # PyJWT validates ``exp`` is numeric during decode; guard the remaining
    # claims so a malformed token raises LicenseInvalidError rather than an
    # uncaught TypeError, keeping the typed error contract at the boundary.
    subject, tier = claims["sub"], claims["tier"]
    features = claims.get("features", [])
    if not isinstance(subject, str) or not isinstance(tier, str):
        raise LicenseInvalidError("Entitlement 'sub' and 'tier' claims must be strings.")
    if not isinstance(features, (list, tuple, set, frozenset)) or not all(
        isinstance(f, str) for f in features
    ):
        raise LicenseInvalidError("Entitlement 'features' claim must be a list of strings.")

    return Entitlement(
        subject=subject,
        tier=tier,
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        machine_id=bound_machine,
        features=frozenset(features),
    )


def require_feature(entitlement: Entitlement | None, feature: str) -> Entitlement:
    """Return ``entitlement`` if it grants ``feature``, else raise.

    Gating premium code on this call keeps enforcement tied to a verified,
    signed entitlement: there is no plain boolean to patch out.
    """
    if entitlement is None or not entitlement.grants(feature):
        raise LicenseInvalidError(
            f"Premium feature '{feature}' requires a valid entitlement. "
            "Run 'panoptis upgrade' to unlock it."
        )
    return entitlement
