"""Tests for Evidence Sufficiency and Temporal Contract Verification (v3.1 EvidenceLab)."""

import json
from datetime import datetime

import pytest

from panoptis.contracts.evaluator import evaluate_contract
from panoptis.contracts.schema import (
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceContract,
    EvidenceVerdict,
    MissingReason,
)
from panoptis.events.model import (
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)


def make_event(
    event_id: str,
    operation: str,
    start_iso: str,
    obs_id: str,
    attrs: dict | None = None,
    uncertainty_ms: float = 0.0,
) -> ComputationalEvent:
    dt = datetime.fromisoformat(start_iso)
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


def test_preceding_event_supported_with_minimal_witness(payment_contract):
    """Approval observed 10 seconds before transfer with matching transaction_id -> SUPPORTED."""
    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00Z",
        "obs-approval-1",
        {"transaction_id": "tx-999"},
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-999"},
    )

    assessment = evaluate_contract([approval, transfer], payment_contract)

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-approval-1" in assessment.support_obs_ids
    assert "obs-transfer-1" in assessment.support_obs_ids
    assert len(assessment.missing_facts) == 0


def test_missing_sensor_yields_inconclusive_never_false_alarm(payment_contract):
    """Target observed without approval event and NO authoritative sensor -> INCONCLUSIVE."""
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-999"},
    )

    # Sensor for approvals was disabled / not present
    assessment = evaluate_contract([transfer], payment_contract, coverage=[])

    assert assessment.verdict == EvidenceVerdict.INCONCLUSIVE
    assert len(assessment.missing_facts) == 1
    assert assessment.missing_facts[0].reason == MissingReason.SENSOR_DISABLED
    assert "Enable capture sensor" in assessment.remedies[0]


def test_authoritative_log_absence_yields_refuted(payment_contract):
    """Target observed without approval, but an authoritative sensor logged zero drops -> REFUTED."""
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:10Z",
        "obs-transfer-1",
        {"transaction_id": "tx-999"},
    )

    coverage = [
        CoverageSnapshot(
            sensor_id="audit_ledger_sensor",
            authoritative_for=("approval.granted",),
            active=True,
            dropped_count=0,
        )
    ]

    assessment = evaluate_contract([transfer], payment_contract, coverage=coverage)

    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "obs-transfer-1" in assessment.refute_obs_ids
    assert "Authoritative sensors" in assessment.summary


def test_preceding_event_window_exceeded_yields_refuted(payment_contract):
    """Approval occurred 1 hour ago (max window is 10 min) -> REFUTED."""
    old_approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T09:00:00Z",
        "obs-approval-old",
        {"transaction_id": "tx-999"},
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:01:00Z",
        "obs-transfer-1",
        {"transaction_id": "tx-999"},
    )

    assessment = evaluate_contract([old_approval, transfer], payment_contract)

    assert assessment.verdict == EvidenceVerdict.REFUTED
    assert "exceeded max allowed window" in assessment.summary


def test_clock_ambiguity_yields_conflicting_evidence(payment_contract):
    """Time delta is 2ms, but clock uncertainty is 5ms -> CONFLICTING_EVIDENCE."""
    approval = make_event(
        "evt-01",
        "approval.granted",
        "2026-10-16T10:00:00.002Z",
        "obs-approval-1",
        {"transaction_id": "tx-999"},
        uncertainty_ms=5.0,
    )
    transfer = make_event(
        "evt-02",
        "payments.transfer",
        "2026-10-16T10:00:00.000Z",
        "obs-transfer-1",
        {"transaction_id": "tx-999"},
        uncertainty_ms=5.0,
    )

    assessment = evaluate_contract([approval, transfer], payment_contract)

    assert assessment.verdict == EvidenceVerdict.CONFLICTING_EVIDENCE
    assert "clock uncertainty" in assessment.summary.lower()


def test_eventually_within_operator():
    """Verify follow-up confirmation event within 30 seconds."""
    contract = EvidenceContract(
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

    order = make_event(
        "evt-10",
        "order.placed",
        "2026-10-16T10:00:00Z",
        "obs-order-10",
        {"order_id": "ord-123"},
    )
    email = make_event(
        "evt-11",
        "email.sent",
        "2026-10-16T10:00:15Z",
        "obs-email-11",
        {"order_id": "ord-123"},
    )

    assessment = evaluate_contract([order, email], contract)

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-order-10" in assessment.support_obs_ids
    assert "obs-email-11" in assessment.support_obs_ids
