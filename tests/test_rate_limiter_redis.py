"""Tests for the Redis-backed distributed rate limiter."""

from __future__ import annotations

import pytest

from panoptis.api.middleware.rate_limiter import (
    InMemoryBackend,
    RateLimitBackend,
    RateLimiter,
    RedisBackend,
)


class FakeRedis:
    """Minimal shared Redis stand-in (INCR/EXPIRE/GET); TTL tracked, not expired."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        if nx and key in self.ttls:
            return False
        self.ttls[key] = seconds
        return True

    def get(self, key: str) -> int | None:
        return self.store.get(key)


def test_redis_backend_satisfies_protocol():
    assert isinstance(RedisBackend(FakeRedis()), RateLimitBackend)
    assert isinstance(InMemoryBackend(), RateLimitBackend)


def test_shared_redis_enforces_one_quota_across_replicas():
    shared = FakeRedis()
    replica_a = RateLimiter(user_limit=3, global_limit=100, backend=RedisBackend(shared))
    replica_b = RateLimiter(user_limit=3, global_limit=100, backend=RedisBackend(shared))

    # 4 total hits for one user across replicas; the 4th exceeds the limit of 3.
    assert replica_a.check_rate_limit("alice")[0] is True
    assert replica_a.check_rate_limit("alice")[0] is True
    assert replica_b.check_rate_limit("alice")[0] is True
    allowed, quota = replica_b.check_rate_limit("alice")
    assert allowed is False
    assert quota["user_remaining"] == 0


def test_shared_redis_enforces_global_limit_across_replicas():
    shared = FakeRedis()
    replica_a = RateLimiter(user_limit=100, global_limit=3, backend=RedisBackend(shared))
    replica_b = RateLimiter(user_limit=100, global_limit=3, backend=RedisBackend(shared))

    assert replica_a.check_rate_limit("u1")[0] is True
    assert replica_b.check_rate_limit("u2")[0] is True
    assert replica_a.check_rate_limit("u3")[0] is True
    assert replica_b.check_rate_limit("u4")[0] is False  # global limit hit


def test_in_memory_backends_do_not_share_quota():
    # The bug the Redis backend fixes: per-process counters don't share quota.
    replica_a = RateLimiter(user_limit=1, backend=InMemoryBackend())
    replica_b = RateLimiter(user_limit=1, backend=InMemoryBackend())

    assert replica_a.check_rate_limit("bob")[0] is True
    assert replica_b.check_rate_limit("bob")[0] is True


def test_expire_only_set_on_first_hit():
    shared = FakeRedis()
    backend = RedisBackend(shared)
    backend.hit("user:x", window_sec=60)
    shared.ttls["panoptis:ratelimit:user:x"] = 5  # TTL counting down
    backend.hit("user:x", window_sec=60)
    assert shared.ttls["panoptis:ratelimit:user:x"] == 5  # NX: not reset


def test_peek_does_not_increment():
    shared = FakeRedis()
    limiter = RateLimiter(user_limit=5, backend=RedisBackend(shared))
    limiter.check_rate_limit("carol")
    before = limiter.get_remaining_quota("carol")
    after = limiter.get_remaining_quota("carol")
    assert before == after
    assert before["user_remaining"] == 4


def test_default_backend_is_in_memory():
    limiter = RateLimiter()
    assert isinstance(limiter.backend, InMemoryBackend)


def test_from_redis_url_falls_back_when_unreachable(monkeypatch):
    limiter = RateLimiter.from_redis_url("redis://127.0.0.1:1/0", user_limit=42)
    assert isinstance(limiter.backend, InMemoryBackend)
    assert limiter.user_limit == 42


def test_from_redis_url_uses_redis_when_reachable(monkeypatch):
    shared = FakeRedis()
    shared.ping = lambda: True  # type: ignore[attr-defined]

    class _FakeRedisModule:
        class Redis:
            @staticmethod
            def from_url(url: str, decode_responses: bool = False) -> FakeRedis:
                return shared

    monkeypatch.setitem(__import__("sys").modules, "redis", _FakeRedisModule)

    limiter = RateLimiter.from_redis_url("redis://localhost:6379/0")
    assert isinstance(limiter.backend, RedisBackend)


@pytest.mark.parametrize("backend_factory", [InMemoryBackend, lambda: RedisBackend(FakeRedis())])
def test_interface_preserved_for_both_backends(backend_factory):
    limiter = RateLimiter(user_limit=100, global_limit=10000, backend=backend_factory())
    assert limiter.user_limit == 100
    assert limiter.global_limit == 10000
    assert limiter.window_sec == 3600
    allowed, quota = limiter.check_rate_limit("u")
    assert allowed is True
    assert set(quota) == {
        "user_limit",
        "user_remaining",
        "global_limit",
        "global_remaining",
    }
