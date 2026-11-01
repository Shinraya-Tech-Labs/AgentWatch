"""AgentWatch Protect: unified runtime enforcement for AI agent tool calls.

Quick start::

    from agentwatch.protect import ProtectionPipeline

    pipeline = ProtectionPipeline()


    @pipeline.guard(agent_id="coder", session_id="s1", objective="fix the failing test")
    def run_shell(command: str) -> str: ...

See :mod:`agentwatch.protect.pipeline` for the stage order and failure behaviour.
"""

from agentwatch.protect.approvals import ApprovalBroker, ApprovalOutcome, ApprovalRequest
from agentwatch.protect.control import ControlState
from agentwatch.protect.intent import GroundingConfig, IntentGrounder, openai_compatible_verifier
from agentwatch.protect.ledger import ProtectLedger
from agentwatch.protect.loops import LoopConfig, LoopDetector
from agentwatch.protect.model import (
    ActionBlocked,
    Decision,
    Finding,
    OutputInspection,
    Severity,
    Stage,
    ToolAction,
    Verdict,
)
from agentwatch.protect.output import OutputInspector
from agentwatch.protect.pipeline import ProtectionConfig, ProtectionPipeline
from agentwatch.protect.rbac import PolicyError, PolicyStore, RBACEngine, parse_policy
from agentwatch.protect.threats import ThreatAnalyzer

__all__ = [
    "ActionBlocked",
    "ApprovalBroker",
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
    "openai_compatible_verifier",
    "parse_policy",
]
