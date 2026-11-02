"""AgentWatch Protect: unified runtime enforcement for AI agent tool calls.

Quick start::

    from panoptis.protect import ProtectionPipeline

    pipeline = ProtectionPipeline()


    @pipeline.guard(agent_id="coder", session_id="s1", objective="fix the failing test")
    def run_shell(command: str) -> str: ...

See :mod:`panoptis.protect.pipeline` for the stage order and failure behaviour.
"""

from panoptis.protect.approvals import (
    ApprovalBroker,
    ApprovalError,
    ApprovalOutcome,
    ApprovalRequest,
    action_digest,
)
from panoptis.protect.control import ControlState
from panoptis.protect.enforce import (
    aenforce_call,
    current_context,
    enforce_call,
    get_default_pipeline,
    protect_tool,
    protection_context,
    set_default_pipeline,
)
from panoptis.protect.intent import GroundingConfig, IntentGrounder, openai_compatible_verifier
from panoptis.protect.ledger import ProtectLedger
from panoptis.protect.loops import LoopConfig, LoopDetector
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
from panoptis.protect.rbac import PolicyError, PolicyStore, RBACEngine, parse_policy
from panoptis.protect.registry import ProtectedToolRegistry
from panoptis.protect.threats import ThreatAnalyzer

__all__ = [
    "ActionBlocked",
    "ApprovalBroker",
    "ApprovalError",
    "action_digest",
    "ApprovalOutcome",
    "ApprovalRequest",
    "ControlState",
    "Decision",
    "Finding",
    "GroundingConfig",
    "IntentGrounder",
    "LoopConfig",
    "LoopDetector",
    "OutputInspection",
    "OutputInspector",
    "PolicyError",
    "PolicyStore",
    "ProtectLedger",
    "ProtectionConfig",
    "ProtectionPipeline",
    "RBACEngine",
    "Severity",
    "Stage",
    "ThreatAnalyzer",
    "ToolAction",
    "Verdict",
    "aenforce_call",
    "current_context",
    "enforce_call",
    "get_default_pipeline",
    "openai_compatible_verifier",
    "protect_tool",
    "ProtectedToolRegistry",
    "protection_context",
    "set_default_pipeline",
    "parse_policy",
]
