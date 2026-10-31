"""AWBench-Sufficiency: Adversarial Missingness Oracle & Blind Split Verification (Issue #737)."""

from benchmarks.awbench.sufficiency.injectors import (
    PerturbationConfig,
    PerturbationResult,
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
    GroundTruthRecord,
)
from benchmarks.awbench.sufficiency.runner import (
    BenchmarkSuiteResult,
    run_sufficiency_benchmark,
)
from benchmarks.awbench.sufficiency.workflows import (
    ALL_WORKFLOW_GENERATORS,
    WorkflowCase,
    generate_code_sandbox,
    generate_financial_transfer,
    generate_orchestrator_retries,
    generate_rag_synthesizer,
    generate_support_handoff,
)

__all__ = [
    "ALL_WORKFLOW_GENERATORS",
    "BenchmarkSuiteResult",
    "ClosedWorldBaselineEvaluator",
    "GroundTruthOracle",
    "GroundTruthRecord",
    "PerturbationConfig",
    "PerturbationResult",
    "SufficiencyMetrics",
    "WorkflowCase",
    "apply_adversarial_perturbations",
    "compute_metrics",
    "format_markdown_report",
    "generate_code_sandbox",
    "generate_financial_transfer",
    "generate_orchestrator_retries",
    "generate_rag_synthesizer",
    "generate_support_handoff",
    "run_sufficiency_benchmark",
]
