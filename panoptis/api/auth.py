"""
CMP-005 (API layer) — SAML SSO + RBAC enforcement for the dashboard API.

The RBAC roles, per-team policies, and SAML token primitives live in
:mod:`panoptis.governance.rbac`. That module's docstring notes that the
"real wiring is at the API layer" — this module is that wiring.

It exposes FastAPI dependencies that:

1. Extract and verify a SAML session token from the ``Authorization: Bearer``
   header using the existing :func:`panoptis.governance.rbac.verify_token`.
2. Resolve the caller's :class:`~panoptis.governance.rbac.Role` from the
   verified claims.
3. Enforce a required permission via
   :meth:`~panoptis.governance.rbac.RBACEngine.has_permission`, returning
   HTTP 401/403 as appropriate.

Design — opt-in and non-breaking, mirroring the existing ``_require_api_key``
guard in ``server.py``:

* When ``AGENTWATCH_SAML_SECRET`` is **unset**, SAML/RBAC enforcement is a
  no-op so local development and the existing API-key-only deployments keep
  working unchanged.
* When the secret **is** set, protected routes require a valid bearer token
  whose role carries the required permission.
* In production (``AGENTWATCH_ENV=production``) a route guarded by
  :func:`require_permission` fails closed if the secret is missing.

This module intentionally does not implement its own crypto or role model — it
delegates entirely to ``governance.rbac`` so there is a single source of truth
for roles, permissions, and token verification.
"""

from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException, status

from panoptis.governance.rbac import RBACEngine, Role, SAMLClaims, User, verify_token

# Secret used to verify SAML session tokens. Unset → enforcement disabled.
_SAML_SECRET: bytes | None = (
    os.getenv("AGENTWATCH_SAML_SECRET").encode() if os.getenv("AGENTWATCH_SAML_SECRET") else None
)

_ENV = os.getenv("AGENTWATCH_ENV") or os.getenv("ENVIRONMENT") or "development"
_IS_PROD = _ENV.lower() == "production"


def saml_enforcement_enabled() -> bool:
    """True when a SAML secret is configured (enforcement is active)."""
    return _SAML_SECRET is not None


def _extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def authenticate(
    authorization: str | None = Header(default=None),
) -> SAMLClaims | None:
    """FastAPI dependency: verify the SAML bearer token, return its claims.

    Returns ``None`` when enforcement is disabled (no secret configured) so
    that unauthenticated/dev deployments are unaffected. When enforcement is
    enabled, a missing or invalid token raises HTTP 401.
    """
    if _SAML_SECRET is None:
        # Enforcement disabled — behave as a no-op (dev / API-key-only mode).
        return None

    token = _extract_bearer(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing SAML session token. Supply 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = verify_token(token, _SAML_SECRET)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired SAML session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


def _engine_for(claims: SAMLClaims) -> RBACEngine:
    """Build a single-user RBAC engine from verified token claims.

    The token is the source of truth for the caller's role and team, so we
    register exactly that user and let the engine evaluate permissions. This
    keeps permission logic in ``governance.rbac`` rather than duplicating it.
    """
    engine = RBACEngine()
    engine.add_user(
        User(
            user_id=claims.sub,
            email=claims.email,
            role=claims.role,
            team_id=claims.team_id,
        )
    )
    return engine


def require_permission(permission: str):
    """Build a FastAPI dependency that enforces a single RBAC permission.

    Usage::

        @app.get("/api/v1/sessions")
        async def list_sessions(
            _perm: SAMLClaims | None = Depends(require_permission("session:read")),
        ): ...

    When SAML enforcement is disabled the dependency is a no-op (returns
    ``None``). When enabled it verifies the token, resolves the role, and
    returns HTTP 403 if the role lacks ``permission``.
    """

    def _dependency(
        claims: SAMLClaims | None = Depends(authenticate),
    ) -> SAMLClaims | None:
        # In production, a permission-guarded route must have a configured
        # secret; otherwise fail closed rather than silently allowing access.
        if _SAML_SECRET is None:
            if _IS_PROD:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Server misconfiguration: AGENTWATCH_SAML_SECRET is required "
                        "in production for permission-protected routes."
                    ),
                )
            return None  # dev / disabled — no-op

        # Enforcement enabled: claims is guaranteed non-None here because
        # authenticate() would have raised otherwise. Guard explicitly rather
        # than asserting so behaviour is identical under python -O.
        if claims is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        engine = _engine_for(claims)
        if not engine.has_permission(claims.sub, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{claims.role.value}' lacks the required permission '{permission}'."
                ),
            )
        return claims

    return _dependency


def require_role(minimum: Role):
    """Build a FastAPI dependency that requires at least ``minimum`` role.

    Roles are ordered viewer < operator < admin < owner. Useful for routes
    that are gated by role tier rather than a specific permission string.
    """
    order = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2, Role.OWNER: 3}

    def _dependency(
        claims: SAMLClaims | None = Depends(authenticate),
    ) -> SAMLClaims | None:
        if _SAML_SECRET is None:
            if _IS_PROD:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Server misconfiguration: AGENTWATCH_SAML_SECRET is required "
                        "in production for role-protected routes."
                    ),
                )
            return None
        if claims is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if order[claims.role] < order[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{claims.role.value}' is below the required "
                    f"minimum role '{minimum.value}'."
                ),
            )
        return claims

    return _dependency


__all__ = [
    "saml_enforcement_enabled",
    "authenticate",
    "require_permission",
    "require_role",
]
