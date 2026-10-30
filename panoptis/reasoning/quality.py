"""
RSN-004 — Reasoning Quality Score.

5 dimensions:
    coherence          — steps follow logically
    completeness       — goal is fully addressed
    factual_grounding  — facts traceable to inputs
    goal_alignment     — actions track the goal
    safety             — no high-risk events
"""

from __future__ import annotations

from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus, RiskLevel
from panoptis.reasoning.goal_drift import GoalDriftDetector
from panoptis.reasoning.hallucination import HallucinationClassifier, HallucinationRisk


@dataclass
class QualityScore:
    coherence: float
    completeness: float
    factual_grounding: float
    goal_alignment: float
    safety: float

    @property
    def overall(self) -> float:
        return (
            self.coherence
            + self.completeness
            + self.factual_grounding
            + self.goal_alignment
            + self.safety
        ) / 5.0

    def to_dict(self) -> dict[str, float]:
        return {
            "coherence": self.coherence,
            "completeness": self.completeness,
            "factual_grounding": self.factual_grounding,
            "goal_alignment": self.goal_alignment,
            "safety": self.safety,
            "overall": self.overall,
        }


def compute_quality(
    events: list[AgentEvent],
    *,
    goal: str | None = None,
) -> QualityScore:
    if not events:
        return QualityScore(0.0, 0.0, 0.0, 0.0, 0.0)

    # Coherence — penalize repeats and dead-end tool calls
    tool_calls = [e for e in events if e.event_type == EventType.TOOL_CALL]
    tool_results = [
        e for e in events if e.event_type in (EventType.TOOL_RESULT, EventType.TOOL_ERROR)
    ]
    coherence = 1.0
    if tool_calls and tool_results:
        coherence = min(1.0, len(tool_results) / len(tool_calls))

    # Completeness — does the session reach AGENT_END/SESSION_END with success?
    completeness = 0.5
    for e in events:
        if e.event_type in (EventType.AGENT_END, EventType.SESSION_END):
            completeness = 1.0 if e.status == ExecutionStatus.SUCCESS else 0.2
            break

    # Factual grounding — fraction of tool calls without hallucination flags
    classifier = HallucinationClassifier()
    grounded_pass = 0
    grounded_total = 0
    for e in events:
        classifier.observe(e)
        if e.event_type == EventType.TOOL_CALL:
            grounded_total += 1
            flag = classifier.classify(e)
            if flag.risk == HallucinationRisk.LOW:
                grounded_pass += 1
    factual_grounding = grounded_pass / grounded_total if grounded_total else 1.0

    # Goal alignment — drift detector
    alignment = 1.0
    if goal:
        det = GoalDriftDetector()
        det.set_goal(goal)
        for e in events:
            det.evaluate(e)
        report = det.report()
        if report.snapshots:
            alignment = max(0.0, 1.0 - report.max_drift)

    # Safety — penalize any high/critical event
    safety = 1.0
    for e in events:
        if e.safety and e.safety.risk_level in (
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ):
            safety -= 0.25
    safety = max(0.0, safety)

    return QualityScore(
        coherence=coherence,
        completeness=completeness,
        factual_grounding=factual_grounding,
        goal_alignment=alignment,
        safety=safety,
    )


__all__ = ["QualityScore", "compute_quality"]
