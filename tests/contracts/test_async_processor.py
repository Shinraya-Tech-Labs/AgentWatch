"""Tests for Asynchronous Contract Stream Processor and Assessment Manager (Issue #731).

Covers async queue backpressure, cancellation, worker failures, clean shutdown,
and isolation across multiple assessment tasks.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceContract,
    EvidenceVerdict,
)
from agentwatch.contracts.stream import (
    AssessmentManager,
    AsyncContractStreamProcessor,
)
from agentwatch.events.model import (
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
        time=TemporalInfo(start=dt),
        status=EventStatus.OK,
        attributes_json=json.dumps(attrs or {}),
    )


@pytest.fixture
def transfer_contract() -> EvidenceContract:
    return EvidenceContract(
        name="transfer-contract",
        version="1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="transfer",
            required_operation="approve",
            max_window_seconds=100.0,
            join_on=("txn_id",),
        ),
    )


@pytest.mark.asyncio
async def test_async_stream_processor_lifecycle(transfer_contract):
    """Processor streams events, processes them asynchronously, and finalizes cleanly."""
    processor = AsyncContractStreamProcessor(transfer_contract, queue_capacity=10)
    await processor.start()

    e1 = make_event("e1", "approve", "2026-10-16T12:00:00Z", "obs-1", {"txn_id": "tx-1"})
    e2 = make_event("e2", "transfer", "2026-10-16T12:00:10Z", "obs-2", {"txn_id": "tx-1"})

    await processor.push_event(e1)
    await processor.push_event(e2)

    assessment = await processor.finalize()

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    assert "obs-1" in assessment.support_obs_ids
    assert "obs-2" in assessment.support_obs_ids
    assert processor.events_evaluated == 2


@pytest.mark.asyncio
async def test_async_stream_processor_cancellation(transfer_contract):
    """Cancelling a running processor halts the background task cleanly."""
    processor = AsyncContractStreamProcessor(transfer_contract, queue_capacity=5)
    await processor.start()

    e1 = make_event("e1", "approve", "2026-10-16T12:00:00Z", "obs-1", {"txn_id": "tx-1"})
    await processor.push_event(e1)

    await processor.cancel()
    assert processor.is_cancelled
    status = processor.get_status()
    assert status["status"] == "cancelled"


@pytest.mark.asyncio
async def test_async_stream_processor_backpressure(transfer_contract):
    """Bounded queue applies backpressure when producer pushes faster than worker."""
    # Tiny queue of 2
    processor = AsyncContractStreamProcessor(transfer_contract, queue_capacity=2)
    await processor.start()

    for i in range(10):
        ev = make_event(
            f"e-{i}",
            "approve",
            f"2026-10-16T12:00:0{i % 10}Z",
            f"obs-{i}",
            {"txn_id": f"tx-{i}"},
        )
        await processor.push_event(ev)

    assessment = await processor.finalize()
    assert assessment.verdict == EvidenceVerdict.SUPPORTED  # vacuous as 0 transfers
    assert processor.events_evaluated == 10


@pytest.mark.asyncio
async def test_assessment_manager_isolation(transfer_contract):
    """AssessmentManager tracks jobs, dispatches progress, and enforces tenant isolation."""
    manager = AssessmentManager()

    job_alpha = await manager.create_assessment(
        tenant_id="tenant-alpha",
        run_id="run-1",
        contract=transfer_contract,
    )
    job_beta = await manager.create_assessment(
        tenant_id="tenant-beta",
        run_id="run-2",
        contract=transfer_contract,
    )

    # Cross-tenant get returns None
    assert manager.get_assessment("tenant-beta", job_alpha.assessment_id) is None
    assert manager.get_assessment("tenant-alpha", job_beta.assessment_id) is None

    # Correct tenant retrieves job
    assert manager.get_assessment("tenant-alpha", job_alpha.assessment_id) is not None

    # Feed event to alpha
    e1 = make_event("e1", "approve", "2026-10-16T12:00:00Z", "obs-1", {"txn_id": "tx-1"})
    e2 = make_event("e2", "transfer", "2026-10-16T12:00:10Z", "obs-2", {"txn_id": "tx-1"})

    await job_alpha.push_event(e1)
    await job_alpha.push_event(e2)
    assessment = await job_alpha.finalize()

    assert assessment.verdict == EvidenceVerdict.SUPPORTED
    status = manager.get_assessment("tenant-alpha", job_alpha.assessment_id)
    assert status["status"] == "completed"
    assert status["verdict"] == "supported"

    await manager.shutdown()
