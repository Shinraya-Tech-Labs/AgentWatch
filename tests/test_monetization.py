"""CMP — CLI-to-Web monetization: checkout handoff + entitlement store + upgrade CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from typer.testing import CliRunner

from panoptis.cli.main import app
from panoptis.security.checkout import (
    DEFAULT_CHECKOUT_URL,
    SESSION_TTL,
    checkout_url,
    new_session,
)
from panoptis.security.entitlement_store import (
    clear_entitlement,
    load_entitlement,
    load_entitlement_token,
    store_entitlement_token,
)

runner = CliRunner()


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
        **claims,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the entitlement store at an isolated temp home."""
    monkeypatch.setenv("AGENTWATCH_HOME", str(tmp_path))
    return tmp_path


# ── checkout handoff ─────────────────────────────────────────────


def test_new_session_is_unique_and_short_lived():
    s1, s2 = new_session(), new_session()
    assert s1.token != s2.token
    assert len(s1.token) >= 32
    assert not s1.is_expired()
    # Lifetime tracks the configured TTL (small tolerance for clock skew).
    remaining = s1.expires_at - datetime.now(UTC)
    assert SESSION_TTL - timedelta(seconds=2) <= remaining <= SESSION_TTL
    assert s1.is_expired(now=datetime.now(UTC) + timedelta(hours=1))


def test_checkout_url_carries_session_token():
    session = new_session()
    url = checkout_url(session)
    assert url.startswith(DEFAULT_CHECKOUT_URL + "?")
    assert f"session={session.token}" in url


def test_checkout_url_respects_base_override():
    session = new_session()
    url = checkout_url(session, base="https://corp.internal/pay")
    assert url.startswith("https://corp.internal/pay?")


# ── entitlement store ────────────────────────────────────────────


def test_store_and_load_roundtrip(home, keypair):
    private_pem, public_pem = keypair
    token = _token(private_pem)

    path = store_entitlement_token(token)
    assert path.exists()
    assert load_entitlement_token() == token

    ent = load_entitlement(public_pem)
    assert ent is not None
    assert ent.tier == "enterprise"


def test_load_absent_entitlement_is_none(home, keypair):
    _, public_pem = keypair
    assert load_entitlement_token() is None
    assert load_entitlement(public_pem) is None


def test_tampered_store_fails_verification(home, keypair):
    private_pem, public_pem = keypair
    store_entitlement_token(_token(private_pem))
    # Hand-edit the stored token so the signature no longer matches.
    cfg = next(home.glob("config.toml"))
    cfg.write_text('[premium]\nentitlement_token = "not.a.valid.token"\n', encoding="utf-8")
    assert load_entitlement(public_pem) is None


def test_clear_entitlement(home, keypair):
    private_pem, _ = keypair
    store_entitlement_token(_token(private_pem))
    clear_entitlement()
    assert load_entitlement_token() is None


# ── upgrade CLI ──────────────────────────────────────────────────


def test_upgrade_dry_run_prints_url():
    result = runner.invoke(app, ["upgrade", "--dry-run"])
    assert result.exit_code == 0
    assert DEFAULT_CHECKOUT_URL in result.stdout
    assert "session=" in result.stdout


def test_upgrade_status_free_tier(home):
    result = runner.invoke(app, ["upgrade", "--status"])
    assert result.exit_code == 0
    assert "Free" in result.stdout


def test_upgrade_activate_stores_and_status_reflects(home, keypair, monkeypatch):
    private_pem, public_pem = keypair
    monkeypatch.setenv("AGENTWATCH_LICENSE_PUBLIC_KEY", public_pem)
    token = _token(private_pem)

    activate = runner.invoke(app, ["upgrade", "--activate", token])
    assert activate.exit_code == 0
    assert "Premium activated" in activate.stdout

    status = runner.invoke(app, ["upgrade", "--status"])
    assert status.exit_code == 0
    assert "enterprise" in status.stdout


def test_upgrade_activate_rejects_unsigned_token(home, keypair, monkeypatch):
    _, public_pem = keypair
    monkeypatch.setenv("AGENTWATCH_LICENSE_PUBLIC_KEY", public_pem)

    result = runner.invoke(app, ["upgrade", "--activate", "garbage.token.value"])
    assert result.exit_code == 1
    assert "rejected" in result.stdout
    assert load_entitlement_token() is None


def test_ensure_premium_prompts_on_free_tier(home):
    import typer

    from panoptis.cli.main import _ensure_premium

    with pytest.raises(typer.Exit) as exc_info:
        _ensure_premium("redteam")
    assert exc_info.value.exit_code == 1
