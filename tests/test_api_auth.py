"""
Tests for CMP-005 API-layer wiring — SAML SSO + RBAC enforcement.

These verify that the FastAPI auth dependencies in ``panoptis.api.auth``
correctly delegate to the existing ``governance.rbac`` primitives:
token verification, role resolution, and permission/role enforcement —
including the opt-in, non-breaking behaviour when no SAML secret is set.

The dependency callables are exercised directly (the same way FastAPI would
call them after resolving their sub-dependencies), which keeps the tests fast
and free of a running server while still covering the real enforcement logic.
"""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi import HTTPException

from panoptis.governance.rbac import Role, SAMLClaims, issue_token

# Test-only fixture value (not a real credential).
_SECRET_STR = "unit-test-saml-secret"  # noqa: S105
_SECRET = _SECRET_STR.encode()


def _reload_auth_with_secret(monkeypatch, secret: str | None, env: str | None = None):
    """Reload the auth module with a chosen SAML secret / environment."""
    if secret is None:
        monkeypatch.delenv("AGENTWATCH_SAML_SECRET", raising=False)
    else:
        monkeypatch.setenv("AGENTWATCH_SAML_SECRET", secret)
    if env is None:
        monkeypatch.delenv("AGENTWATCH_ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("AGENTWATCH_ENV", env)

    import panoptis.api.auth as auth

    importlib.reload(auth)
    return auth


def _token(role: Role, *, team_id: str | None = "team-1", expired: bool = False) -> str:
    now = int(time.time())
    claims = SAMLClaims(
        sub=f"user-{role.value}",
        email=f"{role.value}@example.com",
        role=role,
        team_id=team_id,
        issued_at=now,
        expires_at=now - 10 if expired else now + 3600,
    )
    return issue_token(claims, _SECRET)


# ── Enforcement enabled ────────────────────────────────────────────────────


def test_authenticate_accepts_valid_token(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    claims = auth.authenticate(authorization=f"Bearer {_token(Role.VIEWER)}")
    assert claims is not None
    assert claims.role is Role.VIEWER


def test_authenticate_rejects_missing_token(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(authorization=None)
    assert exc.value.status_code == 401


def test_authenticate_rejects_garbage_token(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(authorization="Bearer not.a.valid.token")
    assert exc.value.status_code == 401


def test_authenticate_rejects_expired_token(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(authorization=f"Bearer {_token(Role.ADMIN, expired=True)}")
    assert exc.value.status_code == 401


def test_authenticate_rejects_non_bearer_scheme(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate(authorization=f"Basic {_token(Role.VIEWER)}")
    assert exc.value.status_code == 401


def test_require_permission_grants_when_role_has_it(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    claims = auth.authenticate(authorization=f"Bearer {_token(Role.VIEWER)}")
    dep = auth.require_permission("session:read")
    assert dep(claims=claims) is claims


def test_require_permission_denies_when_role_lacks_it(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    claims = auth.authenticate(authorization=f"Bearer {_token(Role.VIEWER)}")
    dep = auth.require_permission("policy:write")
    with pytest.raises(HTTPException) as exc:
        dep(claims=claims)
    assert exc.value.status_code == 403


def test_admin_has_policy_write(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    claims = auth.authenticate(authorization=f"Bearer {_token(Role.ADMIN)}")
    assert auth.require_permission("policy:write")(claims=claims) is claims


def test_owner_has_wildcard_permission(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    claims = auth.authenticate(authorization=f"Bearer {_token(Role.OWNER)}")
    # Owner has "*" — even an arbitrary permission is granted.
    assert auth.require_permission("anything:at:all")(claims=claims) is claims


def test_require_role_enforces_minimum(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, _SECRET_STR)
    viewer = auth.authenticate(authorization=f"Bearer {_token(Role.VIEWER)}")
    with pytest.raises(HTTPException) as exc:
        auth.require_role(Role.OPERATOR)(claims=viewer)
    assert exc.value.status_code == 403

    admin = auth.authenticate(authorization=f"Bearer {_token(Role.ADMIN)}")
    assert auth.require_role(Role.OPERATOR)(claims=admin) is admin


# ── Enforcement disabled (non-breaking default) ────────────────────────────


def test_disabled_when_no_secret(monkeypatch):
    auth = _reload_auth_with_secret(monkeypatch, None)
    assert auth.saml_enforcement_enabled() is False
    # No token, no error — authenticate is a no-op returning None.
    assert auth.authenticate(authorization=None) is None
    # Permission/role deps are no-ops too.
    assert auth.require_permission("policy:write")(claims=None) is None
    assert auth.require_role(Role.OWNER)(claims=None) is None


def test_production_without_secret_fails_closed_on_permission(monkeypatch):
    # Production + no secret + permission-guarded route → 500 (fail closed).
    auth = _reload_auth_with_secret(monkeypatch, None, env="production")
    with pytest.raises(HTTPException) as exc:
        auth.require_permission("session:read")(claims=None)
    assert exc.value.status_code == 500


def test_cleanup_reload(monkeypatch):
    # Restore the module to its unset-secret state for any later imports.
    _reload_auth_with_secret(monkeypatch, None)
    import panoptis.api.auth as auth

    assert auth.saml_enforcement_enabled() is False
