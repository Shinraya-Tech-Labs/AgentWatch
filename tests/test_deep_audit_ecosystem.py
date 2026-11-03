"""Tests verifying deep ecosystem audit remediations (AUDIT-09 through AUDIT-14)."""

import concurrent.futures
import time

from panoptis.api.middleware.rate_limiter import InMemoryBackend
from panoptis.evidence.model import ObservationDraft, SensorRef
from panoptis.models.tenant import _verify_api_key
from panoptis.security.webhook_signing import WebhookIdempotencyTracker
from panoptis.storage.store import Store


def test_audit09_malformed_key_hash_fails_closed():
    """Verify that corrupt or non-delimited key hash strings fail closed without crashing."""
    assert not _verify_api_key("some_key", "malformed_hash_without_colon")
    assert not _verify_api_key("some_key", "")
    assert not _verify_api_key("some_key", None)  # type: ignore


def test_audit10_get_observation_tenant_isolation(tmp_path):
    """Verify get_observation respects tenant_id boundary."""
    db_path = tmp_path / "test_iso.db"
    store = Store(f"sqlite:///{db_path}")
    store.init()

    # Append observation under tenant_A
    draft = ObservationDraft(
        sensor=SensorRef("agent", "1.0", "inst"),
        source_kind="agent",
        payload={"action": "read_secret"},
        tenant_id="tenant_A",
    )
    oid_a = store.append([draft]).accepted[0]

    # Verify query without tenant_id finds it
    assert store.get_observation(oid_a) is not None

    # Verify query with matching tenant_id finds it
    assert store.get_observation(oid_a, tenant_id="tenant_A") is not None

    # Verify query with cross-tenant tenant_id returns None (IDOR blocked)
    assert store.get_observation(oid_a, tenant_id="tenant_B") is None


def test_audit11_webhook_idempotency_concurrency():
    """Verify thread-safety of WebhookIdempotencyTracker under concurrent access."""
    tracker = WebhookIdempotencyTracker(max_entries=50, default_ttl_seconds=60)

    def worker(idx: int):
        event_id = f"item_{idx % 20}"
        tracker.record_event(event_id, provider="custom_provider")
        tracker.is_duplicate(event_id, provider="custom_provider")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, i) for i in range(200)]
        concurrent.futures.wait(futures)
        for f in futures:
            assert f.exception() is None


def test_audit12_in_memory_rate_limiter_bounded_eviction():
    """Verify InMemoryBackend evicts stale keys when exceeding max_buckets."""
    backend = InMemoryBackend(max_buckets=10)

    # Insert 15 expired keys
    now = time.time() - 100
    for i in range(15):
        backend._buckets[f"user_{i}"] = {"count": 1, "start": now}

    assert len(backend._buckets) == 15

    # Access a new key with window_sec=60
    backend.hit("new_active_user", window_sec=60)

    # Stale keys must have been pruned
    assert len(backend._buckets) <= 11
    assert "new_active_user" in backend._buckets
