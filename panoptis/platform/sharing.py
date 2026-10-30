"""
PLT-001 — Session Sharing via Public Link.

- Public share API endpoint
- Public replay token generation
- Configurable: full or redacted view
- Expiry time on shared links
- Viewer can observe only, not modify
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from panoptis.security.redaction import MASK, default_redactor


class ShareScope(str, Enum):
    FULL = "full"
    REDACTED = "redacted"


@dataclass
class ShareLink:
    token: str
    session_id: str
    scope: ShareScope
    created_at: datetime
    expires_at: datetime
    revoked: bool = False

    @property
    def url_path(self) -> str:
        return f"/replay/{self.token}"

    @property
    def active(self) -> bool:
        return not self.revoked and datetime.now(UTC) < self.expires_at


class ShareLinkRegistry:
    """In-memory share-link registry with HMAC signing."""

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._links: dict[str, ShareLink] = {}

    def create(
        self,
        session_id: str,
        *,
        scope: ShareScope = ShareScope.REDACTED,
        ttl: timedelta = timedelta(days=7),
    ) -> ShareLink:
        payload = {
            "sid": session_id,
            "scope": scope.value,
            "exp": int((datetime.now(UTC) + ttl).timestamp()),
            "n": secrets.token_hex(8),
        }
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
            b"="
        )
        sig = hmac.new(self._secret, body, hashlib.sha256).digest()
        token = (body + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode()
        link = ShareLink(
            token=token,
            session_id=session_id,
            scope=scope,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + ttl,
        )
        self._links[token] = link
        return link

    def resolve(self, token: str) -> ShareLink | None:
        link = self._links.get(token)
        if link is None or not link.active:
            return None
        # Verify the signature too — defense in depth
        try:
            body_s, sig_s = token.split(".", 1)
            body = body_s.encode()
            expected = hmac.new(self._secret, body, hashlib.sha256).digest()
            actual = base64.urlsafe_b64decode(sig_s + "=" * (-len(sig_s) % 4))
            if not hmac.compare_digest(expected, actual):
                return None
        except Exception:
            return None
        return link

    def revoke(self, token: str) -> bool:
        link = self._links.get(token)
        if link is None:
            return False
        link.revoked = True
        return True

    def list_for_session(self, session_id: str) -> list[ShareLink]:
        return [link for link in self._links.values() if link.session_id == session_id]


def render_for_viewer(
    session_payload: dict[str, Any],
    scope: ShareScope,
) -> dict[str, Any]:
    """Strip sensitive fields when scope is REDACTED."""
    if scope == ShareScope.FULL:
        return session_payload
    redacted = dict(session_payload)
    for key in ("api_keys", "credentials", "environment_vars", "raw_command"):
        if key in redacted:
            redacted[key] = "[REDACTED]"
    # Walk events and redact raw commands
    events = redacted.get("events", [])
    for ev in events:
        if isinstance(ev, dict):
            tc = ev.get("tool_call")
            if isinstance(tc, dict):
                if tc.get("raw_command"):
                    tc["raw_command"] = "[REDACTED]"
                if "arguments" in tc:
                    tc["arguments"] = {k: "[REDACTED]" for k in tc["arguments"]}
    return redacted


# Community endpoint the CLI ``share`` command uploads to; overridable per call.
DEFAULT_SHARE_URL = "https://share.agentwatch.dev"

# Keys whose values are secrets that free-text regexes cannot reliably detect
# (API tokens, raw ``.env`` dumps); their values are masked structurally.
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_keys",
        "apikey",
        "access_key",
        "secret_key",
        "authorization",
        "auth_token",
        "credential",
        "credentials",
        "env",
        "environ",
        "environment",
        "environment_vars",
        "password",
        "passwd",
        "secret",
        "secrets",
        "token",
    }
)


def sanitize_trace(payload: Any) -> Any:
    """
    Return a deep copy of a trace made safe to upload to a public viewer.

    Sanitization is unconditional — there is no "full" escape hatch — because a
    shared link is world-readable. Two passes are applied:

    1. Structural: any mapping key naming a secret (API keys, raw environment
       dumps, credentials; see :data:`_SECRET_KEYS`) has its value replaced
       with the redaction mask.
    2. Content: every remaining string is run through the PII/PHI
       :class:`~panoptis.security.redaction.Redactor`, masking emails, SSNs,
       cloud keys, medical identifiers, and similar in place.
    """
    return default_redactor().redact_payload(_strip_secret_keys(payload))


def _strip_secret_keys(value: Any) -> Any:
    """Recursively mask values filed under a secret-bearing key."""
    if isinstance(value, dict):
        return {
            k: MASK
            if isinstance(k, str) and any(sk in k.lower() for sk in _SECRET_KEYS)
            else _strip_secret_keys(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_secret_keys(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_strip_secret_keys(v) for v in value)
    return value


__all__ = [
    "ShareScope",
    "ShareLink",
    "ShareLinkRegistry",
    "render_for_viewer",
    "sanitize_trace",
    "DEFAULT_SHARE_URL",
]
