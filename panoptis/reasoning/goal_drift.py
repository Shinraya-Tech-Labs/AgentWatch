"""
RSN-003 — In-Session Goal Drift Detector.

Compares each step's apparent intent against the original session goal
using cosine similarity over hashed-token vectors (fallback) or
sentence-transformers when available.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from panoptis.core.schema import AgentEvent, EventType
from panoptis.scoring.drift import cosine, embed


@dataclass
class DriftSnapshot:
    step: int
    similarity: float
    drifted: bool
    excerpt: str


@dataclass
class DriftReport:
    goal: str
    snapshots: list[DriftSnapshot] = field(default_factory=list)
    max_drift: float = 0.0
    drift_events: int = 0


class GoalDriftDetector:
    """Track per-step alignment with the original goal."""

    def __init__(self, similarity_threshold: float = 0.25):
        self.threshold = similarity_threshold
        self.goal: str = ""
        self._goal_vec: list[float] = []
        self.snapshots: list[DriftSnapshot] = []

    def set_goal(self, goal: str) -> None:
        self.goal = goal
        self._goal_vec = embed(goal) if goal else []

    def evaluate(self, event: AgentEvent) -> DriftSnapshot | None:
        if not self._goal_vec:
            # Try to extract goal from a session_start
            if event.event_type == EventType.SESSION_START and event.goal:
                self.set_goal(event.goal)
            return None

        text = self._step_text(event)
        if not text:
            return None
        vec = embed(text)
        sim = cosine(vec, self._goal_vec)
        snap = DriftSnapshot(
            step=event.step_number,
            similarity=sim,
            drifted=sim < self.threshold,
            excerpt=text[:160],
        )
        self.snapshots.append(snap)
        return snap

    def _step_text(self, event: AgentEvent) -> str:
        if event.planner_output_preview:
            return event.planner_output_preview
        if event.tool_call:
            tc = event.tool_call
            return f"{tc.tool_name} {tc.raw_command or repr(tc.arguments)[:120]}"
        return event.prompt_preview or ""

    def report(self) -> DriftReport:
        snaps = list(self.snapshots)
        max_drift = max((1 - s.similarity for s in snaps), default=0.0)
        return DriftReport(
            goal=self.goal,
            snapshots=snaps,
            max_drift=max_drift,
            drift_events=sum(1 for s in snaps if s.drifted),
        )


__all__ = ["GoalDriftDetector", "DriftSnapshot", "DriftReport"]
