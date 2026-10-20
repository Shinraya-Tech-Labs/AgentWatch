"""Performance benchmark for Incremental Contract Evaluation (Issue #731).

Generates 10,000 synthetic ComputationalEvents, streams them through
IncrementalContractEvaluator, and measures throughput and per-event latency.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from agentwatch.contracts.incremental import IncrementalContractEvaluator
from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    EvidenceContract,
    EvidenceVerdict,
)
from agentwatch.events.model import (
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)


def generate_synthetic_events(count: int = 10_000) -> list[ComputationalEvent]:
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=timezone.utc)
    events: list[ComputationalEvent] = []

    # 5,000 pairs: approval.granted -> payments.transfer
    pairs = count // 2
    for i in range(pairs):
        t1 = base_time + timedelta(milliseconds=i * 20)
        t2 = t1 + timedelta(milliseconds=5)

        tx_id = f"tx-{i}"
        e1 = ComputationalEvent(
            event_id=f"evt-req-{i}",
            tenant_id="perf-tenant",
            interp_id="interp-perf",
            normalizer="bench@1.0",
            derived_from=(f"obs-req-{i}",),
            kind=EventKind.TOOL_INVOCATION,
            operation="approval.granted",
            time=TemporalInfo(start=t1),
            status=EventStatus.OK,
            attributes_json=json.dumps({"transaction_id": tx_id}),
        )
        e2 = ComputationalEvent(
            event_id=f"evt-tgt-{i}",
            tenant_id="perf-tenant",
            interp_id="interp-perf",
            normalizer="bench@1.0",
            derived_from=(f"obs-tgt-{i}",),
            kind=EventKind.TOOL_INVOCATION,
            operation="payments.transfer",
            time=TemporalInfo(start=t2),
            status=EventStatus.OK,
            attributes_json=json.dumps({"transaction_id": tx_id}),
        )
        events.append(e1)
        events.append(e2)

    return events


def test_synthetic_10k_events_benchmark():
    contract = EvidenceContract(
        name="bench-contract",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="payments.transfer",
            required_operation="approval.granted",
            max_window_seconds=60.0,
            join_on=("transaction_id",),
        ),
    )

    events = generate_synthetic_events(10_000)
    assert len(events) == 10_000

    evaluator = IncrementalContractEvaluator(contract, max_buffer_size=10_000)

    start_time = time.perf_counter()
    for ev in events:
        evaluator.process_event(ev)
    assessment = evaluator.finalize()
    total_time = time.perf_counter() - start_time

    events_per_sec = len(events) / total_time
    avg_latency_ms = (total_time / len(events)) * 1000.0

    print(
        f"\n[Benchmark] Processed {len(events)} events in {total_time:.3f}s: "
        f"{events_per_sec:.1f} events/sec, {avg_latency_ms:.4f} ms/event"
    )

    # Invariants
    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert evaluator.events_evaluated == 10_000
    assert not evaluator.has_overflow
    # Latency sub-10ms per event
    assert avg_latency_ms < 10.0
