"""High-concurrency and throughput stress test for observation ingestion."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from panoptis.evidence.model import ObservationDraft, SensorRef
from panoptis.storage.store import Store


@pytest.mark.asyncio
async def test_concurrent_observation_ingestion(tmp_path):
    """Verify that multiple concurrent async tasks can ingest observations without race conditions."""
    db_file = tmp_path / "concurrent_perf.db"
    store = Store(f"sqlite:///{db_file}")

    num_concurrent = 40
    ref = SensorRef("test-runner", "1.0.0", "worker-1")
    start_time = time.perf_counter()

    async def ingest_task(index: int):
        draft = ObservationDraft(
            sensor=ref,
            source_kind="load.test",
            payload={"load_index": index},
            source_seq=index,
            observed_at=datetime.now(UTC),
            declared_ids={"job": f"job-{index}"},
        )
        return await asyncio.to_thread(store.append, [draft])

    # Launch all workers concurrently
    results = await asyncio.gather(*(ingest_task(i) for i in range(num_concurrent)))
    elapsed = time.perf_counter() - start_time

    # Performance expectation: 40 concurrent writes in under 2.5s
    assert elapsed < 2.5, f"Concurrency test took too long: {elapsed:.2f}s"

    total_accepted = sum(len(r.accepted) for r in results)
    assert total_accepted == num_concurrent

    # Verify all records stored
    obs_list = store.observations(source_kinds=["load.test"])
    assert len(obs_list) == num_concurrent


@pytest.mark.asyncio
async def test_concurrent_idempotency_deduplication(tmp_path):
    """Verify that concurrent duplicate submissions safely deduplicate without collision crashes."""
    db_file = tmp_path / "idem_perf.db"
    store = Store(f"sqlite:///{db_file}")

    ref = SensorRef("test-runner", "1.0.0", "worker-idem")
    fixed_time = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC)

    # Identical draft definition (same sensor, sequence, payload, time)
    def make_duplicate_draft():
        return ObservationDraft(
            sensor=ref,
            source_kind="duplicate.test",
            payload={"status": "duplicate_attempt"},
            source_seq=999,
            observed_at=fixed_time,
            declared_ids={},
        )

    num_concurrent = 10

    async def duplicate_ingest():
        return await asyncio.to_thread(store.append, [make_duplicate_draft()])

    results = await asyncio.gather(*(duplicate_ingest() for _ in range(num_concurrent)))

    total_accepted = sum(len(r.accepted) for r in results)
    total_duplicates = sum(len(r.duplicates) for r in results)

    # Exactly 1 accepted across all concurrent tasks, remainder recorded as duplicates
    assert total_accepted == 1
    assert total_duplicates == (num_concurrent - 1)

    obs_list = store.observations(source_kinds=["duplicate.test"])
    assert len(obs_list) == 1
