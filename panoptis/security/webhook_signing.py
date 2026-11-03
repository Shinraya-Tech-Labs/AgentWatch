"""Webhook signature generation, verification, and idempotency tracking."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import OrderedDict


def generate_webhook_signature(payload: bytes, secret: str) -> str:
    """Generate an HMAC-SHA256 signature for a webhook payload.

    Args:
        payload: The exact bytes of the HTTP request body that will be sent.
        secret: The shared secret key used for signing.

    Returns:
        The signature string in the format 'sha256=<hex_digest>'.
    """
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """Verify an HMAC-SHA256 webhook signature with constant-time equality check.

    Supports:
    - Standard signature: 'sha256=<hex>' or raw '<hex>'
    - Stripe / modern timestamped signature: 't=<timestamp>,v1=<hex>'

    Args:
        payload: The raw request body bytes.
        signature_header: The incoming signature header value.
        secret: The shared secret key.
        tolerance: Maximum allowed timestamp drift in seconds (for timestamped headers).

    Returns:
        True if valid, False otherwise.
    """
    if not signature_header or not secret:
        return False

    secret_bytes = secret.encode("utf-8")

    # Handle timestamped signature: t=<timestamp>,v1=<sig>
    if "t=" in signature_header and "v1=" in signature_header:
        parts: dict[str, str] = {}
        for item in signature_header.split(","):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                parts[k] = v

        timestamp_str = parts.get("t")
        expected_sig = parts.get("v1")
        if not timestamp_str or not expected_sig:
            return False

        try:
            ts = int(timestamp_str)
        except ValueError:
            return False

        if tolerance > 0 and abs(time.time() - ts) > tolerance:
            return False

        # Signed payload is f"{ts}." + payload
        to_sign = f"{ts}.".encode() + payload
        computed = hmac.new(secret_bytes, to_sign, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, expected_sig)

    # Standard format: 'sha256=<hex>' or raw '<hex>'
    expected_hex = (
        signature_header.split("sha256=", 1)[1]
        if signature_header.startswith("sha256=")
        else signature_header
    )
    computed_hex = hmac.new(secret_bytes, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hex, expected_hex)


class WebhookIdempotencyTracker:
    """Bounded, thread-safe in-memory idempotency tracker for webhook deduplication.

    Records processed event IDs to prevent duplicate execution from webhook replays.
    """

    def __init__(self, max_entries: int = 10000, default_ttl_seconds: int = 86400):
        self._max_entries = max_entries
        self._default_ttl = default_ttl_seconds
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def _key(self, event_id: str, provider: str) -> str:
        return f"{provider}:{event_id}"

    def _prune(self, now: float) -> None:
        expired = [k for k, exp in self._cache.items() if exp < now]
        for k in expired:
            del self._cache[k]
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def is_duplicate(self, event_id: str, provider: str = "default") -> bool:
        """Check if an event ID has already been processed and is within TTL."""
        now = time.time()
        with self._lock:
            self._prune(now)
            key = self._key(event_id, provider)
            if key in self._cache:
                if self._cache[key] >= now:
                    return True
                del self._cache[key]
            return False

    def record_event(
        self, event_id: str, provider: str = "default", ttl_seconds: int | None = None
    ) -> bool:
        """Record an event ID. Returns True if recorded (first time), False if duplicate."""
        now = time.time()
        with self._lock:
            self._prune(now)
            key = self._key(event_id, provider)
            if key in self._cache and self._cache[key] >= now:
                return False  # Already processed
            ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
            self._cache[key] = now + ttl
            self._cache.move_to_end(key)
            return True

    def clear(self) -> None:
        """Reset the tracker cache."""
        with self._lock:
            self._cache.clear()
