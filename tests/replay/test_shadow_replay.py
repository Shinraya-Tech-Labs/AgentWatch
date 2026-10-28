"""Shadow replay: deterministic vs stochastic failure isolation (issue #751).

Every scenario builds a small recorded session, replays it with a scripted "model" and checks
the classification. No test needs a real LLM, a network or a database.
"""

from __future__ import annotations

import json
import socket

import pytest

from agentwatch.core.schema import (
    AgentEvent,
    EventType,
    ExecutionStatus,
    ToolCallData,
    ToolResultData,
)
from agentwatch.replay import (
    PromptCompletionModel,
    ShadowClassification,
    ShadowConfig,
    ShadowContext,
    ShadowReplayRunner,
    ToolEvidenceStore,
    from_step_fn,
    run_shadow_replay,
)
from agentwatch.replay.shadow import (
    arguments_hash,
    compare_traces,
    event_signature,
    historical_failed,
)

SESSION = "sess-1"
AGENT = "agent-1"


# ─────────────────────────────────────────────
# Builders
# ─────────────────────────────────────────────


def plan(text: str, prompt: str = "Find the answer") -> AgentEvent:
    return AgentEvent(
        session_id=SESSION,
        agent_id=AGENT,
        event_type=EventType.PLANNER_OUTPUT,
        status=ExecutionStatus.SUCCESS,
        prompt_preview=prompt,
        planner_output_preview=text,
    )


def call(tool: str, args: dict | None = None, tool_id: str | None = None) -> AgentEvent:
    return AgentEvent(
        session_id=SESSION,
        agent_id=AGENT,
        event_type=EventType.TOOL_CALL,
        status=ExecutionStatus.SUCCESS,
        tool_call=ToolCallData(tool_name=tool, arguments=args or {}, tool_id=tool_id),
    )


def result(tool: str, output=None, tool_id: str | None = None) -> AgentEvent:
    return AgentEvent(
        session_id=SESSION,
        agent_id=AGENT,
        event_type=EventType.TOOL_RESULT,
        status=ExecutionStatus.SUCCESS,
        tool_result=ToolResultData(tool_name=tool, output=output, tool_id=tool_id),
    )


def tool_error(tool: str, error: str, tool_id: str | None = None) -> AgentEvent:
    return AgentEvent(
        session_id=SESSION,
        agent_id=AGENT,
        event_type=EventType.TOOL_ERROR,
        status=ExecutionStatus.FAILURE,
        tool_result=ToolResultData(tool_name=tool, error=error, tool_id=tool_id),
    )


def finish(text: str, status: ExecutionStatus = ExecutionStatus.SUCCESS) -> AgentEvent:
    return AgentEvent(
        session_id=SESSION,
        agent_id=AGENT,
        event_type=EventType.AGENT_END,
        status=status,
        planner_output_preview=text,
    )


def echo(ctx: ShadowContext) -> AgentEvent | None:
    """A model that makes exactly the decision the original run made."""
    return ctx.recorded


def scripted(*decisions: AgentEvent | None):
    """A model that returns the given decisions in order, ignoring what was recorded."""
    queue = list(decisions)

    def model(ctx: ShadowContext) -> AgentEvent | None:
        return queue.pop(0) if queue else None

    return model


SUCCESS_SESSION = [
    plan("I will search"),
    call("search", {"q": "bibble"}),
    result("search", {"hits": 3}),
    finish("Found 3 hits"),
]

FAILED_SESSION = [
    plan("I will query"),
    call("db_query", {"sql": "SELECT 1"}),
    tool_error("db_query", "connection reset"),
    finish("could not complete", ExecutionStatus.FAILURE),
]


# ─────────────────────────────────────────────
# The three classifications from the issue
# ─────────────────────────────────────────────


def test_identical_replay_when_nothing_changed_and_the_run_succeeded():
    report = run_shadow_replay(SUCCESS_SESSION, echo)
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY
    assert report.divergences == []
    assert report.first_divergence_index is None
    assert report.historical_failed is False
    assert report.trajectory_distance == 0.0
    assert report.evidence_served == 1 and report.evidence_unused == 0


def test_deterministic_corroboration_when_a_recorded_failure_reproduces():
    report = run_shadow_replay(FAILED_SESSION, echo)
    assert report.classification == ShadowClassification.DETERMINISTIC_CORROBORATION
    assert report.deterministic is True
    assert report.historical_failed is True
    assert report.failure_reproduced is True
    assert report.divergences == []
    assert "deterministic" in report.summary.lower()


def test_stochastic_divergence_when_the_model_branches_despite_identical_tool_returns():
    # Same call, same recorded result, but the model ends with a different answer.
    model = scripted(
        plan("I will search"), call("search", {"q": "bibble"}), finish("Found nothing")
    )
    report = run_shadow_replay(SUCCESS_SESSION, model)
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.first_divergence_index == 3
    d = report.divergences[0]
    assert d.fields_changed == ("text",)
    assert d.at_evidence_event is False
    assert report.failure_reproduced is False


def test_a_recorded_failure_that_does_not_reproduce_is_stochastic_not_deterministic():
    # The tool error is served again, but this time the model recovers.
    model = scripted(
        plan("I will query"),
        call("db_query", {"sql": "SELECT 1"}),
        finish("recovered", ExecutionStatus.SUCCESS),
    )
    report = run_shadow_replay(FAILED_SESSION, model)
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.historical_failed is True
    assert report.failure_reproduced is False
    assert "did not reproduce" in report.summary


def test_the_model_diverging_at_the_first_decision_is_still_stochastic():
    model = scripted(plan("Totally different plan"))
    report = run_shadow_replay(SUCCESS_SESSION, model)
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.first_divergence_index == 0


# ─────────────────────────────────────────────
# Missing evidence
# ─────────────────────────────────────────────


def test_a_call_with_no_recorded_answer_is_insufficient_evidence_and_stops_the_replay():
    model = scripted(
        plan("I will search"), call("web_fetch", {"url": "http://x"}), finish("unreachable")
    )
    report = run_shadow_replay(SUCCESS_SESSION, model)

    assert report.classification == ShadowClassification.INSUFFICIENT_EVIDENCE
    assert report.failure_reproduced is False
    [missing] = report.missing_evidence
    assert missing.tool_name == "web_fetch"
    assert missing.shadow_index == 1
    # The replay stopped at the call: the final answer was never asked for.
    assert [e.event_type for e in report.shadow_events] == [
        EventType.PLANNER_OUTPUT,
        EventType.TOOL_CALL,
        EventType.TOOL_ERROR,
    ]
    assert report.shadow_events[-1].metadata["shadow_missing_evidence"] is True
    assert "never recorded" in report.summary


def test_same_tool_with_different_arguments_is_missing_and_reports_the_near_miss():
    model = scripted(plan("I will search"), call("search", {"q": "something else"}))
    report = run_shadow_replay(SUCCESS_SESSION, model)
    assert report.classification == ShadowClassification.INSUFFICIENT_EVIDENCE
    assert report.missing_evidence[0].other_recorded_argument_sets == 1


def test_a_recorded_call_whose_result_was_never_captured_has_no_evidence():
    crashed = [plan("go"), call("search", {"q": "x"})]  # the session died before the result
    report = run_shadow_replay(crashed, echo)
    assert report.classification == ShadowClassification.INSUFFICIENT_EVIDENCE
    assert report.evidence_total == 0
    assert report.missing_evidence[0].tool_name == "search"


def test_asking_more_times_than_recorded_is_missing_evidence():
    recorded = [plan("go"), call("search", {"q": "x"}), result("search", 1), finish("done")]
    model = scripted(plan("go"), call("search", {"q": "x"}), call("search", {"q": "x"}))
    report = run_shadow_replay(recorded, model)
    assert report.classification == ShadowClassification.INSUFFICIENT_EVIDENCE
    assert report.evidence_served == 1
    assert report.missing_evidence[0].shadow_index == 3


def test_an_empty_recording_is_an_identical_replay_of_nothing():
    report = run_shadow_replay([], echo)
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY
    assert report.evidence_total == 0


# ─────────────────────────────────────────────
# Evidence matching
# ─────────────────────────────────────────────


def test_argument_key_order_does_not_matter():
    recorded = [call("search", {"q": "a", "limit": 5}), result("search", "ok"), finish("done")]
    model = scripted(call("search", {"limit": 5, "q": "a"}), finish("done"))
    report = run_shadow_replay(recorded, model)
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY
    assert arguments_hash({"a": 1, "b": 2}) == arguments_hash({"b": 2, "a": 1})
    assert arguments_hash({"a": 1}) != arguments_hash({"a": 2})


def test_repeated_identical_calls_get_their_own_recorded_answers_in_order():
    # A flaky tool: the same call failed first and succeeded on retry.
    recorded = [
        call("api", {"id": 1}),
        tool_error("api", "503"),
        call("api", {"id": 1}),
        result("api", {"ok": True}),
        finish("done"),
    ]
    report = run_shadow_replay(recorded, echo)
    assert (
        report.classification == ShadowClassification.DETERMINISTIC_CORROBORATION
    )  # the 503 is a recorded failure
    assert [e.event_type for e in report.shadow_events[:4]] == [
        EventType.TOOL_CALL,
        EventType.TOOL_ERROR,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    ]
    assert report.shadow_events[3].tool_result.output == {"ok": True}


def test_parallel_calls_are_paired_by_tool_id_not_by_order():
    recorded = [
        call("lookup", {"k": "a"}, tool_id="t1"),
        call("lookup", {"k": "b"}, tool_id="t2"),
        result("lookup", "B-value", tool_id="t2"),  # answers come back out of order
        result("lookup", "A-value", tool_id="t1"),
        finish("done"),
    ]
    store = ToolEvidenceStore.from_events(recorded)
    a = store.take("lookup", {"k": "a"})
    b = store.take("lookup", {"k": "b"})
    assert a is not None and a.result_event.tool_result.output == "A-value"
    assert b is not None and b.result_event.tool_result.output == "B-value"
    assert store.orphan_results == 0


def test_a_result_with_no_call_is_counted_as_an_orphan_and_never_served():
    store = ToolEvidenceStore.from_events([result("search", "stray"), call("x"), result("x", 1)])
    assert store.orphan_results == 1
    assert store.total == 1
    assert store.take("search", {}) is None


def test_unused_evidence_is_reported_when_the_shadow_skips_a_call():
    recorded = [call("a"), result("a", 1), call("b"), result("b", 2), finish("done")]
    model = scripted(call("a"), finish("done"))
    report = run_shadow_replay(recorded, model)
    assert report.evidence_total == 2
    assert report.evidence_served == 1
    assert report.evidence_unused == 1
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE


# ─────────────────────────────────────────────
# Safety: no side effects, no mutation, repeatable
# ─────────────────────────────────────────────


def test_replay_never_touches_the_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("shadow replay must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    report = run_shadow_replay(SUCCESS_SESSION, echo)
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY


def test_the_model_cannot_change_the_historical_record():
    recorded = [
        plan("go"),
        call("search", {"q": "x"}),
        result("search", {"hits": [1, 2]}),
        finish("done"),
    ]
    before = [e.model_dump_json() for e in recorded]

    def vandal(ctx: ShadowContext) -> AgentEvent | None:
        for event in ctx.history:
            event.metadata["tampered"] = True
            if event.tool_result:
                event.tool_result.output["hits"].append(999)
        if ctx.recorded is not None:
            ctx.recorded.metadata["tampered"] = True
        return ctx.recorded

    run_shadow_replay(recorded, vandal)
    assert [e.model_dump_json() for e in recorded] == before


def test_served_evidence_is_a_copy_and_cannot_poison_later_runs():
    recorded = [call("search"), result("search", {"hits": [1]}), finish("done")]
    runner = ShadowReplayRunner(echo)
    first = runner.run(recorded)
    first.shadow_events[1].tool_result.output["hits"].append("poison")
    second = runner.run(recorded)
    assert second.shadow_events[1].tool_result.output == {"hits": [1]}


def test_two_runs_of_the_same_replay_give_the_same_report():
    a = run_shadow_replay(FAILED_SESSION, echo).to_dict()
    b = run_shadow_replay(FAILED_SESSION, echo).to_dict()
    assert a == b


def test_the_original_session_is_not_modified_by_a_replay():
    recorded = [e.model_copy(deep=True) for e in FAILED_SESSION]
    before = [e.model_dump_json() for e in recorded]
    run_shadow_replay(recorded, echo)
    assert [e.model_dump_json() for e in recorded] == before


# ─────────────────────────────────────────────
# Model interface and configuration
# ─────────────────────────────────────────────


def test_the_model_receives_zero_temperature_and_the_seed():
    seen: list[ShadowConfig] = []

    def model(ctx: ShadowContext) -> AgentEvent | None:
        seen.append(ctx.config)
        return ctx.recorded

    run_shadow_replay(SUCCESS_SESSION, model, ShadowConfig(seed=1234))
    assert seen and all(c.temperature == 0.0 and c.seed == 1234 for c in seen)
    assert ShadowConfig().temperature == 0.0


def test_prompt_completion_model_resends_the_original_prompt_with_the_shadow_config():
    asked: list[tuple[str, ShadowConfig]] = []

    def complete(prompt: str, config: ShadowConfig) -> str:
        asked.append((prompt, config))
        return "I will search"  # the same completion as recorded

    recorded = [plan("I will search", prompt="Find the answer"), finish("done")]
    report = run_shadow_replay(recorded, PromptCompletionModel(complete), ShadowConfig(seed=7))
    assert asked == [("Find the answer", ShadowConfig(seed=7))]
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY


def test_prompt_completion_model_flags_a_different_completion_as_stochastic():
    recorded = [plan("I will search"), finish("done")]
    report = run_shadow_replay(recorded, PromptCompletionModel(lambda p, c: "I will guess"))
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.divergences[0].fields_changed == ("text",)


def test_an_existing_step_fn_can_drive_a_shadow_replay():
    calls: list[int] = []

    def step_fn(history: list[AgentEvent], recorded: AgentEvent) -> AgentEvent | None:
        calls.append(len(history))
        return recorded

    report = run_shadow_replay(SUCCESS_SESSION, from_step_fn(step_fn))
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY
    assert calls == [0, 1, 3]  # history includes the served tool result


def test_a_model_that_stops_early_leaves_the_rest_of_the_trace_missing():
    report = run_shadow_replay(SUCCESS_SESSION, scripted(plan("I will search")))
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.divergences[0].fields_changed == ("missing_in_shadow",)


def test_a_model_that_keeps_going_after_the_recorded_run_ended_is_a_divergence():
    recorded = [plan("a"), plan("b")]  # the recording stops here, without a terminal event
    queue = [plan("a"), plan("b"), plan("one more thing")]

    def model(ctx: ShadowContext) -> AgentEvent | None:
        return queue.pop(0) if queue else None

    report = run_shadow_replay(recorded, model)
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.first_divergence_index == 2
    assert report.divergences[0].fields_changed == ("extra_in_shadow",)


def test_a_terminal_event_ends_the_replay_even_if_the_model_has_more_to_say():
    queue = [
        *[e for e in SUCCESS_SESSION if e.event_type != EventType.TOOL_RESULT],
        plan("after the end"),
    ]

    def model(ctx: ShadowContext) -> AgentEvent | None:
        return queue.pop(0) if queue else None

    report = run_shadow_replay(SUCCESS_SESSION, model)
    assert report.classification == ShadowClassification.IDENTICAL_REPLAY
    assert queue, "the extra decision after AGENT_END was never requested"


def test_extra_decisions_are_bounded_by_the_config():
    recorded = [plan("a")]  # not terminal, so the model may continue
    produced: list[int] = []

    def endless(ctx: ShadowContext) -> AgentEvent | None:
        produced.append(ctx.index)
        return plan("a" if ctx.index == 0 else "more")

    report = run_shadow_replay(recorded, endless, ShadowConfig(max_extra_decisions=2))
    assert len(produced) == 3  # one recorded decision plus two extra
    assert report.classification == ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
    assert report.divergences[0].fields_changed == ("extra_in_shadow",)

    produced.clear()
    run_shadow_replay(recorded, endless, ShadowConfig(max_extra_decisions=0))
    assert len(produced) == 1


def test_a_model_that_injects_its_own_tool_result_is_not_a_valid_replay():
    # Evidence may only come from the recording. A decision that is itself a tool result is
    # not a model decision, so the replay cannot be trusted and no cause is concluded.
    model = scripted(plan("I will search"), result("search", {"hits": 999}))
    report = run_shadow_replay(SUCCESS_SESSION, model)
    assert report.classification == ShadowClassification.INSUFFICIENT_EVIDENCE
    assert report.first_divergence_index == 1
    assert report.divergences[0].at_evidence_event is True


# ─────────────────────────────────────────────
# Comparison and signatures
# ─────────────────────────────────────────────


def test_signatures_ignore_ids_timestamps_and_durations():
    a = result("search", {"hits": 3})
    b = result("search", {"hits": 3})
    b.duration_ms = 999.0
    assert a.event_id != b.event_id
    assert event_signature(a) == event_signature(b)


def test_signatures_notice_every_meaningful_difference():
    base = event_signature(result("search", {"hits": 3}))
    assert event_signature(result("search", {"hits": 4})) != base
    assert event_signature(result("lookup", {"hits": 3})) != base
    assert event_signature(call("search", {"q": "a"})) != event_signature(
        call("search", {"q": "b"})
    )
    assert event_signature(plan("a")) != event_signature(plan("b"))
    assert event_signature(finish("x", ExecutionStatus.SUCCESS)) != event_signature(
        finish("x", ExecutionStatus.FAILURE)
    )


def test_compare_traces_lists_what_changed_and_where():
    divergences = compare_traces(SUCCESS_SESSION, [*SUCCESS_SESSION[:3], finish("different")])
    assert [d.index for d in divergences] == [3]
    assert divergences[0].fields_changed == ("text",)
    assert compare_traces(SUCCESS_SESSION, SUCCESS_SESSION) == []


def test_the_divergence_list_is_capped():
    left = [plan(f"a{i}") for i in range(200)]
    right = [plan(f"b{i}") for i in range(200)]
    assert len(compare_traces(left, right)) == 50


def test_trajectory_distance_reflects_a_different_tool_sequence():
    model = scripted(plan("I will search"), call("search", {"q": "bibble"}), finish("Found 3 hits"))
    assert run_shadow_replay(SUCCESS_SESSION, model).trajectory_distance == 0.0
    other = [plan("go"), call("a"), result("a", 1), call("b"), result("b", 2), finish("x")]
    swapped = scripted(plan("go"), call("a"), call("b"), finish("x"))
    assert run_shadow_replay(other, swapped).trajectory_distance == 0.0
    diverged = scripted(plan("go"), call("a"), finish("stop"))
    assert run_shadow_replay(other, diverged).trajectory_distance == 0.5


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([plan("a"), finish("b")], False),
        ([plan("a"), tool_error("x", "boom")], True),
        ([plan("a"), finish("b", ExecutionStatus.FAILURE)], True),
        ([plan("a"), finish("b", ExecutionStatus.BLOCKED)], True),
        ([plan("a"), finish("b", ExecutionStatus.TIMEOUT)], True),
        ([], False),
    ],
)
def test_historical_failure_detection(events, expected):
    assert historical_failed(events) is expected


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────


def test_the_report_is_json_serialisable_and_complete():
    report = run_shadow_replay(
        SUCCESS_SESSION,
        scripted(plan("I will search"), call("search", {"q": "bibble"}), finish("Other")),
    )
    data = json.loads(json.dumps(report.to_dict()))
    assert data["classification"] == "STOCHASTIC_MODEL_DIVERGENCE"
    assert data["first_divergence_index"] == 3
    assert data["evidence"] == {"total": 1, "served": 1, "unused": 0, "orphan_results": 0}
    assert data["config"]["temperature"] == 0.0
    assert data["divergences"][0]["fields_changed"] == ["text"]
    assert data["shadow_event_count"] == 4


def test_the_public_api_is_exported_from_the_replay_package():
    import agentwatch.replay as replay

    for name in (
        "ShadowReplayRunner",
        "ShadowClassification",
        "ShadowConfig",
        "ToolEvidenceStore",
        "run_shadow_replay",
        "PromptCompletionModel",
    ):
        assert name in replay.__all__
        assert hasattr(replay, name)
