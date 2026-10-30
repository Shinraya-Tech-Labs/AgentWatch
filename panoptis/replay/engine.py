"""
AgentWatch Replay Engine
Step-by-step execution replay, divergence detection, failure root-cause analysis,
and timeline reconstruction from stored event traces.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from panoptis.core.schema import (
    AgentEvent,
    AgentSession,
    EventType,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


class ReplaySpeed(str, Enum):
    INSTANT = "instant"  # No delays
    FAST = "fast"  # 10ms between steps
    NORMAL = "normal"  # Proportional to original timing
    SLOW = "slow"  # 2x original timing


class DivergenceType(str, Enum):
    TOOL_MISMATCH = "tool_mismatch"
    ARGUMENT_MISMATCH = "argument_mismatch"
    STATUS_MISMATCH = "status_mismatch"
    MISSING_EVENT = "missing_event"
    EXTRA_EVENT = "extra_event"
    TIMING_ANOMALY = "timing_anomaly"
    RISK_ESCALATION = "risk_escalation"


class FailureCause(str, Enum):
    REPEATED_TOOL_FAILURE = "repeated_tool_failure"
    GOAL_DRIFT = "goal_drift"
    SAFETY_BLOCK = "safety_block"
    INFINITE_LOOP = "infinite_loop"
    RESOURCE_NOT_FOUND = "resource_not_found"
    PERMISSION_DENIED = "permission_denied"
    HALLUCINATED_COMPLETION = "hallucinated_completion"
    CONTEXT_OVERFLOW = "context_overflow"
    UNKNOWN = "unknown"


# ─────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────


class ReplayStep:
    def __init__(self, index: int, event: AgentEvent, annotations: list[str] | None = None):
        self.index = index
        self.event = event
        self.annotations = annotations or []
        self.is_failure_point = False
        self.divergences: list[Divergence] = []


class Divergence:
    def __init__(
        self,
        divergence_type: DivergenceType,
        step_index: int,
        description: str,
        original_event: AgentEvent | None = None,
        replay_event: AgentEvent | None = None,
        severity: str = "medium",
    ):
        self.divergence_type = divergence_type
        self.step_index = step_index
        self.description = description
        self.original_event = original_event
        self.replay_event = replay_event
        self.severity = severity


class FailureAnalysis:
    def __init__(self):
        self.primary_cause: FailureCause = FailureCause.UNKNOWN
        self.contributing_factors: list[str] = []
        self.first_anomaly_step: int | None = None
        self.failure_step: int | None = None
        self.failure_event: AgentEvent | None = None
        self.tool_error_counts: dict[str, int] = defaultdict(int)
        self.repeated_tools: list[str] = []
        self.blocked_actions: list[AgentEvent] = []
        self.summary: str = ""
        self.recommendations: list[str] = []


class ReplaySession:
    def __init__(self, session: AgentSession, events: list[AgentEvent]):
        self.session = session
        self.events = events
        self.steps: list[ReplayStep] = []
        self.failure_analysis: FailureAnalysis | None = None
        self.divergences: list[Divergence] = []
        self.total_steps = len(events)
        self.current_step = 0
        self._built = False

    def build(self) -> ReplaySession:
        """Build step list and run analysis."""
        self.steps = [ReplayStep(i, e) for i, e in enumerate(self.events)]
        self.failure_analysis = _analyze_failures(self.steps)
        self._mark_failure_points()
        self._built = True
        return self

    def _mark_failure_points(self) -> None:
        if not self.failure_analysis:
            return
        fp = self.failure_analysis.failure_step
        fa = self.failure_analysis.first_anomaly_step
        for step in self.steps:
            if fp is not None and step.index == fp:
                step.is_failure_point = True
                step.annotations.append("🔴 FAILURE POINT")
            if fa is not None and step.index == fa:
                step.annotations.append("⚠️  FIRST ANOMALY")
            if step.event.is_blocked:
                step.annotations.append("🚫 BLOCKED by safety engine")
            if step.event.event_type == EventType.TOOL_ERROR:
                step.annotations.append("❌ Tool error")

    def step_at(self, index: int) -> ReplayStep | None:
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session.session_id,
            "agent_id": self.session.agent_id,
            "framework": self.session.framework.value,
            "started_at": self.session.started_at.isoformat(),
            "ended_at": self.session.ended_at.isoformat() if self.session.ended_at else None,
            "status": self.session.status.value,
            "goal": self.session.goal,
            "total_events": self.total_steps,
            "failure_analysis": _failure_analysis_to_dict(self.failure_analysis),
            "steps": [_step_to_dict(s) for s in self.steps],
        }


# ─────────────────────────────────────────────
# Failure analysis
# ─────────────────────────────────────────────


def _analyze_failures(steps: list[ReplayStep]) -> FailureAnalysis:
    analysis = FailureAnalysis()
    tool_call_sequence: list[str] = []
    tool_errors: dict[str, int] = defaultdict(int)
    blocked_events: list[AgentEvent] = []
    first_error_step: int | None = None

    for step in steps:
        event = step.event

        # Track tool errors
        if event.event_type == EventType.TOOL_ERROR:
            tool_name = (event.tool_result and event.tool_result.tool_name) or "unknown"
            tool_errors[tool_name] += 1
            if first_error_step is None:
                first_error_step = step.index
                analysis.first_anomaly_step = step.index

        # Track tool calls for loop detection
        if event.event_type == EventType.TOOL_CALL and event.tool_call:
            tool_call_sequence.append(event.tool_call.tool_name)

        # Track blocked events
        if event.is_blocked:
            blocked_events.append(event)
            if first_error_step is None:
                first_error_step = step.index
                analysis.first_anomaly_step = step.index

        # Detect failure terminal event
        if event.status == ExecutionStatus.FAILURE:
            analysis.failure_step = step.index
            analysis.failure_event = event

    analysis.tool_error_counts = dict(tool_errors)
    analysis.blocked_actions = blocked_events

    # Repeated tool detection (loop)
    if len(tool_call_sequence) >= 6:
        window = 3
        for i in range(len(tool_call_sequence) - window * 2):
            segment = tool_call_sequence[i : i + window]
            repeat = tool_call_sequence[i + window : i + window * 2]
            if segment == repeat:
                analysis.repeated_tools = segment
                break

    # Determine primary cause
    if blocked_events:
        analysis.primary_cause = FailureCause.SAFETY_BLOCK
        analysis.contributing_factors.append(
            f"{len(blocked_events)} action(s) blocked by safety engine"
        )

    elif analysis.repeated_tools:
        analysis.primary_cause = FailureCause.INFINITE_LOOP
        analysis.contributing_factors.append(f"Repeated tool sequence: {analysis.repeated_tools}")

    elif tool_errors:
        most_failed = max(tool_errors, key=lambda k: tool_errors[k])
        if tool_errors[most_failed] >= 3:
            analysis.primary_cause = FailureCause.REPEATED_TOOL_FAILURE
            analysis.contributing_factors.append(
                f"Tool '{most_failed}' failed {tool_errors[most_failed]} times"
            )

    elif analysis.failure_event:
        error_text = (analysis.failure_event.metadata or {}).get("error", "")
        if "permission" in error_text.lower() or "denied" in error_text.lower():
            analysis.primary_cause = FailureCause.PERMISSION_DENIED
        elif "not found" in error_text.lower() or "no such file" in error_text.lower():
            analysis.primary_cause = FailureCause.RESOURCE_NOT_FOUND
        else:
            analysis.primary_cause = FailureCause.UNKNOWN

    # Generate summary
    analysis.summary = _generate_summary(analysis)
    analysis.recommendations = _generate_recommendations(analysis)

    return analysis


def _generate_summary(analysis: FailureAnalysis) -> str:
    cause = analysis.primary_cause
    if cause == FailureCause.SAFETY_BLOCK:
        return (
            f"Execution halted: {len(analysis.blocked_actions)} dangerous action(s) "
            f"blocked by AgentWatch safety engine."
        )
    elif cause == FailureCause.INFINITE_LOOP:
        tools = ", ".join(analysis.repeated_tools)
        return f"Agent entered a repetitive loop calling: [{tools}] repeatedly without progress."
    elif cause == FailureCause.REPEATED_TOOL_FAILURE:
        counts = ", ".join(f"{k}({v}x)" for k, v in analysis.tool_error_counts.items())
        return f"Persistent tool failures prevented task completion: {counts}"
    elif cause == FailureCause.PERMISSION_DENIED:
        return "Agent attempted operations without sufficient permissions."
    elif cause == FailureCause.RESOURCE_NOT_FOUND:
        return "Agent attempted to access resources that do not exist."
    return "Execution failed. Root cause could not be determined automatically."


def _generate_recommendations(analysis: FailureAnalysis) -> list[str]:
    recs = []
    cause = analysis.primary_cause

    if cause == FailureCause.SAFETY_BLOCK:
        recs.append("Review blocked actions and adjust safety policy if legitimate.")
        recs.append("Use the approval workflow to allow specific high-risk actions.")

    elif cause == FailureCause.INFINITE_LOOP:
        recs.append("Add explicit loop-detection to the agent's planner prompt.")
        recs.append("Set a max_iterations limit on tool call sequences.")
        recs.append("Increase verbosity of the planner to surface stuck states.")

    elif cause == FailureCause.REPEATED_TOOL_FAILURE:
        recs.append("Check tool configuration and dependencies before running.")
        recs.append("Add retry logic with exponential backoff.")
        recs.append("Add fallback behavior when primary tools fail.")

    elif cause == FailureCause.RESOURCE_NOT_FOUND:
        recs.append("Verify paths and resource names exist before starting.")
        recs.append("Consider adding a pre-flight check step.")

    recs.append("Use `panoptis replay --session <id>` to inspect step-by-step.")
    return recs


# ─────────────────────────────────────────────
# Replay Engine
# ─────────────────────────────────────────────


class ReplayEngine:
    """
    Loads and replays captured AgentWatch sessions.
    Supports step-by-step navigation, divergence detection,
    and failure root-cause analysis.
    """

    def __init__(self, storage_path: Path | None = None):
        self._storage_path = storage_path or Path(".agentwatch/sessions")
        self._loaded_sessions: dict[str, ReplaySession] = {}

    def load_from_events(self, session: AgentSession, events: list[AgentEvent]) -> ReplaySession:
        """Load a replay session from in-memory events."""
        rs = ReplaySession(session=session, events=events)
        rs.build()
        self._loaded_sessions[session.session_id] = rs
        return rs

    def load_from_file(self, path: Path) -> ReplaySession:
        """Load a replay session from a JSON file."""
        with open(path) as f:
            data = json.load(f)

        session = AgentSession(**data["session"])
        events = [AgentEvent(**e) for e in data["events"]]
        return self.load_from_events(session, events)

    def save_to_file(self, rs: ReplaySession, path: Path | None = None) -> Path:
        """Persist a replay session to disk."""
        out_path = path or (self._storage_path / f"{rs.session.session_id}.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "session": rs.session.model_dump(mode="json"),
            "events": [e.model_dump_for_storage() for e in rs.events],
        }
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2, default=str)

        logger.info("Saved replay session to %s", out_path)
        return out_path

    def get_session(self, session_id: str) -> ReplaySession | None:
        return self._loaded_sessions.get(session_id)

    async def replay_async(
        self,
        rs: ReplaySession,
        speed: ReplaySpeed = ReplaySpeed.NORMAL,
        on_step: Callable[[ReplayStep], None] | None = None,
        start_step: int = 0,
        end_step: int | None = None,
    ) -> AsyncIterator[ReplayStep]:
        """
        Async generator that yields replay steps with optional timing.
        """
        steps = rs.steps[start_step:end_step]

        prev_ts: datetime | None = None

        for step in steps:
            # Compute delay
            if speed != ReplaySpeed.INSTANT and prev_ts:
                delta = (step.event.timestamp - prev_ts).total_seconds()
                delay = 0.0
                if speed == ReplaySpeed.FAST:
                    delay = 0.01
                elif speed == ReplaySpeed.NORMAL:
                    delay = min(delta, 2.0)  # Cap at 2s
                elif speed == ReplaySpeed.SLOW:
                    delay = min(delta * 2, 4.0)
                if delay > 0:
                    await asyncio.sleep(delay)

            prev_ts = step.event.timestamp
            if on_step:
                on_step(step)

            yield step

    def compare_sessions(
        self,
        session_a: ReplaySession,
        session_b: ReplaySession,
    ) -> list[Divergence]:
        """
        Compare two replay sessions for divergences.
        Useful for comparing original vs re-run executions.
        """
        divergences: list[Divergence] = []
        steps_a = session_a.steps
        steps_b = session_b.steps
        max_len = max(len(steps_a), len(steps_b))

        for i in range(max_len):
            if i >= len(steps_a):
                divergences.append(
                    Divergence(
                        divergence_type=DivergenceType.EXTRA_EVENT,
                        step_index=i,
                        description=f"Session B has extra event at step {i}: {steps_b[i].event.event_type.value}",
                        replay_event=steps_b[i].event,
                        severity="low",
                    )
                )
                continue
            if i >= len(steps_b):
                divergences.append(
                    Divergence(
                        divergence_type=DivergenceType.MISSING_EVENT,
                        step_index=i,
                        description=f"Session B is missing event at step {i}: {steps_a[i].event.event_type.value}",
                        original_event=steps_a[i].event,
                        severity="medium",
                    )
                )
                continue

            ea = steps_a[i].event
            eb = steps_b[i].event

            if ea.event_type != eb.event_type:
                divergences.append(
                    Divergence(
                        divergence_type=DivergenceType.TOOL_MISMATCH,
                        step_index=i,
                        description=f"Step {i}: event type differs ({ea.event_type.value} vs {eb.event_type.value})",
                        original_event=ea,
                        replay_event=eb,
                        severity="high",
                    )
                )

            elif ea.event_type == eb.event_type and ea.tool_call and eb.tool_call:
                if ea.tool_call.tool_name != eb.tool_call.tool_name:
                    divergences.append(
                        Divergence(
                            divergence_type=DivergenceType.TOOL_MISMATCH,
                            step_index=i,
                            description=(
                                f"Step {i}: tool name differs "
                                f"({ea.tool_call.tool_name} vs {eb.tool_call.tool_name})"
                            ),
                            original_event=ea,
                            replay_event=eb,
                            severity="high",
                        )
                    )
                elif ea.tool_call.arguments != eb.tool_call.arguments:
                    divergences.append(
                        Divergence(
                            divergence_type=DivergenceType.ARGUMENT_MISMATCH,
                            step_index=i,
                            description=f"Step {i}: tool arguments differ for {ea.tool_call.tool_name}",
                            original_event=ea,
                            replay_event=eb,
                            severity="medium",
                        )
                    )

            if ea.status != eb.status:
                divergences.append(
                    Divergence(
                        divergence_type=DivergenceType.STATUS_MISMATCH,
                        step_index=i,
                        description=f"Step {i}: status differs ({ea.status.value} vs {eb.status.value})",
                        original_event=ea,
                        replay_event=eb,
                        severity="high" if eb.status == ExecutionStatus.FAILURE else "medium",
                    )
                )

        return divergences


# ─────────────────────────────────────────────
# Serialization helpers
# ─────────────────────────────────────────────


def _step_to_dict(step: ReplayStep) -> dict[str, Any]:
    return {
        "index": step.index,
        "event": step.event.model_dump_for_storage(),
        "annotations": step.annotations,
        "is_failure_point": step.is_failure_point,
    }


def _failure_analysis_to_dict(fa: FailureAnalysis | None) -> dict[str, Any] | None:
    if not fa:
        return None
    return {
        "primary_cause": fa.primary_cause.value,
        "contributing_factors": fa.contributing_factors,
        "first_anomaly_step": fa.first_anomaly_step,
        "failure_step": fa.failure_step,
        "tool_error_counts": fa.tool_error_counts,
        "repeated_tools": fa.repeated_tools,
        "blocked_action_count": len(fa.blocked_actions),
        "summary": fa.summary,
        "recommendations": fa.recommendations,
    }
