"""Tests for rate limiting.

Covers two layers:
- The reusable ``RateLimiter`` / ``RateLimitMiddleware`` (per-user and global
  limits) under ``panoptis.api.middleware.rate_limiter``.
- The per-IP rate limiting wired directly into the AgentWatch API server.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from panoptis.api.middleware.rate_limiter import (
    RateLimiter,
    RateLimitMiddleware,
    client_ip,
)


class TestRateLimiter:
    """Test the RateLimiter class."""

    def test_initialization(self):
        """Test RateLimiter initializes with correct defaults."""
        limiter = RateLimiter()
        assert limiter.user_limit == 100
        assert limiter.global_limit == 10000
        assert limiter.window_sec == 3600

    def test_per_user_rate_limiting(self):
        """Test per-user rate limiting enforcement."""
        limiter = RateLimiter(user_limit=3, window_sec=3600)

        for i in range(3):
            allowed, quota = limiter.check_rate_limit("user1")
            assert allowed is True

        allowed, quota = limiter.check_rate_limit("user1")
        assert allowed is False

    def test_global_rate_limiting(self):
        """Test global rate limiting enforcement."""
        limiter = RateLimiter(user_limit=10, global_limit=5, window_sec=3600)

        for i in range(5):
            user_id = f"user{i}"
            allowed, quota = limiter.check_rate_limit(user_id)
            assert allowed is True

        allowed, quota = limiter.check_rate_limit("user99")
        assert allowed is False


class TestRateLimitMiddleware:
    """Test the RateLimitMiddleware integration."""

    @pytest.fixture
    def app_with_limiter(self):
        """Create a FastAPI app with rate limiting middleware for testing."""
        app = FastAPI()
        limiter = RateLimiter(user_limit=3, global_limit=10, window_sec=3600)
        app.add_middleware(RateLimitMiddleware, limiter=limiter)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "success"}

        return app

    def test_successful_request_includes_headers(self, app_with_limiter):
        """Test that successful requests include rate limit headers."""
        client = TestClient(app_with_limiter)
        response = client.get("/test")

        assert response.status_code == 200
        assert "X-RateLimit-User-Limit" in response.headers

    def test_rate_limit_exceeded_returns_429(self, app_with_limiter):
        """Test that exceeding rate limit returns 429."""
        client = TestClient(app_with_limiter)

        for i in range(3):
            response = client.get("/test")
            assert response.status_code == 200

        response = client.get("/test")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.fixture
def rate_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Fresh limiter with a low write cap so 429 is reachable quickly."""
    monkeypatch.setenv("API_RATE_LIMIT_WRITE", "10")
    monkeypatch.setenv("API_RATE_LIMIT_READ", "1000")
    monkeypatch.delenv("AGENTWATCH_API_KEY", raising=False)
    monkeypatch.delenv("AGENTWATCH_ENV", raising=False)

    import panoptis.api.server as server

    # monkeypatch restores these even when a test fails; assigning them leaked a 10-write limit
    # into every later test in the process
    monkeypatch.setattr(server, "RATE_WRITE", 10)
    monkeypatch.setattr(server, "RATE_READ", 1000)
    server.reset_rate_limiter_for_tests()

    return TestClient(server.app, raise_server_exceptions=False)


def test_read_endpoint_returns_rate_limit_headers(rate_client: TestClient) -> None:
    r = rate_client.get("/health")
    assert r.status_code == 200, r.text
    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers


def test_write_endpoint_returns_429_after_limit(rate_client: TestClient) -> None:
    last_status = None
    for i in range(25):
        r = rate_client.post(
            "/api/v1/sessions",
            json={
                "session_id": f"test-{i}",
                "agent_id": "agent-1",
                "agent_name": "Test",
                "framework": "claude_code",
                "status": "running",
            },
        )
        last_status = r.status_code
        if r.status_code == 429:
            break
    assert last_status == 429, (
        f"Expected 429 after exceeding write limit, last status was {last_status}"
    )


def test_429_body_matches_spec(rate_client: TestClient) -> None:
    last = None
    for i in range(25):
        last = rate_client.post(
            "/api/v1/sessions",
            json={
                "session_id": f"spec-{i}",
                "agent_id": "agent-1",
                "agent_name": "Test",
                "framework": "claude_code",
                "status": "running",
            },
        )
        if last.status_code == 429:
            break
    assert last is not None, "No responses received from write endpoint"
    assert last.status_code == 429, "Expected a 429 response but none was returned"
    assert last.json() == {"error": "rate_limit_exceeded"}
    assert "Retry-After" in last.headers


class _Peer:
    host = "10.0.0.1"


class _ForwardedRequest:
    client = _Peer()
    headers = Headers({"x-forwarded-for": "203.0.113.9, 70.41.3.18"})


def test_client_ip_ignores_forwarded_for_from_an_unknown_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTWATCH_TRUSTED_PROXIES", raising=False)
    assert client_ip(_ForwardedRequest()) == "10.0.0.1"  # type: ignore[arg-type]


def test_client_ip_takes_the_hop_a_trusted_proxy_saw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTWATCH_TRUSTED_PROXIES", "10.0.0.1")
    # the first entry is whatever the sender wrote; the proxy appended the last one
    assert client_ip(_ForwardedRequest()) == "70.41.3.18"  # type: ignore[arg-type]


def test_client_ip_walks_past_every_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTWATCH_TRUSTED_PROXIES", "10.0.0.1, 70.41.3.18")
    assert client_ip(_ForwardedRequest()) == "203.0.113.9"  # type: ignore[arg-type]


def test_client_ip_reads_every_forwarded_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTWATCH_TRUSTED_PROXIES", "10.0.0.1")

    class _TwoLines(_ForwardedRequest):
        headers = Headers(
            raw=[(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-for", b"70.41.3.18")]
        )

    assert client_ip(_TwoLines()) == "70.41.3.18"  # type: ignore[arg-type]


def test_client_ip_never_returns_a_forged_bucket_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTWATCH_TRUSTED_PROXIES", "10.0.0.1")

    class _Forged(_ForwardedRequest):
        headers = Headers({"x-forwarded-for": "tenant:acme"})

    assert client_ip(_Forged()) == "10.0.0.1"  # type: ignore[arg-type]


def test_limiter_prunes_stale_buckets() -> None:
    import time

    import panoptis.api.server as server

    server.reset_rate_limiter_for_tests()
    server._limiter._buckets["stale-ip:w"] = {
        "count": 1,
        "start": time.time() - server.RATE_BUCKET_TTL_SEC - 1,
    }
    server._limiter._prune_stale(time.time())
    assert "stale-ip:w" not in server._limiter._buckets
