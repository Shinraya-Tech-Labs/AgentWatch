"""AgentWatch Temporal Contract and Evidence Sufficiency Module (v3.1 EvidenceLab)."""

from agentwatch.contracts.coverage import (
    CoverageProfileRegistry,
    SequenceContinuityTracker,
    build_coverage_profile,
    parse_event_sequence,
)
from agentwatch.contracts.evaluator import evaluate_contract
from agentwatch.contracts.irreversible import (
    SAFETY_INVARIANT_BREACH,
    IrreversibleInvariantMonitor,
)
from agentwatch.contracts.order import (
    CausalCycleError,
    CausalRelation,
    EventTimeWatermarkBuffer,
    HappensBeforeGraph,
    IngestionResult,
    IngestionStatus,
    IntervalPrecedence,
    TimestampInterval,
)
from agentwatch.contracts.remedies import get_remedy_for_missing_fact
from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    CoverageSnapshot,
    EvidenceAssessment,
    EvidenceContract,
    EvidenceVerdict,
    MissingFact,
    MissingReason,
    Recoverability,
    SequenceGap,
)
from agentwatch.contracts.streaming import (
    ObligationState,
    PendingObligation,
    StreamAssessmentDelta,
    StreamingContractEvaluator,
)

__all__ = [
    "CausalCycleError",
    "CausalRelation",
    "ContractOperator",
    "ContractRule",
    "CoverageProfileRegistry",
    "CoverageSnapshot",
    "EventTimeWatermarkBuffer",
    "EvidenceAssessment",
    "EvidenceContract",
    "EvidenceVerdict",
    "HappensBeforeGraph",
    "IngestionResult",
    "IngestionStatus",
    "IntervalPrecedence",
    "IrreversibleInvariantMonitor",
    "MissingFact",
    "MissingReason",
    "ObligationState",
    "PendingObligation",
    "Recoverability",
    "SAFETY_INVARIANT_BREACH",
    "SequenceContinuityTracker",
    "SequenceGap",
    "StreamAssessmentDelta",
    "StreamingContractEvaluator",
    "TimestampInterval",
    "build_coverage_profile",
    "evaluate_contract",
    "get_remedy_for_missing_fact",
    "parse_event_sequence",
]
