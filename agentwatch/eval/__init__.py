"""Batch evaluation and benchmarking for AgentWatch (issue #342)."""

from agentwatch.eval.retrieval_drift import (
    ALERT_RETRIEVAL_PRECISION_DRIFT,
    DriftReport,
    RetrievalDriftDetector,
)
from agentwatch.eval.runner import (
    DEFAULT_PASS_THRESHOLD,
    AgentRunner,
    EvalCase,
    EvalCaseResult,
    EvalReport,
    aggregate,
    evaluate_case,
    load_agent,
    load_dataset,
    run_eval,
)

__all__ = [
    "DEFAULT_PASS_THRESHOLD",
    "AgentRunner",
    "EvalCase",
    "EvalCaseResult",
    "EvalReport",
    "aggregate",
    "evaluate_case",
    "load_agent",
    "load_dataset",
    "run_eval",
    "RetrievalDriftDetector",
    "DriftReport",
    "ALERT_RETRIEVAL_PRECISION_DRIFT",
]
