"""Comprehensive tests for Incremental Sliding-Window Stream Processor (StreamingContractEvaluator).

Verifies Issue #786 acceptance criteria:
1. Functional Equivalence: Stream-by-event feeding produces identical verdicts as batch evaluate_contract().
2. Out-of-Order Ingestion: Out-of-order events within lateness tolerance fulfill obligations without spurious REFUTED.
3. Open-World Gap Handling: SequenceGap in SequenceContinuityTracker covering deadline resolves to INCONCLUSIVE.
4. Memory Boundness: Feeding 100,000 events maintains flat retained event footprint.
5. Latency Benchmark: Median per-event ingestion latency < 0.5ms with 50 active contracts.
6. PanoptiGuard Integration: Property and configuration wiring.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentwatch.contracts.coverage import SequenceContinuityTracker
from agentwatch.contracts.evaluator import evaluate_contract
from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceContract,
    EvidenceVerdict,
    MissingReason,
    SequenceGap,
)
from agentwatch.contracts.streaming import (
    ObligationState,
    StreamingContractEvaluator,
)
from agentwatch.events.model import (
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)
from panopti.guard import PanoptiGuard


def make_test_event(
    event_id: str,
    operation: str,
    dt: datetime,
    obs_id: str = "obs-01",
    attrs: dict | None = None,
    ordering_key: str = "",
) -> ComputationalEvent:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="tenant-alpha",
        interp_id="interp-01",
        normalizer="test_normalizer@1.0",
        derived_from=(obs_id,),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(start=dt, uncertainty_ms=0.0, ordering_key=ordering_key),
        status=EventStatus.OK,
        attributes_json=json.dumps(attrs or {}),
    )


@pytest.fixture
def payment_contract() -> EvidenceContract:
    return EvidenceContract(
        name="payment-requires-approval",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="payments.transfer",
            required_operation="approval.granted",
            max_window_seconds=600.0,
            join_on=("transaction_id",),
        ),
    )


@pytest.fixture
def email_contract() -> EvidenceContract:
    return EvidenceContract(
        name="order-requires-email",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.EVENTUALLY_WITHIN,
            target_operation="order.placed",
            required_operation="email.sent",
            max_window_seconds=30.0,
            join_on=("order_id",),
        ),
    )


def test_functional_equivalence_supported(payment_contract):
    """Streaming ingestion produces identical SUPPORTED verdict and witnesses as batch evaluation."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=10)

    evt_approval = make_test_event(
        "e1", "approval.granted", t0, "obs-app", {"transaction_id": "tx-101"}
    )
    evt_transfer = make_test_event(
        "e2", "payments.transfer", t1, "obs-tx", {"transaction_id": "tx-101"}
    )

    # Batch verdict
    batch_assessment = evaluate_contract([evt_approval, evt_transfer], payment_contract)

    # Streaming evaluator
    stream_evaluator = StreamingContractEvaluator(contracts=[payment_contract])
    d1 = stream_evaluator.ingest(evt_approval)
    assert len(d1) == 0

    d2 = stream_evaluator.ingest(evt_transfer)
    assert len(d2) == 1
    assert d2[0].verdict == EvidenceVerdict.SUPPORTED

    stream_assessment = stream_evaluator.get_assessment(payment_contract.name)
    assert stream_assessment is not None
    assert stream_assessment.verdict == batch_assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-app" in stream_assessment.support_obs_ids
    assert "obs-tx" in stream_assessment.support_obs_ids


def test_functional_equivalence_refuted_with_authoritative_sensor(payment_contract):
    """Streaming ingestion produces identical REFUTED verdict when authoritative sensor attests 0 drops."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    evt_transfer = make_test_event(
        "e2", "payments.transfer", t0, "obs-tx", {"transaction_id": "tx-101"}
    )

    coverage = [
        CoverageSnapshot(
            sensor_id="audit_ledger",
            authoritative_for=("approval.granted",),
            active=True,
            dropped_count=0,
        )
    ]

    batch_assessment = evaluate_contract([evt_transfer], payment_contract, coverage=coverage)

    stream_evaluator = StreamingContractEvaluator(contracts=[payment_contract], coverage=coverage)
    stream_evaluator.ingest(evt_transfer)
    stream_assessment = stream_evaluator.get_assessment(payment_contract.name)

    assert stream_assessment is not None
    assert stream_assessment.verdict == batch_assessment.verdict == EvidenceVerdict.REFUTED
    assert "obs-tx" in stream_assessment.refute_obs_ids


def test_functional_equivalence_inconclusive_missing_sensor(payment_contract):
    """Target observed with no authoritative sensor attesting absence produces identical INCONCLUSIVE."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    evt_transfer = make_test_event(
        "e2", "payments.transfer", t0, "obs-tx", {"transaction_id": "tx-101"}
    )

    batch_assessment = evaluate_contract([evt_transfer], payment_contract, coverage=[])

    stream_evaluator = StreamingContractEvaluator(contracts=[payment_contract], coverage=[])
    stream_evaluator.ingest(evt_transfer)
    stream_assessment = stream_evaluator.get_assessment(payment_contract.name)

    assert stream_assessment is not None
    assert stream_assessment.verdict == batch_assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert len(stream_assessment.missing_facts) == 1
    assert stream_assessment.missing_facts[0].reason == MissingReason.SENSOR_DISABLED


def test_functional_equivalence_vacuous_satisfaction(payment_contract):
    """Zero target operations produces identical vacuous SUPPORTED assessment."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    unrelated_evt = make_test_event("e0", "telemetry.ping", t0, "obs-ping")

    batch_assessment = evaluate_contract([unrelated_evt], payment_contract)

    stream_evaluator = StreamingContractEvaluator(contracts=[payment_contract])
    stream_evaluator.ingest(unrelated_evt)
    stream_assessment = stream_evaluator.get_assessment(payment_contract.name)

    assert stream_assessment is not None
    assert stream_assessment.verdict == batch_assessment.verdict == EvidenceVerdict.SUPPORTED


def test_out_of_order_ingestion_within_lateness_tolerance(payment_contract):
    """Out-of-order event arriving within lateness tolerance fulfills pending obligation without false alarms."""
    stream_evaluator = StreamingContractEvaluator(
        contracts=[payment_contract],
        lateness_tolerance_seconds=5.0,
    )

    t_transfer = datetime(2026, 10, 20, 10, 0, 2, tzinfo=UTC)
    t_approval = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)

    # 1. Target arrives FIRST (out-of-order)
    evt_transfer = make_test_event(
        "e2", "payments.transfer", t_transfer, "obs-tx", {"transaction_id": "tx-42"}
    )
    d1 = stream_evaluator.ingest(evt_transfer)
    assert len(d1) == 0  # Preceding event not yet seen; pending obligation registered
    assert stream_evaluator.pending_count == 1

    # 2. Required event arrives SECOND (within lateness tolerance)
    evt_approval = make_test_event(
        "e1", "approval.granted", t_approval, "obs-app", {"transaction_id": "tx-42"}
    )
    d2 = stream_evaluator.ingest(evt_approval)

    assert len(d2) == 1
    assert d2[0].previous_state == ObligationState.PENDING
    assert d2[0].current_state == ObligationState.SUPPORTED
    assert d2[0].verdict == EvidenceVerdict.SUPPORTED
    assert stream_evaluator.pending_count == 0

    assessment = stream_evaluator.get_assessment(payment_contract.name)
    assert assessment.verdict == EvidenceVerdict.SUPPORTED


def test_eventually_within_lifecycle(email_contract):
    """EVENTUALLY_WITHIN registers pending obligation and resolves upon subsequent matching event."""
    stream_evaluator = StreamingContractEvaluator(contracts=[email_contract])

    t_order = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    evt_order = make_test_event(
        "e-order", "order.placed", t_order, "obs-ord", {"order_id": "ord-77"}
    )

    deltas_1 = stream_evaluator.ingest(evt_order)
    assert len(deltas_1) == 0
    assert stream_evaluator.pending_count == 1

    # Follow-up event within 15 seconds (contract window is 30s)
    t_email = t_order + timedelta(seconds=15)
    evt_email = make_test_event("e-email", "email.sent", t_email, "obs-em", {"order_id": "ord-77"})

    deltas_2 = stream_evaluator.ingest(evt_email)
    assert len(deltas_2) == 1
    assert deltas_2[0].verdict == EvidenceVerdict.SUPPORTED
    assert stream_evaluator.pending_count == 0


def test_open_world_gap_handling(email_contract):
    """When a SequenceGap overlaps the observation window, expired obligation strictly resolves to INCONCLUSIVE."""
    tracker = SequenceContinuityTracker()
    coverage = [
        CoverageSnapshot(
            sensor_id="email_sensor",
            authoritative_for=("email.sent",),
            active=True,
            gaps=(
                SequenceGap(
                    sensor_id="email_sensor",
                    seq_lost_start=10,
                    seq_lost_end=15,
                    count=6,
                    t_before=datetime(2026, 10, 20, 10, 0, 5, tzinfo=UTC),
                    t_after=datetime(2026, 10, 20, 10, 0, 25, tzinfo=UTC),
                ),
            ),
        )
    ]

    stream_evaluator = StreamingContractEvaluator(
        contracts=[email_contract],
        coverage=coverage,
        continuity_tracker=tracker,
    )

    t_order = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    evt_order = make_test_event(
        "e-order", "order.placed", t_order, "obs-ord", {"order_id": "ord-88"}
    )
    stream_evaluator.ingest(evt_order)

    # Advance stream past the 30s window without email event
    t_future = t_order + timedelta(seconds=45)
    evt_tick = make_test_event("e-tick", "heartbeat", t_future, "obs-tick")
    deltas = stream_evaluator.ingest(evt_tick)

    # Must resolve to INCONCLUSIVE with MissingReason.SEQUENCE_GAP, never spurious REFUTED!
    inconclusive_deltas = [d for d in deltas if d.verdict == EvidenceVerdict.INCONCLUSIVE]
    assert len(inconclusive_deltas) == 1
    assert inconclusive_deltas[0].missing_facts[0].reason == MissingReason.SEQUENCE_GAP

    assessment = stream_evaluator.get_assessment(email_contract.name)
    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE


def test_memory_boundness_stress():
    """Stress test: feeding 100,000 events over a sliding window keeps retained footprint strictly bounded."""
    contract = EvidenceContract(
        name="sliding-test",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.EVENTUALLY_WITHIN,
            target_operation="step.a",
            required_operation="step.b",
            max_window_seconds=10.0,
        ),
    )

    stream_evaluator = StreamingContractEvaluator(
        contracts=[contract],
        max_window_seconds=10.0,
        lateness_tolerance_seconds=2.0,
    )

    t_start = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    total_events = 100_000

    # Stream 100,000 events advancing 0.1s each = 10,000 seconds of trace
    for i in range(total_events):
        t_curr = t_start + timedelta(seconds=i * 0.1)
        evt = make_test_event(
            f"evt-{i}",
            "telemetry.tick",
            t_curr,
            f"obs-{i}",
        )
        stream_evaluator.ingest(evt)

    # Sliding window is 10s + 2s lateness = 12s (~120 events). Retained events must be bounded << total_events
    retained = stream_evaluator.retained_count
    assert retained < 500, (
        f"Expected bounded retention (<500 events), got {retained} out of {total_events}"
    )


def test_latency_benchmark_with_50_active_contracts():
    """Benchmark: median per-event latency must be < 0.5ms with 50 active contracts."""
    contracts = [
        EvidenceContract(
            name=f"contract-{i}",
            version="1.0",
            rule=ContractRule(
                operator=ContractOperator.PRECEDING_EVENT
                if i % 2 == 0
                else ContractOperator.EVENTUALLY_WITHIN,
                target_operation=f"target.op.{i}",
                required_operation=f"required.op.{i}",
                max_window_seconds=30.0,
                join_on=("session_id",),
            ),
        )
        for i in range(50)
    ]

    stream_evaluator = StreamingContractEvaluator(contracts=contracts)
    t_start = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)

    latencies: list[float] = []
    # Ingest 1,000 events across the 50 contract operations
    for i in range(1000):
        t_curr = t_start + timedelta(milliseconds=i * 50)
        c_idx = i % 50
        op = f"target.op.{c_idx}" if (i // 50) % 2 == 0 else f"required.op.{c_idx}"
        evt = make_test_event(
            f"bench-{i}",
            op,
            t_curr,
            f"obs-{i}",
            attrs={"session_id": f"sess-{i % 10}"},
        )
        t0 = time.perf_counter()
        stream_evaluator.ingest(evt)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)  # ms

    latencies.sort()
    median_latency_ms = latencies[len(latencies) // 2]
    assert median_latency_ms < 0.5, f"Median latency {median_latency_ms:.3f}ms exceeds target 0.5ms"


def test_panopti_guard_streaming_evaluator_integration():
    """Verify PanoptiGuard attaches and exposes streaming_evaluator."""
    stream_evaluator = StreamingContractEvaluator()
    guard = PanoptiGuard(streaming_evaluator=stream_evaluator)
    assert guard.streaming_evaluator is stream_evaluator

    guard2 = PanoptiGuard()
    assert guard2.streaming_evaluator is None
    guard2.streaming_evaluator = stream_evaluator
    assert guard2.streaming_evaluator is stream_evaluator
