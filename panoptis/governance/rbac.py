"""
CMP-005 — Enterprise RBAC + SAML SSO.

Roles: viewer / operator / admin / owner.
Per-team agent policies.
SAML SSO integration (token verification stub — real wiring is at the API layer).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum

from panoptis.governance.audit_log import AuditLog


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    OWNER = "owner"


# Role → set of permission strings
_PERMISSIONS: dict[Role, set[str]] = {
    Role.VIEWER: {"session:read", "trace:read", "report:read"},
    Role.OPERATOR: {
        "session:read",
        "session:create",
        "trace:read",
        "report:read",
        "policy:read",
        "rollback:trigger",
    },
    Role.ADMIN: {
        "session:read",
        "session:create",
        "session:delete",
        "trace:read",
        "report:read",
        "report:write",
        "policy:read",
        "policy:write",
        "rollback:trigger",
        "user:invite",
    },
    Role.OWNER: {
        "*",  # full access
    },
}


@dataclass
class User:
    user_id: str
    email: str
    role: Role
    team_id: str | None = None


@dataclass
class TeamPolicy:
    team_id: str
    allowed_tools: set[str] = field(default_factory=set)
    blocked_tools: set[str] = field(default_factory=set)
    require_approval_above_usd: float = 100.0


class RBACEngine:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._team_policies: dict[str, TeamPolicy] = {}
        # Tamper-evident record of every RBAC / policy change (CMP-005).
        self.audit = AuditLog()

    def add_user(self, user: User, *, actor: str | None = None) -> None:
        self._users[user.user_id] = user
        self.audit.append(
            "user.add",
            user.user_id,
            actor=actor,
            details={"role": user.role.value, "team_id": user.team_id},
        )

    def set_role(self, user_id: str, role: Role, *, actor: str | None = None) -> bool:
        """Change a user's role, recording the change in the audit log.

        Returns ``False`` when the user is unknown (no change, no audit entry).
        """
        user = self._users.get(user_id)
        if user is None:
            return False
        previous = user.role
        user.role = role
        self.audit.append(
            "role.change",
            user_id,
            actor=actor,
            details={"from": previous.value, "to": role.value},
        )
        return True

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def set_team_policy(self, policy: TeamPolicy, *, actor: str | None = None) -> None:
        self._team_policies[policy.team_id] = policy
        self.audit.append(
            "policy.set",
            policy.team_id,
            actor=actor,
            details={
                "allowed_tools": sorted(policy.allowed_tools),
                "blocked_tools": sorted(policy.blocked_tools),
                "require_approval_above_usd": policy.require_approval_above_usd,
            },
        )

    def team_policy(self, team_id: str) -> TeamPolicy | None:
        return self._team_policies.get(team_id)

    def has_permission(self, user_id: str, permission: str) -> bool:
        user = self._users.get(user_id)
        if user is None:
            return False
        perms = _PERMISSIONS.get(user.role, set())
        return "*" in perms or permission in perms

    def can_run_tool(self, user_id: str, tool: str) -> bool:
        user = self._users.get(user_id)
        if user is None:
            return False
        if user.role == Role.OWNER:
            return True
        if user.team_id is None:
            return False  # deny-by-default: no team → no policy → no access
        policy = self._team_policies.get(user.team_id)
        if policy is None:
            return False  # deny-by-default: missing policy → no access
        if tool in policy.blocked_tools:
            return False
        if policy.allowed_tools and tool not in policy.allowed_tools:
            return False
        return True


# ── SAML token (compact, HMAC-signed) ──────────────────────────────────────


@dataclass
class SAMLClaims:
    sub: str  # subject
    email: str
    role: Role
    team_id: str | None = None
    issued_at: int = 0
    expires_at: int = 0


def issue_token(claims: SAMLClaims, secret: bytes) -> str:
    payload = {
        "sub": claims.sub,
        "email": claims.email,
        "role": claims.role.value,
        "team_id": claims.team_id,
        "iat": claims.issued_at or int(time.time()),
        "exp": claims.expires_at or int(time.time()) + 3600,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
        b"="
    )
    sig = hmac.new(secret, body, hashlib.sha256).digest()
    sig_b = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return (body + b"." + sig_b).decode()


def verify_token(token: str, secret: bytes) -> SAMLClaims | None:
    try:
        body_str, sig_str = token.split(".", 1)
        body = body_str.encode()
        expected = hmac.new(secret, body, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(sig_str + "=" * (-len(sig_str) % 4))
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) % 4)))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return SAMLClaims(
            sub=payload["sub"],
            email=payload["email"],
            role=Role(payload["role"]),
            team_id=payload.get("team_id"),
            issued_at=int(payload.get("iat", 0)),
            expires_at=int(payload.get("exp", 0)),
        )
    except Exception:
        return None


__all__ = [
    "Role",
    "User",
    "TeamPolicy",
    "RBACEngine",
    "SAMLClaims",
    "issue_token",
    "verify_token",
]
