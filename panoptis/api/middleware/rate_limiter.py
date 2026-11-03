"""
Rate Limiting Middleware

Per-user and global rate limiting over a pluggable counter backend. The default
in-memory backend is per-process (multiplies the effective limit across replicas);
the Redis backend shares one quota across replicas. The RateLimiter
interface is identical for both.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Protocol, runtime_checkable

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "panoptis:ratelimit:"


@runtime_checkable
class RateLimitBackend(Protocol):
    """Counter store for a fixed-window rate limiter."""

    def hit(self, key: str, window_sec: int) -> int:
        """Increment the counter for `key` and return the new count."""
        ...

    def peek(self, key: str, window_sec: int) -> int:
        """Return the current count for `key` without incrementing."""
        ...


class InMemoryBackend:
    """Process-local fixed-window counters (not shared across replicas)."""

    def __init__(self, max_buckets: int = 50_000) -> None:
        self._max_buckets = max_buckets
        self._buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "start": time.time()}
        )
        self._lock = threading.Lock()

    def _current(self, key: str, window_sec: int) -> dict[str, Any]:
        now = time.time()
        # Evict expired keys when bucket map gets large to prevent memory leaks
        if len(self._buckets) > self._max_buckets:
            stale = [k for k, b in self._buckets.items() if now - b["start"] > window_sec]
            for k in stale:
                self._buckets.pop(k, None)
        bucket = self._buckets[key]
        if now - bucket["start"] > window_sec:
            bucket["count"] = 0
            bucket["start"] = now
        return bucket

    def hit(self, key: str, window_sec: int) -> int:
        with self._lock:
            bucket = self._current(key, window_sec)
            bucket["count"] += 1
            return bucket["count"]

    def peek(self, key: str, window_sec: int) -> int:
        with self._lock:
            now = time.time()
            if key not in self._buckets:
                return 0
            bucket = self._buckets[key]
            if now - bucket["start"] > window_sec:
                return 0
            return bucket["count"]


class RedisBackend:
    """Shared fixed-window counters backed by Redis (atomic INCR + EXPIRE NX)."""

    def __init__(self, client: Any, *, key_prefix: str = _REDIS_KEY_PREFIX) -> None:
        self._client = client
        self._key_prefix = key_prefix

    def hit(self, key: str, window_sec: int) -> int:
        redis_key = f"{self._key_prefix}{key}"
        count = int(self._client.incr(redis_key))
        # NX: set TTL only on the first hit so the window is fixed, not sliding.
        self._client.expire(redis_key, window_sec, nx=True)
        return count

    def peek(self, key: str, window_sec: int) -> int:
        value = self._client.get(f"{self._key_prefix}{key}")
        return int(value) if value is not None else 0


class RateLimiter:
    """Per-user and global rate limiting over a pluggable counter backend."""

    _GLOBAL_KEY = "__global__"

    def __init__(
        self,
        user_limit: int = 100,
        global_limit: int = 10000,
        window_sec: int = 3600,
        *,
        backend: RateLimitBackend | None = None,
    ) -> None:
        """Configure limits; backend defaults to in-memory."""
        self.user_limit = user_limit
        self.global_limit = global_limit
        self.window_sec = window_sec
        self.backend: RateLimitBackend = backend if backend is not None else InMemoryBackend()

    @classmethod
    def from_redis_url(
        cls,
        redis_url: str,
        *,
        user_limit: int = 100,
        global_limit: int = 10000,
        window_sec: int = 3600,
    ) -> RateLimiter:
        """Build a Redis-backed limiter, falling back to in-memory on failure."""
        backend: RateLimitBackend
        try:
            import redis

            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            backend = RedisBackend(client)
        except Exception as exc:
            logger.warning(
                "Redis unavailable for rate limiting (%s); using in-memory counters. "
                "Limits will NOT be shared across replicas.",
                exc,
            )
            backend = InMemoryBackend()
        return cls(
            user_limit=user_limit,
            global_limit=global_limit,
            window_sec=window_sec,
            backend=backend,
        )

    def _quota(self, user_count: int, global_count: int) -> dict[str, int]:
        return {
            "user_limit": self.user_limit,
            "user_remaining": max(0, self.user_limit - user_count),
            "global_limit": self.global_limit,
            "global_remaining": max(0, self.global_limit - global_count),
        }

    def check_rate_limit(self, user_id: str) -> tuple[bool, dict[str, int]]:
        """
        Check if user and global limits are not exceeded.

        Args:
            user_id: The user identifier

        Returns:
            Tuple of (is_allowed, quota_info) where quota_info contains:
            - user_limit, user_remaining, global_limit, global_remaining
        """
        global_count = self.backend.hit(self._GLOBAL_KEY, self.window_sec)
        user_count = self.backend.hit(f"user:{user_id}", self.window_sec)

        allowed = user_count <= self.user_limit and global_count <= self.global_limit
        return allowed, self._quota(user_count, global_count)

    def get_remaining_quota(self, user_id: str) -> dict[str, int]:
        """
        Get current remaining quota for user without incrementing.

        Args:
            user_id: The user identifier

        Returns:
            Dictionary with user and global remaining quota
        """
        global_count = self.backend.peek(self._GLOBAL_KEY, self.window_sec)
        user_count = self.backend.peek(f"user:{user_id}", self.window_sec)
        return self._quota(user_count, global_count)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limits on API requests."""

    def __init__(self, app, limiter: RateLimiter) -> None:
        """
        Initialize middleware with rate limiter instance.

        Args:
            app: FastAPI application
            limiter: RateLimiter instance to use for all requests
        """
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        """
        Process request and enforce rate limits.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            Response with rate limit headers, or 429 if limit exceeded
        """
        # Extract user ID from request (from Authorization header or IP)
        user_id = self._extract_user_id(request)

        # Check rate limits
        allowed, quota = self.limiter.check_rate_limit(user_id)

        # Store quota info in request state for response headers
        request.state.rate_limit_quota = quota
        request.state.rate_limit_allowed = allowed

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error": "rate_limit_exceeded"},
                headers=self._build_rate_limit_headers(quota),
            )

        # Continue with request
        response = await call_next(request)

        # Add rate limit headers to response
        response.headers.update(self._build_rate_limit_headers(quota))

        return response

    def _extract_user_id(self, request: Request) -> str:
        """
        Extract user ID from request.

        Args:
            request: HTTP request object

        Returns:
            User ID string (from Authorization header or IP address)
        """
        # Try to get from Authorization header first
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]

        # Fall back to client IP address
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()

        if request.client:
            return request.client.host

        return "unknown"

    def _build_rate_limit_headers(self, quota: dict[str, int]) -> dict[str, str]:
        """
        Build HTTP headers with rate limit info.

        Args:
            quota: Rate limit quota dictionary from limiter

        Returns:
            Dictionary of rate limit headers
        """
        return {
            "X-RateLimit-User-Limit": str(quota["user_limit"]),
            "X-RateLimit-User-Remaining": str(quota["user_remaining"]),
            "X-RateLimit-Global-Limit": str(quota["global_limit"]),
            "X-RateLimit-Global-Remaining": str(quota["global_remaining"]),
        }


__all__ = [
    "RateLimitBackend",
    "InMemoryBackend",
    "RedisBackend",
    "RateLimiter",
    "RateLimitMiddleware",
]
