"""
RSN-011 — Why This Was Blocked AI Explainer.

When an action is blocked, generate a plain-English explanation:
    - which rule / threshold triggered
    - what the agent was trying to do
    - what the safe alternative would be

Output is fully template-driven; if a custom LLM judge is wired in,
its rationale is used to enrich the explanation.
"""

from __future__ import annotations

from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, RiskLevel, SafetyCheckData


@dataclass
class BlockExplanation:
    headline: str
    detail: str
    rule_triggered: str
    suggested_alternative: str


_TOOL_ALTERNATIVES = {
    "bash": "Run a more specific, scoped command in a sandboxed shell.",
    "shell": "Run a more specific, scoped command in a sandboxed shell.",
    "rm": "Use `git rm` if removing tracked files, or confine to a scratch dir.",
    "curl": "Use a typed HTTP client with an explicit allow-list of endpoints.",
    "write_file": "Write to an approved workspace path inside the project root.",
    "subprocess_exec": "Use an audited tool wrapper with parameter validation.",
}


def _alt_for(event: AgentEvent) -> str:
    if event.tool_call:
        name = event.tool_call.tool_name.lower()
        for key, alt in _TOOL_ALTERNATIVES.items():
            if key in name:
                return alt
    return "Ask the operator to approve the action explicitly, or reduce its scope."


def explain(event: AgentEvent) -> BlockExplanation:
    safety: SafetyCheckData | None = event.safety
    if safety is None:
        return BlockExplanation(
            headline="Action blocked",
            detail="An action was blocked but no safety record was attached.",
            rule_triggered="unspecified",
            suggested_alternative="Review trace logs for the upstream safety check.",
        )

    rule = ", ".join(safety.matched_policies) or "unspecified_policy"
    reasons = "; ".join(safety.reasons) or "no_explicit_reason"

    tool_name = event.tool_call.tool_name if event.tool_call else "n/a"
    raw_cmd = event.tool_call.raw_command if event.tool_call and event.tool_call.raw_command else ""

    if safety.risk_level == RiskLevel.CRITICAL:
        headline = f"Critical: {tool_name} blocked"
    elif safety.risk_level == RiskLevel.HIGH:
        headline = f"High-risk {tool_name} blocked"
    else:
        headline = f"{tool_name} held for review"

    detail_parts = [
        f"The agent attempted to invoke `{tool_name}`",
    ]
    if raw_cmd:
        detail_parts.append(f"with command `{raw_cmd[:160]}`")
    detail_parts.append(
        f"but the safety engine flagged it ({safety.risk_level.value}, "
        f"score={safety.risk_score:.2f}). Reasons: {reasons}."
    )

    return BlockExplanation(
        headline=headline,
        detail=" ".join(detail_parts),
        rule_triggered=rule,
        suggested_alternative=_alt_for(event),
    )


__all__ = ["BlockExplanation", "explain"]
