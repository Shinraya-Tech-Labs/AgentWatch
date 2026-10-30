"""Deterministic simulation suite for Issue #729.

Simulates 120 out-of-order event streams across 4 multi-agent topologies:
1. Streams 1-30: Causal chains with network arrival permutations (shuffled delivery).
2. Streams 31-60: Concurrent independent agents with clock drift & overlapping intervals.
3. Streams 61-90: Interleaved multi-agent request-response delegation topologies.
4. Streams 91-120: Watermark sliding-window buffering with late-arrival recomputation.

Acceptance criteria:
- Fully deterministic results across all 120 simulated streams.
- Zero (0) false REFUTED violations triggered by concurrent, causally independent events.
- Deterministic PRNG with fixed seed.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from panoptis.contracts.evaluator import evaluate_contract
from panoptis.contracts.order import (
    CausalRelation,
    EventTimeWatermarkBuffer,
    HappensBeforeGraph,
    IngestionStatus,
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


def make_sim_event(
    event_id: str,
    operation: str,
    dt: datetime,
    uncertainty_ms: float = 0.0,
    ordering_key: str = "",
    source_ids: tuple[tuple[str, str], ...] = (),
    parents: tuple[DeclaredLink, ...] = (),
    attrs: dict[str, Any] | None = None,
) -> ComputationalEvent:
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="tenant-sim",
        interp_id="interp-sim",
        normalizer="sim_normalizer@1.0",
        derived_from=(f"obs-{event_id}",),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(
            start=dt,
            uncertainty_ms=uncertainty_ms,
            ordering_key=ordering_key,
        ),
        status=EventStatus.OK,
        source_ids=source_ids,
        parents=parents,
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


# ==============================================================================
# Topology 1: Causal Chains with Network Delivery Jitter (30 streams)
# ==============================================================================


@pytest.mark.parametrize("stream_idx", range(30))
def test_sim_causal_chain_delivery_permutations(
    stream_idx: int, transfer_contract: EvidenceContract
):
    """Simulate 30 out-of-order arrival permutations of causally linked transactions.

    Verifies happens-before partial order reconstructs true causality despite arrival order.
    """
    rng = random.Random(1000 + stream_idx)  # noqa: S311 # nosec B311 — deterministic PRNG with fixed seed
    base_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)

    # Build 5 transactions: each has approval -> transfer (causally linked via parents)
    events: list[ComputationalEvent] = []
    for i in range(5):
        tx_id = f"tx-c1-{stream_idx}-{i}"
        t_appr = base_time + timedelta(seconds=i * 20)
        # Add clock skew: transfer wall-clock may even appear earlier due to drift
        t_xfer = t_appr + timedelta(seconds=rng.uniform(-2.0, 5.0))

        appr = make_sim_event(
            f"appr-{stream_idx}-{i}",
            "approval.granted",
            t_appr,
            source_ids=(("auth.token", f"tok-{stream_idx}-{i}"),),
            ordering_key=f"auth_sensor:{i + 1:012d}",
            attrs={"transaction_id": tx_id},
        )
        xfer = make_sim_event(
            f"xfer-{stream_idx}-{i}",
            "payments.transfer",
            t_xfer,
            parents=(DeclaredLink("parent", "auth.token", f"tok-{stream_idx}-{i}"),),
            ordering_key=f"pay_sensor:{i + 1:012d}",
            attrs={"transaction_id": tx_id},
        )
        events.extend([appr, xfer])

    # Shuffle arrival order randomly
    shuffled_events = list(events)
    rng.shuffle(shuffled_events)

    # Evaluate each transaction over the shuffled batch
    for i in range(5):
        tx_id = f"tx-c1-{stream_idx}-{i}"
        tx_events = [e for e in shuffled_events if e.attributes.get("transaction_id") == tx_id]
        assessment = evaluate_contract(tx_events, transfer_contract)

        # Causal order must guarantee SUPPORTED
        assert assessment.verdict == EvidenceVerdict.SUPPORTED, (
            f"Stream {stream_idx} tx {tx_id} expected SUPPORTED but got {assessment.verdict}"
        )


# ==============================================================================
# Topology 2: Concurrent Independent Multi-Agent Streams (30 streams)
# ==============================================================================


@pytest.mark.parametrize("stream_idx", range(30))
def test_sim_concurrent_independent_agents_zero_false_refutations(
    stream_idx: int, transfer_contract: EvidenceContract
):
    """Simulate 30 streams where independent agents operate with overlapping uncertainty.

    Acceptance criterion: ZERO (0) false violations (REFUTED) caused by concurrent events.
    """
    rng = random.Random(2000 + stream_idx)  # noqa: S311 # nosec B311 — deterministic PRNG with fixed seed
    base_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)

    # Agent A emits payments.transfer, Agent B emits approval.granted independently
    # Both have large clock uncertainty (±1000ms), and time difference is < 800ms
    events: list[ComputationalEvent] = []
    num_tx = 4
    for i in range(num_tx):
        tx_id = f"tx-c2-{stream_idx}-{i}"
        t_center = base_time + timedelta(seconds=i * 15)
        # Jitter around center within uncertainty window
        dt_a = t_center + timedelta(milliseconds=rng.uniform(-400, 400))
        dt_b = t_center + timedelta(milliseconds=rng.uniform(-400, 400))

        transfer = make_sim_event(
            f"xfer-indep-{stream_idx}-{i}",
            "payments.transfer",
            dt_a,
            uncertainty_ms=1000.0,
            ordering_key=f"agent_a:{i + 1:012d}",
            attrs={"transaction_id": tx_id},
        )
        approval = make_sim_event(
            f"appr-indep-{stream_idx}-{i}",
            "approval.granted",
            dt_b,
            uncertainty_ms=1000.0,
            ordering_key=f"agent_b:{i + 1:012d}",
            attrs={"transaction_id": tx_id},
        )
        events.extend([transfer, approval])

    shuffled_events = list(events)
    rng.shuffle(shuffled_events)

    false_refutations = 0
    for i in range(num_tx):
        tx_id = f"tx-c2-{stream_idx}-{i}"
        tx_events = [e for e in shuffled_events if e.attributes.get("transaction_id") == tx_id]
        assessment = evaluate_contract(tx_events, transfer_contract)

        # STRICT ACCEPTANCE CRITERION: NEVER REFUTED under concurrency & interval overlap!
        assert assessment.verdict != EvidenceVerdict.REFUTED, (
            f"Stream {stream_idx} tx {tx_id} falsely REFUTED concurrent independent event!"
        )
        assert assessment.verdict in (
            EvidenceVerdict.CONFLICTING_EVIDENCE,
            EvidenceVerdict.SUPPORTED,
        )
        if assessment.verdict == EvidenceVerdict.REFUTED:
            false_refutations += 1

    assert false_refutations == 0, (
        f"Encountered {false_refutations} false refutations in stream {stream_idx}!"
    )


# ==============================================================================
# Topology 3: Interleaved Request-Response Multi-Hop Chains (30 streams)
# ==============================================================================


@pytest.mark.parametrize("stream_idx", range(30))
def test_sim_multi_hop_delegation_chains(stream_idx: int):
    """Simulate 30 streams of 3-hop distributed delegation: Orchestrator -> Worker 1 -> Worker 2.

    Verifies transitive partial order closure across multi-agent boundaries.
    """
    rng = random.Random(3000 + stream_idx)  # noqa: S311 # nosec B311 — deterministic PRNG with fixed seed
    base_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)

    # Chain: e_orch (req) -> e_w1 (process) -> e_w2 (exec)
    e_orch = make_sim_event(
        f"orch-{stream_idx}",
        "orchestrator.dispatch",
        base_time,
        source_ids=(("delegation", f"del-{stream_idx}-root"),),
        ordering_key=f"orch_agent:{stream_idx:012d}",
    )
    e_w1 = make_sim_event(
        f"w1-{stream_idx}",
        "worker1.process",
        base_time + timedelta(seconds=rng.uniform(1.0, 3.0)),
        source_ids=(("delegation", f"del-{stream_idx}-w1"),),
        parents=(DeclaredLink("follows", "delegation", f"del-{stream_idx}-root"),),
        ordering_key=f"w1_agent:{stream_idx:012d}",
    )
    e_w2 = make_sim_event(
        f"w2-{stream_idx}",
        "worker2.execute",
        base_time + timedelta(seconds=rng.uniform(4.0, 6.0)),
        parents=(DeclaredLink("responds_to", "delegation", f"del-{stream_idx}-w1"),),
        ordering_key=f"w2_agent:{stream_idx:012d}",
    )

    arrivals = [e_orch, e_w1, e_w2]
    rng.shuffle(arrivals)

    graph = HappensBeforeGraph(arrivals)
    assert not graph.has_cycle()

    # Direct edges
    assert graph.happens_before(f"orch-{stream_idx}", f"w1-{stream_idx}")
    assert graph.happens_before(f"w1-{stream_idx}", f"w2-{stream_idx}")

    # Transitive reachability across multi-agent boundary
    assert graph.happens_before(f"orch-{stream_idx}", f"w2-{stream_idx}")
    assert graph.relate(f"orch-{stream_idx}", f"w2-{stream_idx}") == CausalRelation.BEFORE
    assert graph.relate(f"w2-{stream_idx}", f"orch-{stream_idx}") == CausalRelation.AFTER


# ==============================================================================
# Topology 4: Watermark Sliding-Window with Late Arrivals (30 streams)
# ==============================================================================


@pytest.mark.parametrize("stream_idx", range(30))
def test_sim_watermark_buffer_out_of_order_streaming(
    stream_idx: int, transfer_contract: EvidenceContract
):
    """Simulate 30 streaming runs with event-time watermark advancing and late-arriving witnesses.

    Verifies late-arriving events are classified correctly, trigger target recomputations,
    and update previously inconclusive verdicts to SUPPORTED.
    """
    rng = random.Random(4000 + stream_idx)  # noqa: S311 # nosec B311 — deterministic PRNG with fixed seed
    base_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)

    buf = EventTimeWatermarkBuffer(
        lateness_bound_seconds=3.0,
        max_window_seconds=30.0,
    )

    # 1. Target transfer arrives at t = 10:00:10
    t_tx = base_time + timedelta(seconds=10)
    tx_event = make_sim_event(
        f"tx-wm-{stream_idx}",
        "payments.transfer",
        t_tx,
        attrs={"transaction_id": f"tx-wm-{stream_idx}"},
    )
    buf.ingest(tx_event, target_operations=("payments.transfer",))

    # Evaluate target before approval arrives: No candidates -> INCONCLUSIVE
    assessment_initial = evaluate_contract(buf.retained_events, transfer_contract)
    assert assessment_initial.verdict == EvidenceVerdict.INCONCLUSIVE

    # 2. Advance time past watermark: future events arrive at t = 10:00:15
    t_adv = base_time + timedelta(seconds=15)
    buf.ingest(make_sim_event(f"adv-{stream_idx}", "metrics.tick", t_adv))
    assert buf.watermark == t_adv - timedelta(seconds=3.0)  # 10:00:12

    # 3. Late-arriving approval event (behind watermark 10:00:12, but within retention)
    t_appr = base_time + timedelta(seconds=rng.uniform(2.0, 7.0))
    appr_event = make_sim_event(
        f"appr-wm-{stream_idx}",
        "approval.granted",
        t_appr,
        attrs={"transaction_id": f"tx-wm-{stream_idx}"},
    )
    ingest_res = buf.ingest(appr_event, target_operations=("payments.transfer",))

    # Verify classified as LATE and affected target is identified
    assert ingest_res.is_late
    assert ingest_res.status == IngestionStatus.LATE
    assert f"tx-wm-{stream_idx}" in ingest_res.affected_target_ids

    # 4. Recompute contract over buffer: target is now SUPPORTED!
    assessment_revised = evaluate_contract(buf.retained_events, transfer_contract)
    assert assessment_revised.verdict == EvidenceVerdict.SUPPORTED
    assert f"obs-tx-wm-{stream_idx}" in assessment_revised.support_obs_ids
    assert f"obs-appr-wm-{stream_idx}" in assessment_revised.support_obs_ids
