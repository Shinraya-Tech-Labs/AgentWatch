"""Tests for the batch evaluation runner (issue #342)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from panoptis.core.schema import AgentEvent, EventType, ToolCallData
from panoptis.eval.runner import (
    EvalCase,
    EvalCaseResult,
    aggregate,
    evaluate_case,
    load_agent,
    load_dataset,
    run_eval,
)
from panoptis.scoring.confidence import ConfidenceScorer


def _event(text: str = "ok") -> AgentEvent:
    return AgentEvent(
        session_id="s",
        agent_id="a",
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name="echo", raw_command=text, arguments={}),
    )


_AGENT_SRC = """
from panoptis.core.schema import AgentEvent, EventType, ToolCallData


def run(prompt):
    return [
        AgentEvent(
            session_id="s",
            agent_id="a",
            event_type=EventType.TOOL_CALL,
            tool_call=ToolCallData(tool_name="echo", raw_command=prompt, arguments={}),
        )
    ]
"""


# ── load_dataset ───────────────────────────────────────────────────────────


def test_load_dataset_parses_cases(tmp_path: Path) -> None:
    f = tmp_path / "d.json"
    f.write_text(
        json.dumps([{"id": "c1", "prompt": "hello", "expected": "hi"}, {"prompt": "second"}]),
        encoding="utf-8",
    )
    cases = load_dataset(f)
    assert [c.id for c in cases] == ["c1", "2"]  # id defaults to 1-based index
    assert cases[0].expected == "hi"
    assert cases[1].expected is None


def test_load_dataset_rejects_non_array(tmp_path: Path) -> None:
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"prompt": "x"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(f)


def test_load_dataset_requires_prompt(tmp_path: Path) -> None:
    f = tmp_path / "d.json"
    f.write_text(json.dumps([{"id": "c1"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(f)


def test_load_dataset_rejects_empty(tmp_path: Path) -> None:
    f = tmp_path / "d.json"
    f.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(f)


def test_load_dataset_rejects_invalid_json(tmp_path: Path) -> None:
    f = tmp_path / "d.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(f)


# ── load_agent ─────────────────────────────────────────────────────────────


def test_load_agent_returns_callable(tmp_path: Path) -> None:
    f = tmp_path / "agent.py"
    f.write_text(_AGENT_SRC, encoding="utf-8")
    run = load_agent(f)
    events = run("hello world")
    assert len(events) == 1
    assert events[0].tool_call is not None
    assert events[0].tool_call.raw_command == "hello world"


def test_load_agent_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_agent(tmp_path / "nope.py")


def test_load_agent_without_run_raises(tmp_path: Path) -> None:
    f = tmp_path / "agent.py"
    f.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AttributeError):
        load_agent(f)


# ── evaluate_case ──────────────────────────────────────────────────────────


def test_evaluate_case_passes_at_threshold_zero() -> None:
    r = evaluate_case(
        EvalCase(id="c", prompt="p"), lambda _: [_event()], ConfidenceScorer(), threshold=0.0
    )
    assert r.passed is True
    assert r.error is None
    assert 0.0 <= r.confidence <= 1.0
    assert 0.0 <= r.hallucination_risk <= 1.0


def test_evaluate_case_fails_impossible_threshold() -> None:
    r = evaluate_case(
        EvalCase(id="c", prompt="p"), lambda _: [_event()], ConfidenceScorer(), threshold=1.01
    )
    assert r.passed is False


def test_evaluate_case_expected_present_passes() -> None:
    case = EvalCase(id="c", prompt="say hello", expected="hello")
    r = evaluate_case(case, lambda p: [_event(p)], ConfidenceScorer(), threshold=0.0)
    assert r.passed is True


def test_evaluate_case_expected_absent_fails() -> None:
    case = EvalCase(id="c", prompt="p", expected="not-in-trace-xyz")
    r = evaluate_case(case, lambda _: [_event("something else")], ConfidenceScorer(), threshold=0.0)
    assert r.passed is False


def test_evaluate_case_agent_exception_is_errored() -> None:
    def boom(_prompt: str) -> list[AgentEvent]:
        raise RuntimeError("agent crashed")

    r = evaluate_case(EvalCase(id="c", prompt="p"), boom, ConfidenceScorer())
    assert r.passed is False
    assert r.error is not None
    assert "agent crashed" in r.error


def test_evaluate_case_wrong_return_type_is_errored() -> None:
    r = evaluate_case(EvalCase(id="c", prompt="p"), lambda _: "not a list", ConfidenceScorer())  # type: ignore[arg-type,return-value]
    assert r.passed is False
    assert r.error is not None


# ── aggregate / EvalReport ─────────────────────────────────────────────────


def test_aggregate_computes_summary() -> None:
    results = [
        EvalCaseResult(
            id="1", passed=True, confidence=0.9, hallucination_risk=0.1, hallucinated=False
        ),
        EvalCaseResult(
            id="2", passed=False, confidence=0.3, hallucination_risk=0.7, hallucinated=True
        ),
        EvalCaseResult(
            id="3",
            passed=False,
            confidence=0.0,
            hallucination_risk=1.0,
            hallucinated=False,
            error="boom",
        ),
    ]
    report = aggregate(results, threshold=0.5)
    assert report.total == 3
    assert report.passed == 1
    assert report.pass_rate == pytest.approx(1 / 3)
    assert report.avg_confidence == pytest.approx((0.9 + 0.3 + 0.0) / 3)
    assert report.avg_hallucination_risk == pytest.approx((0.1 + 0.7 + 1.0) / 3)
    assert report.num_hallucinated == 1
    assert report.num_errored == 1


def test_empty_report_no_divide_error() -> None:
    report = aggregate([], threshold=0.5)
    assert report.pass_rate == 0.0
    assert report.avg_confidence == 0.0
    assert report.total == 0


def test_report_to_dict_shape() -> None:
    report = aggregate(
        [
            EvalCaseResult(
                id="1", passed=True, confidence=0.8, hallucination_risk=0.2, hallucinated=False
            )
        ],
        threshold=0.5,
    )
    data = report.to_dict()
    assert data["threshold"] == 0.5
    assert data["summary"]["total"] == 1
    assert data["summary"]["passed"] == 1
    assert data["results"][0]["id"] == "1"


# ── end-to-end run_eval ────────────────────────────────────────────────────


def test_run_eval_end_to_end(tmp_path: Path) -> None:
    agent = tmp_path / "agent.py"
    agent.write_text(_AGENT_SRC, encoding="utf-8")
    dataset = tmp_path / "d.json"
    dataset.write_text(
        json.dumps(
            [
                {"id": "a", "prompt": "say hello", "expected": "hello"},
                {"id": "b", "prompt": "another prompt"},
            ]
        ),
        encoding="utf-8",
    )
    report = run_eval(dataset, agent, threshold=0.0)
    assert report.total == 2
    assert report.passed == 2  # both pass at threshold 0.0 (expected "hello" is echoed)
    assert {r.id for r in report.results} == {"a", "b"}
