"""Shared data model of the protection pipeline: actions, findings, verdicts, decisions.

Every stage of :class:`panoptis.protect.pipeline.ProtectionPipeline` produces
:class:`Finding` objects. A finding is explainable by construction: it names the stage and
rule that produced it, a human-readable message and the evidence (the argument path, the
matched text, the policy entry) that triggered it. The pipeline folds findings into one
:class:`Decision`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from panoptis.core.watcher import AgentWatchBlockedError


class Verdict(str, Enum):
    """What happens to a proposed tool call."""

    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE_HITL = "escalate_hitl"
    MUTATE_AND_ALLOW = "mutate_and_allow"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def score(self) -> float:
        return _SCORE[self]


_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
_SCORE = {
    Severity.INFO: 0.0,
    Severity.LOW: 0.2,
    Severity.MEDIUM: 0.5,
    Severity.HIGH: 0.8,
    Severity.CRITICAL: 1.0,
}


class Stage(str, Enum):
    IDENTITY = "identity"
    KILLSWITCH = "killswitch"
    RBAC = "rbac"
    THREAT = "threat"
    LOOP = "loop"
    INTENT = "intent"
    HITL = "hitl"
    COUNTERFACTUAL = "counterfactual"
    EXECUTION = "execution"
    OUTPUT = "output"
    PIPELINE = "pipeline"


@dataclass(frozen=True)
class Finding:
    """One explainable observation from one stage."""

    stage: Stage
    code: str
    severity: Severity
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    threat: str | None = None  # OWASP agentic vector (panoptis.security.owasp.OwaspVector)
    # what this finding alone asks for; the pipeline combines them
    recommends: Verdict = Verdict.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": self.evidence,
            "threat": self.threat,
            "recommends": self.recommends.value,
        }


@dataclass
class ToolAction:
    """A tool call an agent proposes, before it runs."""

    agent_id: str
    session_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    objective: str | None = None  # the user's task, when the integration knows it
    reasoning: str | None = None  # the model's stated reason for this call, if supplied
    source: str = "sdk"  # sdk | mcp-stdio | mcp-http | adapter:<name> | cli
    call_id: str = field(default_factory=lambda: f"act-{uuid.uuid4().hex[:16]}")
    proposed_at: float = field(default_factory=time.time)
    tenant_id: str = "default"  # from the authenticated principal, never from the agent
    trace_id: str | None = None  # the caller's trace (OTel trace id, run id, ...)
    parent_call_id: str | None = None  # the call that triggered this one (nested tools)
    # True when agent or session id were defaults, not supplied by the caller or context
    defaulted_identity: bool = False

    @property
    def scope(self) -> str:
        """Key for per-session state: sessions of different tenants never share it."""
        return f"{self.tenant_id}/{self.session_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "objective": self.objective,
            "reasoning": self.reasoning,
            "source": self.source,
            "proposed_at": self.proposed_at,
            "tenant_id": self.tenant_id,
            "trace_id": self.trace_id,
            "parent_call_id": self.parent_call_id,
        }


def changed_paths(before: Any, after: Any, path: str = "$") -> list[str]:
    """JSON paths whose value differs between ``before`` and ``after`` (for mutation records)."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        for k in sorted(set(before) | set(after), key=str):
            if k not in before or k not in after:
                out.append(f"{path}.{k}")
            else:
                out.extend(changed_paths(before[k], after[k], f"{path}.{k}"))
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            p
            for i, (x, y) in enumerate(zip(before, after, strict=True))
            for p in changed_paths(x, y, f"{path}[{i}]")
        ]
    return [] if before == after else [path]


def combine_risk(findings: list[Finding]) -> float:
    """Noisy-OR of finding severities: independent signals compound, one critical is 1.0."""
    p = 1.0
    for f in findings:
        p *= 1.0 - f.severity.score
    return round(1.0 - p, 4)


@dataclass
class Decision:
    """The pipeline's verdict for one action, with everything needed to explain it."""

    action: ToolAction
    verdict: Verdict
    risk_score: float
    findings: list[Finding] = field(default_factory=list)
    mutated_arguments: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None  # request id, outcome, decided_by
    timings_ms: dict[str, float] = field(default_factory=dict)
    decided_at: float = field(default_factory=time.time)
    policy_version: str | None = None
    threat_rules_version: str | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict in (Verdict.ALLOW, Verdict.MUTATE_AND_ALLOW)

    @property
    def primary(self) -> Finding | None:
        """The most severe finding that drove the verdict."""
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: (f.severity.rank, _VERDICT_RANK[f.recommends]))

    @property
    def reason(self) -> str:
        p = self.primary
        if p is None:
            return "no findings"
        return f"[{p.stage.value}:{p.code}] {p.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.action.call_id,
            "action": self.action.to_dict(),
            "verdict": self.verdict.value,
            "allowed": self.allowed,
            "risk_score": self.risk_score,
            "reason": self.reason,
            "findings": [f.to_dict() for f in self.findings],
            "mutated_arguments": self.mutated_arguments,
            "mutation": (
                {"changed_paths": changed_paths(self.action.arguments, self.mutated_arguments)}
                if self.mutated_arguments is not None
                else None
            ),
            "approval": self.approval,
            "timings_ms": self.timings_ms,
            "decided_at": self.decided_at,
            "policy_version": self.policy_version,
            "threat_rules_version": self.threat_rules_version,
        }


_VERDICT_RANK = {
    Verdict.ALLOW: 0,
    Verdict.MUTATE_AND_ALLOW: 1,
    Verdict.ESCALATE_HITL: 2,
    Verdict.BLOCK: 3,
}


def strongest(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: _VERDICT_RANK[v], default=Verdict.ALLOW)


@dataclass
class OutputInspection:
    """Result of inspecting what a tool returned, before the agent sees it."""

    safe: bool
    sanitized_output: Any
    findings: list[Finding] = field(default_factory=list)
    redactions: int = 0
    quarantined: int = 0
    withheld: bool = False  # the whole result was replaced (policy: block on injection)

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "findings": [f.to_dict() for f in self.findings],
            "redactions": self.redactions,
            "quarantined": self.quarantined,
            "withheld": self.withheld,
        }


class ActionBlocked(AgentWatchBlockedError):  # noqa: N818 - public API name
    """Raised by guarded calls when the pipeline does not allow the action.

    A subclass of :class:`panoptis.core.watcher.AgentWatchBlockedError`, so code that
    already handles PANOPTIS blocks keeps working.
    """

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(
            f"{decision.action.tool}: {decision.verdict.value}: {decision.reason}",
            tool_name=decision.action.tool,
            reasons=[f.message for f in decision.findings if f.recommends is Verdict.BLOCK]
            or [decision.reason],
        )
