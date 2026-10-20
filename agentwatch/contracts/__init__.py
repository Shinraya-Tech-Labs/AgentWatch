"""AgentWatch Temporal Contract and Evidence Sufficiency Module (v3.1 EvidenceLab)."""

from agentwatch.contracts.evaluator import evaluate_contract
from agentwatch.contracts.incremental import IncrementalContractEvaluator
from agentwatch.contracts.remedies import get_remedy_for_missing_fact
from agentwatch.contracts.schema import (
    AssessmentStatus,
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceAssessment,
    EvidenceContract,
    EvidenceVerdict,
    MissingFact,
    MissingReason,
    Recoverability,
)
from agentwatch.contracts.stream import (
    AssessmentJob,
    AssessmentManager,
    AsyncContractStreamProcessor,
    get_assessment_manager,
)

__all__ = [
    "AssessmentJob",
    "AssessmentManager",
    "AssessmentStatus",
    "AsyncContractStreamProcessor",
    "ContractOperator",
    "ContractRule",
    "CoverageSnapshot",
    "EvidenceAssessment",
    "EvidenceContract",
    "EvidenceVerdict",
    "IncrementalContractEvaluator",
    "MissingFact",
    "MissingReason",
    "Recoverability",
    "evaluate_contract",
    "get_assessment_manager",
    "get_remedy_for_missing_fact",
]
