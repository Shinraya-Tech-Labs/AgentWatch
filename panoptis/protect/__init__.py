"""AgentWatch Protect: unified runtime enforcement for AI agent tool calls.

Quick start::

    from panoptis.protect import ProtectionPipeline

    pipeline = ProtectionPipeline()


    @pipeline.guard(agent_id="coder", session_id="s1", objective="fix the failing test")
    def run_shell(command: str) -> str: ...

See :mod:`panoptis.protect.pipeline` for the stage order and failure behaviour.
"""

from panoptis.protect.approvals import ApprovalBroker, ApprovalOutcome, ApprovalRequest
from panoptis.protect.control import ControlState
from panoptis.protect.counterfactual import (
    CounterfactualConfig,
    CounterfactualEngine,
    CounterfactualResult,
)
from panoptis.protect.crescendo_drift import (
    ConversationTurn,
    CrescendoDriftMonitor,
    DriftAssessment,
    DriftStatus,
)
from panoptis.protect.enclave_attestation import (
    EnclaveAttestationDocument,
    EnclaveAttestationVerifier,
    EnclavePlatform,
    EnclaveVerificationConfig,
    EnclaveVerificationResult,
)
from panoptis.protect.enforce import (
    aenforce_call,
    current_context,
    enforce_call,
    get_default_pipeline,
    protect_tool,
    protection_context,
    set_default_pipeline,
)
from panoptis.protect.formal_verifier import (
    FormalFinding,
    FormalVerificationCertificate,
    PolicyFormalVerifier,
    VerificationStatus,
)
from panoptis.protect.ifc_lattice import (
    DEFAULT_SINK_CLEARANCES,
    InformationFlowEngine,
    SecurityLabel,
    TaintContext,
)
from panoptis.protect.integrity import RuntimeIntegrityGuard
from panoptis.protect.intent import GroundingConfig, IntentGrounder, openai_compatible_verifier
from panoptis.protect.kernel_sandbox import (
    KernelSandbox,
    KernelSandboxConfig,
    KernelSandboxStatus,
)
from panoptis.protect.ledger import ProtectLedger
from panoptis.protect.loops import LoopConfig, LoopDetector
from panoptis.protect.ltl_trajectory import LTLTrajectoryVerifier, TrajectoryStep
from panoptis.protect.memory_sentry import (
    MemoryInspectionResult,
    MemoryRecord,
    MemoryRiskType,
    MemorySentry,
    MemoryTrustLevel,
)
from panoptis.protect.model import (
    ActionBlocked,
    Decision,
    Finding,
    OutputInspection,
    Severity,
    Stage,
    ToolAction,
    Verdict,
)
from panoptis.protect.output import OutputInspector
from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline
from panoptis.protect.pqc_attestation import (
    PQCAlgorithm,
    PQCKeyPair,
    PQCSigner,
    SovereignAttestationCertificate,
    SovereignAttestationEngine,
    StarkFRICommitment,
    StarkProof,
    StarkRecursiveProver,
    StarkRecursiveVerifier,
)
from panoptis.protect.rag_sanitizer import (
    RAGPoisonStatus,
    RAGSanitizationResult,
    RAGSanitizer,
)
from panoptis.protect.rbac import PolicyError, PolicyStore, RBACEngine, parse_policy
from panoptis.protect.taint_tracker import DynamicTaintTracker
from panoptis.protect.temporal_simulator import (
    CounterfactualHazardProof,
    DownstreamBlastRadiusEvaluator,
    FastForwardSimulationEngine,
    SimulatedStep,
    SimulationConfig,
    TemporalSimulationResult,
    TrajectoryBranch,
)
from panoptis.protect.threats import ThreatAnalyzer
from panoptis.protect.transactor import SpeculativeSideEffect, SpeculativeTransactor
from panoptis.protect.wasi_sandbox import (
    SandboxExecutionError,
    SandboxResult,
    SnapshotDiff,
    WasiSandbox,
    WasiSandboxConfig,
    WorkspaceSnapshot,
)

__all__ = [
    "ActionBlocked",
    "ApprovalBroker",
    "ApprovalOutcome",
    "ApprovalRequest",
    "ControlState",
    "ConversationTurn",
    "CounterfactualConfig",
    "CounterfactualEngine",
    "CounterfactualHazardProof",
    "CounterfactualResult",
    "CrescendoDriftMonitor",
    "DEFAULT_SINK_CLEARANCES",
    "Decision",
    "DownstreamBlastRadiusEvaluator",
    "DriftAssessment",
    "DriftStatus",
    "DynamicTaintTracker",
    "EnclaveAttestationDocument",
    "EnclaveAttestationVerifier",
    "EnclavePlatform",
    "EnclaveVerificationConfig",
    "EnclaveVerificationResult",
    "FastForwardSimulationEngine",
    "Finding",
    "FormalFinding",
    "FormalVerificationCertificate",
    "GroundingConfig",
    "InformationFlowEngine",
    "IntentGrounder",
    "KernelSandbox",
    "KernelSandboxConfig",
    "KernelSandboxStatus",
    "LTLTrajectoryVerifier",
    "LoopConfig",
    "LoopDetector",
    "MemoryInspectionResult",
    "MemoryRecord",
    "MemoryRiskType",
    "MemorySentry",
    "MemoryTrustLevel",
    "OutputInspection",
    "OutputInspector",
    "PQCAlgorithm",
    "PQCKeyPair",
    "PQCSigner",
    "PolicyError",
    "PolicyFormalVerifier",
    "PolicyStore",
    "ProtectLedger",
    "ProtectionConfig",
    "ProtectionPipeline",
    "RAGPoisonStatus",
    "RAGSanitizationResult",
    "RAGSanitizer",
    "RBACEngine",
    "RuntimeIntegrityGuard",
    "SandboxExecutionError",
    "SandboxResult",
    "SecurityLabel",
    "SimulatedStep",
    "SimulationConfig",
    "SnapshotDiff",
    "Severity",
    "SovereignAttestationCertificate",
    "SovereignAttestationEngine",
    "SpeculativeSideEffect",
    "SpeculativeTransactor",
    "Stage",
    "StarkFRICommitment",
    "StarkProof",
    "StarkRecursiveProver",
    "StarkRecursiveVerifier",
    "TaintContext",
    "TemporalSimulationResult",
    "ThreatAnalyzer",
    "ToolAction",
    "TrajectoryBranch",
    "TrajectoryStep",
    "Verdict",
    "VerificationStatus",
    "WasiSandbox",
    "WasiSandboxConfig",
    "WorkspaceSnapshot",
    "aenforce_call",
    "current_context",
    "enforce_call",
    "get_default_pipeline",
    "openai_compatible_verifier",
    "protect_tool",
    "protection_context",
    "set_default_pipeline",
    "parse_policy",
]
