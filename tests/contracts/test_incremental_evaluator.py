"""Tests for the Incremental Contract Evaluator (Issue #731).

Verifies incremental stream evaluation over ComputationalEvents, bounded memory,
TTL eviction, clock uncertainty, open-world semantics, and equivalence with batch evaluation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentwatch.contracts.evaluator import evaluate_contract
from agentwatch.contracts.incremental import IncrementalContractEvaluator
from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceContract,
    EvidenceVerdict,
    MissingReason,
)
from agentwatch.events.model import (
    ComputationalEvent,
    DeclaredLink,
    EventKind,
    EventStatus,
    TemporalInfo,
)


def make_event(
    event_id: str,
    operation: str,
    start_iso: str | None,
    obs_id: str,
    attrs: dict | None = None,
    uncertainty_ms: float = 0.0,
    parents: tuple[DeclaredLink, ...] = (),
    source_ids: tuple[tuple[str, str], ...] = (),
) -> ComputationalEvent:
    dt = datetime.fromisoformat(start_iso) if start_iso else None
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="tenant-alpha",
        interp_id="interp-01",
        normalizer="test_normalizer@1.0",
        derived_from=(obs_id,),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(start=dt, uncertainty_ms=uncertainty_ms),
        status=EventStatus.OK,
        attributes_json=json.dumps(attrs or {}),
        parents=parents,
        source_ids=source_ids,
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
def order_contract() -> EvidenceContract:
    return EvidenceContract(
        name="order-requires-confirmation",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.EVENTUALLY_WITHIN,
            target_operation="order.placed",
            required_operation="email.sent",
            max_window_seconds=30.0,
            join_on=("order_id",),
        ),
    )


def test_incremental_preceding_event_supported(payment_contract):
    """Incremental evaluation supports preceding event with minimal witnesses."""
    evaluator = IncrementalContractEvaluator(payment_contract)

    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00Z",
        "obs-approval-1",
        {"transaction_id": "tx-100"},
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )

    evaluator.process_event(approval)
    evaluator.process_event(transfer)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-approval-1" in assessment.support_obs_ids
    assert "obs-transfer-1" in assessment.support_obs_ids
    assert len(assessment.missing_facts) == 0


def test_incremental_preceding_event_out_of_order(payment_contract):
    """Target arrives before preceding required event; required arrives later within window."""
    evaluator = IncrementalContractEvaluator(payment_contract)

    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )
    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00Z",
        "obs-approval-1",
        {"transaction_id": "tx-100"},
    )

    evaluator.process_event(transfer)
    evaluator.process_event(approval)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-approval-1" in assessment.support_obs_ids
    assert "obs-transfer-1" in assessment.support_obs_ids


def test_incremental_preceding_event_missing_authoritative_refutes(payment_contract):
    """Target arrives without approval; authoritative sensor attests completeness -> REFUTED."""
    coverage = [
        CoverageSnapshot(
            sensor_id="audit_sensor",
            authoritative_for=("approval.granted",),
            active=True,
            dropped_count=0,
        )
    ]
    evaluator = IncrementalContractEvaluator(payment_contract, coverage=coverage)

    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )

    evaluator.process_event(transfer)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "obs-transfer-1" in assessment.refute_obs_ids
    assert "Authoritative sensors" in assessment.summary


def test_incremental_preceding_event_missing_non_authoritative_inconclusive(payment_contract):
    """Target arrives without approval and no authoritative coverage -> INCONCLUSIVE."""
    evaluator = IncrementalContractEvaluator(payment_contract, coverage=[])

    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )

    evaluator.process_event(transfer)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert len(assessment.missing_facts) == 1
    assert assessment.missing_facts[0].reason == MissingReason.SENSOR_DISABLED


def test_incremental_preceding_event_occurred_after_refuted(payment_contract):
    """Approval occurred after transfer -> REFUTED."""
    evaluator = IncrementalContractEvaluator(payment_contract)

    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:00Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )
    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:10Z",
        "obs-approval-1",
        {"transaction_id": "tx-100"},
    )

    evaluator.process_event(transfer)
    evaluator.process_event(approval)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "occurred after" in assessment.summary


def test_incremental_preceding_event_window_exceeded_refuted(payment_contract):
    """Approval occurred 700s before transfer (max window 600s) -> REFUTED."""
    evaluator = IncrementalContractEvaluator(payment_contract)

    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00Z",
        "obs-approval-1",
        {"transaction_id": "tx-100"},
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:11:40Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )

    evaluator.process_event(approval)
    evaluator.process_event(transfer)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "exceeded max allowed window" in assessment.summary


def test_incremental_eventually_within_supported(order_contract):
    """Eventual follow-up within 30s -> SUPPORTED."""
    evaluator = IncrementalContractEvaluator(order_contract)

    order = make_event(
        "evt-10",
        "order.placed",
        "2026-10-16T10:00:00Z",
        "obs-order-10",
        {"order_id": "ord-1"},
    )
    email = make_event(
        "evt-11",
        "email.sent",
        "2026-10-16T10:00:20Z",
        "obs-email-11",
        {"order_id": "ord-1"},
    )

    evaluator.process_event(order)
    evaluator.process_event(email)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-order-10" in assessment.support_obs_ids
    assert "obs-email-11" in assessment.support_obs_ids


def test_incremental_eventually_within_expired_authoritative_refuted(order_contract):
    """Target arrives, deadline passes, authoritative coverage exists -> REFUTED."""
    coverage = [
        CoverageSnapshot(
            sensor_id="mail_sensor",
            authoritative_for=("email.sent",),
            active=True,
            dropped_count=0,
        )
    ]
    evaluator = IncrementalContractEvaluator(order_contract, coverage=coverage)

    order = make_event(
        "evt-10",
        "order.placed",
        "2026-10-16T10:00:00Z",
        "obs-order-10",
        {"order_id": "ord-1"},
    )
    # Another unrelated event later in time
    unrelated = make_event(
        "evt-99",
        "ui.click",
        "2026-10-16T10:01:00Z",
        "obs-ui-99",
    )

    evaluator.process_event(order)
    evaluator.process_event(unrelated)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "obs-order-10" in assessment.refute_obs_ids


def test_incremental_eventually_within_expired_non_authoritative_inconclusive(order_contract):
    """Target arrives, deadline passes, no authoritative coverage -> INCONCLUSIVE."""
    evaluator = IncrementalContractEvaluator(order_contract, coverage=[])

    order = make_event(
        "evt-10",
        "order.placed",
        "2026-10-16T10:00:00Z",
        "obs-order-10",
        {"order_id": "ord-1"},
    )

    evaluator.process_event(order)
    assessment = evaluator.finalize()

    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert len(assessment.missing_facts) >= 1


def test_clock_ambiguity_and_causal_link_resolution(payment_contract):
    """Clock ambiguity without causal link -> CONFLICTING_EVIDENCE; with link -> SUPPORTED."""
    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00.002Z",
        "obs-approval-1",
        {"transaction_id": "tx-100"},
        uncertainty_ms=10.0,
        source_ids=(("span", "span-approval"),),
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:00.000Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
        uncertainty_ms=10.0,
    )

    ev1 = IncrementalContractEvaluator(payment_contract)
    ev1.process_event(approval)
    ev1.process_event(transfer)
    assert ev1.finalize().verdict == EvidenceVerdict.CONFLICTING_EVIDENCE

    # Now with explicit causal link on transfer
    transfer_linked = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:00.000Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
        uncertainty_ms=10.0,
        parents=(DeclaredLink("parent", "span", "span-approval"),),
    )

    ev2 = IncrementalContractEvaluator(payment_contract)
    ev2.process_event(approval)
    ev2.process_event(transfer_linked)
    assert ev2.finalize().verdict == EvidenceVerdict.SUPPORTED


def test_missing_timestamp_inconclusive(payment_contract):
    """Missing start timestamp yields INCONCLUSIVE."""
    approval = make_event(
        "evt-01",
        "approval.granted",
        None,
        "obs-approval-1",
        {"transaction_id": "tx-100"},
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-100"},
    )

    evaluator = IncrementalContractEvaluator(payment_contract)
    evaluator.process_event(approval)
    evaluator.process_event(transfer)
    res = evaluator.finalize()

    assert res.verdict == EvidenceVerdict.INCONCLUSIVE
    assert any(f.reason == MissingReason.FIELD_ABSENT for f in res.missing_facts)


def test_bounded_buffer_overflow_prevents_false_supported(payment_contract):
    """When bounded buffer capacity is exceeded, overflow is recorded and verdict is INCONCLUSIVE."""
    # Set capacity to 2 items
    evaluator = IncrementalContractEvaluator(payment_contract, max_buffer_size=2)

    # Push 5 candidate events
    for i in range(5):
        evaluator.process_event(
            make_event(
                f"evt-req-{i}",
                "approval.granted",
                f"2026-10-16T10:00:0{i}Z",
                f"obs-req-{i}",
                {"transaction_id": f"tx-{i}"},
            )
        )

    assert evaluator.has_overflow

    # Complete one transaction that was retained
    evaluator.process_event(
        make_event(
            "evt-target-4",
            "payments.transfer",
            "2026-10-16T10:00:10Z",
            "obs-target-4",
            {"transaction_id": "tx-4"},
        )
    )

    assessment = evaluator.finalize()
    # Must NOT report SUPPORTED because buffer overflow lost potential facts
    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert any(f.reason == MissingReason.SEQUENCE_GAP for f in assessment.missing_facts)


def test_ttl_eviction_removes_stale_candidates(payment_contract):
    """Candidates older than max_window_seconds are evicted as stream time advances."""
    evaluator = IncrementalContractEvaluator(payment_contract)

    # Approval at 10:00:00
    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00Z",
        "obs-approval-1",
        {"transaction_id": "tx-old"},
    )
    evaluator.process_event(approval)
    assert evaluator.buffer_stats()["total_candidates"] == 1

    # Unrelated event at 10:15:00 (900s later, exceeds max_window_seconds of 600s)
    later = make_event(
        "evt-02",
        "unrelated.action",
        "2026-10-16T10:15:00Z",
        "obs-later",
    )
    evaluator.process_event(later)

    # Stale candidate for tx-old was evicted
    assert evaluator.buffer_stats()["total_candidates"] == 0


def test_vacuous_satisfaction_on_empty_targets(payment_contract):
    """Trace with zero target operations is vacuously supported."""
    evaluator = IncrementalContractEvaluator(payment_contract)
    evaluator.process_event(make_event("evt-01", "other.action", "2026-10-16T10:00:00Z", "obs-01"))
    assessment = evaluator.finalize()
    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "vacuously satisfied" in assessment.summary


def test_interleaved_runs_and_correlation_keys(payment_contract):
    """Interleaved events for multiple transactions match correctly by correlation key."""
    evaluator = IncrementalContractEvaluator(payment_contract)

    e1 = make_event(
        "e1", "approval.granted", "2026-10-16T10:00:01Z", "o1", {"transaction_id": "tx-A"}
    )
    e2 = make_event(
        "e2", "approval.granted", "2026-10-16T10:00:02Z", "o2", {"transaction_id": "tx-B"}
    )
    e3 = make_event(
        "e3", "payments.transfer", "2026-10-16T10:00:05Z", "o3", {"transaction_id": "tx-A"}
    )
    e4 = make_event(
        "e4", "payments.transfer", "2026-10-16T10:00:06Z", "o4", {"transaction_id": "tx-B"}
    )

    for ev in [e1, e2, e3, e4]:
        evaluator.process_event(ev)

    assessment = evaluator.finalize()
    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert set(assessment.support_obs_ids) == {"o1", "o2", "o3", "o4"}


def test_equivalence_with_batch_evaluator(payment_contract, order_contract):
    """Incremental evaluation yields identical verdict and witness IDs as batch evaluator."""
    coverage = [
        CoverageSnapshot(
            sensor_id="audit_ledger_sensor",
            authoritative_for=("approval.granted",),
            active=True,
            dropped_count=0,
        )
    ]

    events = [
        make_event(
            "e1", "approval.granted", "2026-10-16T10:00:00Z", "o1", {"transaction_id": "tx-1"}
        ),
        make_event(
            "e2", "payments.transfer", "2026-10-16T10:00:15Z", "o2", {"transaction_id": "tx-1"}
        ),
    ]

    batch_res = evaluate_contract(events, payment_contract, coverage=coverage)
    inc_eval = IncrementalContractEvaluator(payment_contract, coverage=coverage)
    for e in events:
        inc_eval.process_event(e)
    inc_res = inc_eval.finalize()

    assert inc_res.verdict == batch_res.verdict
    assert set(inc_res.support_obs_ids) == set(batch_res.support_obs_ids)
    assert set(inc_res.refute_obs_ids) == set(batch_res.refute_obs_ids)
