"""
RSN-009 — Dual-Level Goal Evaluation.

Step-level: did each reasoning step make sense?
Session-level: did the agent achieve the original goal?
"""

from __future__ import annotations

from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus
from panoptis.reasoning.goal_drift import GoalDriftDetector
from panoptis.reasoning.quality import compute_quality


@dataclass
class DualEvalResult:
    step_passed: int
    step_failed: int
    step_score: float  # mean step alignment
    session_achieved: bool  # did session end successfully on-goal
    session_score: float  # 0..1 overall quality
    notes: list[str]


def dual_evaluate(events: list[AgentEvent], *, goal: str) -> DualEvalResult:
    # Step-level: drift detector
    det = GoalDriftDetector()
    det.set_goal(goal)
    step_pass = step_fail = 0
    for e in events:
        snap = det.evaluate(e)
        if snap is None:
            continue
        if snap.drifted:
            step_fail += 1
        else:
            step_pass += 1
    step_total = step_pass + step_fail
    step_score = step_pass / step_total if step_total else 1.0

    # Session-level: compute aggregate quality and check end status
    quality = compute_quality(events, goal=goal)
    final = next(
        (
            e
            for e in reversed(events)
            if e.event_type in (EventType.SESSION_END, EventType.AGENT_END)
        ),
        None,
    )
    achieved = (
        final is not None and final.status == ExecutionStatus.SUCCESS and quality.overall >= 0.6
    )

    notes: list[str] = []
    if step_score < 0.6 and achieved:
        notes.append("aced_steps_but_session_off_goal")
    if step_score >= 0.8 and not achieved:
        notes.append("good_steps_but_failed_session")

    return DualEvalResult(
        step_passed=step_pass,
        step_failed=step_fail,
        step_score=step_score,
        session_achieved=achieved,
        session_score=quality.overall,
        notes=notes,
    )


__all__ = ["DualEvalResult", "dual_evaluate"]
