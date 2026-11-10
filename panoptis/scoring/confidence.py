"""
PANOPTIS Confidence Scoring Engine
Workflow anomaly detection and execution consistency analysis.

NOTE: This is NOT cognition inspection or chain-of-thought extraction.
This is behavioral/structural analysis of observable execution artifacts.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from panoptis.core.schema import (
    AgentEvent,
    ConfidenceData,
    EventType,
    ExecutionStatus,
    RiskLevel,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Anomaly flag constants
# ─────────────────────────────────────────────

ANOMALY_GOAL_DRIFT = "goal_drift"
ANOMALY_TOOL_LOOP = "tool_loop"
ANOMALY_REPEATED_FAILURES = "repeated_failures"
ANOMALY_HALLUCINATED_SUCCESS = "hallucinated_success"
ANOMALY_NO_PROGRESS = "no_progress"
ANOMALY_HIGH_RISK_ACTION = "high_risk_action"
ANOMALY_MEMORY_CONTRADICTION = "memory_contradiction"
ANOMALY_IRRELEVANT_TOOLS = "irrelevant_tools"
ANOMALY_CONTEXT_EXPLOSION = "context_explosion"


@dataclass
class ScoringResult:
    overall_score: float  # 0.0 = very anomalous, 1.0 = healthy
    goal_alignment: float
    consistency_score: float
    anomaly_flags: list[str] = field(default_factory=list)
    explanation: str = ""
    component_scores: dict[str, float] = field(default_factory=dict)

    def to_confidence_data(self) -> ConfidenceData:
        return ConfidenceData(
            overall_score=self.overall_score,
            goal_alignment=self.goal_alignment,
            consistency_score=self.consistency_score,
            anomaly_flags=self.anomaly_flags,
            explanation=self.explanation,
        )


# ─────────────────────────────────────────────
# Individual heuristic checkers
# ─────────────────────────────────────────────


def _check_tool_loop(events: list[AgentEvent]) -> tuple[float, list[str]]:
    """Detect repetitive tool call sequences with no progress."""
    tool_calls = [
        e.tool_call.tool_name for e in events if e.event_type == EventType.TOOL_CALL and e.tool_call
    ]
    if len(tool_calls) < 4:
        return 1.0, []

    for window in (3, 2):
        for i in range(len(tool_calls) - window * 2 + 1):
            seg = tuple(tool_calls[i : i + window])
            rep = tuple(tool_calls[i + window : i + window * 2])
            if seg == rep:
                penalty = 0.4 if window == 2 else 0.3
                return penalty, [ANOMALY_TOOL_LOOP]

    counts = Counter(tool_calls)
    most_common, freq = counts.most_common(1)[0]
    if freq >= 5 and len(tool_calls) >= 6:
        ratio = freq / len(tool_calls)
        if ratio > 0.7:
            return 0.5, [ANOMALY_TOOL_LOOP]

    return 1.0, []


def _check_repeated_failures(events: list[AgentEvent]) -> tuple[float, list[str]]:
    """Penalize runs with many tool errors."""
    errors = sum(1 for e in events if e.event_type == EventType.TOOL_ERROR)
    total_tool_events = sum(
        1
        for e in events
        if e.event_type in (EventType.TOOL_CALL, EventType.TOOL_RESULT, EventType.TOOL_ERROR)
    )
    if total_tool_events == 0:
        return 1.0, []

    error_rate = errors / total_tool_events
    if error_rate > 0.5:
        return max(0.2, 1.0 - error_rate), [ANOMALY_REPEATED_FAILURES]
    if error_rate > 0.25:
        return 0.7, [ANOMALY_REPEATED_FAILURES]
    return 1.0, []


def _check_hallucinated_success(events: list[AgentEvent]) -> tuple[float, list[str]]:
    """
    Detect sessions that report success despite evidence of failure.
    Heuristic: final event is SUCCESS but there were blocked/failed tool calls.
    """
    if not events:
        return 1.0, []

    last_event = events[-1]
    if last_event.status != ExecutionStatus.SUCCESS:
        return 1.0, []

    blocks = sum(1 for e in events if e.is_blocked)
    errors = sum(1 for e in events if e.event_type == EventType.TOOL_ERROR)

    if blocks > 0 and errors > 2:
        return 0.35, [ANOMALY_HALLUCINATED_SUCCESS]
    if blocks > 2:
        return 0.5, [ANOMALY_HALLUCINATED_SUCCESS]
    return 1.0, []


def _check_goal_alignment(events: list[AgentEvent], goal: str | None) -> tuple[float, list[str]]:
    """
    Approximate goal alignment check using keyword overlap between goal
    and observable planner outputs / tool call arguments.
    This is NOT semantic analysis — it's keyword heuristics.
    """
    if not goal:
        return 1.0, []

    goal_tokens = set(re.findall(r"\b\w{4,}\b", goal.lower()))
    if not goal_tokens:
        return 1.0, []

    observable_texts: list[str] = []
    for e in events:
        if e.planner_output_preview:
            observable_texts.append(e.planner_output_preview.lower())
        if e.tool_call and e.tool_call.raw_command:
            observable_texts.append(e.tool_call.raw_command.lower())
        if e.tool_call:
            for v in e.tool_call.arguments.values():
                if isinstance(v, str):
                    observable_texts.append(v.lower())

    if not observable_texts:
        return 0.8, []

    combined = " ".join(observable_texts)
    combined_tokens = set(re.findall(r"\b\w{4,}\b", combined))

    overlap = len(goal_tokens & combined_tokens) / max(len(goal_tokens), 1)
    score = min(1.0, overlap * 2)

    flags = []
    if score < 0.2:
        flags.append(ANOMALY_GOAL_DRIFT)

    return max(0.1, score), flags


def _check_no_progress(events: list[AgentEvent]) -> tuple[float, list[str]]:
    """
    Detect sessions that have many events but no successful tool results.
    """
    tool_calls = sum(1 for e in events if e.event_type == EventType.TOOL_CALL)
    successes = sum(
        1
        for e in events
        if e.event_type == EventType.TOOL_RESULT and (not e.tool_result or not e.tool_result.error)
    )

    if tool_calls < 5:
        return 1.0, []

    success_rate = successes / tool_calls
    if success_rate < 0.1:
        return 0.3, [ANOMALY_NO_PROGRESS]
    return 1.0, []


def _check_high_risk_actions(events: list[AgentEvent]) -> tuple[float, list[str]]:
    """Penalize sessions with critical/high risk actions."""
    critical = sum(1 for e in events if e.safety and e.safety.risk_level == RiskLevel.CRITICAL)
    high = sum(1 for e in events if e.safety and e.safety.risk_level == RiskLevel.HIGH)

    if critical > 0:
        return max(0.2, 0.7 - (critical * 0.1)), [ANOMALY_HIGH_RISK_ACTION]
    if high > 2:
        return max(0.5, 0.9 - (high * 0.05)), [ANOMALY_HIGH_RISK_ACTION]
    return 1.0, []


def _check_context_explosion(events: list[AgentEvent]) -> tuple[float, list[str]]:
    """Detect sessions where token usage grew exponentially."""
    token_counts = [
        e.token_usage.total_tokens
        for e in events
        if e.token_usage and e.token_usage.total_tokens > 0
    ]
    if len(token_counts) < 3:
        return 1.0, []

    early_avg = sum(token_counts[:3]) / 3
    if early_avg == 0:
        return 1.0, []

    peak = max(token_counts[-3:])
    ratio = peak / early_avg
    if ratio > 10:
        return 0.5, [ANOMALY_CONTEXT_EXPLOSION]
    return 1.0, []


# ─────────────────────────────────────────────
# Confidence Scorer
# ─────────────────────────────────────────────


class ConfidenceScorer:
    """
    Computes a multi-dimensional confidence score for an agent execution.

    Scores are based on observable execution artifacts:
    - Tool call patterns
    - Error rates
    - Risk levels
    - Goal keyword alignment
    - Execution consistency

    This is NOT reasoning inspection or chain-of-thought analysis.
    """

    WEIGHTS: dict[str, float] = {
        "tool_loop": 0.20,
        "repeated_failures": 0.20,
        "hallucinated_success": 0.15,
        "goal_alignment": 0.20,
        "no_progress": 0.15,
        "high_risk": 0.05,
        "context": 0.05,
    }

    def score(
        self,
        events: list[AgentEvent],
        goal: str | None = None,
    ) -> ScoringResult:
        if not events:
            return ScoringResult(
                overall_score=1.0,
                goal_alignment=1.0,
                consistency_score=1.0,
                explanation="No events to score.",
            )

        loop_score, loop_flags = _check_tool_loop(events)
        failure_score, failure_flags = _check_repeated_failures(events)
        halluc_score, halluc_flags = _check_hallucinated_success(events)
        goal_score, goal_flags = _check_goal_alignment(events, goal)
        progress_score, progress_flags = _check_no_progress(events)
        risk_score, risk_flags = _check_high_risk_actions(events)
        context_score, context_flags = _check_context_explosion(events)

        component_scores = {
            "tool_loop": loop_score,
            "repeated_failures": failure_score,
            "hallucinated_success": halluc_score,
            "goal_alignment": goal_score,
            "no_progress": progress_score,
            "high_risk": risk_score,
            "context": context_score,
        }

        overall = sum(component_scores[k] * self.WEIGHTS[k] for k in component_scores)

        all_flags = (
            loop_flags
            + failure_flags
            + halluc_flags
            + goal_flags
            + progress_flags
            + risk_flags
            + context_flags
        )

        consistency_components = [
            loop_score,
            failure_score,
            halluc_score,
            progress_score,
            context_score,
        ]
        consistency = sum(consistency_components) / len(consistency_components)

        explanation = _build_explanation(overall, all_flags, component_scores)

        return ScoringResult(
            overall_score=round(overall, 3),
            goal_alignment=round(goal_score, 3),
            consistency_score=round(consistency, 3),
            anomaly_flags=list(set(all_flags)),
            explanation=explanation,
            component_scores={k: round(v, 3) for k, v in component_scores.items()},
        )

    def score_incremental(
        self,
        existing_result: ScoringResult | None,
        new_events: list[AgentEvent],
        all_events: list[AgentEvent],
        goal: str | None = None,
    ) -> ScoringResult:
        """Re-score with updated event list for streaming use cases."""
        return self.score(all_events, goal=goal)


def _build_explanation(
    overall: float,
    flags: list[str],
    components: dict[str, float],
) -> str:
    if not flags:
        return f"Execution appears healthy (score: {overall:.2f}). No anomalies detected."

    parts = [f"Confidence score: {overall:.2f}. Detected anomalies:"]
    flag_descriptions = {
        ANOMALY_TOOL_LOOP: "Repetitive tool call pattern detected — possible loop.",
        ANOMALY_REPEATED_FAILURES: "High rate of tool errors suggests execution instability.",
        ANOMALY_HALLUCINATED_SUCCESS: "Session reported success despite blocked/failed actions.",
        ANOMALY_GOAL_DRIFT: "Observable execution shows low alignment with stated goal.",
        ANOMALY_NO_PROGRESS: "Many tool calls with few successful results — limited progress.",
        ANOMALY_HIGH_RISK_ACTION: "High or critical risk actions were attempted.",
        ANOMALY_MEMORY_CONTRADICTION: "Contradictory memory entries detected.",
        ANOMALY_CONTEXT_EXPLOSION: "Token usage escalated significantly — possible runaway context.",
    }
    for flag in set(flags):
        desc = flag_descriptions.get(flag, flag)
        parts.append(f"  • {desc}")

    worst_component = min(components, key=lambda k: components[k])
    parts.append(f"Lowest scoring component: {worst_component} ({components[worst_component]:.2f})")

    return "\n".join(parts)
