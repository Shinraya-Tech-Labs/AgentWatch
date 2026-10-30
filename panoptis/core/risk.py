"""
SAF-002 â€” Risk Scoring Engine.

Score 0..100 per action combining:
    command_danger + context_risk + goal_alignment_penalty
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from panoptis.core.safety import rm_targets_critical_path
from panoptis.core.schema import AgentEvent, RiskLevel

_DANGEROUS_CMD = [
    (re.compile(r"\brm\s+-rf?\s+/(?:\s|$)"), 95),
    (re.compile(r"\bcurl\b.*\|\s*sh\b"), 90),
    (re.compile(r"\bDROP\s+TABLE\b", re.I), 85),
    (re.compile(r"\bsudo\s+rm\b"), 80),
    (re.compile(r"\bchmod\s+777\b"), 60),
    (re.compile(r"\bdd\b.*of=/dev/"), 95),
    (re.compile(r"\bmkfs\."), 95),
    (re.compile(r"\bnetcat\b|\bnc\s+-e"), 80),
    (re.compile(r"\beval\b\s*\("), 50),
    (re.compile(r"\b/etc/passwd\b"), 70),
    (re.compile(r"\bAWS_SECRET|API_KEY|PRIVATE_KEY"), 75),
]


@dataclass
class RiskScore:
    command_danger: int
    context_risk: int
    goal_alignment_penalty: int
    total: int  # 0..100
    matched: list[str]


def _command_danger(raw: str | None) -> tuple[int, list[str]]:
    if not raw:
        return 0, []
    matched: list[str] = []
    score = 0
    for pat, weight in _DANGEROUS_CMD:
        if pat.search(raw):
            matched.append(pat.pattern)
            score = max(score, weight)
    # Recursive-force ``rm`` on a critical path across all flag spellings
    # (e.g. ``rm -r -f /``, ``rm --recursive --force /``) that the narrow
    # regex above misses.
    if rm_targets_critical_path(raw):
        score = max(score, 95)
        flag = "rm_recursive_force_critical_path"
        if flag not in matched:
            matched.append(flag)
    return score, matched


def _context_risk(event: AgentEvent) -> int:
    score = 0
    # Repeated retries in metadata
    if event.metadata.get("retry_count", 0) >= 3:
        score += 20
    # Already-confidence-tagged low confidence
    if event.confidence and event.confidence.overall_score < 0.3:
        score += 25
    # Safety field already set elevated
    if event.safety and event.safety.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        score += 30
    return min(score, 60)


def _goal_alignment(event: AgentEvent) -> int:
    # If a previous off-task signal tagged the event
    if "goal_drift" in event.tags or "off_task" in event.tags:
        return 20
    return 0


def score_event(event: AgentEvent) -> RiskScore:
    raw = ""
    if event.tool_call:
        # Use raw_command exclusively for pattern matching. The ToolCallData
        # validator ensures it is populated for command-like tools.
        raw = event.tool_call.raw_command or ""

    cmd_danger, matched = _command_danger(raw)
    ctx = _context_risk(event)
    goal = _goal_alignment(event)
    total = min(100, cmd_danger + (ctx // 2) + (goal // 2))
    return RiskScore(
        command_danger=cmd_danger,
        context_risk=ctx,
        goal_alignment_penalty=goal,
        total=total,
        matched=matched,
    )


__all__ = ["RiskScore", "score_event"]
