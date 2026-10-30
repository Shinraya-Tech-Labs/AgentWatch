"""Tests for server-side premium entitlement enforcement (issue #462)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from panoptis.api import entitlement


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
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


def _token(private_pem: str, **claims) -> str:
    payload = {
        "sub": "user@example.com",
        "tier": "enterprise",
        "exp": datetime.now(UTC) + timedelta(days=30),
        "features": ["compliance"],
        **claims,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def _configure(monkeypatch, public_key: str | None, *, prod: bool = False):
    """Set the enforcement state via monkeypatch so it is restored per test."""
    monkeypatch.setattr(entitlement, "_LICENSE_PUBLIC_KEY", public_key)
    monkeypatch.setattr(entitlement, "_IS_PROD", prod)
    return entitlement


def test_disabled_without_key(monkeypatch):
    ent = _configure(monkeypatch, None)
    assert ent.entitlement_enforcement_enabled() is False
    assert ent.authenticate_entitlement(x_entitlement_token=None) is None
    assert ent.require_entitlement("compliance")(entitlement=None) is None


def test_production_without_key_fails_closed(monkeypatch):
    ent = _configure(monkeypatch, None, prod=True)
    with pytest.raises(HTTPException) as exc:
        ent.require_entitlement("compliance")(entitlement=None)
    assert exc.value.status_code == 500


def test_valid_token_grants_feature(monkeypatch, keypair):
    private_pem, public_pem = keypair
    ent = _configure(monkeypatch, public_pem)
    granted = ent.authenticate_entitlement(x_entitlement_token=_token(private_pem))
    assert ent.require_entitlement("compliance")(entitlement=granted) is granted


def test_missing_token_rejected(monkeypatch, keypair):
    _, public_pem = keypair
    ent = _configure(monkeypatch, public_pem)
    with pytest.raises(HTTPException) as exc:
        ent.authenticate_entitlement(x_entitlement_token=None)
    assert exc.value.status_code == 402


def test_invalid_token_rejected(monkeypatch, keypair):
    _, public_pem = keypair
    ent = _configure(monkeypatch, public_pem)
    with pytest.raises(HTTPException) as exc:
        ent.authenticate_entitlement(x_entitlement_token="bad.token.value")  # noqa: S106
    assert exc.value.status_code == 402


def test_feature_not_granted_rejected(monkeypatch, keypair):
    private_pem, public_pem = keypair
    ent = _configure(monkeypatch, public_pem)
    granted = ent.authenticate_entitlement(
        x_entitlement_token=_token(private_pem, features=["sso"])
    )
    with pytest.raises(HTTPException) as exc:
        ent.require_entitlement("compliance")(entitlement=granted)
    assert exc.value.status_code == 402


def test_machine_bound_token_checked_against_header(monkeypatch, keypair):
    private_pem, public_pem = keypair
    ent = _configure(monkeypatch, public_pem)
    bound = _token(private_pem, machine_id="client-fp")
    assert ent.authenticate_entitlement(x_entitlement_token=bound, x_machine_id="client-fp")
    with pytest.raises(HTTPException) as exc:
        ent.authenticate_entitlement(x_entitlement_token=bound, x_machine_id="other")
    assert exc.value.status_code == 402


def test_eu_ai_act_report_gated(monkeypatch, keypair):
    private_pem, public_pem = keypair
    from fastapi.testclient import TestClient

    from panoptis.api.server import app

    monkeypatch.setattr("panoptis.api.server._API_KEY", None)
    monkeypatch.setattr("panoptis.api.server._IS_PROD", False)
    monkeypatch.setattr(entitlement, "_LICENSE_PUBLIC_KEY", public_pem)
    client = TestClient(app)

    assert client.get("/api/v1/governance/eu-ai-act-report").status_code == 402
    ok = client.get(
        "/api/v1/governance/eu-ai-act-report",
        headers={"X-Entitlement-Token": _token(private_pem)},
    )
    assert ok.status_code == 200
    assert ok.json()["article"] == "EU AI Act Article 15"


def test_bearer_token_model_spoofed_machine_id_succeeds(monkeypatch, keypair):
    """
    Verify that an entitlement token bound to a specific machine can be verified
    with the matching X-Machine-Id header supplied by the client (since it's a bearer
    token model, not cryptographically bound to the device).
    """
    private_pem, public_pem = keypair
    ent = _configure(monkeypatch, public_pem)
    bound_token = _token(private_pem, machine_id="target-machine")

    # An attacker spoofing the machine ID header with the correct value
    # gets verified successfully, confirming we treat X-Machine-Id as a metadata check
    # rather than a cryptographic device binding or proof-of-possession.
    verified = ent.authenticate_entitlement(
        x_entitlement_token=bound_token, x_machine_id="target-machine"
    )
    assert verified is not None
    assert verified.machine_id == "target-machine"


def test_public_key_loading_robustness(monkeypatch, tmp_path):
    """
    Verify that _public_key gracefully handles:
    - missing files
    - permission errors
    - invalid encoding
    without raising exceptions (returning None) and producing clear log messages.
    """
    from pathlib import Path

    # 1. Clear env to test files
    monkeypatch.delenv("AGENTWATCH_LICENSE_PUBLIC_KEY", raising=False)

    # 3. File not found
    monkeypatch.setenv("AGENTWATCH_LICENSE_PUBLIC_KEY_FILE", str(tmp_path / "non_existent_key.pem"))
    assert entitlement._public_key() is None

    # 4. Permission error
    key_file = tmp_path / "key.pem"
    key_file.touch()
    monkeypatch.setenv("AGENTWATCH_LICENSE_PUBLIC_KEY_FILE", str(key_file))

    def mock_read_text_permission_error(*args, **kwargs):
        raise PermissionError("Access Denied")

    monkeypatch.setattr(Path, "read_text", mock_read_text_permission_error)
    assert entitlement._public_key() is None

    # 5. UnicodeDecodeError
    def mock_read_text_decode_error(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(Path, "read_text", mock_read_text_decode_error)
    assert entitlement._public_key() is None

    # 6. Generic OSError
    def mock_read_text_os_error(*args, **kwargs):
        raise OSError("Disk failure")

    monkeypatch.setattr(Path, "read_text", mock_read_text_os_error)
    assert entitlement._public_key() is None
