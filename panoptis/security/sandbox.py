"""
SAF-010 — Live Safety Sandbox.

A pure-simulation harness: the user types an agent command, PANOPTIS
shows what would be blocked, what would pass, and why. No real agent
involved. Used in the dashboard's onboarding flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from panoptis.core.blast_radius import BlastRadiusEstimator
from panoptis.core.injection import scan_text
from panoptis.core.policy_dsl import PolicyAction, PolicyEngine
from panoptis.core.risk import score_event
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ToolCallData,
)
from panoptis.security.exfiltration import detect as detect_exfil


@dataclass
class SandboxResult:
    command: str
    blocked: bool
    risk_score: int
    blast_radius_score: int
    policy_action: str
    exfil_findings: list[str]
    injection_findings: list[str]
    explanation: str
    threat_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "blocked": self.blocked,
            "risk_score": self.risk_score,
            "blast_radius_score": self.blast_radius_score,
            "policy_action": self.policy_action,
            "exfil_findings": self.exfil_findings,
            "injection_findings": self.injection_findings,
            "explanation": self.explanation,
            "threat_path": self.threat_path,
        }


class LiveSandbox:
    """Run a simulated agent command through the full safety stack."""

    def __init__(
        self,
        policy: PolicyEngine | None = None,
        blast_estimator: BlastRadiusEstimator | None = None,
    ):
        self.policy = policy or PolicyEngine()
        self.blast = blast_estimator or BlastRadiusEstimator()

    def simulate(self, tool: str, command: str) -> SandboxResult:
        ev = AgentEvent(
            session_id="sandbox",
            agent_id="sandbox",
            framework=AgentFramework.CUSTOM,
            event_type=EventType.TOOL_CALL,
            tool_call=ToolCallData(
                tool_name=tool,
                arguments={"command": command},
                raw_command=command,
            ),
        )

        # 1. Risk score
        risk = score_event(ev)

        # 2. Blast radius
        blast = self.blast.estimate(ev)
        requires_approval = self.blast.requires_approval(blast)

        # 3. Policy
        decision = self.policy.evaluate(ev)

        # 4. Exfil & injection
        exfil = detect_exfil(ev)
        injection = scan_text(command)

        threat_path = []
        if risk.matched:
            threat_path.append(f"command_match:{risk.matched[0]}")
        if blast.downstream_services:
            threat_path.append(f"service_hit:{blast.downstream_services[0]}")
        if exfil:
            threat_path.append(f"exfil:{exfil[0].destination}")
        if injection.detected:
            threat_path.append(f"injection:{injection.findings[0].pattern}")

        blocked = (
            risk.total >= 70
            or requires_approval
            or decision.action == PolicyAction.BLOCK
            or bool(exfil)
        )

        explanation = self._explain(
            blocked=blocked,
            risk_score=risk.total,
            blast_score=blast.score,
            policy=decision.action.value,
            exfil_n=len(exfil),
            injection=injection.detected,
        )

        return SandboxResult(
            command=command,
            blocked=blocked,
            risk_score=risk.total,
            blast_radius_score=blast.score,
            policy_action=decision.action.value,
            exfil_findings=[f.destination for f in exfil],
            injection_findings=[f.pattern for f in injection.findings],
            explanation=explanation,
            threat_path=threat_path,
        )

    def _explain(
        self,
        *,
        blocked: bool,
        risk_score: int,
        blast_score: int,
        policy: str,
        exfil_n: int,
        injection: bool,
    ) -> str:
        if not blocked:
            return f"Allowed — risk={risk_score}, blast={blast_score}, policy={policy}."
        parts = []
        if risk_score >= 70:
            parts.append(f"high risk score {risk_score}/100")
        if blast_score >= 60:
            parts.append(f"large blast radius {blast_score}/100")
        if exfil_n:
            parts.append(f"{exfil_n} exfiltration pattern(s)")
        if injection:
            parts.append("prompt injection signal")
        return "Blocked: " + ", ".join(parts) + "."


__all__ = ["LiveSandbox", "SandboxResult"]
