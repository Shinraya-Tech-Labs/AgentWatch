"""AgentWatch Temporal Contract and Evidence Sufficiency Module (v3.1 EvidenceLab)."""

from panoptis.contracts.coverage import (
    CoverageProfileRegistry,
    SequenceContinuityTracker,
    build_coverage_profile,
    parse_event_sequence,
)
from panoptis.contracts.evaluator import evaluate_contract
from panoptis.contracts.irreversible import (
    SAFETY_INVARIANT_BREACH,
    IrreversibleInvariantMonitor,
)
from panoptis.contracts.order import (
    CausalCycleError,
    CausalRelation,
    EventTimeWatermarkBuffer,
    HappensBeforeGraph,
    IngestionResult,
    IngestionStatus,
    IntervalPrecedence,
    TimestampInterval,
)
from panoptis.contracts.remedies import get_remedy_for_missing_fact
from panoptis.contracts.schema import (
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
from panoptis.contracts.streaming import (
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
