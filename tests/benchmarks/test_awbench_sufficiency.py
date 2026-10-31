"""Tests for AWBench-Sufficiency Benchmark Suite (Issue #737 / Hypothesis H-S1).

Verifies:
1. 5 Multi-agent workflow families (financial transfer, support handoff, code sandbox, RAG, orchestrator).
2. Combinatoric adversarial perturbation injectors.
3. Isolated evaluation oracle and ground-truth encryption/digests.
4. Metric harness and statistical calculations.
5. CI Acceptance Gate: Closed-world baseline hits >30% false certainty under packet loss
   while AgentWatch maintains <= 2% false certainty rate.
"""

from __future__ import annotations

from agentwatch.contracts.schema import EvidenceVerdict
from benchmarks.awbench.sufficiency.injectors import (
    PerturbationConfig,
    apply_adversarial_perturbations,
)
from benchmarks.awbench.sufficiency.metrics import (
    compute_metrics,
    format_markdown_report,
)
from benchmarks.awbench.sufficiency.oracle import (
    ClosedWorldBaselineEvaluator,
    GroundTruthOracle,
)
from benchmarks.awbench.sufficiency.runner import run_sufficiency_benchmark
from benchmarks.awbench.sufficiency.workflows import (
    generate_code_sandbox,
    generate_financial_transfer,
    generate_orchestrator_retries,
    generate_rag_synthesizer,
    generate_support_handoff,
)


def test_workflow_families_generation():
    # Test all 5 workflow generators
    w_fin = generate_financial_transfer("run_fin", seed=101, should_violate=False)
    assert w_fin.family == "financial_transfer"
    assert len(w_fin.events) == 5
    assert w_fin.ground_truth_satisfied is True

    w_fin_viol = generate_financial_transfer("run_fin_v", seed=102, should_violate=True)
    assert len(w_fin_viol.events) == 4  # approval omitted
    assert w_fin_viol.ground_truth_satisfied is False

    w_supp = generate_support_handoff("run_supp", seed=201)
    assert w_supp.family == "customer_support"
    assert len(w_supp.events) >= 3

    w_code = generate_code_sandbox("run_code", seed=301)
    assert w_code.family == "code_generation"
    assert len(w_code.events) >= 3

    w_rag = generate_rag_synthesizer("run_rag", seed=401)
    assert w_rag.family == "multidoc_rag"
    assert len(w_rag.events) >= 3

    w_orch = generate_orchestrator_retries("run_orch", seed=501)
    assert w_orch.family == "orchestrator_retries"
    assert len(w_orch.events) >= 3


def test_adversarial_perturbations_injection():
    workflow = generate_financial_transfer("run_pert", seed=42, should_violate=False)
    config = PerturbationConfig(
        packet_drop_rate=0.5,
        disabled_sensors=("sensor_approval",),
        clock_drift_ms=100.0,
        jitter_reorder_prob=0.5,
        seed=42,
    )

    res = apply_adversarial_perturbations(
        workflow.events,
        workflow.target_operation,
        workflow.required_operation,
        workflow.sensor_ids,
        config,
    )

    # Required operation (ManagerApproval on sensor_approval) must be unobserved
    assert res.has_unobserved_required_telemetry is True
    assert "sensor_approval" in res.disabled_sensors
    # Target operation (ExecuteTransfer) must survive
    surviving_ops = [e.operation for e in res.events]
    assert workflow.target_operation in surviving_ops


def test_isolated_oracle_integrity():
    oracle = GroundTruthOracle()
    digest1 = oracle.register_case(
        run_id="run_1",
        invariant_holds=True,
        required_op="Approval",
        target_op="Transfer",
        required_event_id="e_appr_1",
        is_telemetry_complete=True,
    )
    assert len(digest1) == 64  # SHA-256 digest
    assert oracle.is_answerable("run_1") is True

    # Case where telemetry is incomplete
    oracle.register_case(
        run_id="run_2",
        invariant_holds=True,
        required_op="Approval",
        target_op="Transfer",
        required_event_id="e_appr_2",
        is_telemetry_complete=False,
    )
    assert oracle.is_answerable("run_2") is False


def test_closed_world_baseline_evaluator():
    evaluator = ClosedWorldBaselineEvaluator()
    workflow = generate_financial_transfer("run_base", seed=77, should_violate=False)

    # Complete trace: evaluates to SUPPORTED
    verdict_complete = evaluator.evaluate(
        workflow.events,
        workflow.target_operation,
        workflow.required_operation,
    )
    assert verdict_complete == EvidenceVerdict.SUPPORTED

    # Drop the required approval event
    filtered_events = [e for e in workflow.events if e.operation != workflow.required_operation]
    verdict_dropped = evaluator.evaluate(
        filtered_events,
        workflow.target_operation,
        workflow.required_operation,
    )
    # Naive baseline asserts REFUTED (false certainty under missing data)
    assert verdict_dropped == EvidenceVerdict.REFUTED


def test_metrics_calculation_and_markdown_report():
    oracle = GroundTruthOracle()
    oracle.register_case("r1", True, "Req", "Targ", "e1", is_telemetry_complete=True)
    oracle.register_case("r2", True, "Req", "Targ", "e2", is_telemetry_complete=False)

    # Evaluator that abstains on r2 (open-world)
    open_results = [
        ("r1", EvidenceVerdict.SUPPORTED, ("obs_1",)),
        ("r2", EvidenceVerdict.INCONCLUSIVE, ()),
    ]
    m_open = compute_metrics("OpenWorld", 0.1, open_results, oracle)
    assert m_open.false_certainty_cases == 0
    assert m_open.false_certainty_rate == 0.0
    assert m_open.meets_critical_gate() is True

    # Evaluator that refutes r2 (closed-world naive)
    closed_results = [
        ("r1", EvidenceVerdict.SUPPORTED, ("obs_1",)),
        ("r2", EvidenceVerdict.REFUTED, ()),
    ]
    m_closed = compute_metrics("ClosedWorld", 0.1, closed_results, oracle)
    assert m_closed.false_certainty_cases == 1
    assert m_closed.false_certainty_rate == 1.0
    assert m_closed.meets_critical_gate() is False

    report = format_markdown_report([m_open, m_closed])
    assert "AWBench-Sufficiency" in report
    assert "PASSED" in report
    assert "FAILED" in report


def test_acceptance_criteria_h_s1_benchmark_suite():
    """Validates Issue #737 Acceptance Criteria:

    Benchmark runs deterministically in CI, proving that closed-world baselines
    hit >30% false certainty under packet loss while AgentWatch maintains <= 2%.
    """
    res = run_sufficiency_benchmark(
        drop_rates=(0.0, 0.1, 0.25, 0.4),
        cases_per_family=4,
        seed=42,
    )

    # Verify Markdown report generation
    assert res.markdown_report is not None
    assert len(res.metrics) > 0

    # Verify Acceptance Gate:
    # 1. Closed-world baseline exceeds 30% false certainty under packet loss
    assert res.baseline_exceeded_30_pct_false_certainty is True, (
        "Closed-world baseline should commit >30% false certainty under telemetry drops!"
    )

    # 2. AgentWatch EvidenceLab maintains <= 2% false certainty rate
    assert res.agentwatch_maintained_sub_2_pct_false_certainty is True, (
        "AgentWatch EvidenceLab must maintain <= 2% false certainty rate per hypothesis H-S1!"
    )
    assert res.passed() is True
