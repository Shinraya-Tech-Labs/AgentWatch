"""
RSN-008 — Reasoning Fingerprinting.

Per-session semantic fingerprint of the model's reasoning style.
If the fingerprint shifts mid-session (e.g. provider silently rolled out
a new model version), surface a warning.

Style features (no chain-of-thought inspection — only observable artifacts):
    - mean tokens per planner step
    - lexical-diversity of planner text
    - average tool calls between plans
    - punctuation cadence
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, EventType


@dataclass
class StyleFingerprint:
    mean_planner_tokens: float
    lex_diversity: float
    tools_per_plan: float
    punctuation_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "mean_planner_tokens": self.mean_planner_tokens,
            "lex_diversity": self.lex_diversity,
            "tools_per_plan": self.tools_per_plan,
            "punctuation_rate": self.punctuation_rate,
        }

    def distance(self, other: StyleFingerprint) -> float:
        return math.sqrt(
            (self.mean_planner_tokens - other.mean_planner_tokens) ** 2 / 100
            + (self.lex_diversity - other.lex_diversity) ** 2
            + (self.tools_per_plan - other.tools_per_plan) ** 2 / 10
            + (self.punctuation_rate - other.punctuation_rate) ** 2
        )


def fingerprint(events: list[AgentEvent]) -> StyleFingerprint:
    plans = [e for e in events if e.event_type == EventType.PLANNER_OUTPUT]
    tools = [e for e in events if e.event_type == EventType.TOOL_CALL]
    if not plans:
        return StyleFingerprint(0.0, 0.0, 0.0, 0.0)

    token_counts: list[int] = []
    diversities: list[float] = []
    punct_rates: list[float] = []
    for e in plans:
        text = e.planner_output_preview or ""
        tokens = text.split()
        if not tokens:
            continue
        token_counts.append(len(tokens))
        diversities.append(len(set(tokens)) / len(tokens))
        punct = sum(1 for c in text if c in ".,;:!?")
        punct_rates.append(punct / max(1, len(text)))

    return StyleFingerprint(
        mean_planner_tokens=sum(token_counts) / max(1, len(token_counts)),
        lex_diversity=sum(diversities) / max(1, len(diversities)),
        tools_per_plan=len(tools) / max(1, len(plans)),
        punctuation_rate=sum(punct_rates) / max(1, len(punct_rates)),
    )


def _has_planner_output(events: list[AgentEvent]) -> bool:
    """True if the slice contains at least one PLANNER_OUTPUT event."""
    return any(e.event_type == EventType.PLANNER_OUTPUT for e in events)


def detect_mid_session_change(
    events: list[AgentEvent],
    *,
    split_ratio: float = 0.5,
    distance_threshold: float = 1.0,
) -> tuple[bool, float]:
    """Split events in half and compare fingerprints; flag if too different."""
    if len(events) < 6:
        return False, 0.0
    cut = int(len(events) * split_ratio)
    first, second = events[:cut], events[cut:]
    # A half with no PLANNER_OUTPUT has no reasoning style to fingerprint:
    # fingerprint() returns the all-zeros placeholder, which means "no signal",
    # not a real style. Comparing a genuine fingerprint against that placeholder
    # yields a large distance and falsely reports a mid-session model change —
    # e.g. for an agent that plans up front and then only calls tools, so the
    # planner events all land in one half. Treat this as "not enough signal to
    # compare" rather than "the model changed".
    if not _has_planner_output(first) or not _has_planner_output(second):
        return False, 0.0
    a = fingerprint(first)
    b = fingerprint(second)
    dist = a.distance(b)
    return dist >= distance_threshold, dist


__all__ = ["StyleFingerprint", "fingerprint", "detect_mid_session_change"]
