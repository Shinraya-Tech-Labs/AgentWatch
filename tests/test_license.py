"""CMP — premium entitlement verification tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from panoptis.security.license import (
    Entitlement,
    LicenseExpiredError,
    LicenseInvalidError,
    MachineMismatchError,
    current_machine_id,
    require_feature,
    verify_entitlement,
)


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """An RSA keypair as (private_pem, public_pem) for signing test tokens."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _make_token(private_pem: str, **claims) -> str:
    payload = {
        "sub": "user@example.com",
        "tier": "enterprise",
        "exp": datetime.now(UTC) + timedelta(days=30),
        **claims,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_valid_token_verifies(keypair):
    private_pem, public_pem = keypair
    token = _make_token(private_pem, features=["redteam", "compliance"])

    ent = verify_entitlement(token, public_pem)

    assert isinstance(ent, Entitlement)
    assert ent.subject == "user@example.com"
    assert ent.tier == "enterprise"
    assert ent.grants("redteam")
    assert not ent.grants("sso")


def test_expired_token_rejected(keypair):
    private_pem, public_pem = keypair
    token = _make_token(private_pem, exp=datetime.now(UTC) - timedelta(seconds=1))

    with pytest.raises(LicenseExpiredError):
        verify_entitlement(token, public_pem)


def test_tampered_signature_rejected(keypair):
    private_pem, public_pem = keypair
    header, payload, signature = _make_token(private_pem).split(".")
    # Corrupt a character mid-signature so the bytes genuinely change
    # (flipping a trailing char can be absorbed by base64url padding bits).
    i = len(signature) // 2
    flipped = signature[:i] + ("A" if signature[i] != "A" else "B") + signature[i + 1 :]
    tampered = f"{header}.{payload}.{flipped}"

    with pytest.raises(LicenseInvalidError):
        verify_entitlement(tampered, public_pem)


def test_token_signed_by_other_key_rejected(keypair):
    _, public_pem = keypair
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker_pem = attacker.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    token = _make_token(attacker_pem)

    with pytest.raises(LicenseInvalidError):
        verify_entitlement(token, public_pem)


def test_machine_binding_enforced(keypair):
    private_pem, public_pem = keypair
    token = _make_token(private_pem, machine_id="machine-A")

    ent = verify_entitlement(token, public_pem, machine_id="machine-A")
    assert ent.machine_id == "machine-A"

    with pytest.raises(MachineMismatchError):
        verify_entitlement(token, public_pem, machine_id="machine-B")


def test_unbound_token_skips_machine_check(keypair):
    private_pem, public_pem = keypair
    token = _make_token(private_pem)  # no machine_id claim

    ent = verify_entitlement(token, public_pem, machine_id="anything")
    assert ent.machine_id is None


def test_missing_required_claim_rejected(keypair):
    private_pem, public_pem = keypair
    # Sign a payload without the mandatory 'tier' claim.
    token = jwt.encode(
        {"sub": "x", "exp": datetime.now(UTC) + timedelta(days=1)},
        private_pem,
        algorithm="RS256",
    )

    with pytest.raises(LicenseInvalidError):
        verify_entitlement(token, public_pem)


def test_non_numeric_exp_rejected(keypair):
    private_pem, public_pem = keypair
    token = jwt.encode(
        {"sub": "x", "tier": "pro", "exp": "tomorrow"}, private_pem, algorithm="RS256"
    )
    with pytest.raises(LicenseInvalidError):
        verify_entitlement(token, public_pem)


def test_non_string_features_rejected(keypair):
    private_pem, public_pem = keypair
    token = _make_token(private_pem, features=123)
    with pytest.raises(LicenseInvalidError):
        verify_entitlement(token, public_pem)


def test_non_string_tier_rejected(keypair):
    private_pem, public_pem = keypair
    token = _make_token(private_pem, tier=42)
    with pytest.raises(LicenseInvalidError):
        verify_entitlement(token, public_pem)


def test_current_machine_id_is_stable_and_hashed():
    first = current_machine_id()
    assert first == current_machine_id()
    assert len(first) == 64  # sha256 hex digest
    assert first.isalnum()


def test_require_feature_gate(keypair):
    private_pem, public_pem = keypair
    ent = verify_entitlement(_make_token(private_pem, features=["sso"]), public_pem)

    assert require_feature(ent, "sso") is ent
    with pytest.raises(LicenseInvalidError):
        require_feature(ent, "redteam")
    with pytest.raises(LicenseInvalidError):
        require_feature(None, "sso")
