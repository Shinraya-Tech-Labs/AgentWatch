"""Tests for L3 Counterfactual Replay Perturbation Engine & Invariant Falsification.

Issue #735 test suite verifying:
- Deterministic baseline and counterfactual replay reproducibility.
- Step-specific interventions (tool-result mutation, simulated API errors, prompt changes).
- Validation of invalid intervention inputs.
- Trajectory divergence metrics (normalized edit distance, structured output diffs).
- Invariant evaluation and falsification reporting.
- Empirical necessity and sufficiency causal scoring marked EXPERIMENTAL.
- Hermetic execution and zero host filesystem mutations.
- Storage and memory blob store compatibility.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from panoptis.core.schema import (
    AgentEvent,
    EventType,
    ExecutionStatus,
    RiskLevel,
    SafetyCheckData,
    ToolCallData,
    ToolResultData,
)
from panoptis.evidence.blobs import MemoryBlobStore
from panoptis.replay.causal_scoring import (
    compute_empirical_causal_scores,
    generate_experimental_hypothesis,
)
from panoptis.replay.counterfactual import (
    CounterfactualEngine,
    CounterfactualScenario,
    L3CounterfactualResult,
    L3PerturbationEngine,
)
from panoptis.replay.divergence import (
    compare_structured_outputs,
    compute_trajectory_divergence,
    levenshtein_distance,
    normalized_tool_sequence_edit_distance,
)
from panoptis.replay.interventions import (
    PromptContextChange,
    SimulatedAPIError,
    ToolResultMutation,
)
from panoptis.replay.invariants import (
    custom_invariant,
    evaluate_invariants,
    forbidden_tools_invariant,
    max_steps_invariant,
    no_safety_blocks_invariant,
    no_tool_errors_invariant,
    output_schema_invariant,
    required_tools_invariant,
)


def _make_sample_trace(n_steps: int = 4) -> list[AgentEvent]:
    """Helper to create a reproducible baseline event trace."""
    events: list[AgentEvent] = []
    tools = ["read_file", "transform_data", "write_output", "send_notification"]

    for i in range(n_steps):
        t_name = tools[i % len(tools)]
        ev = AgentEvent(
            session_id="sess-test-001",
            agent_id="agent-test",
            event_type=EventType.TOOL_CALL,
            step_number=i,
            prompt_preview=f"Execute step {i}",
            tool_call=ToolCallData(
                tool_name=t_name,
                arguments={"step": i, "target": f"resource_{i}"},
            ),
            tool_result=ToolResultData(
                tool_name=t_name,
                output={"status": "ok", "value": i * 10},
            ),
            status=ExecutionStatus.SUCCESS,
        )
        events.append(ev)
    return events


# ─────────────────────────────────────────────────────────────────────────────
# 1. Reproducible Baseline & Counterfactual Replay
# ─────────────────────────────────────────────────────────────────────────────


def test_reproducible_baseline_suffix_replay() -> None:
    events = _make_sample_trace(4)
    engine = L3PerturbationEngine()

    intervention = ToolResultMutation(
        target_step=1, replacement={"status": "mutated", "value": 999}
    )
    res = engine.run(events, intervention)

    assert isinstance(res, L3CounterfactualResult)
    assert res.diverged is True
    assert res.diverged_at_step == 1
    assert len(res.counterfactual_events) == 4
    assert res.counterfactual_events[1].tool_result.output == {"status": "mutated", "value": 999}
    assert res.counterfactual_events[1].metadata.get("counterfactual") is True

    # Check that baseline was not mutated
    assert events[1].tool_result.output == {"status": "ok", "value": 10}


def test_reproducible_step_fn_simulation() -> None:
    events = _make_sample_trace(3)

    # Simulator that branches if step 1 returns error
    def step_simulator(prior: list[AgentEvent], latest: AgentEvent) -> AgentEvent | None:
        if latest.event_type == EventType.TOOL_ERROR:
            # emit fallback tool call
            return AgentEvent(
                session_id=latest.session_id,
                agent_id=latest.agent_id,
                event_type=EventType.TOOL_CALL,
                tool_call=ToolCallData(
                    tool_name="fallback_recovery", arguments={"action": "recover"}
                ),
            )
        return None

    engine = L3PerturbationEngine(step_fn=step_simulator)
    intv = SimulatedAPIError(target_step=1, error_message="Network partition", status_code=504)
    res = engine.run(events, intv)

    assert res.diverged is True
    assert len(res.counterfactual_events) >= 3
    fallback_event = res.counterfactual_events[-1]
    assert fallback_event.tool_call is not None
    assert fallback_event.tool_call.tool_name == "fallback_recovery"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Interventions & Input Validation
# ─────────────────────────────────────────────────────────────────────────────


def test_tool_result_mutation_with_mutator_callable() -> None:
    events = _make_sample_trace(2)
    engine = L3PerturbationEngine()

    intv = ToolResultMutation(
        target_step=0,
        mutator=lambda orig: {"status": "doubled", "val": orig["value"] * 2},
    )
    res = engine.run(events, intv)

    assert res.counterfactual_events[0].tool_result.output == {"status": "doubled", "val": 0}


def test_simulated_api_error_intervention() -> None:
    events = _make_sample_trace(3)
    engine = L3PerturbationEngine()

    intv = SimulatedAPIError(
        target_step=1,
        error_message="Gateway Timeout",
        error_type="UpstreamTimeoutError",
        status_code=504,
        notes="Chaos testing timeout resilience",
    )
    res = engine.run(events, intv)

    cf_ev = res.counterfactual_events[1]
    assert cf_ev.event_type == EventType.TOOL_ERROR
    assert cf_ev.status == ExecutionStatus.FAILURE
    assert "UpstreamTimeoutError: Gateway Timeout (status=504)" in cf_ev.tool_result.error
    assert cf_ev.metadata["simulated_status_code"] == 504


def test_prompt_context_change_intervention() -> None:
    events = _make_sample_trace(2)
    engine = L3PerturbationEngine()

    intv = PromptContextChange(
        target_step=0,
        prompt_replacement="Perform high-security validation",
        context_updates={"security_tier": "enhanced"},
    )
    res = engine.run(events, intv)

    cf_ev = res.counterfactual_events[0]
    assert cf_ev.prompt_preview == "Perform high-security validation"
    assert cf_ev.metadata["security_tier"] == "enhanced"


def test_prompt_mutator_callable() -> None:
    events = _make_sample_trace(2)
    engine = L3PerturbationEngine()

    intv = PromptContextChange(
        target_step=0,
        prompt_mutator=lambda p: f"PREFIX: {p}",
    )
    res = engine.run(events, intv)
    assert res.counterfactual_events[0].prompt_preview == "PREFIX: Execute step 0"


def test_invalid_intervention_inputs() -> None:
    # Negative target step
    with pytest.raises(ValueError, match="non-negative"):
        ToolResultMutation(target_step=-1, replacement="val")

    # Non-integer target step
    with pytest.raises(TypeError, match="must be an integer"):
        ToolResultMutation(target_step="first", replacement="val")  # type: ignore

    # Missing replacement and mutator
    with pytest.raises(ValueError, match="requires either replacement or mutator"):
        ToolResultMutation(target_step=0)

    # Non-callable mutator
    with pytest.raises(TypeError, match="mutator must be callable"):
        ToolResultMutation(target_step=0, mutator="not_a_function")  # type: ignore

    # SimulatedAPIError invalid status code
    with pytest.raises(TypeError, match="status_code must be an integer"):
        SimulatedAPIError(target_step=0, status_code="bad")  # type: ignore

    # PromptContextChange empty
    with pytest.raises(ValueError, match="requires prompt_replacement, prompt_mutator"):
        PromptContextChange(target_step=0)

    # Out of range step during replay
    events = _make_sample_trace(2)
    engine = L3PerturbationEngine()
    with pytest.raises(ValueError, match="out of bounds"):
        engine.run(events, ToolResultMutation(target_step=99, replacement="val"))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Trajectory Divergence Metrics
# ─────────────────────────────────────────────────────────────────────────────


def test_levenshtein_distance_unit_cases() -> None:
    assert levenshtein_distance([], []) == 0
    assert levenshtein_distance(["a"], ["a"]) == 0
    assert levenshtein_distance(["a"], ["b"]) == 1
    assert levenshtein_distance(["a", "b"], ["a", "c", "b"]) == 1
    assert levenshtein_distance(["a", "b", "c"], ["a", "c"]) == 1


def test_normalized_tool_sequence_edit_distance() -> None:
    assert normalized_tool_sequence_edit_distance([], []) == 0.0
    assert normalized_tool_sequence_edit_distance(["t1", "t2"], ["t1", "t2"]) == 0.0
    assert normalized_tool_sequence_edit_distance(["t1"], ["t2"]) == 1.0
    assert normalized_tool_sequence_edit_distance(["t1", "t2"], ["t1"]) == 0.5


def test_trajectory_divergence_identical_traces() -> None:
    events = _make_sample_trace(3)
    div = compute_trajectory_divergence(events, events)

    assert div.has_diverged is False
    assert div.earliest_divergence_step is None
    assert div.normalized_tool_edit_distance == 0.0
    assert div.tool_sequence_similarity == 1.0
    assert div.output_comparison["changed"] is False


def test_structured_output_comparison() -> None:
    out_a = {"alpha": 1, "beta": "test", "gamma": True}
    out_b = {"alpha": 1, "beta": "changed", "delta": [1, 2]}

    comp = compare_structured_outputs(out_a, out_b)
    assert comp["changed"] is True
    assert "delta" in comp["added_keys"]
    assert "gamma" in comp["removed_keys"]
    assert "beta" in comp["modified_fields"]
    assert comp["modified_fields"]["beta"] == {"before": "test", "after": "changed"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Invariant Passing and Falsification
# ─────────────────────────────────────────────────────────────────────────────


def test_no_tool_errors_invariant_falsification() -> None:
    events = _make_sample_trace(3)
    engine = L3PerturbationEngine(invariants=[no_tool_errors_invariant()])

    # Pass case
    pass_res = engine.run(events, ToolResultMutation(target_step=1, replacement={"status": "ok"}))
    assert pass_res.invariants_report is not None
    assert pass_res.invariants_report.falsified is False
    assert pass_res.invariants_report.passed_count == 1

    # Falsification case
    fail_res = engine.run(
        events, SimulatedAPIError(target_step=1, error_message="Database offline")
    )
    assert fail_res.invariants_report is not None
    assert fail_res.invariants_report.falsified is True
    assert len(fail_res.invariants_report.failed_invariants) == 1
    failed_inv = fail_res.invariants_report.failed_invariants[0]
    assert failed_inv.name == "no_tool_errors"
    assert failed_inv.failed_at_step == 1
    assert "Database offline" in failed_inv.reason


def test_max_steps_invariant_falsification() -> None:
    events = _make_sample_trace(4)
    engine = L3PerturbationEngine(invariants=[max_steps_invariant(max_steps=2)])

    res = engine.run(events, ToolResultMutation(target_step=0, replacement="mut"))
    assert res.invariants_report.falsified is True
    assert "exceeded maximum limit of 2" in res.invariants_report.failed_invariants[0].reason


def test_forbidden_tools_invariant() -> None:
    events = _make_sample_trace(4)
    # Trace contains 'write_output' at step 2
    engine = L3PerturbationEngine(invariants=[forbidden_tools_invariant(["write_output"])])

    res = engine.run(events, ToolResultMutation(target_step=0, replacement="mut"))
    assert res.invariants_report.falsified is True
    assert (
        "Forbidden tool 'write_output' invoked" in res.invariants_report.failed_invariants[0].reason
    )


def test_required_tools_invariant() -> None:
    events = _make_sample_trace(2)  # has read_file, transform_data
    engine = L3PerturbationEngine(
        invariants=[required_tools_invariant(["read_file", "send_notification"])]
    )

    res = engine.run(events, ToolResultMutation(target_step=0, replacement="mut"))
    assert res.invariants_report.falsified is True
    assert (
        "Required tool(s) not invoked: ['send_notification']"
        in res.invariants_report.failed_invariants[0].reason
    )


def test_output_schema_invariant() -> None:
    events = _make_sample_trace(2)
    # Output of step 1 is {"status": "ok", "value": 10}
    inv = output_schema_invariant(required_keys=["status", "checksum"])
    report = evaluate_invariants(events, [inv])

    assert report.falsified is True
    assert "Missing required keys in output: ['checksum']" in report.failed_invariants[0].reason


def test_no_safety_blocks_invariant() -> None:
    events = _make_sample_trace(2)
    events[1].safety = SafetyCheckData(
        risk_level=RiskLevel.CRITICAL,
        risk_score=0.95,
        blocked=True,
        reasons=["Unauthorized credential access attempt"],
    )

    report = evaluate_invariants(events, [no_safety_blocks_invariant()])
    assert report.falsified is True
    assert "Action blocked by safety engine" in report.failed_invariants[0].reason


def test_custom_invariant() -> None:
    events = _make_sample_trace(3)
    inv = custom_invariant(
        name="must_not_have_odd_step_numbers",
        predicate=lambda evs: all(e.step_number % 2 == 0 for e in evs),
        failure_reason="Found odd step numbers",
    )
    report = evaluate_invariants(events, [inv])
    assert report.falsified is True
    assert report.failed_invariants[0].reason == "Found odd step numbers"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Causal Scoring & Hypothesis Generation
# ─────────────────────────────────────────────────────────────────────────────


def test_empirical_necessity_sufficiency_scoring() -> None:
    # Trial 1: Baseline had failure (True), intervention prevented failure (False) -> Necessity demonstrated
    # Trial 2: Baseline had success (False), intervention triggered failure (True) -> Sufficiency demonstrated
    trials = [
        (True, False),  # Prevents failure -> contributes to necessity
        (True, False),  # Prevents failure -> contributes to necessity
        (False, True),  # Causes failure -> contributes to sufficiency
        (False, False),  # Did not cause failure
    ]

    scores = compute_empirical_causal_scores(trials)
    assert scores.necessity_score == 1.0  # 2/2 prevented
    assert scores.sufficiency_score == 0.5  # 1/2 induced
    assert scores.trials_count == 4
    assert scores.maturity == "EXPERIMENTAL"
    assert scores.evidence_class == "INTERVENTIONAL"


def test_empirical_scores_edge_cases() -> None:
    # Empty trials
    scores_empty = compute_empirical_causal_scores([])
    assert scores_empty.necessity_score is None
    assert scores_empty.sufficiency_score is None

    # Only baseline successes
    scores_succ = compute_empirical_causal_scores([(False, True)])
    assert scores_succ.necessity_score is None
    assert scores_succ.sufficiency_score == 1.0


def test_generate_experimental_hypothesis() -> None:
    trials = [(True, False)]
    scores = compute_empirical_causal_scores(trials)
    hyp = generate_experimental_hypothesis(
        cause="db_timeout_retry",
        effect="transaction_abort",
        scoring_result=scores,
    )

    assert hyp["cause"] == "db_timeout_retry"
    assert hyp["effect"] == "transaction_abort"
    assert hyp["maturity"] == "EXPERIMENTAL"
    assert hyp["evidence_class"] == "INTERVENTIONAL"
    assert hyp["status"] == "PROPOSED"
    assert "Empirical" in hyp["rationale"]


def test_engine_run_with_causal_predicate() -> None:
    events = _make_sample_trace(3)

    # Define target outcome predicate: True if any tool error occurs
    def predicate(evs: list[AgentEvent]) -> bool:
        return any(e.status == ExecutionStatus.FAILURE for e in evs)

    engine = L3PerturbationEngine()
    intv = SimulatedAPIError(target_step=1, error_message="Injected outage")

    res = engine.run(
        events,
        intv,
        target_outcome_predicate=predicate,
        cause_name="injected_outage",
        effect_name="execution_failure",
    )

    assert res.causal_scoring is not None
    # Baseline had no failure (False), CF had failure (True) -> Sufficiency 1.0
    assert res.causal_scoring.sufficiency_score == 1.0
    assert res.hypothesis is not None
    assert res.hypothesis["maturity"] == "EXPERIMENTAL"
    assert res.hypothesis["cause"] == "injected_outage"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Hermetic Replay & Zero Host Filesystem Mutations
# ─────────────────────────────────────────────────────────────────────────────


def test_zero_host_filesystem_mutations(tmp_path: Path) -> None:
    """Verify that counterfactual replay does not write to the host filesystem."""
    # Snapshot directory before
    snapshot_before = set(os.listdir(tmp_path))

    events = _make_sample_trace(5)
    blob_store = MemoryBlobStore()
    engine = L3PerturbationEngine(blob_store=blob_store)

    interventions = [
        ToolResultMutation(target_step=1, replacement={"data": "isolated"}),
        SimulatedAPIError(target_step=3, error_message="Memory sandbox error"),
    ]

    res = engine.run(events, interventions)
    assert res.diverged is True

    # Snapshot directory after
    snapshot_after = set(os.listdir(tmp_path))
    assert snapshot_after == snapshot_before, (
        "Replay engine must not create files in test directory"
    )


def test_memory_blob_store_compatibility() -> None:
    blob_store = MemoryBlobStore()
    payload = b'{"tool_payload": "offline_mock_data"}'
    digest = blob_store.put(payload)

    assert blob_store.exists(digest)
    assert blob_store.get(digest) == payload

    engine = L3PerturbationEngine(blob_store=blob_store)
    events = _make_sample_trace(2)
    res = engine.run(events, ToolResultMutation(target_step=0, replacement={"blob": digest}))

    assert res.counterfactual_events[0].tool_result.output == {"blob": digest}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Backward Compatibility with CounterfactualEngine (v0.2)
# ─────────────────────────────────────────────────────────────────────────────


def test_counterfactual_engine_backward_compat() -> None:
    events = _make_sample_trace(3)
    legacy_engine = CounterfactualEngine()
    scenario = CounterfactualScenario(
        rewind_to_step=1, tool_id="transform_data", replacement="LEGACY_SWAP"
    )

    result = legacy_engine.run(events, scenario)
    assert result.diverged is True
    assert result.diverged_at_step == 1
    assert result.alternate_events[1].tool_result.output == "LEGACY_SWAP"
    assert result.divergence is not None
    assert result.divergence.has_diverged is True


# ─────────────────────────────────────────────────────────────────────────────
# 8. Additional Integration & Rigorous Edge Case Verification
# ─────────────────────────────────────────────────────────────────────────────


def test_workspace_hypothesis_consumption_compatibility() -> None:
    """Verify that generated hypotheses match repository schema and are consumed by panoptis.causality."""
    from panoptis.causality import hypotheses
    from panoptis.query.workspace import Workspace
    from panoptis.storage.store import Store

    ws = Workspace(Store("sqlite:///:memory:"))
    trials = [(True, False)]
    scores = compute_empirical_causal_scores(trials)

    hyp_doc = generate_experimental_hypothesis(
        cause="rate_limit_throttle",
        effect="task_failure",
        scoring_result=scores,
        ws=ws,
    )
    assert "hypothesis_id" in hyp_doc
    hid = hyp_doc["hypothesis_id"]

    # Retrieve through causality module
    retrieved = hypotheses.get(ws, hid)
    assert retrieved["cause"] == "rate_limit_throttle"
    assert retrieved["effect"] == "task_failure"
    assert retrieved["maturity"] == "EXPERIMENTAL"
    assert retrieved["status"] == "PROPOSED"

    # List through causality module
    all_hyp = hypotheses.list_hypotheses(ws)
    assert any(h["cause"] == "rate_limit_throttle" for h in all_hyp)


def test_invariant_evaluator_exception_gracefully_handled() -> None:
    """Verify that an evaluator raising an unexpected exception does not crash replay."""
    from panoptis.replay.invariants import Invariant

    def crashing_evaluator(events: list[AgentEvent]) -> tuple[bool, str | None, int | None]:
        raise ZeroDivisionError("artificial division by zero in custom evaluator")

    inv = Invariant(name="crashing_inv", evaluator=crashing_evaluator)
    events = _make_sample_trace(2)
    report = evaluate_invariants(events, [inv])

    assert report.falsified is True
    assert len(report.failed_invariants) == 1
    failed = report.failed_invariants[0]
    assert failed.name == "crashing_inv"
    assert "ZeroDivisionError" in failed.reason


def test_l3_engine_run_suite_multi_trial() -> None:
    """Verify run_suite evaluates multiple scenarios and returns aggregate causal scores."""
    events = _make_sample_trace(3)

    # Predicate: failure if step 1 has value > 50
    def predicate(evs: list[AgentEvent]) -> bool:
        return any(
            e.tool_result
            and isinstance(e.tool_result.output, dict)
            and e.tool_result.output.get("value", 0) > 50
            for e in evs
        )

    # In baseline, step 1 has value 10 -> predicate is False
    engine = L3PerturbationEngine()
    scenarios = [
        ToolResultMutation(
            target_step=1, replacement={"status": "ok", "value": 100}
        ),  # CF True -> Sufficiency hit
        ToolResultMutation(
            target_step=1, replacement={"status": "ok", "value": 200}
        ),  # CF True -> Sufficiency hit
        ToolResultMutation(target_step=1, replacement={"status": "ok", "value": 5}),  # CF False
    ]

    results, aggregate_scores = engine.run_suite(
        events,
        scenarios,
        target_outcome_predicate=predicate,
        cause_name="large_tool_value",
        effect_name="value_threshold_exceeded",
    )

    assert len(results) == 3
    assert aggregate_scores.trials_count == 3
    # 2 out of 3 baseline success trials induced the effect: sufficiency = 2/3 = 0.6667
    assert aggregate_scores.sufficiency_score == 0.6667


def test_tool_sequence_extraction_with_paired_events() -> None:
    """Verify that traces with paired TOOL_CALL and TOOL_RESULT events do not duplicate tool names."""
    from panoptis.replay.divergence import _extract_tool_sequence

    events = [
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.TOOL_CALL,
            step_number=0,
            tool_call=ToolCallData(tool_name="bash"),
        ),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.TOOL_RESULT,
            step_number=1,
            tool_result=ToolResultData(tool_name="bash", output="ok"),
        ),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.TOOL_CALL,
            step_number=2,
            tool_call=ToolCallData(tool_name="python_repl"),
        ),
        AgentEvent(
            session_id="S",
            agent_id="A",
            event_type=EventType.TOOL_RESULT,
            step_number=3,
            tool_result=ToolResultData(tool_name="python_repl", output="done"),
        ),
    ]

    tools = _extract_tool_sequence(events)
    assert tools == ["bash", "python_repl"]
