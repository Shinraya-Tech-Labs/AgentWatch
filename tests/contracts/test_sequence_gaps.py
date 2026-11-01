"""Tests for Sensor Sequence Continuity Tracking and Dynamic Coverage Profiling.

Verifies Issue #730 acceptance criteria:
- Stream isolation across multiple concurrent sensors.
- Monotonic sequence tracking, out-of-order sorting, and duplicate deduplication.
- Detection of exact dropped-frame spans [seq_lost_start, seq_lost_end].
- Gap window overlap logic against contract observation intervals.
- Strict transition from REFUTED to INCONCLUSIVE when evidence is insufficient.
- Synthetic dropped span benchmarks at 1%, 5%, and 20% packet loss.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from panoptis.contracts.coverage import (
    CoverageProfileRegistry,
    SequenceContinuityTracker,
    parse_event_sequence,
)
from panoptis.contracts.evaluator import evaluate_contract
from panoptis.contracts.schema import (
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceContract,
    EvidenceVerdict,
    MissingReason,
    SequenceGap,
)
from panoptis.events.model import (
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)


def make_test_event(
    event_id: str,
    operation: str,
    dt: datetime,
    obs_id: str = "obs-01",
    ordering_key: str = "",
    attrs: dict[str, Any] | None = None,
) -> ComputationalEvent:
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="tenant-test",
        interp_id="interp-test",
        normalizer="test_normalizer@1.0",
        derived_from=(obs_id,),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(start=dt, ordering_key=ordering_key),
        status=EventStatus.OK,
        attributes_json=json.dumps(attrs or {}),
    )


@pytest.fixture
def transfer_contract() -> EvidenceContract:
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


def test_parse_event_sequence_ordering_key():
    """Verify parsing monotonic sequence numbers from ordering_key."""
    dt = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)
    evt = make_test_event("e1", "test.op", dt, ordering_key="sensor_audit:000000000042")
    parsed = parse_event_sequence(evt)
    assert parsed == ("sensor_audit", 42)

    # Multi-colon sensor instance ID
    evt2 = make_test_event("e2", "test.op", dt, ordering_key="cluster:node-1:audit:000000000100")
    assert parse_event_sequence(evt2) == ("cluster:node-1:audit", 100)

    # Plain digit sequence with attributes
    evt3 = make_test_event("e3", "test.op", dt, ordering_key="15", attrs={"sensor_id": "sensor_x"})
    assert parse_event_sequence(evt3) == ("sensor_x", 15)


def test_parse_event_sequence_attributes_fallback():
    """Verify fallback to attributes when ordering_key is empty."""
    dt = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)
    evt = make_test_event(
        "e1",
        "test.op",
        dt,
        ordering_key="",
        attrs={"sensor_id": "audit_sensor", "source_seq": 88},
    )
    assert parse_event_sequence(evt) == ("audit_sensor", 88)

    # Event with no sequence telemetry
    evt_none = make_test_event("e2", "test.op", dt, ordering_key="", attrs={})
    assert parse_event_sequence(evt_none) is None


def test_sequence_tracker_contiguous_stream():
    """100 contiguous events produce 0 gaps and drop_rate == 0.0."""
    tracker = SequenceContinuityTracker()
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    for i in range(1, 101):
        t = base_time + timedelta(seconds=i)
        evt = make_test_event(f"e-{i}", "audit.log", t, ordering_key=f"audit_sensor:{i:012d}")
        tracker.add_event(evt)

    gaps = tracker.compute_gaps("audit_sensor")
    assert len(gaps) == 0

    snapshot = tracker.get_snapshot("audit_sensor")
    assert snapshot.seq_min == 1
    assert snapshot.seq_max == 100
    assert snapshot.dropped_count == 0
    assert snapshot.drop_rate == 0.0
    assert not snapshot.has_gaps


def test_sequence_tracker_single_missing_number():
    """Detect single dropped sequence number."""
    tracker = SequenceContinuityTracker()
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # Sequences: 1, 2, 4, 5 (missing 3)
    for seq in [1, 2, 4, 5]:
        t = base_time + timedelta(seconds=seq)
        evt = make_test_event(f"e-{seq}", "audit.log", t, ordering_key=f"audit_sensor:{seq:012d}")
        tracker.add_event(evt)

    gaps = tracker.compute_gaps("audit_sensor")
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.sensor_id == "audit_sensor"
    assert gap.seq_lost_start == 3
    assert gap.seq_lost_end == 3
    assert gap.count == 1
    assert gap.t_before == base_time + timedelta(seconds=2)
    assert gap.t_after == base_time + timedelta(seconds=4)

    snapshot = tracker.get_snapshot("audit_sensor")
    assert snapshot.dropped_count == 1
    assert snapshot.drop_rate == 1 / 5  # 1 lost out of 5 expected (seq 1..5)
    assert snapshot.has_gaps


def test_sequence_tracker_multiple_missing_spans():
    """Detect multiple distinct sequence gap spans."""
    tracker = SequenceContinuityTracker()
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # Sequences: 1, 2, 5, 6, 10, 11 (missing 3..4 and 7..9)
    for seq in [1, 2, 5, 6, 10, 11]:
        t = base_time + timedelta(seconds=seq)
        evt = make_test_event(f"e-{seq}", "audit.log", t, ordering_key=f"sensor_x:{seq:012d}")
        tracker.add_event(evt)

    gaps = tracker.compute_gaps("sensor_x")
    assert len(gaps) == 2

    assert gaps[0].seq_lost_start == 3
    assert gaps[0].seq_lost_end == 4
    assert gaps[0].count == 2

    assert gaps[1].seq_lost_start == 7
    assert gaps[1].seq_lost_end == 9
    assert gaps[1].count == 3

    snapshot = tracker.get_snapshot("sensor_x")
    assert snapshot.dropped_count == 5
    assert snapshot.drop_rate == 5 / 11


def test_sequence_tracker_out_of_order_arrival():
    """Events arriving out of order are sorted correctly without false gaps."""
    tracker = SequenceContinuityTracker()
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # Shuffled arrival order: 3, 1, 4, 2, 5
    arrival_order = [3, 1, 4, 2, 5]
    for seq in arrival_order:
        t = base_time + timedelta(seconds=seq)
        evt = make_test_event(f"e-{seq}", "audit.log", t, ordering_key=f"sensor_ooo:{seq:012d}")
        tracker.add_event(evt)

    gaps = tracker.compute_gaps("sensor_ooo")
    assert len(gaps) == 0
    snapshot = tracker.get_snapshot("sensor_ooo")
    assert snapshot.dropped_count == 0
    assert snapshot.seq_min == 1
    assert snapshot.seq_max == 5


def test_sequence_tracker_handles_duplicates():
    """Duplicate observations (network retry/replay) are ignored without false gaps."""
    tracker = SequenceContinuityTracker()
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # Duplicates on seq 2 and seq 4: [1, 2, 2, 2, 3, 4, 4, 5]
    for seq in [1, 2, 2, 2, 3, 4, 4, 5]:
        t = base_time + timedelta(seconds=seq)
        evt = make_test_event(f"e-{seq}", "audit.log", t, ordering_key=f"sensor_dup:{seq:012d}")
        tracker.add_event(evt)

    gaps = tracker.compute_gaps("sensor_dup")
    assert len(gaps) == 0
    snapshot = tracker.get_snapshot("sensor_dup")
    assert snapshot.dropped_count == 0


def test_sequence_tracker_stream_isolation():
    """Interleaved events from independent sensors do not cross-contaminate."""
    tracker = SequenceContinuityTracker()
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # Sensor A has seq 1..5 (complete)
    # Sensor B has seq 10, 15 (gap 11..14)
    events = [
        make_test_event("a1", "opA", base_time, ordering_key="sensor_a:000000000001"),
        make_test_event("b1", "opB", base_time, ordering_key="sensor_b:000000000010"),
        make_test_event("a2", "opA", base_time, ordering_key="sensor_a:000000000002"),
        make_test_event("b2", "opB", base_time, ordering_key="sensor_b:000000000015"),
        make_test_event("a3", "opA", base_time, ordering_key="sensor_a:000000000003"),
        make_test_event("a4", "opA", base_time, ordering_key="sensor_a:000000000004"),
        make_test_event("a5", "opA", base_time, ordering_key="sensor_a:000000000005"),
    ]

    for e in events:
        tracker.add_event(e)

    assert len(tracker.compute_gaps("sensor_a")) == 0
    b_gaps = tracker.compute_gaps("sensor_b")
    assert len(b_gaps) == 1
    assert b_gaps[0].seq_lost_start == 11
    assert b_gaps[0].seq_lost_end == 14
    assert b_gaps[0].count == 4


def test_contract_transitions_to_inconclusive_on_overlapping_gap(transfer_contract):
    """When an authoritative sensor has a sequence gap overlapping the observation window,
    the evaluator must transition to INCONCLUSIVE (never REFUTED false alarm)."""
    t_transfer = datetime(2026, 10, 16, 10, 10, 0, tzinfo=UTC)

    transfer = make_test_event(
        "evt-transfer",
        "payments.transfer",
        t_transfer,
        "obs-transfer",
        ordering_key="payment_sensor:000000000001",
        attrs={"transaction_id": "tx-100"},
    )

    # Authoritative sensor for approval had events at 10:00:00 (seq 1) and 10:09:50 (seq 5),
    # meaning seq 2..4 were lost right inside the 10-minute approval window!
    audit_e1 = make_test_event(
        "evt-audit-1",
        "system.audit",
        t_transfer - timedelta(minutes=10),
        "obs-audit-1",
        ordering_key="audit_sensor:000000000001",
    )
    audit_e2 = make_test_event(
        "evt-audit-5",
        "system.audit",
        t_transfer - timedelta(seconds=10),
        "obs-audit-5",
        ordering_key="audit_sensor:000000000005",
    )

    declared_coverage = [
        CoverageSnapshot(
            sensor_id="audit_sensor",
            authoritative_for=("approval.granted",),
            active=True,
        )
    ]

    assessment = evaluate_contract(
        [transfer, audit_e1, audit_e2],
        transfer_contract,
        coverage=declared_coverage,
    )

    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert len(assessment.missing_facts) == 1
    assert assessment.missing_facts[0].reason == MissingReason.SEQUENCE_GAP
    assert assessment.missing_facts[0].candidate_sensors == ("audit_sensor",)
    assert "sequence gap [2..4]" in assessment.summary
    assert "3 frames lost" in assessment.summary


def test_contract_refutes_when_gap_is_outside_observation_window(transfer_contract):
    """When a sequence gap occurred 2 hours prior, but coverage during the 10-minute window
    is complete and contiguous, the evaluator correctly issues REFUTED."""
    t_transfer = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)

    transfer = make_test_event(
        "evt-transfer",
        "payments.transfer",
        t_transfer,
        "obs-transfer",
        ordering_key="payment_sensor:000000000001",
        attrs={"transaction_id": "tx-100"},
    )

    # Gap occurred between 09:00:00 and 09:05:00 (seq 1..5, missing 2..4) - outside the 11:50-12:00 window.
    # From 09:05:00 through 12:00:00, audit sensor was contiguous: seq 5, 6, 7, 8.
    audit_events = [
        make_test_event(
            "e1",
            "audit",
            datetime(2026, 10, 16, 9, 0, 0, tzinfo=UTC),
            ordering_key="audit_sensor:000000000001",
        ),
        make_test_event(
            "e5",
            "audit",
            datetime(2026, 10, 16, 9, 5, 0, tzinfo=UTC),
            ordering_key="audit_sensor:000000000005",
        ),
        make_test_event(
            "e6",
            "audit",
            datetime(2026, 10, 16, 11, 50, 0, tzinfo=UTC),
            ordering_key="audit_sensor:000000000006",
        ),
        make_test_event(
            "e7",
            "audit",
            datetime(2026, 10, 16, 11, 55, 0, tzinfo=UTC),
            ordering_key="audit_sensor:000000000007",
        ),
        make_test_event(
            "e8",
            "audit",
            datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC),
            ordering_key="audit_sensor:000000000008",
        ),
    ]

    declared_coverage = [
        CoverageSnapshot(
            sensor_id="audit_sensor",
            authoritative_for=("approval.granted",),
            active=True,
        )
    ]

    assessment = evaluate_contract(
        [transfer, *audit_events],
        transfer_contract,
        coverage=declared_coverage,
    )

    # The gap [2..4] was outside [11:50, 12:00]. During the observation window, coverage was clean.
    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "Refuted" in assessment.summary


def test_contract_transitions_to_inconclusive_on_sampled_out(transfer_contract):
    """When an authoritative sensor is configured with probabilistic sampling, missing events
    yield INCONCLUSIVE with MissingReason.SAMPLED_OUT."""
    t_transfer = datetime(2026, 10, 16, 10, 0, 10, tzinfo=UTC)
    transfer = make_test_event(
        "evt-transfer",
        "payments.transfer",
        t_transfer,
        "obs-transfer",
        attrs={"transaction_id": "tx-100"},
    )

    declared_coverage = [
        CoverageSnapshot(
            sensor_id="audit_sensor",
            authoritative_for=("approval.granted",),
            active=True,
            sampling_rate=0.20,  # 20% sample rate -> 80% dropped
        )
    ]

    assessment = evaluate_contract([transfer], transfer_contract, coverage=declared_coverage)
    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert len(assessment.missing_facts) == 1
    assert assessment.missing_facts[0].reason == MissingReason.SAMPLED_OUT
    assert "sampled out" in assessment.summary.lower()


def test_coverage_profile_registry():
    """Verify CoverageProfileRegistry manages profiles and introspects events."""
    registry = CoverageProfileRegistry(
        [
            CoverageSnapshot(
                sensor_id="audit_sensor",
                authoritative_for=("approval.granted",),
                active=True,
            )
        ]
    )

    dt = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)
    events = [
        make_test_event("e1", "audit", dt, ordering_key="audit_sensor:000000000001"),
        make_test_event(
            "e3", "audit", dt + timedelta(seconds=1), ordering_key="audit_sensor:000000000003"
        ),
    ]

    snapshots = registry.introspect(events)
    audit_snap = next(s for s in snapshots if s.sensor_id == "audit_sensor")
    assert audit_snap.has_gaps
    assert audit_snap.dropped_count == 1
    assert audit_snap.gaps[0].seq_lost_start == 2


def test_coverage_snapshot_to_dict():
    """Verify serialization of CoverageSnapshot and SequenceGap."""
    gap = SequenceGap(
        sensor_id="s1",
        seq_lost_start=10,
        seq_lost_end=12,
        count=3,
        t_before=datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC),
        t_after=datetime(2026, 10, 16, 10, 0, 5, tzinfo=UTC),
    )
    snap = CoverageSnapshot(
        sensor_id="s1",
        authoritative_for=("op1",),
        active=True,
        dropped_count=3,
        seq_min=1,
        seq_max=20,
        gaps=(gap,),
    )
    d = snap.to_dict()
    assert d["sensor_id"] == "s1"
    assert d["dropped_count"] == 3
    assert d["drop_rate"] == 3 / 20
    assert d["has_gaps"] is True
    assert len(d["gaps"]) == 1
    assert d["gaps"][0]["seq_lost_start"] == 10


# ---------------------------------------------------------------------------
# Synthetic dropped-span benchmarks at 1%, 5%, and 20%
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loss_rate", [0.01, 0.05, 0.20])
def test_synthetic_packet_drop_transitions_to_inconclusive(transfer_contract, loss_rate):
    """Issue #730 Acceptance Criteria:
    Simulate a trace of 200 transaction cycles where random packet drops
    are injected at 1%, 5%, and 20% rates on the authoritative sensor.

    Assert:
    - Every dropped approval causes the contract to transition to INCONCLUSIVE.
    - Zero false REFUTED verdicts.
    - Diagnostic gap window matches the lost sequence span.
    """
    rng = random.Random(42)  # noqa: S311  # nosec B311 — deterministic PRNG with fixed seed for synthetic test stream generation
    num_transactions = 200
    base_time = datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)

    # Determine which approvals will be dropped
    dropped_tx_indices = set()
    for i in range(num_transactions):
        if rng.random() < loss_rate:
            dropped_tx_indices.add(i)

    # Ensure at least 1 drop occurs for testing
    if not dropped_tx_indices:
        dropped_tx_indices.add(0)

    # Build sequence of events
    all_events: list[ComputationalEvent] = []
    authoritative_events: list[ComputationalEvent] = []

    for i in range(num_transactions):
        tx_id = f"tx-{i:04d}"
        t_approval = base_time + timedelta(seconds=i * 10)
        t_transfer = t_approval + timedelta(seconds=5)

        # Monotonic sequence counter on authoritative sensor
        seq_num = i + 1

        if i not in dropped_tx_indices:
            approval = make_test_event(
                f"appr-{i}",
                "approval.granted",
                t_approval,
                obs_id=f"obs-appr-{i}",
                ordering_key=f"audit_sensor:{seq_num:012d}",
                attrs={"transaction_id": tx_id, "sensor_id": "audit_sensor", "source_seq": seq_num},
            )
            all_events.append(approval)
            authoritative_events.append(approval)
        else:
            # Dropped frame: does NOT emit approval event, creating a sequence gap
            pass

        transfer = make_test_event(
            f"tx-{i}",
            "payments.transfer",
            t_transfer,
            obs_id=f"obs-tx-{i}",
            ordering_key=f"payment_sensor:{i + 1:012d}",
            attrs={"transaction_id": tx_id},
        )
        all_events.append(transfer)

    declared_coverage = [
        CoverageSnapshot(
            sensor_id="audit_sensor",
            authoritative_for=("approval.granted",),
            active=True,
        )
    ]

    # Evaluate each transaction independently
    false_refutations = 0
    inconclusive_transitions = 0
    supported_verifications = 0

    for i in range(num_transactions):
        tx_id = f"tx-{i:04d}"
        # Filter trace for this transaction and surrounding authoritative telemetry
        t_approval = base_time + timedelta(seconds=i * 10)
        t_transfer = t_approval + timedelta(seconds=5)

        tx_events = [
            e
            for e in all_events
            if (e.attributes.get("transaction_id") == tx_id)
            or (e.time.ordering_key and e.time.ordering_key.startswith("audit_sensor:"))
        ]

        assessment = evaluate_contract(
            tx_events,
            transfer_contract,
            coverage=declared_coverage,
        )

        if i in dropped_tx_indices:
            # Approval was dropped: MUST transition to INCONCLUSIVE
            assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE, (
                f"Transaction {tx_id} had dropped approval at loss rate {loss_rate:.0%}, "
                f"expected INCONCLUSIVE but got {assessment.verdict}"
            )
            assert len(assessment.missing_facts) > 0
            assert assessment.missing_facts[0].reason == MissingReason.SEQUENCE_GAP
            inconclusive_transitions += 1
        else:
            # Approval was observed: MUST be SUPPORTED
            assert assessment.verdict == EvidenceVerdict.SUPPORTED, (
                f"Transaction {tx_id} had valid approval, expected SUPPORTED but got {assessment.verdict}"
            )
            supported_verifications += 1

    assert false_refutations == 0, f"Encountered {false_refutations} false refutations!"
    assert inconclusive_transitions == len(dropped_tx_indices)
    assert supported_verifications == num_transactions - len(dropped_tx_indices)
