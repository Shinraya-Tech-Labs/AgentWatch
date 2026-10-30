"""
RSN-012 — Trust Score per Session.

Aggregate 0–100 score from observable signals:
    - confidence history
    - hallucination flags
    - goal drift events
    - blocked actions
    - safety violations

Trend over session lifetime is reconstructed by recomputing the score on
each prefix of the event list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from panoptis.core.schema import AgentEvent, EventType, RiskLevel
from panoptis.reasoning.goal_drift import GoalDriftDetector
from panoptis.reasoning.hallucination import HallucinationClassifier, HallucinationRisk


@dataclass
class TrustScore:
    score: float  # 0..100
    confidence_component: float
    hallucination_component: float
    drift_component: float
    safety_component: float
    block_component: float
    trend: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "components": {
                "confidence": self.confidence_component,
                "hallucination": self.hallucination_component,
                "drift": self.drift_component,
                "safety": self.safety_component,
                "blocks": self.block_component,
            },
            "trend": self.trend,
        }

    @property
    def grade(self) -> str:
        if self.score >= 85:
            return "A"
        if self.score >= 70:
            return "B"
        if self.score >= 55:
            return "C"
        if self.score >= 40:
            return "D"
        return "F"


def _score_prefix(events: list[AgentEvent], goal: str | None) -> TrustScore:
    confidences = [
        e.confidence.overall_score
        for e in events
        if e.confidence and e.confidence.overall_score is not None
    ]
    confidence_avg = sum(confidences) / max(1, len(confidences)) if confidences else 1.0

    classifier = HallucinationClassifier()
    halluc_high = 0
    halluc_total = 0
    for e in events:
        classifier.observe(e)
        if e.event_type == EventType.TOOL_CALL:
            halluc_total += 1
            f = classifier.classify(e)
            if f.risk == HallucinationRisk.HIGH:
                halluc_high += 1
    halluc_ratio = halluc_high / halluc_total if halluc_total else 0.0

    drift_events = 0
    if goal:
        det = GoalDriftDetector()
        det.set_goal(goal)
        for e in events:
            det.evaluate(e)
        drift_events = det.report().drift_events
    drift_ratio = min(1.0, drift_events / 10)

    safety_violations = sum(
        1
        for e in events
        if e.safety and e.safety.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    )
    blocks = sum(1 for e in events if e.event_type == EventType.SAFETY_BLOCK)

    confidence_component = confidence_avg * 100
    hallucination_component = (1 - halluc_ratio) * 100
    drift_component = (1 - drift_ratio) * 100
    safety_component = max(0.0, 100 - safety_violations * 25)
    block_component = max(0.0, 100 - blocks * 15)

    overall = (
        0.30 * confidence_component
        + 0.20 * hallucination_component
        + 0.15 * drift_component
        + 0.20 * safety_component
        + 0.15 * block_component
    )
    return TrustScore(
        score=round(overall, 2),
        confidence_component=confidence_component,
        hallucination_component=hallucination_component,
        drift_component=drift_component,
        safety_component=safety_component,
        block_component=block_component,
    )


def compute_trust(events: list[AgentEvent], *, goal: str | None = None) -> TrustScore:
    if not events:
        return TrustScore(100.0, 100.0, 100.0, 100.0, 100.0, 100.0, trend=[])

    final = _score_prefix(events, goal)
    # Trend — sample 10 evenly spaced prefixes
    step = max(1, len(events) // 10)
    trend = [_score_prefix(events[: i + 1], goal).score for i in range(0, len(events), step)]
    final.trend = trend
    return final


__all__ = ["TrustScore", "compute_trust"]
