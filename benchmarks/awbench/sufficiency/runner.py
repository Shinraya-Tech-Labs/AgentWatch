"""AWBench-Sufficiency Runner (Issue #737 / H-S1).

Executes the benchmark across:
- 5 multi-agent workflow families.
- Grid of packet drop rates (0% to 40%) and sensor disabling perturbations.
- Both AgentWatch EvidenceLab and Naive Closed-World Baseline.
- Verifies Hypothesis H-S1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agentwatch.contracts.evaluator import evaluate_contract
from agentwatch.contracts.schema import EvidenceVerdict
from benchmarks.awbench.sufficiency.injectors import (
    PerturbationConfig,
    apply_adversarial_perturbations,
)
from benchmarks.awbench.sufficiency.metrics import (
    SufficiencyMetrics,
    compute_metrics,
    format_markdown_report,
)
from benchmarks.awbench.sufficiency.oracle import (
    ClosedWorldBaselineEvaluator,
    GroundTruthOracle,
)
from benchmarks.awbench.sufficiency.workflows import (
    ALL_WORKFLOW_GENERATORS,
    WorkflowCase,
)


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteResult:
    """Complete results from an AWBench-Sufficiency benchmark run."""

    metrics: tuple[SufficiencyMetrics, ...]
    markdown_report: str
    baseline_exceeded_30_pct_false_certainty: bool
    agentwatch_maintained_sub_2_pct_false_certainty: bool

    def passed(self) -> bool:
        return (
            self.agentwatch_maintained_sub_2_pct_false_certainty
            and self.baseline_exceeded_30_pct_false_certainty
        )


def run_sufficiency_benchmark(
    drop_rates: Sequence[float] = (0.0, 0.1, 0.2, 0.3, 0.4),
    cases_per_family: int = 10,
    seed: int = 42,
) -> BenchmarkSuiteResult:
    """Executes the full AWBench-Sufficiency benchmark suite."""
    oracle = GroundTruthOracle()
    baseline_evaluator = ClosedWorldBaselineEvaluator()

    all_metrics: list[SufficiencyMetrics] = []

    for drop_rate in drop_rates:
        agentwatch_results: list[tuple[str, EvidenceVerdict, Sequence[str]]] = []
        baseline_results: list[tuple[str, EvidenceVerdict, Sequence[str]]] = []

        case_idx = 0
        for gen in ALL_WORKFLOW_GENERATORS:
            for i in range(cases_per_family):
                case_idx += 1
                case_seed = seed + case_idx * 17
                run_id = f"awbench_{int(drop_rate * 100)}_{case_idx}"

                # Alternate between compliant cases (should hold) and genuine violation cases
                should_violate = i % 2 == 1
                workflow: WorkflowCase = gen(run_id, case_seed, should_violate=should_violate)

                # Configure perturbations
                pert_config = PerturbationConfig(
                    packet_drop_rate=drop_rate,
                    clock_drift_ms=50.0 if drop_rate > 0 else 0.0,
                    seed=case_seed,
                )

                pert_res = apply_adversarial_perturbations(
                    workflow.events,
                    workflow.target_operation,
                    workflow.required_operation,
                    workflow.sensor_ids,
                    pert_config,
                )

                # Find required event ID in unperturbed trace
                req_ev = next(
                    (e for e in workflow.events if e.operation == workflow.required_operation), None
                )
                req_ev_id = req_ev.event_id if req_ev else None

                # Register in isolated oracle
                is_complete = not pert_res.has_unobserved_required_telemetry
                oracle.register_case(
                    run_id=run_id,
                    invariant_holds=workflow.ground_truth_satisfied,
                    required_op=workflow.required_operation,
                    target_op=workflow.target_operation,
                    required_event_id=req_ev_id,
                    is_telemetry_complete=is_complete,
                )

                # 1. Evaluate with AgentWatch EvidenceLab
                aw_assessment = evaluate_contract(
                    pert_res.events,
                    workflow.contract,
                    pert_res.coverage,
                )
                agentwatch_results.append(
                    (
                        run_id,
                        aw_assessment.verdict,
                        aw_assessment.support_obs_ids + aw_assessment.refute_obs_ids,
                    )
                )

                # 2. Evaluate with Naive Closed-World Baseline
                base_verdict = baseline_evaluator.evaluate(
                    pert_res.events,
                    workflow.target_operation,
                    workflow.required_operation,
                )
                baseline_results.append(
                    (
                        run_id,
                        base_verdict,
                        (),
                    )
                )

        # Score both evaluators for this drop rate
        aw_metrics = compute_metrics(
            "AgentWatch EvidenceLab", drop_rate, agentwatch_results, oracle
        )
        base_metrics = compute_metrics("Closed-World Baseline", drop_rate, baseline_results, oracle)

        all_metrics.append(aw_metrics)
        all_metrics.append(base_metrics)

    md_report = format_markdown_report(all_metrics)

    # Check critical acceptance criteria:
    # 1. AgentWatch maintains false certainty rate <= 2% (0.02) across all drop rates
    aw_all_fcr = [
        m.false_certainty_rate for m in all_metrics if m.evaluator_name == "AgentWatch EvidenceLab"
    ]
    aw_maintained = all(fcr <= 0.02 for fcr in aw_all_fcr)

    # 2. Baseline exceeds 30% false certainty rate at high drop rates (e.g. >= 20%)
    high_loss_baseline_fcr = [
        m.false_certainty_rate
        for m in all_metrics
        if m.evaluator_name == "Closed-World Baseline" and m.drop_rate >= 0.20
    ]
    baseline_exceeded_30 = any(fcr >= 0.30 for fcr in high_loss_baseline_fcr)

    return BenchmarkSuiteResult(
        metrics=tuple(all_metrics),
        markdown_report=md_report,
        baseline_exceeded_30_pct_false_certainty=baseline_exceeded_30,
        agentwatch_maintained_sub_2_pct_false_certainty=aw_maintained,
    )
