"""
OBS-005 — Silent Failure Detector.

Statistical anomaly detection across sessions. The agent returns success
but the trace pattern (no tool calls, suspiciously fast finish, output
shape mismatch) implies the result is plausible-but-wrong.

Baseline is the agent's own history, not generic patterns.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from panoptis.core.schema import AgentEvent, AgentSession, EventType, ExecutionStatus


@dataclass
class SilenceFinding:
    session_id: str
    confidence: float  # 0..1, how confident we are it's a silent failure
    flags: list[str]
    notes: dict[str, float] = field(default_factory=dict)


class SilentFailureDetector:
    """Build a baseline from successful sessions, then flag outliers."""

    def __init__(self, min_baseline: int = 5):
        self.min_baseline = min_baseline
        self.sample_size: int = 0
        self.mean_tool_calls: float = 0.0
        self.stdev_tool_calls: float = 0.0
        self.mean_duration_ms: float = 0.0
        self.stdev_duration_ms: float = 0.0
        self.mean_tokens: float = 0.0
        self.stdev_tokens: float = 0.0
        self.common_tools: list[str] = field(default_factory=list)

    # ── training ───────────────────────────────────────────────────────────

    @property
    def baseline(self) -> SilentFailureDetector:
        """Backward-compatible accessor — returns self (fields are now inline)."""
        return self

    def train(
        self,
        sessions: list[tuple[AgentSession, list[AgentEvent]]],
    ) -> None:
        ok = [(s, evs) for (s, evs) in sessions if s.status == ExecutionStatus.SUCCESS]
        if len(ok) < self.min_baseline:
            self.sample_size = len(ok)
            return

        tool_counts = []
        durations = []
        token_counts = []
        tool_freq: Counter = Counter()

        for s, evs in ok:
            calls = [e for e in evs if e.event_type == EventType.TOOL_CALL]
            tool_counts.append(len(calls))
            if s.ended_at and s.started_at:
                durations.append((s.ended_at - s.started_at).total_seconds() * 1000.0)
            token_counts.append(s.total_tokens)
            for e in calls:
                if e.tool_call:
                    tool_freq[e.tool_call.tool_name] += 1

        def _safe_stdev(xs: Sequence[float]) -> float:
            return statistics.stdev(xs) if len(xs) > 1 else 0.0

        self.sample_size = len(ok)
        self.mean_tool_calls = statistics.mean(tool_counts) if tool_counts else 0.0
        self.stdev_tool_calls = _safe_stdev(tool_counts)
        self.mean_duration_ms = statistics.mean(durations) if durations else 0.0
        self.stdev_duration_ms = _safe_stdev(durations)
        self.mean_tokens = statistics.mean(token_counts) if token_counts else 0.0
        self.stdev_tokens = _safe_stdev(token_counts)
        self.common_tools = [t for t, _ in tool_freq.most_common(10)]

    # ── detection ──────────────────────────────────────────────────────────

    def detect(
        self,
        session: AgentSession,
        events: list[AgentEvent],
    ) -> SilenceFinding:
        flags: list[str] = []
        notes: dict[str, float] = {}

        if session.status != ExecutionStatus.SUCCESS:
            return SilenceFinding(
                session_id=session.session_id, confidence=0.0, flags=["not_success"]
            )

        calls = [e for e in events if e.event_type == EventType.TOOL_CALL]
        n_calls = len(calls)
        notes["tool_calls"] = float(n_calls)

        if n_calls == 0 and any(e.event_type == EventType.PLANNER_OUTPUT for e in events):
            flags.append("planned_but_did_nothing")

        if session.ended_at and session.started_at:
            duration_ms = (session.ended_at - session.started_at).total_seconds() * 1000.0
            notes["duration_ms"] = duration_ms
            if duration_ms < 50:
                flags.append("suspiciously_fast")

        if session.total_tokens == 0:
            flags.append("zero_tokens")

        if self.sample_size >= self.min_baseline:
            if self.stdev_tool_calls > 0:
                z = abs(n_calls - self.mean_tool_calls) / self.stdev_tool_calls
                if z > 2.0:
                    flags.append(f"tool_count_outlier_z={z:.2f}")
                    notes["z_tool_calls"] = z
            if self.stdev_tokens > 0:
                z = abs(session.total_tokens - self.mean_tokens) / self.stdev_tokens
                if z > 2.0:
                    flags.append(f"token_count_outlier_z={z:.2f}")
                    notes["z_tokens"] = z

        used_tools = {e.tool_call.tool_name for e in calls if e.tool_call}
        if self.common_tools and used_tools:
            if not (used_tools & set(self.common_tools)):
                flags.append("all_uncommon_tools")

        confidence = min(1.0, len(flags) * 0.25)
        return SilenceFinding(
            session_id=session.session_id,
            confidence=confidence,
            flags=flags,
            notes=notes,
        )


__all__ = ["SilenceFinding", "SilentFailureDetector"]
