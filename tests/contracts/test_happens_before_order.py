"""Tests for Happens-Before Partial Order Graph, Interval Logic, and Watermarking.

Verifies Issue #729 Phase 1 requirements:
- TimestampInterval derivation, boundary handling, and strict precedence.
- HappensBeforeGraph: direct links, transitive reachability, sequence monotonicity.
- Stream isolation: independent streams remain concurrent without explicit links.
- Cycle and contradiction detection: mutual links, sequence-link contradictions.
- EventTimeWatermarkBuffer: watermark advancement, late arrivals, eviction, and target tracking.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from panoptis.contracts.order import (
    CausalCycleError,
    CausalRelation,
    EventTimeWatermarkBuffer,
    HappensBeforeGraph,
    IngestionStatus,
    TimestampInterval,
)
from panoptis.contracts.schema import (
    ContractOperator,
    ContractRule,
    EvidenceContract,
    EvidenceVerdict,
)
from panoptis.events.model import (
    ComputationalEvent,
    DeclaredLink,
    EventKind,
    EventStatus,
    TemporalInfo,
)


def make_test_event(
    event_id: str,
    operation: str,
    dt: datetime | None,
    dt_end: datetime | None = None,
    uncertainty_ms: float | None = None,
    ordering_key: str = "",
    source_ids: tuple[tuple[str, str], ...] = (),
    parents: tuple[DeclaredLink, ...] = (),
    attrs: dict[str, Any] | None = None,
) -> ComputationalEvent:
    """Helper creating a ComputationalEvent for ordering tests."""
    time_info = (
        TemporalInfo(
            start=dt,
            end=dt_end,
            uncertainty_ms=uncertainty_ms,
            ordering_key=ordering_key,
        )
        if dt is not None
        else TemporalInfo(start=None)
    )
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="tenant-test",
        interp_id="interp-test",
        normalizer="test_normalizer@1.0",
        derived_from=(f"obs-{event_id}",),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=time_info,
        status=EventStatus.OK,
        source_ids=source_ids,
        parents=parents,
        attributes_json=json.dumps(attrs or {}),
    )


# ==============================================================================
# 1. TimestampInterval Tests
# ==============================================================================


def test_timestamp_interval_from_event():
    """Derive interval from point and duration events with uncertainty."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    e_point = make_test_event("e1", "op", t0, uncertainty_ms=50.0)

    interval = TimestampInterval.from_event(e_point)
    assert interval is not None
    assert interval.uncertainty_ms == 50.0
    assert interval.start_min == t0 - timedelta(milliseconds=50)
    assert interval.start_max == t0 + timedelta(milliseconds=50)
    assert interval.effective_end_max == t0 + timedelta(milliseconds=50)

    # Event with duration
    t1 = t0 + timedelta(seconds=2)
    e_duration = make_test_event("e2", "op", t0, dt_end=t1, uncertainty_ms=100.0)
    inv_dur = TimestampInterval.from_event(e_duration)
    assert inv_dur is not None
    assert inv_dur.effective_start_min == t0 - timedelta(milliseconds=100)
    assert inv_dur.effective_end_max == t1 + timedelta(milliseconds=100)


def test_timestamp_interval_missing_and_negative_uncertainty():
    """Handle missing start time and negative/zero uncertainty."""
    e_missing = make_test_event("e_miss", "op", None)
    assert TimestampInterval.from_event(e_missing) is None

    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # Negative uncertainty should be clamped to 0.0
    e_neg = make_test_event("e_neg", "op", t0, uncertainty_ms=-25.0)
    inv_neg = TimestampInterval.from_event(e_neg)
    assert inv_neg is not None
    assert inv_neg.uncertainty_ms == 0.0
    assert inv_neg.start_min == t0
    assert inv_neg.start_max == t0


def test_strict_interval_precedence_and_overlap():
    """Verify strict precedence (a_max < b_min) and overlapping boundaries."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # inv_a: [09:59:59.900 .. 10:00:00.100] (uncertainty 100ms)
    inv_a = TimestampInterval(
        start_min=t0 - timedelta(milliseconds=100),
        start_max=t0 + timedelta(milliseconds=100),
        uncertainty_ms=100.0,
    )

    # inv_b strictly after: [10:00:00.200 .. 10:00:00.400]
    t_b = t0 + timedelta(milliseconds=300)
    inv_b = TimestampInterval(
        start_min=t_b - timedelta(milliseconds=100),
        start_max=t_b + timedelta(milliseconds=100),
        uncertainty_ms=100.0,
    )
    assert inv_a.precedes(inv_b)
    assert not inv_b.precedes(inv_a)
    assert not inv_a.overlaps(inv_b)

    # inv_c overlapping: [10:00:00.050 .. 10:00:00.250]
    t_c = t0 + timedelta(milliseconds=150)
    inv_c = TimestampInterval(
        start_min=t_c - timedelta(milliseconds=100),
        start_max=t_c + timedelta(milliseconds=100),
        uncertainty_ms=100.0,
    )
    assert not inv_a.precedes(inv_c)
    assert not inv_c.precedes(inv_a)
    assert inv_a.overlaps(inv_c)

    # Boundary touch: [10:00:00.100 .. 10:00:00.300] (inv_a.max == inv_d.min)
    # Under strict precedence (<), boundary touch is treated as overlapping
    inv_d = TimestampInterval(
        start_min=t0 + timedelta(milliseconds=100),
        start_max=t0 + timedelta(milliseconds=300),
        uncertainty_ms=100.0,
    )
    assert not inv_a.precedes(inv_d)
    assert inv_a.overlaps(inv_d)


# ==============================================================================
# 2. HappensBeforeGraph Tests
# ==============================================================================


def test_causal_graph_direct_supported_links():
    """Verify all supported link types establish antecedent ≺ descendant."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    e_parent = make_test_event("p1", "parent.op", t0, source_ids=(("span", "span-root"),))

    for relation in ["parent", "follows", "responds_to", "caused_by", "depends_on"]:
        e_child = make_test_event(
            f"c_{relation}",
            "child.op",
            t0 + timedelta(seconds=1),
            parents=(DeclaredLink(relation, "span", "span-root"),),
        )
        graph = HappensBeforeGraph([e_parent, e_child])
        assert graph.happens_before("p1", f"c_{relation}"), f"Failed for relation {relation}"
        assert not graph.happens_before(f"c_{relation}", "p1")
        assert graph.relate("p1", f"c_{relation}") == CausalRelation.BEFORE
        assert graph.relate(f"c_{relation}", "p1") == CausalRelation.AFTER


def test_causal_graph_transitive_reachability():
    """Verify multi-hop transitive reachability: A ≺ B ≺ C ≺ D."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    ea = make_test_event("ea", "opA", t0, source_ids=(("otel", "trace/span-a"),))
    eb = make_test_event(
        "eb",
        "opB",
        t0 + timedelta(seconds=1),
        source_ids=(("otel", "trace/span-b"),),
        parents=(DeclaredLink("parent", "otel", "trace/span-a"),),
    )
    ec = make_test_event(
        "ec",
        "opC",
        t0 + timedelta(seconds=2),
        source_ids=(("otel", "trace/span-c"),),
        parents=(DeclaredLink("follows", "otel", "trace/span-b"),),
    )
    ed = make_test_event(
        "ed",
        "opD",
        t0 + timedelta(seconds=3),
        parents=(DeclaredLink("depends_on", "otel", "trace/span-c"),),
    )

    graph = HappensBeforeGraph([ea, eb, ec, ed])
    assert not graph.has_cycle()
    assert graph.happens_before("ea", "eb")
    assert graph.happens_before("ea", "ec")
    assert graph.happens_before("ea", "ed")
    assert graph.happens_before("eb", "ed")
    assert graph.relate("ea", "ed") == CausalRelation.BEFORE
    assert graph.relate("ed", "ea") == CausalRelation.AFTER


def test_causal_graph_sequence_ordering_and_isolation():
    """Verify intra-stream sequence monotonicity and cross-stream isolation."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # Stream S1: seq 10, 20, 30
    s1_10 = make_test_event("s1_10", "op", t0, ordering_key="s1:000000000010")
    s1_20 = make_test_event(
        "s1_20", "op", t0 + timedelta(seconds=1), ordering_key="s1:000000000020"
    )
    s1_30 = make_test_event(
        "s1_30", "op", t0 + timedelta(seconds=2), ordering_key="s1:000000000030"
    )

    # Stream S2: seq 5, 15 (independent agent)
    s2_05 = make_test_event("s2_05", "op", t0, ordering_key="s2:000000000005")
    s2_15 = make_test_event(
        "s2_15", "op", t0 + timedelta(seconds=1), ordering_key="s2:000000000015"
    )

    graph = HappensBeforeGraph([s1_10, s1_20, s1_30, s2_05, s2_15])

    # Intra-stream ordering
    assert graph.happens_before("s1_10", "s1_20")
    assert graph.happens_before("s1_10", "s1_30")
    assert graph.happens_before("s2_05", "s2_15")

    # Stream isolation: unlinked cross-stream events are CONCURRENT
    assert not graph.happens_before("s1_10", "s2_05")
    assert not graph.happens_before("s2_05", "s1_10")
    assert graph.relate("s1_10", "s2_05") == CausalRelation.CONCURRENT
    assert graph.relate("s1_30", "s2_15") == CausalRelation.CONCURRENT


def test_causal_graph_combined_sequence_and_link():
    """Verify cross-stream causality when an explicit link bridges two streams."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # Agent 1 emits e1_1 then e1_2
    e1_1 = make_test_event(
        "e1_1",
        "req",
        t0,
        ordering_key="a1:000000000001",
        source_ids=(("msg", "req-1"),),
    )
    e1_2 = make_test_event(
        "e1_2", "audit", t0 + timedelta(seconds=1), ordering_key="a1:000000000002"
    )

    # Agent 2 responds to e1_1, then emits e2_2
    e2_1 = make_test_event(
        "e2_1",
        "resp",
        t0 + timedelta(seconds=2),
        ordering_key="a2:000000000001",
        parents=(DeclaredLink("responds_to", "msg", "req-1"),),
    )
    e2_2 = make_test_event(
        "e2_2", "commit", t0 + timedelta(seconds=3), ordering_key="a2:000000000002"
    )

    graph = HappensBeforeGraph([e1_1, e1_2, e2_1, e2_2])
    # e1_1 ≺ e2_1 (via responds_to link)
    # e2_1 ≺ e2_2 (via a2 sequence)
    # Transitive: e1_1 ≺ e2_2
    assert graph.happens_before("e1_1", "e2_1")
    assert graph.happens_before("e1_1", "e2_2")
    # e1_2 and e2_2 are concurrent (e1_2 had no link to Agent 2)
    assert graph.relate("e1_2", "e2_1") == CausalRelation.CONCURRENT


def test_causal_graph_cycle_and_contradiction_detection():
    """Detect cyclic links and sequence contradictions explicitly."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # Mutual parent cycle
    e1 = make_test_event(
        "c_e1",
        "op",
        t0,
        source_ids=(("k", "v1"),),
        parents=(DeclaredLink("parent", "k", "v2"),),
    )
    e2 = make_test_event(
        "c_e2",
        "op",
        t0,
        source_ids=(("k", "v2"),),
        parents=(DeclaredLink("parent", "k", "v1"),),
    )

    graph = HappensBeforeGraph([e1, e2])
    assert graph.has_cycle()
    assert "c_e1" in graph.cycle_nodes
    assert "c_e2" in graph.cycle_nodes
    assert graph.relate("c_e1", "c_e2") == CausalRelation.CONTRADICTORY

    with pytest.raises(CausalCycleError):
        graph.check_acyclic()


def test_causal_graph_duplicate_events_and_repeated_links():
    """Verify idempotent graph construction under duplicate events and redundant links."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    e1 = make_test_event("e1", "op", t0, source_ids=(("k", "v1"),))
    # Redundant parent links
    e2 = make_test_event(
        "e2",
        "op",
        t0 + timedelta(seconds=1),
        parents=(
            DeclaredLink("parent", "k", "v1"),
            DeclaredLink("parent", "k", "v1"),
        ),
    )

    # Ingest list containing duplicates of e1
    graph = HappensBeforeGraph([e1, e1, e2])
    assert not graph.has_cycle()
    assert graph.happens_before("e1", "e2")
    assert graph.relate("e1", "e2") == CausalRelation.BEFORE


# ==============================================================================
# 3. EventTimeWatermarkBuffer Tests
# ==============================================================================


def test_watermark_advancement_and_classification():
    """Verify watermark tracks max_time - lateness_bound and classifies arrivals."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    buf = EventTimeWatermarkBuffer(lateness_bound_seconds=5.0, max_window_seconds=60.0)

    # 1. First event at t=10:00:00 -> Watermark = 09:59:55
    res1 = buf.ingest(make_test_event("e1", "op", t0))
    assert res1.status == IngestionStatus.ACCEPTED
    assert not res1.is_late
    assert buf.watermark == t0 - timedelta(seconds=5)

    # 2. Advance time: event at t=10:00:10 -> Watermark = 10:00:05
    res2 = buf.ingest(make_test_event("e2", "op", t0 + timedelta(seconds=10)))
    assert res2.status == IngestionStatus.ACCEPTED
    assert buf.watermark == t0 + timedelta(seconds=5)

    # 3. Late arrival: event at t=10:00:02 (behind watermark 10:00:05, but within retention)
    res_late = buf.ingest(make_test_event("e_late", "op", t0 + timedelta(seconds=2)))
    assert res_late.status == IngestionStatus.LATE
    assert res_late.is_late

    # 4. Duplicate event
    res_dup = buf.ingest(make_test_event("e1", "op", t0))
    assert res_dup.status == IngestionStatus.DUPLICATE

    # 5. Event older than retention horizon (watermark - max_window = 10:00:05 - 60s = 09:59:05)
    t_ancient = t0 - timedelta(seconds=70)  # 09:58:50
    res_ancient = buf.ingest(make_test_event("e_ancient", "op", t_ancient))
    assert res_ancient.status == IngestionStatus.DROPPED_TOO_OLD


def test_watermark_buffer_affected_targets_and_bounded_eviction():
    """Verify identifying affected target windows and bounded memory eviction."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    buf = EventTimeWatermarkBuffer(lateness_bound_seconds=2.0, max_window_seconds=10.0)

    # Target event at t=10:00:05
    t_target = t0 + timedelta(seconds=5)
    target = make_test_event("target_1", "payments.transfer", t_target)
    buf.ingest(target, target_operations=("payments.transfer",))

    # Late-arriving approval event at t=10:00:01 (within 10s window of target)
    approval = make_test_event("appr_late", "approval.granted", t0 + timedelta(seconds=1))
    res_appr = buf.ingest(approval, target_operations=("payments.transfer",))
    assert "target_1" in res_appr.affected_target_ids

    # Unrelated event far in the future advances watermark and triggers eviction
    t_future = t0 + timedelta(seconds=30)
    buf.ingest(make_test_event("future_1", "heartbeat", t_future))
    # Horizon is now (30 - 2) - 10 = 18s after t0. Events at t0+1 and t0+5 are expired!
    evicted = buf.evict_expired()
    assert evicted >= 2
    assert "target_1" not in [e.event_id for e in buf.retained_events]
    assert "appr_late" not in [e.event_id for e in buf.retained_events]
    assert buf.evicted_count == evicted


# ==============================================================================
# 4. Evaluator Integration Tests with Happens-Before & Intervals
# ==============================================================================


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


def test_evaluator_causal_order_overrides_wall_clock_inversion(transfer_contract):
    """Causal link overrides inverted wall-clock timestamps (clock skew) within window."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # Clock skew: approval timestamp appears 2 seconds AFTER transfer in wall-clock time!
    t_transfer = t0
    t_approval = t0 + timedelta(seconds=2)

    approval = make_test_event(
        "appr-01",
        "approval.granted",
        t_approval,
        source_ids=(("auth.token", "tok-999"),),
        attrs={"transaction_id": "tx-123"},
    )
    transfer = make_test_event(
        "tx-01",
        "payments.transfer",
        t_transfer,
        parents=(DeclaredLink("parent", "auth.token", "tok-999"),),
        attrs={"transaction_id": "tx-123"},
    )

    from panoptis.contracts.evaluator import evaluate_contract
    from panoptis.contracts.schema import EvidenceVerdict

    assessment = evaluate_contract([approval, transfer], transfer_contract)
    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-appr-01" in assessment.support_obs_ids
    assert "obs-tx-01" in assessment.support_obs_ids


def test_evaluator_causal_order_window_exceeded_yields_refuted(transfer_contract):
    """Causal link does NOT bypass true contract time window expiration."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # 2 hours earlier (window is 600 seconds)
    t_approval = t0 - timedelta(hours=2)
    t_transfer = t0

    approval = make_test_event(
        "appr-old",
        "approval.granted",
        t_approval,
        source_ids=(("auth.token", "tok-old"),),
        attrs={"transaction_id": "tx-123"},
    )
    transfer = make_test_event(
        "tx-01",
        "payments.transfer",
        t_transfer,
        parents=(DeclaredLink("parent", "auth.token", "tok-old"),),
        attrs={"transaction_id": "tx-123"},
    )

    from panoptis.contracts.evaluator import evaluate_contract
    from panoptis.contracts.schema import EvidenceVerdict

    assessment = evaluate_contract([approval, transfer], transfer_contract)
    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "exceeded max allowed window" in assessment.summary


def test_evaluator_concurrent_independent_events_overlapping_yields_conflicting(transfer_contract):
    """Concurrent events with overlapping uncertainty yield CONFLICTING_EVIDENCE, NEVER REFUTED."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # Agent 1 (transfer) and Agent 2 (approval) are causally independent
    # Intervals overlap: [09:59:59.500 .. 10:00:00.500] vs [09:59:59.700 .. 10:00:00.700]
    approval = make_test_event(
        "appr-indep",
        "approval.granted",
        t0 + timedelta(milliseconds=200),
        uncertainty_ms=500.0,
        attrs={"transaction_id": "tx-123"},
    )
    transfer = make_test_event(
        "tx-indep",
        "payments.transfer",
        t0,
        uncertainty_ms=500.0,
        attrs={"transaction_id": "tx-123"},
    )

    from panoptis.contracts.evaluator import evaluate_contract
    from panoptis.contracts.schema import EvidenceVerdict, MissingReason

    assessment = evaluate_contract([approval, transfer], transfer_contract)
    assert assessment.verdict == EvidenceVerdict.CONFLICTING_EVIDENCE
    assert assessment.verdict != EvidenceVerdict.REFUTED
    assert len(assessment.missing_facts) > 0
    assert assessment.missing_facts[0].reason == MissingReason.CLOCK_AMBIGUITY


def test_evaluator_causal_cycle_yields_conflicting_evidence(transfer_contract):
    """Causal cycles yield CONFLICTING_EVIDENCE rather than invalid verification."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    e1 = make_test_event(
        "cyc-1",
        "approval.granted",
        t0,
        source_ids=(("k", "v1"),),
        parents=(DeclaredLink("parent", "k", "v2"),),
        attrs={"transaction_id": "tx-123"},
    )
    e2 = make_test_event(
        "cyc-2",
        "payments.transfer",
        t0 + timedelta(seconds=1),
        source_ids=(("k", "v2"),),
        parents=(DeclaredLink("parent", "k", "v1"),),
        attrs={"transaction_id": "tx-123"},
    )

    from panoptis.contracts.evaluator import evaluate_contract
    from panoptis.contracts.schema import EvidenceVerdict

    assessment = evaluate_contract([e1, e2], transfer_contract)
    assert assessment.verdict == EvidenceVerdict.CONFLICTING_EVIDENCE
    assert "causal cycle" in assessment.summary.lower()


@pytest.fixture
def order_fulfillment_contract() -> EvidenceContract:
    return EvidenceContract(
        name="order-requires-fulfillment",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.EVENTUALLY_WITHIN,
            target_operation="order.placed",
            required_operation="order.fulfilled",
            max_window_seconds=30.0,
            join_on=("order_id",),
        ),
    )


def test_evaluator_eventually_within_causal_after_supported(order_fulfillment_contract):
    """Causal follow-up satisfies eventually_within within allowed window."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    placed = make_test_event(
        "ord-1",
        "order.placed",
        t0,
        source_ids=(("order", "ord-100"),),
        attrs={"order_id": "ord-100"},
    )
    # Clock skew: fulfilled appears earlier in wall-clock time by 1s, but has parent placed!
    fulfilled = make_test_event(
        "ful-1",
        "order.fulfilled",
        t0 - timedelta(seconds=1),
        parents=(DeclaredLink("parent", "order", "ord-100"),),
        attrs={"order_id": "ord-100"},
    )

    from panoptis.contracts.evaluator import evaluate_contract

    assessment = evaluate_contract([placed, fulfilled], order_fulfillment_contract)
    assert assessment.verdict == EvidenceVerdict.SUPPORTED


def test_evaluator_eventually_within_window_exceeded_refuted(order_fulfillment_contract):
    """Eventually_within is refuted when follow-up exceeds max allowed window."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    placed = make_test_event(
        "ord-2",
        "order.placed",
        t0,
        source_ids=(("order", "ord-200"),),
        attrs={"order_id": "ord-200"},
    )
    # 60s later (window is 30s)
    fulfilled = make_test_event(
        "ful-2",
        "order.fulfilled",
        t0 + timedelta(seconds=60),
        parents=(DeclaredLink("parent", "order", "ord-200"),),
        attrs={"order_id": "ord-200"},
    )

    from panoptis.contracts.evaluator import evaluate_contract

    assessment = evaluate_contract([placed, fulfilled], order_fulfillment_contract)
    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "exceeded max follow-up window" in assessment.summary


def test_evaluator_eventually_within_causal_before_refuted(order_fulfillment_contract):
    """Eventually_within is refuted when required operation causally precedes target."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    # fulfilled causally caused placed!
    fulfilled = make_test_event(
        "ful-3",
        "order.fulfilled",
        t0,
        source_ids=(("order", "ord-300"),),
        attrs={"order_id": "ord-300"},
    )
    placed = make_test_event(
        "ord-3",
        "order.placed",
        t0 + timedelta(seconds=5),
        parents=(DeclaredLink("parent", "order", "ord-300"),),
        attrs={"order_id": "ord-300"},
    )

    from panoptis.contracts.evaluator import evaluate_contract

    assessment = evaluate_contract([placed, fulfilled], order_fulfillment_contract)
    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "occurred before target when expected after" in assessment.summary


def test_evaluator_eventually_within_concurrent_overlapping_ambiguity(order_fulfillment_contract):
    """Concurrent events with overlapping uncertainty yield CONFLICTING_EVIDENCE under eventually_within."""
    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    placed = make_test_event(
        "ord-4",
        "order.placed",
        t0,
        uncertainty_ms=400.0,
        attrs={"order_id": "ord-400"},
    )
    fulfilled = make_test_event(
        "ful-4",
        "order.fulfilled",
        t0 + timedelta(milliseconds=100),
        uncertainty_ms=400.0,
        attrs={"order_id": "ord-400"},
    )

    from panoptis.contracts.evaluator import evaluate_contract

    assessment = evaluate_contract([placed, fulfilled], order_fulfillment_contract)
    assert assessment.verdict == EvidenceVerdict.CONFLICTING_EVIDENCE
    assert assessment.verdict != EvidenceVerdict.REFUTED
