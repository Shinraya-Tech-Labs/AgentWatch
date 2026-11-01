"""
CMP — CLI-to-Web checkout handoff for premium upgrades.

``panoptis upgrade`` generates a short-lived, single-use session token and
opens the user's browser to the hosted checkout page, passing the token so the
web flow can bind the eventual entitlement back to this CLI invocation.

The token is opaque and random; its single-use / expiry semantics are enforced
by the backend when the browser presents it. Generating it here only
establishes the binding — this module performs no payment processing.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

# Hosted checkout portal. Overridable for self-hosted/enterprise deployments.
DEFAULT_CHECKOUT_URL = "https://panoptis.ai/checkout"

# Session tokens are short-lived to limit the interception window during the
# CLI -> browser handoff.
SESSION_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class CheckoutSession:
    """A pending CLI-to-Web checkout handoff."""

    token: str
    expires_at: datetime

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return whether the handoff window has closed."""
        return (now or datetime.now(UTC)) >= self.expires_at


def new_session(*, ttl: timedelta = SESSION_TTL) -> CheckoutSession:
    """Create a new single-use checkout session token bound to this CLI run."""
    return CheckoutSession(
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + ttl,
    )


def checkout_url(session: CheckoutSession, *, base: str = DEFAULT_CHECKOUT_URL) -> str:
    """Build the browser checkout URL carrying the handoff ``session`` token."""
    return f"{base}?{urlencode({'session': session.token})}"
