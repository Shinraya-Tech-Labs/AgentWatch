"""Tests for the irreversible-action MTL invariant monitor (Issue #752).

Rule under test: G(destructive -> O[-T,0] human_approved), both bounds inclusive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from panoptis.contracts import (
    SAFETY_INVARIANT_BREACH,
    EvidenceVerdict,
    IrreversibleInvariantMonitor,
)
from panoptis.events.model import (
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)

T0 = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
DESTRUCTIVE = "db.drop_table"


def ev(
    event_id: str,
    operation: str,
    offset: float | None,
    *,
    run_key: tuple[str, str] | None = None,
    status: EventStatus = EventStatus.OK,
    naive: bool = False,
) -> ComputationalEvent:
    if offset is None:
        start = None
    else:
        start = T0 + timedelta(seconds=offset)
        if naive:
            start = start.replace(tzinfo=None)
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="tenant-test",
        interp_id="interp-test",
        normalizer="test_normalizer@1.0",
        derived_from=("obs-" + event_id,),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(start=start),
        status=status,
        run_key=run_key,
    )


@pytest.fixture
def monitor() -> IrreversibleInvariantMonitor:
    return IrreversibleInvariantMonitor([DESTRUCTIVE, "fs.rm_rf"], window_seconds=30.0)


def test_defaults():
    m = IrreversibleInvariantMonitor([DESTRUCTIVE])
    assert m.witness_operation == "human_approved"
    assert m.window_seconds == 30.0


def test_approval_inside_window_supported(monitor):
    assert monitor.observe(ev("a1", "human_approved", 0)) is None
    result = monitor.observe(ev("d1", DESTRUCTIVE, 10))
    assert result is not None
    assert result.verdict == EvidenceVerdict.SUPPORTED
    assert result.support_obs_ids == ("a1", "d1")
    assert result.refute_obs_ids == ()


def test_no_approval_is_breach(monitor):
    result = monitor.observe(ev("d1", DESTRUCTIVE, 0))
    assert result.verdict == EvidenceVerdict.REFUTED
    assert SAFETY_INVARIANT_BREACH in result.summary
    assert result.refute_obs_ids == ("d1",)
    assert result.support_obs_ids == ()


def test_unrelated_events_return_none(monitor):
    assert monitor.observe(ev("x1", "web.search", 0)) is None


def test_exact_lower_boundary_is_inclusive(monitor):
    monitor.observe(ev("a1", "human_approved", 0))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 30.0))
    assert result.verdict == EvidenceVerdict.SUPPORTED


def test_just_past_lower_boundary_is_breach(monitor):
    monitor.observe(ev("a1", "human_approved", 0))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 30.001))
    assert result.verdict == EvidenceVerdict.REFUTED
    assert SAFETY_INVARIANT_BREACH in result.summary


def test_same_timestamp_is_inclusive_upper_boundary(monitor):
    monitor.observe(ev("a1", "human_approved", 5))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 5))
    assert result.verdict == EvidenceVerdict.SUPPORTED


def test_inverted_order_approval_after_action_is_breach(monitor):
    first = monitor.observe(ev("d1", DESTRUCTIVE, 0))
    assert first.verdict == EvidenceVerdict.REFUTED
    monitor.observe(ev("a1", "human_approved", 1))
    # The later approval must not rescue an earlier action.
    second = monitor.observe(ev("d2", DESTRUCTIVE, 0.5))
    assert second.verdict == EvidenceVerdict.REFUTED


def test_future_witness_in_buffer_ignored(monitor):
    monitor.observe(ev("a1", "human_approved", 10))
    # Action stamped before the approval it arrives after.
    result = monitor.observe(ev("d1", DESTRUCTIVE, 5))
    assert result.verdict == EvidenceVerdict.REFUTED


def test_out_of_window_approval_is_breach(monitor):
    monitor.observe(ev("a1", "human_approved", 0))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 120))
    assert result.verdict == EvidenceVerdict.REFUTED


def test_each_destructive_action_needs_its_own_fresh_approval(monitor):
    monitor.observe(ev("a1", "human_approved", 0))
    assert monitor.observe(ev("d1", DESTRUCTIVE, 10)).verdict == EvidenceVerdict.SUPPORTED
    assert monitor.observe(ev("d2", "fs.rm_rf", 20)).verdict == EvidenceVerdict.SUPPORTED
    assert monitor.observe(ev("d3", DESTRUCTIVE, 40)).verdict == EvidenceVerdict.REFUTED


def test_latest_witness_is_reported(monitor):
    monitor.observe(ev("a1", "human_approved", 0))
    monitor.observe(ev("a2", "human_approved", 8))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 10))
    assert result.support_obs_ids == ("a2", "d1")


def test_run_key_isolation(monitor):
    run_a = ("run", "A")
    run_b = ("run", "B")
    monitor.observe(ev("a1", "human_approved", 0, run_key=run_a))
    ok = monitor.observe(ev("d1", DESTRUCTIVE, 5, run_key=run_a))
    other = monitor.observe(ev("d2", DESTRUCTIVE, 5, run_key=run_b))
    unscoped = monitor.observe(ev("d3", DESTRUCTIVE, 5))
    assert ok.verdict == EvidenceVerdict.SUPPORTED
    assert other.verdict == EvidenceVerdict.REFUTED
    assert unscoped.verdict == EvidenceVerdict.REFUTED


def test_run_key_matching_can_be_disabled():
    m = IrreversibleInvariantMonitor([DESTRUCTIVE], match_run_key=False)
    m.observe(ev("a1", "human_approved", 0, run_key=("run", "A")))
    result = m.observe(ev("d1", DESTRUCTIVE, 5, run_key=("run", "B")))
    assert result.verdict == EvidenceVerdict.SUPPORTED


@pytest.mark.parametrize("status", [EventStatus.ERROR, EventStatus.CANCELLED, EventStatus.TIMEOUT])
def test_failed_approval_is_not_a_witness(monitor, status):
    monitor.observe(ev("a1", "human_approved", 0, status=status))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 5))
    assert result.verdict == EvidenceVerdict.REFUTED


def test_approval_without_time_is_ignored(monitor):
    monitor.observe(ev("a1", "human_approved", None))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 5))
    assert result.verdict == EvidenceVerdict.REFUTED


def test_action_without_time_is_inconclusive(monitor):
    result = monitor.observe(ev("d1", DESTRUCTIVE, None))
    assert result.verdict == EvidenceVerdict.INCONCLUSIVE
    assert result.refute_obs_ids == ()


def test_naive_timestamps_are_treated_as_utc(monitor):
    monitor.observe(ev("a1", "human_approved", 0, naive=True))
    result = monitor.observe(ev("d1", DESTRUCTIVE, 5))
    assert result.verdict == EvidenceVerdict.SUPPORTED


def test_buffer_is_pruned_and_bounded():
    m = IrreversibleInvariantMonitor([DESTRUCTIVE], window_seconds=10.0)
    for i in range(1000):
        m.observe(ev(f"a{i}", "human_approved", float(i)))
    # Only witnesses within the last 10s survive: offsets 989..999.
    assert m.buffered_witnesses == 11


def test_pruning_does_not_change_verdicts():
    m = IrreversibleInvariantMonitor([DESTRUCTIVE], window_seconds=10.0)
    for i in range(100):
        m.observe(ev(f"a{i}", "human_approved", float(i)))
    assert m.observe(ev("d1", DESTRUCTIVE, 100)).verdict == EvidenceVerdict.SUPPORTED
    assert m.observe(ev("d2", DESTRUCTIVE, 200)).verdict == EvidenceVerdict.REFUTED


def test_late_witness_is_kept_in_time_order(monitor):
    monitor.observe(ev("a2", "human_approved", 10))
    monitor.observe(ev("a1", "human_approved", 5))  # arrives late
    result = monitor.observe(ev("d1", DESTRUCTIVE, 12))
    assert result.support_obs_ids == ("a2", "d1")
    # Between the two witnesses, the earlier one is found.
    monitor2 = IrreversibleInvariantMonitor([DESTRUCTIVE], window_seconds=30.0)
    monitor2.observe(ev("a2", "human_approved", 10))
    monitor2.observe(ev("a1", "human_approved", 5))
    mid = monitor2.observe(ev("d0", DESTRUCTIVE, 7))
    assert mid.support_obs_ids == ("a1", "d0")


def test_action_older_than_retention_is_inconclusive_not_breach():
    m = IrreversibleInvariantMonitor([DESTRUCTIVE], window_seconds=10.0)
    m.observe(ev("a1", "human_approved", 0))
    m.observe(ev("x1", "web.search", 100))  # stream clock jumps ahead, a1 pruned
    # Late action at t=5 would have been approved by a1, which is gone.
    result = m.observe(ev("d1", DESTRUCTIVE, 5))
    assert result.verdict == EvidenceVerdict.INCONCLUSIVE
    assert SAFETY_INVARIANT_BREACH not in result.summary


def test_zero_window_requires_same_instant():
    m = IrreversibleInvariantMonitor([DESTRUCTIVE], window_seconds=0)
    m.observe(ev("a1", "human_approved", 0))
    assert m.observe(ev("d1", DESTRUCTIVE, 0)).verdict == EvidenceVerdict.SUPPORTED
    assert m.observe(ev("d2", DESTRUCTIVE, 0.001)).verdict == EvidenceVerdict.REFUTED


def test_check_trace_orders_by_time(monitor):
    events = [
        ev("d1", DESTRUCTIVE, 10),
        ev("a1", "human_approved", 0),
        ev("d2", DESTRUCTIVE, 100),
        ev("x1", "web.search", 50),
    ]
    results = monitor.check_trace(events)
    assert [r.verdict for r in results] == [EvidenceVerdict.SUPPORTED, EvidenceVerdict.REFUTED]


def test_check_trace_places_missing_time_first(monitor):
    results = monitor.check_trace([ev("d1", DESTRUCTIVE, 0), ev("d2", DESTRUCTIVE, None)])
    assert [r.verdict for r in results] == [
        EvidenceVerdict.INCONCLUSIVE,
        EvidenceVerdict.REFUTED,
    ]


def test_assessment_serialises(monitor):
    result = monitor.observe(ev("d1", DESTRUCTIVE, 0))
    data = result.to_dict()
    assert data["verdict"] == "refuted"
    assert data["refute_obs_ids"] == ["d1"]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"irreversible_operations": []}, ValueError),
        ({"irreversible_operations": DESTRUCTIVE}, TypeError),
        ({"irreversible_operations": ["human_approved"]}, ValueError),
        ({"irreversible_operations": [DESTRUCTIVE], "window_seconds": -1}, ValueError),
        ({"irreversible_operations": [DESTRUCTIVE], "window_seconds": float("nan")}, ValueError),
        ({"irreversible_operations": [DESTRUCTIVE], "window_seconds": float("inf")}, ValueError),
    ],
)
def test_invalid_configuration_rejected(kwargs, error):
    with pytest.raises(error):
        IrreversibleInvariantMonitor(**kwargs)
