"""Deterministic trajectory divergence metrics.

Computes:
- Normalized tool-sequence edit distance (Levenshtein).
- Step-by-step earliest divergence detection.
- Structured output difference comparisons.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.schema import AgentEvent, EventType


def levenshtein_distance(seq_a: list[str], seq_b: list[str]) -> int:
    """Compute standard Levenshtein edit distance between two sequences of strings."""
    len_a, len_b = len(seq_a), len(seq_b)
    if len_a == 0:
        return len_b
    if len_b == 0:
        return len_a

    dp = [[0] * (len_b + 1) for _ in range(len_a + 1)]

    for i in range(len_a + 1):
        dp[i][0] = i
    for j in range(len_b + 1):
        dp[0][j] = j

    for i in range(1, len_a + 1):
        for j in range(1, len_b + 1):
            cost = 0 if seq_a[i - 1] == seq_b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,  # deletion
                dp[i][j - 1] + 1,  # insertion
                dp[i - 1][j - 1] + cost,  # substitution
            )

    return dp[len_a][len_b]


def normalized_tool_sequence_edit_distance(tools_a: list[str], tools_b: list[str]) -> float:
    """Normalized edit distance in [0.0, 1.0].

    0.0 indicates completely identical tool call sequences.
    1.0 indicates maximum possible divergence.
    """
    max_len = max(len(tools_a), len(tools_b))
    if max_len == 0:
        return 0.0
    dist = levenshtein_distance(tools_a, tools_b)
    return round(dist / max_len, 4)


def _extract_tool_sequence(events: list[AgentEvent]) -> list[str]:
    """Extract ordered tool invocation names from an event stream.

    If the trace contains explicit TOOL_CALL events, uses them as the canonical
    sequence (consistent with `panoptis.replay.engine`). Otherwise falls back to
    tool calls or tool results without double-counting.
    """
    seq: list[str] = []
    has_explicit_calls = any(ev.event_type == EventType.TOOL_CALL for ev in events)
    for ev in events:
        if has_explicit_calls:
            if ev.event_type == EventType.TOOL_CALL and ev.tool_call and ev.tool_call.tool_name:
                seq.append(ev.tool_call.tool_name)
        else:
            if ev.tool_call and ev.tool_call.tool_name:
                seq.append(ev.tool_call.tool_name)
            elif (
                ev.tool_result
                and ev.tool_result.tool_name
                and ev.tool_result.tool_name != "unknown"
            ):
                seq.append(ev.tool_result.tool_name)
    return seq


def _canonical_structure(val: Any) -> Any:
    """Normalize structured outputs (dict, list, primitive, json string)."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def compare_structured_outputs(val_a: Any, val_b: Any) -> dict[str, Any]:
    """Compare structured outputs (dictionaries, primitives, lists) and return diff analysis."""
    norm_a = _canonical_structure(val_a)
    norm_b = _canonical_structure(val_b)

    if norm_a == norm_b:
        return {
            "changed": False,
            "similarity": 1.0,
            "added_keys": [],
            "removed_keys": [],
            "modified_fields": {},
        }

    if isinstance(norm_a, dict) and isinstance(norm_b, dict):
        keys_a = set(norm_a.keys())
        keys_b = set(norm_b.keys())
        all_keys = keys_a | keys_b

        added = sorted(keys_b - keys_a)
        removed = sorted(keys_a - keys_b)
        common = sorted(keys_a & keys_b)

        modified: dict[str, dict[str, Any]] = {}
        identical_count = 0

        for k in common:
            if norm_a[k] == norm_b[k]:
                identical_count += 1
            else:
                modified[k] = {"before": norm_a[k], "after": norm_b[k]}

        total_keys = max(len(all_keys), 1)
        sim = round(identical_count / total_keys, 4)

        return {
            "changed": True,
            "similarity": sim,
            "added_keys": added,
            "removed_keys": removed,
            "modified_fields": modified,
        }

    # Fallback for primitive or mixed types
    return {
        "changed": True,
        "similarity": 0.0,
        "added_keys": [],
        "removed_keys": [],
        "modified_fields": {"root": {"before": norm_a, "after": norm_b}},
    }


def _extract_final_output(events: list[AgentEvent]) -> Any:
    """Extract final output payload from an event trace."""
    if not events:
        return None
    for ev in reversed(events):
        if ev.planner_output_preview is not None:
            return ev.planner_output_preview
        if ev.tool_result and ev.tool_result.output is not None:
            return ev.tool_result.output
    return None


@dataclass(slots=True)
class TrajectoryDivergence:
    """Complete trajectory divergence measurement."""

    has_diverged: bool
    earliest_divergence_step: int | None
    divergence_type: str | None
    divergence_reason: str | None
    baseline_tool_sequence: list[str]
    counterfactual_tool_sequence: list[str]
    normalized_tool_edit_distance: float
    tool_sequence_similarity: float
    output_comparison: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_diverged": self.has_diverged,
            "earliest_divergence_step": self.earliest_divergence_step,
            "divergence_type": self.divergence_type,
            "divergence_reason": self.divergence_reason,
            "baseline_tool_sequence": self.baseline_tool_sequence,
            "counterfactual_tool_sequence": self.counterfactual_tool_sequence,
            "normalized_tool_edit_distance": self.normalized_tool_edit_distance,
            "tool_sequence_similarity": self.tool_sequence_similarity,
            "output_comparison": self.output_comparison,
        }


def compute_trajectory_divergence(
    baseline_events: list[AgentEvent],
    counterfactual_events: list[AgentEvent],
) -> TrajectoryDivergence:
    """Compute full deterministic divergence analysis between baseline and counterfactual traces."""
    tools_base = _extract_tool_sequence(baseline_events)
    tools_cf = _extract_tool_sequence(counterfactual_events)

    edit_dist = normalized_tool_sequence_edit_distance(tools_base, tools_cf)
    similarity = round(1.0 - edit_dist, 4)

    earliest_step: int | None = None
    div_type: str | None = None
    div_reason: str | None = None

    min_len = min(len(baseline_events), len(counterfactual_events))

    for idx in range(min_len):
        ev_a = baseline_events[idx]
        ev_b = counterfactual_events[idx]

        # Check tool call mismatch
        call_a = ev_a.tool_call
        call_b = ev_b.tool_call
        if (call_a is None) != (call_b is None):
            earliest_step = idx
            div_type = "TOOL_CALL_MISMATCH"
            div_reason = f"Step {idx}: one event had tool call while other did not"
            break

        if call_a and call_b:
            if call_a.tool_name != call_b.tool_name:
                earliest_step = idx
                div_type = "TOOL_NAME_MISMATCH"
                div_reason = (
                    f"Step {idx}: expected tool '{call_a.tool_name}', got '{call_b.tool_name}'"
                )
                break
            if call_a.arguments != call_b.arguments:
                earliest_step = idx
                div_type = "ARGUMENT_MISMATCH"
                div_reason = f"Step {idx}: tool arguments differ"
                break

        # Check status mismatch
        if ev_a.status != ev_b.status:
            earliest_step = idx
            div_type = "STATUS_MISMATCH"
            div_reason = (
                f"Step {idx}: status differed ('{ev_a.status.value}' vs '{ev_b.status.value}')"
            )
            break

        # Check tool result output mismatch
        res_a = ev_a.tool_result
        res_b = ev_b.tool_result
        if (res_a is None) != (res_b is None):
            earliest_step = idx
            div_type = "RESULT_STRUCTURE_MISMATCH"
            div_reason = f"Step {idx}: one event had tool result while other did not"
            break

        if res_a and res_b:
            if res_a.error != res_b.error:
                earliest_step = idx
                div_type = "ERROR_MISMATCH"
                div_reason = f"Step {idx}: error differed ('{res_a.error}' vs '{res_b.error}')"
                break
            if res_a.output != res_b.output:
                earliest_step = idx
                div_type = "OUTPUT_MISMATCH"
                div_reason = f"Step {idx}: tool output differed"
                break

    if earliest_step is None and len(baseline_events) != len(counterfactual_events):
        earliest_step = min_len
        div_type = "LENGTH_MISMATCH"
        div_reason = (
            f"Trace length difference: baseline={len(baseline_events)}, "
            f"counterfactual={len(counterfactual_events)}"
        )

    out_base = _extract_final_output(baseline_events)
    out_cf = _extract_final_output(counterfactual_events)
    out_comp = compare_structured_outputs(out_base, out_cf)

    has_diverged = earliest_step is not None or out_comp["changed"] or edit_dist > 0.0

    return TrajectoryDivergence(
        has_diverged=has_diverged,
        earliest_divergence_step=earliest_step,
        divergence_type=div_type,
        divergence_reason=div_reason,
        baseline_tool_sequence=tools_base,
        counterfactual_tool_sequence=tools_cf,
        normalized_tool_edit_distance=edit_dist,
        tool_sequence_similarity=similarity,
        output_comparison=out_comp,
    )


__all__ = [
    "TrajectoryDivergence",
    "compare_structured_outputs",
    "compute_trajectory_divergence",
    "levenshtein_distance",
    "normalized_tool_sequence_edit_distance",
]
