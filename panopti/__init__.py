"""Panopti: The All-Seeing Pre-Execution Guardrail & Forensic Engine for Autonomous AI.

Panopti is the successor to AgentWatch, providing pre-execution contract verification,
causal confidence decay tracking, cryptographic witness proofs, and deterministic forensic
shadow replay for agent swarms.
"""

from __future__ import annotations

try:
    from agentwatch._version import __version__
except (ImportError, ModuleNotFoundError):
    __version__ = "0.2.0"

from panopti.attest import (
    AttestationEngine,
    PanoptiAttestationSeal,
)
from panopti.contracts import (
    Contract,
    ContractRegistry,
    ContractViolationError,
    Disallows,
    MaxFrequency,
    RequiresPrior,
)
from panopti.court import (
    WitnessChain,
    WitnessEntry,
)
from panopti.egress import (
    EgressGuard,
    SSRFBlockedError,
)
from panopti.killswitch import (
    EmergencyKillSwitch,
    EmergencyStopTriggered,
)
from panopti.mesh import (
    SwarmAgentNode,
    SwarmMesh,
)
from panopti.replay import (
    DropStepPerturbation,
    MutateResultPerturbation,
    Perturbation,
    RecordedCall,
    ReplayReport,
    ReplayTape,
    ShadowReplayer,
)
from panopti.shield import (
    AntiTamperShield,
    TamperAttemptBlocked,
)
from panopti.synth import (
    PolicySynthesizer,
    SynthesizedInvariant,
)

try:
    from agentwatch.causality import (
        CUMULATIVE_CONFIDENCE_COLLAPSE,
        ConfidenceCollapseWarning,
        ConfidenceDecayTracker,
    )
    from agentwatch.circuit_breaker import (
        AgentLoopError,
        AgentLoopException,
        CircuitBreaker,
        CircuitState,
        CircuitThresholds,
        LoopDetector,
        TripReason,
    )
    from agentwatch.core.watcher import (
        AgentWatchBlockedError,
        GenericAdapter,
        detect_framework,
        detect_framework_label,
        watch,
    )
    from panopti.doctor import run_diagnostics
    from panopti.guard import (
        GuardDecision,
        PanoptiGuard,
        guard,
    )

    PanoptiBlockedError = AgentWatchBlockedError
except ImportError:  # pragma: no cover
    watch = None  # type: ignore[assignment]
    guard = None  # type: ignore[assignment]
    detect_framework = None  # type: ignore[assignment]
    detect_framework_label = None  # type: ignore[assignment]
    GenericAdapter = None  # type: ignore[assignment]
    AgentWatchBlockedError = Exception  # type: ignore[assignment,misc]
    PanoptiBlockedError = AgentWatchBlockedError
    CircuitBreaker = None  # type: ignore[assignment]
    CircuitState = None  # type: ignore[assignment]
    CircuitThresholds = None  # type: ignore[assignment]
    TripReason = None  # type: ignore[assignment]
    LoopDetector = None  # type: ignore[assignment]
    AgentLoopError = Exception  # type: ignore[assignment,misc]
    AgentLoopException = AgentLoopError
    PanoptiGuard = None  # type: ignore[assignment]
    GuardDecision = None  # type: ignore[assignment]
    ConfidenceDecayTracker = None  # type: ignore[assignment]
    CUMULATIVE_CONFIDENCE_COLLAPSE = "CUMULATIVE_CONFIDENCE_COLLAPSE"
    ConfidenceCollapseWarning = None  # type: ignore[assignment]
    run_diagnostics = None  # type: ignore[assignment]

__all__ = [
    "__version__",
    "guard",
    "watch",
    "detect_framework",
    "detect_framework_label",
    "GenericAdapter",
    "PanoptiBlockedError",
    "AgentWatchBlockedError",
    "CircuitBreaker",
    "CircuitState",
    "CircuitThresholds",
    "TripReason",
    "LoopDetector",
    "AgentLoopError",
    "AgentLoopException",
    "ConfidenceDecayTracker",
    "CUMULATIVE_CONFIDENCE_COLLAPSE",
    "ConfidenceCollapseWarning",
    "PanoptiGuard",
    "GuardDecision",
    "Contract",
    "ContractRegistry",
    "RequiresPrior",
    "Disallows",
    "MaxFrequency",
    "ContractViolationError",
    "WitnessEntry",
    "WitnessChain",
    "SwarmAgentNode",
    "SwarmMesh",
    "run_diagnostics",
    "RecordedCall",
    "ReplayTape",
    "ShadowReplayer",
    "ReplayReport",
    "Perturbation",
    "DropStepPerturbation",
    "MutateResultPerturbation",
    "PanoptiAttestationSeal",
    "AttestationEngine",
    "PolicySynthesizer",
    "SynthesizedInvariant",
    "EmergencyKillSwitch",
    "EmergencyStopTriggered",
    "AntiTamperShield",
    "TamperAttemptBlocked",
    "EgressGuard",
    "SSRFBlockedError",
]
