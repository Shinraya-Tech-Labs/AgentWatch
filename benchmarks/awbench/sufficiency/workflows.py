"""AWBench-Sufficiency: Multi-Agent Workflow Architectures (Issue #737 / H-S1).

Implements 5 realistic synthetic multi-agent workflow families:
1. Multi-step financial transfer with compliance audit logging.
2. Customer support agent with tiered handoffs.
3. Code generation agent with sandboxed execution.
4. Multi-document RAG synthesizer with citation attestation.
5. Long-running orchestrator with retries.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    EvidenceContract,
)
from agentwatch.events.model import (
    ArtifactRef,
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)


@dataclass(frozen=True, slots=True)
class WorkflowCase:
    """A generated multi-agent workflow trace with ground-truth invariants."""

    family: str
    run_id: str
    events: tuple[ComputationalEvent, ...]
    contract: EvidenceContract
    ground_truth_satisfied: bool  # True if invariant holds; False if true violation occurred
    target_operation: str
    required_operation: str
    sensor_ids: tuple[str, ...]


def _make_event(
    event_id: str,
    operation: str,
    dt_seconds: float,
    tenant_id: str = "awbench_tenant",
    run_id: str = "run_0",
    seq: int = 1,
    sensor_id: str = "sensor_default",
    attributes: dict | None = None,
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
) -> ComputationalEvent:
    base_time = datetime(2026, 10, 30, 10, 0, 0, tzinfo=UTC)
    event_time = base_time + timedelta(seconds=dt_seconds)
    attrs = dict(attributes or {})
    attrs["sequence_number"] = seq
    attrs["sensor_id"] = sensor_id

    return ComputationalEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        interp_id="awbench_interp",
        normalizer="awbench_norm",
        derived_from=(f"obs_{event_id}",),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(start=event_time, uncertainty_ms=2.0),
        status=EventStatus.OK,
        attributes_json=json.dumps(attrs),
        inputs=tuple(ArtifactRef(artifact_id=d, role="input") for d in inputs),
        outputs=tuple(ArtifactRef(artifact_id=d, role="output") for d in outputs),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Financial Transfer with Compliance Log
# ═══════════════════════════════════════════════════════════════════════════════

def generate_financial_transfer(run_id: str, seed: int, should_violate: bool = False) -> WorkflowCase:
    rng = random.Random(seed)  # noqa: S311
    tx_id = f"tx_{rng.randint(100000, 999999)}"
    amount = rng.randint(500, 50000)

    events: list[ComputationalEvent] = []
    # 1. User Intent
    events.append(_make_event(f"{run_id}_intent", "TransferIntent", 0.0, run_id=run_id, seq=1, sensor_id="sensor_gateway", attributes={"transaction_id": tx_id, "amount": amount}))
    # 2. Compliance Check
    events.append(_make_event(f"{run_id}_compliance", "ComplianceCheck", 2.0, run_id=run_id, seq=2, sensor_id="sensor_compliance", attributes={"transaction_id": tx_id, "risk_score": rng.uniform(0.01, 0.3)}))

    # 3. Manager Approval (omitted if should_violate is True)
    if not should_violate:
        events.append(_make_event(f"{run_id}_approval", "ManagerApproval", 4.0, run_id=run_id, seq=3, sensor_id="sensor_approval", attributes={"transaction_id": tx_id, "limit": amount + 5000}))

    # 4. Transfer Execution (Target)
    events.append(_make_event(f"{run_id}_exec", "ExecuteTransfer", 6.0, run_id=run_id, seq=4, sensor_id="sensor_payment", attributes={"transaction_id": tx_id, "amount": amount}))

    # 5. Ledger Audit
    events.append(_make_event(f"{run_id}_audit", "LedgerAudit", 8.0, run_id=run_id, seq=5, sensor_id="sensor_ledger", attributes={"transaction_id": tx_id}))

    contract = EvidenceContract(
        name="financial_compliance_preceding_approval",
        version="v1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="ExecuteTransfer",
            required_operation="ManagerApproval",
            max_window_seconds=60.0,
            join_on=("transaction_id",),
        ),
    )

    return WorkflowCase(
        family="financial_transfer",
        run_id=run_id,
        events=tuple(events),
        contract=contract,
        ground_truth_satisfied=not should_violate,
        target_operation="ExecuteTransfer",
        required_operation="ManagerApproval",
        sensor_ids=("sensor_gateway", "sensor_compliance", "sensor_approval", "sensor_payment", "sensor_ledger"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Customer Support with Agent Handoffs
# ═══════════════════════════════════════════════════════════════════════════════

def generate_support_handoff(run_id: str, seed: int, should_violate: bool = False) -> WorkflowCase:
    rng = random.Random(seed)  # noqa: S311
    ticket_id = f"ticket_{rng.randint(1000, 9999)}"

    events: list[ComputationalEvent] = []
    events.append(_make_event(f"{run_id}_ticket", "TicketCreated", 0.0, run_id=run_id, seq=1, sensor_id="sensor_crm", attributes={"transaction_id": ticket_id}))
    events.append(_make_event(f"{run_id}_triage", "TriageAnalysis", 1.5, run_id=run_id, seq=2, sensor_id="sensor_triage", attributes={"transaction_id": ticket_id}))

    if not should_violate:
        events.append(_make_event(f"{run_id}_ack", "SpecialistAck", 3.0, run_id=run_id, seq=3, sensor_id="sensor_specialist", attributes={"transaction_id": ticket_id}))

    events.append(_make_event(f"{run_id}_resolve", "ResolveTicket", 5.0, run_id=run_id, seq=4, sensor_id="sensor_crm", attributes={"transaction_id": ticket_id}))

    contract = EvidenceContract(
        name="support_specialist_ack_before_resolve",
        version="v1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="ResolveTicket",
            required_operation="SpecialistAck",
            max_window_seconds=120.0,
            join_on=("transaction_id",),
        ),
    )

    return WorkflowCase(
        family="customer_support",
        run_id=run_id,
        events=tuple(events),
        contract=contract,
        ground_truth_satisfied=not should_violate,
        target_operation="ResolveTicket",
        required_operation="SpecialistAck",
        sensor_ids=("sensor_crm", "sensor_triage", "sensor_specialist"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Code Generation with Sandboxed Execution
# ═══════════════════════════════════════════════════════════════════════════════

def generate_code_sandbox(run_id: str, seed: int, should_violate: bool = False) -> WorkflowCase:
    rng = random.Random(seed)  # noqa: S311
    task_id = f"task_{rng.randint(1000, 9999)}"

    events: list[ComputationalEvent] = []
    events.append(_make_event(f"{run_id}_prompt", "PromptAssembly", 0.0, run_id=run_id, seq=1, sensor_id="sensor_ide", attributes={"transaction_id": task_id}))
    events.append(_make_event(f"{run_id}_codegen", "GenerateCode", 2.0, run_id=run_id, seq=2, sensor_id="sensor_llm", attributes={"transaction_id": task_id}))

    if not should_violate:
        events.append(_make_event(f"{run_id}_sandbox", "SandboxExecution", 4.0, run_id=run_id, seq=3, sensor_id="sensor_sandbox", attributes={"transaction_id": task_id, "exit_code": 0}))

    events.append(_make_event(f"{run_id}_deploy", "DeployCode", 6.0, run_id=run_id, seq=4, sensor_id="sensor_deployer", attributes={"transaction_id": task_id}))

    contract = EvidenceContract(
        name="sandbox_verification_before_deployment",
        version="v1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="DeployCode",
            required_operation="SandboxExecution",
            max_window_seconds=180.0,
            join_on=("transaction_id",),
        ),
    )

    return WorkflowCase(
        family="code_generation",
        run_id=run_id,
        events=tuple(events),
        contract=contract,
        ground_truth_satisfied=not should_violate,
        target_operation="DeployCode",
        required_operation="SandboxExecution",
        sensor_ids=("sensor_ide", "sensor_llm", "sensor_sandbox", "sensor_deployer"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Multi-Document RAG Synthesizer
# ═══════════════════════════════════════════════════════════════════════════════

def generate_rag_synthesizer(run_id: str, seed: int, should_violate: bool = False) -> WorkflowCase:
    rng = random.Random(seed)  # noqa: S311
    query_id = f"query_{rng.randint(1000, 9999)}"

    events: list[ComputationalEvent] = []
    events.append(_make_event(f"{run_id}_embed", "QueryEmbedding", 0.0, run_id=run_id, seq=1, sensor_id="sensor_retriever", attributes={"transaction_id": query_id}))

    if not should_violate:
        events.append(_make_event(f"{run_id}_retrieve", "RetrieveDocs", 1.0, run_id=run_id, seq=2, sensor_id="sensor_retriever", attributes={"transaction_id": query_id, "doc_count": 5}))

    events.append(_make_event(f"{run_id}_synthesize", "SynthesizeResponse", 3.0, run_id=run_id, seq=3, sensor_id="sensor_generator", attributes={"transaction_id": query_id}))

    contract = EvidenceContract(
        name="retrieval_before_synthesis",
        version="v1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="SynthesizeResponse",
            required_operation="RetrieveDocs",
            max_window_seconds=60.0,
            join_on=("transaction_id",),
        ),
    )

    return WorkflowCase(
        family="multidoc_rag",
        run_id=run_id,
        events=tuple(events),
        contract=contract,
        ground_truth_satisfied=not should_violate,
        target_operation="SynthesizeResponse",
        required_operation="RetrieveDocs",
        sensor_ids=("sensor_retriever", "sensor_generator"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Long-Running Orchestrator with Retries
# ═══════════════════════════════════════════════════════════════════════════════

def generate_orchestrator_retries(run_id: str, seed: int, should_violate: bool = False) -> WorkflowCase:
    rng = random.Random(seed)  # noqa: S311
    job_id = f"job_{rng.randint(1000, 9999)}"

    events: list[ComputationalEvent] = []
    events.append(_make_event(f"{run_id}_dispatch", "DispatchJob", 0.0, run_id=run_id, seq=1, sensor_id="sensor_orchestrator", attributes={"transaction_id": job_id}))
    events.append(_make_event(f"{run_id}_fail", "WorkerFailure", 2.0, run_id=run_id, seq=2, sensor_id="sensor_worker", attributes={"transaction_id": job_id}))

    if not should_violate:
        events.append(_make_event(f"{run_id}_backoff", "BackoffWait", 3.0, run_id=run_id, seq=3, sensor_id="sensor_orchestrator", attributes={"transaction_id": job_id}))

    events.append(_make_event(f"{run_id}_retry", "WorkerRetry", 5.0, run_id=run_id, seq=4, sensor_id="sensor_worker", attributes={"transaction_id": job_id}))

    contract = EvidenceContract(
        name="backoff_before_retry",
        version="v1.0",
        rule=ContractRule(
            operator=ContractOperator.PRECEDING_EVENT,
            target_operation="WorkerRetry",
            required_operation="BackoffWait",
            max_window_seconds=120.0,
            join_on=("transaction_id",),
        ),
    )

    return WorkflowCase(
        family="orchestrator_retries",
        run_id=run_id,
        events=tuple(events),
        contract=contract,
        ground_truth_satisfied=not should_violate,
        target_operation="WorkerRetry",
        required_operation="BackoffWait",
        sensor_ids=("sensor_orchestrator", "sensor_worker"),
    )


ALL_WORKFLOW_GENERATORS = [
    generate_financial_transfer,
    generate_support_handoff,
    generate_code_sandbox,
    generate_rag_synthesizer,
    generate_orchestrator_retries,
]
