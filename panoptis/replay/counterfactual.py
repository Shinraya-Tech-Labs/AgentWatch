"""OBS-009 & Issue #735 — Counterfactual Replay & L3 Perturbation Engine.

Deterministic counterfactual replay and invariant falsification for agent executions.
Supports step-specific interventions (tool-result mutations, simulated API errors,
prompt-context changes), normalized tool-sequence edit distance divergence metrics,
structured output comparisons, empirical causal scoring (necessity/sufficiency),
and declared invariant falsification.

Hermetic execution guarantees:
- Replays captured tool results through controlled in-memory mocks.
- Zero host filesystem mutations.
- Zero live external tool side effects or network/subprocess execution.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.schema import AgentEvent, EventType, ToolResultData
from panoptis.evidence.blobs import BlobStore, MemoryBlobStore
from panoptis.replay.causal_scoring import (
    CausalScoringResult,
    compute_empirical_causal_scores,
    generate_experimental_hypothesis,
)
from panoptis.replay.divergence import (
    TrajectoryDivergence,
    compute_trajectory_divergence,
)
from panoptis.replay.interventions import (
    Intervention,
    PromptContextChange,
    SimulatedAPIError,
    ToolResultMutation,
)
from panoptis.replay.invariants import (
    Invariant,
    InvariantFalsificationReport,
    evaluate_invariants,
)

logger = logging.getLogger(__name__)


@dataclass
class CounterfactualScenario:
    """A what-if scenario: at step N, replace tool result with `replacement`."""

    rewind_to_step: int
    tool_id: str | None = None
    replacement: Any = None
    notes: str = ""


@dataclass
class CounterfactualResult:
    scenario: CounterfactualScenario | Intervention
    original_events: list[AgentEvent]
    alternate_events: list[AgentEvent]
    diverged_at_step: int | None
    summary: dict[str, Any] = field(default_factory=dict)
    divergence: TrajectoryDivergence | None = None
    invariants_report: InvariantFalsificationReport | None = None
    causal_scoring: CausalScoringResult | None = None

    @property
    def diverged(self) -> bool:
        return self.diverged_at_step is not None


# A user-supplied step function maps (prior_events, latest_event) → next_event_or_None
StepFn = Callable[[list[AgentEvent], AgentEvent], AgentEvent | None]


@dataclass(slots=True)
class L3CounterfactualResult:
    """Rich outcome of an L3 counterfactual replay run."""

    baseline_events: list[AgentEvent]
    counterfactual_events: list[AgentEvent]
    interventions_applied: list[Intervention]
    divergence: TrajectoryDivergence
    invariants_report: InvariantFalsificationReport | None = None
    causal_scoring: CausalScoringResult | None = None
    hypothesis: dict[str, Any] | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def diverged(self) -> bool:
        return self.divergence.has_diverged

    @property
    def diverged_at_step(self) -> int | None:
        return self.divergence.earliest_divergence_step

    def to_dict(self) -> dict[str, Any]:
        return {
            "diverged": self.diverged,
            "diverged_at_step": self.diverged_at_step,
            "interventions_count": len(self.interventions_applied),
            "divergence": self.divergence.to_dict(),
            "invariants_report": self.invariants_report.to_dict()
            if self.invariants_report
            else None,
            "causal_scoring": self.causal_scoring.to_dict() if self.causal_scoring else None,
            "hypothesis": self.hypothesis,
            "summary": self.summary,
        }


class CounterfactualEngine:
    """Substitute an alternate tool output at a chosen step (v0.2 backwards-compatible).

    Also supports typed interventions, divergence calculation, and invariant evaluation.
    """

    def __init__(self, step_fn: StepFn | None = None):
        self.step_fn = step_fn

    def run(
        self,
        events: list[AgentEvent],
        scenario: CounterfactualScenario | Intervention,
        *,
        invariants: Sequence[Invariant] | None = None,
    ) -> CounterfactualResult:
        if not events:
            raise ValueError("events sequence cannot be empty")

        step_idx = (
            scenario.rewind_to_step
            if isinstance(scenario, CounterfactualScenario)
            else scenario.target_step
        )
        if step_idx < 0 or step_idx >= len(events):
            raise ValueError(f"rewind_to_step {step_idx} out of range [0, {len(events)})")

        prefix = [copy.deepcopy(e) for e in events[: step_idx + 1]]
        target = prefix[-1]

        applied = False
        if isinstance(scenario, CounterfactualScenario):
            applied = self._apply_swap(target, scenario)
        else:
            scenario.apply(target)
            applied = True

        alternate: list[AgentEvent] = list(prefix)
        diverged_step: int | None = None

        if self.step_fn is not None:
            while True:
                latest = alternate[-1]
                nxt = self.step_fn(alternate, latest)
                if nxt is None:
                    break
                alternate.append(nxt)
        else:
            original_suffix = events[step_idx + 1 :]
            for idx, orig in enumerate(original_suffix):
                clone = copy.deepcopy(orig)
                clone.metadata["counterfactual"] = True
                alternate.append(clone)
                if diverged_step is None and applied:
                    diverged_step = step_idx + idx

        div = compute_trajectory_divergence(events, alternate)
        inv_report = evaluate_invariants(alternate, invariants) if invariants else None

        summary = {
            "rewind_step": step_idx,
            "swap_applied": applied,
            "original_length": len(events),
            "alternate_length": len(alternate),
            "normalized_tool_edit_distance": div.normalized_tool_edit_distance,
        }

        return CounterfactualResult(
            scenario=scenario,
            original_events=list(events),
            alternate_events=alternate,
            diverged_at_step=diverged_step if applied else None,
            summary=summary,
            divergence=div,
            invariants_report=inv_report,
        )

    def _apply_swap(
        self,
        event: AgentEvent,
        scenario: CounterfactualScenario,
    ) -> bool:
        if event.event_type not in (EventType.TOOL_RESULT, EventType.TOOL_CALL):
            return False

        if scenario.tool_id and event.tool_call:
            if event.tool_call.tool_id != scenario.tool_id:
                if event.tool_call.tool_name != scenario.tool_id:
                    return False

        if event.tool_result is None:
            event.tool_result = ToolResultData(
                tool_name=event.tool_call.tool_name if event.tool_call else "unknown",
                output=scenario.replacement,
            )
        else:
            event.tool_result.output = scenario.replacement
            event.tool_result.error = None

        event.metadata["counterfactual"] = True
        event.metadata["counterfactual_notes"] = scenario.notes
        return True


class L3PerturbationEngine:
    """L3 Counterfactual Perturbation Engine & Invariant Falsification.

    Executes hermetic offline replays with controlled step-specific perturbations,
    deterministic trajectory divergence measurements, declared invariant evaluations,
    and empirical causal scoring without external tool side effects or filesystem writes.
    """

    def __init__(
        self,
        step_fn: StepFn | None = None,
        *,
        captured_tools: dict[str, Any] | None = None,
        blob_store: BlobStore | None = None,
        invariants: Sequence[Invariant] | None = None,
        max_replay_steps: int = 500,
    ) -> None:
        self.step_fn = step_fn
        self.captured_tools = dict(captured_tools or {})
        self.blob_store = blob_store or MemoryBlobStore()
        self.invariants = list(invariants or [])
        self.max_replay_steps = max_replay_steps

    def run(
        self,
        events: list[AgentEvent],
        interventions: Intervention
        | CounterfactualScenario
        | Sequence[Intervention | CounterfactualScenario],
        *,
        step_fn: StepFn | None = None,
        invariants: Sequence[Invariant] | None = None,
        target_outcome_predicate: Callable[[list[AgentEvent]], bool] | None = None,
        cause_name: str | None = None,
        effect_name: str | None = None,
    ) -> L3CounterfactualResult:
        """Execute hermetic counterfactual replay with interventions, divergence, invariants, and causal scoring."""
        if not events:
            raise ValueError("events sequence cannot be empty")

        # Normalize interventions list
        raw_list = (
            [interventions] if not isinstance(interventions, (list, tuple)) else list(interventions)
        )
        typed_interventions: list[Intervention] = []
        for item in raw_list:
            if isinstance(item, CounterfactualScenario):
                typed_interventions.append(
                    ToolResultMutation(
                        target_step=item.rewind_to_step,
                        tool_id=item.tool_id,
                        replacement=item.replacement,
                        notes=item.notes,
                    )
                )
            elif isinstance(item, (ToolResultMutation, SimulatedAPIError, PromptContextChange)):
                typed_interventions.append(item)
            else:
                raise TypeError(f"Unsupported intervention type: {type(item).__name__}")

        if not typed_interventions:
            raise ValueError("At least one intervention must be provided")

        for intv in typed_interventions:
            if intv.target_step < 0 or intv.target_step >= len(events):
                raise ValueError(
                    f"Intervention target_step {intv.target_step} out of bounds [0, {len(events)})"
                )

        interventions_by_step: dict[int, list[Intervention]] = {}
        for intv in typed_interventions:
            interventions_by_step.setdefault(intv.target_step, []).append(intv)

        earliest_step = min(interventions_by_step.keys())
        active_step_fn = step_fn or self.step_fn

        # Populate captured tools from baseline events if not already present
        captures = dict(self.captured_tools)
        for idx, ev in enumerate(events):
            if ev.tool_call:
                t_name = ev.tool_call.tool_name
                if ev.tool_result and ev.tool_result.output is not None:
                    captures.setdefault(f"{t_name}:{idx}", ev.tool_result.output)
                    captures.setdefault(t_name, ev.tool_result.output)

        # Build alternate timeline
        prefix = [copy.deepcopy(e) for e in events[: earliest_step + 1]]
        for intv in interventions_by_step.get(earliest_step, []):
            intv.apply(prefix[-1])

        alternate: list[AgentEvent] = list(prefix)

        if active_step_fn is not None:
            # Step loop through simulator/program logic
            step_count = 0
            while step_count < self.max_replay_steps:
                curr_idx = len(alternate) - 1
                latest = alternate[-1]

                # Check if a tool call was issued in latest event and needs mocking
                has_result = latest.tool_result is not None and (
                    latest.tool_result.output is not None or latest.tool_result.error is not None
                )
                if latest.tool_call and not has_result:
                    t_name = latest.tool_call.tool_name
                    mock_output = captures.get(
                        f"{t_name}:{curr_idx}", captures.get(t_name, {"status": "ok"})
                    )
                    res_event = AgentEvent(
                        session_id=latest.session_id,
                        agent_id=latest.agent_id,
                        event_type=EventType.TOOL_RESULT,
                        tool_call=latest.tool_call,
                        tool_result=ToolResultData(tool_name=t_name, output=mock_output),
                        step_number=curr_idx + 1,
                        metadata={"counterfactual": True, "replay_mocked": True},
                    )
                    # Check if an intervention targets this tool result
                    if curr_idx + 1 in interventions_by_step:
                        for intv in interventions_by_step[curr_idx + 1]:
                            intv.apply(res_event)
                    alternate.append(res_event)
                    continue

                nxt = active_step_fn(alternate, latest)
                if nxt is None:
                    break
                nxt.metadata["counterfactual"] = True
                nxt.step_number = len(alternate)

                # Check if intervention targets nxt step
                if len(alternate) in interventions_by_step:
                    for intv in interventions_by_step[len(alternate)]:
                        intv.apply(nxt)

                alternate.append(nxt)
                step_count += 1
        else:
            # Fast suffix replay
            original_suffix = events[earliest_step + 1 :]
            for offset, orig in enumerate(original_suffix):
                step_idx = earliest_step + 1 + offset
                clone = copy.deepcopy(orig)
                clone.metadata["counterfactual"] = True
                if step_idx in interventions_by_step:
                    for intv in interventions_by_step[step_idx]:
                        intv.apply(clone)
                alternate.append(clone)

        # 1. Divergence analysis
        divergence = compute_trajectory_divergence(events, alternate)

        # 2. Invariant falsification
        active_invariants = list(invariants if invariants is not None else self.invariants)
        inv_report = (
            evaluate_invariants(alternate, active_invariants) if active_invariants else None
        )

        # 3. Empirical causal scoring
        causal_res: CausalScoringResult | None = None
        hypothesis_record: dict[str, Any] | None = None

        if target_outcome_predicate is not None:
            base_outcome = target_outcome_predicate(events)
            cf_outcome = target_outcome_predicate(alternate)
            causal_res = compute_empirical_causal_scores([(base_outcome, cf_outcome)])
            c_name = cause_name or (
                typed_interventions[0].tool_name
                if hasattr(typed_interventions[0], "tool_name") and typed_interventions[0].tool_name
                else f"intervention_step_{earliest_step}"
            )
            e_name = effect_name or "target_outcome_change"
            hypothesis_record = generate_experimental_hypothesis(c_name, e_name, causal_res)

        summary = {
            "baseline_steps": len(events),
            "counterfactual_steps": len(alternate),
            "earliest_intervention_step": earliest_step,
            "interventions_count": len(typed_interventions),
            "diverged": divergence.has_diverged,
            "normalized_tool_edit_distance": divergence.normalized_tool_edit_distance,
            "invariants_falsified": inv_report.falsified if inv_report else False,
        }

        return L3CounterfactualResult(
            baseline_events=list(events),
            counterfactual_events=alternate,
            interventions_applied=typed_interventions,
            divergence=divergence,
            invariants_report=inv_report,
            causal_scoring=causal_res,
            hypothesis=hypothesis_record,
            summary=summary,
        )

    def run_suite(
        self,
        events: list[AgentEvent],
        scenarios: Sequence[
            Intervention | CounterfactualScenario | Sequence[Intervention | CounterfactualScenario]
        ],
        *,
        step_fn: StepFn | None = None,
        invariants: Sequence[Invariant] | None = None,
        target_outcome_predicate: Callable[[list[AgentEvent]], bool] | None = None,
        cause_name: str | None = None,
        effect_name: str | None = None,
    ) -> tuple[list[L3CounterfactualResult], CausalScoringResult]:
        """Run a suite of counterfactual perturbations and compute aggregate necessity and sufficiency."""
        results: list[L3CounterfactualResult] = []
        trials: list[tuple[bool, bool]] = []

        base_outcome = target_outcome_predicate(events) if target_outcome_predicate else False

        for sc in scenarios:
            res = self.run(
                events,
                sc,
                step_fn=step_fn,
                invariants=invariants,
                target_outcome_predicate=target_outcome_predicate,
                cause_name=cause_name,
                effect_name=effect_name,
            )
            results.append(res)
            if target_outcome_predicate:
                cf_outcome = target_outcome_predicate(res.counterfactual_events)
                trials.append((base_outcome, cf_outcome))

        aggregate_scoring = compute_empirical_causal_scores(trials)
        return results, aggregate_scoring


__all__ = [
    "CounterfactualEngine",
    "CounterfactualResult",
    "CounterfactualScenario",
    "L3CounterfactualResult",
    "L3PerturbationEngine",
    "StepFn",
]
