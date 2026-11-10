"""
PLT-009 — PANOPTIS Intelligence.

AI-on-AI: surface patterns in telemetry automatically.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.schema import AgentEvent, AgentSession, EventType, ExecutionStatus


@dataclass
class Insight:
    title: str
    detail: str
    severity: str  # info | warn | critical
    suggestion: str = ""
    evidence_count: int = 0


@dataclass
class IntelligenceReport:
    insights: list[Insight] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "insights": [i.__dict__ for i in self.insights],
            "metrics": self.metrics,
        }


class AgentWatchIntelligence:
    """Mine telemetry for patterns and suggest optimizations."""

    def analyze(
        self,
        sessions: list[tuple[AgentSession, list[AgentEvent]]],
    ) -> IntelligenceReport:
        insights: list[Insight] = []
        metrics: dict[str, float] = {}

        if not sessions:
            insights.append(
                Insight(
                    title="No telemetry yet",
                    detail="Run a session to begin PANOPTIS Intelligence analysis.",
                    severity="info",
                )
            )
            return IntelligenceReport(insights=insights, metrics=metrics)

        # Day-of-week failure pattern
        dow_total: dict[int, int] = defaultdict(int)
        dow_fail: dict[int, int] = defaultdict(int)
        # Hour-of-day failure pattern
        hour_total: dict[int, int] = defaultdict(int)
        hour_fail: dict[int, int] = defaultdict(int)

        # Tool failure rate
        tool_calls: Counter = Counter()
        tool_failures: Counter = Counter()

        # Session-level metrics
        total = len(sessions)
        failures = 0
        durations: list[float] = []
        costs: list[float] = []

        for sess, events in sessions:
            dow = sess.started_at.weekday()
            hour = sess.started_at.hour
            dow_total[dow] += 1
            hour_total[hour] += 1
            failed = sess.status in (ExecutionStatus.FAILURE, ExecutionStatus.BLOCKED)
            if failed:
                dow_fail[dow] += 1
                hour_fail[hour] += 1
                failures += 1
            if sess.ended_at and sess.started_at:
                durations.append((sess.ended_at - sess.started_at).total_seconds())
            if sess.estimated_cost_usd:
                costs.append(float(sess.estimated_cost_usd))
            for ev in events:
                tool_name: str | None = None
                if ev.tool_call:
                    tool_name = ev.tool_call.tool_name
                elif ev.tool_result and ev.tool_result.tool_name not in (None, "unknown"):
                    tool_name = ev.tool_result.tool_name
                if tool_name is None:
                    continue
                if ev.event_type == EventType.TOOL_CALL:
                    tool_calls[tool_name] += 1
                if ev.event_type == EventType.TOOL_ERROR or ev.status == ExecutionStatus.FAILURE:
                    tool_failures[tool_name] += 1

        # Metrics
        metrics["total_sessions"] = float(total)
        metrics["failure_rate"] = failures / total if total else 0.0
        if durations:
            metrics["p50_duration_s"] = statistics.median(durations)
            metrics["p95_duration_s"] = (
                statistics.quantiles(durations, n=20)[18]
                if len(durations) >= 20
                else max(durations)
            )
        if costs:
            metrics["total_usd"] = sum(costs)

        # Day-of-week insight
        dow_rate = {d: dow_fail[d] / dow_total[d] for d in dow_total if dow_total[d] >= 3}
        if dow_rate:
            worst_day = max(dow_rate, key=lambda d: dow_rate[d])
            if dow_rate[worst_day] >= 0.25:
                names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                insights.append(
                    Insight(
                        title=f"Failures cluster on {names[worst_day]}",
                        detail=(
                            f"{dow_rate[worst_day] * 100:.0f}% of {names[worst_day]} "
                            f"sessions fail vs. baseline."
                        ),
                        severity="warn",
                        suggestion=f"Investigate dependencies/jobs scheduled on {names[worst_day]}.",
                        evidence_count=dow_total[worst_day],
                    )
                )

        # Tool failure rate
        for tool, calls in tool_calls.most_common(10):
            if calls < 5:
                continue
            rate = tool_failures.get(tool, 0) / calls
            if rate >= 0.3:
                insights.append(
                    Insight(
                        title=f"Tool '{tool}' failing {rate * 100:.0f}%",
                        detail=f"{tool_failures.get(tool, 0)} failures out of {calls} calls.",
                        severity="warn",
                        suggestion=f"Add input validation or switch to a fallback for '{tool}'.",
                        evidence_count=calls,
                    )
                )

        # Cost outlier
        if costs:
            mean_cost = statistics.mean(costs)
            if max(costs) > mean_cost * 5:
                insights.append(
                    Insight(
                        title="Cost outliers detected",
                        detail=f"At least one session ran {max(costs) / mean_cost:.1f}x the mean.",
                        severity="warn",
                        suggestion="Enable CostAnomalyDetector and configure budget caps.",
                        evidence_count=len(costs),
                    )
                )

        if not insights:
            insights.append(
                Insight(
                    title="No patterns surfaced",
                    detail="Telemetry looks healthy across the analyzed window.",
                    severity="info",
                )
            )

        return IntelligenceReport(insights=insights, metrics=metrics)


__all__ = ["Insight", "IntelligenceReport", "AgentWatchIntelligence"]
