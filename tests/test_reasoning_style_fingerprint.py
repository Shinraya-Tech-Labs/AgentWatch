"""Tests for RSN-008 reasoning style fingerprint integration with the auditor.

Covers:
- ReasoningStyleFingerprint and StyleSwapAlert schema models.
- ReasoningAuditor.fingerprint_session() end-to-end.
- Auto-derivation of StyleSwapAlert.reason for non-detection cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from panoptis.core.schema import (
    AgentEvent,
    EventType,
    ReasoningStyleFingerprint,
    StyleSwapAlert,
)
from panoptis.reasoning.auditor import ReasoningAuditor
from panoptis.reasoning.fingerprint import StyleFingerprint


def _plan(text: str, index: int) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.PLANNER_OUTPUT,
        planner_output_preview=text,
        step_number=index,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _tool(index: int) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.TOOL_CALL,
        step_number=index,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_reasoning_style_fingerprint_from_dataclass():
    """Adapt the dataclass form to the pydantic schema model."""
    fp = StyleFingerprint(
        mean_planner_tokens=12.5,
        lex_diversity=0.8,
        tools_per_plan=2.0,
        punctuation_rate=0.05,
    )
    pyd = ReasoningStyleFingerprint.from_dataclass(fp)
    assert pyd.mean_planner_tokens == 12.5
    assert pyd.lex_diversity == 0.8
    assert pyd.tools_per_plan == 2.0
    assert pyd.punctuation_rate == 0.05
    assert pyd.sample_size == 0


def test_style_swap_alert_serialises():
    """StyleSwapAlert round-trips through model_dump for the API."""
    fp = ReasoningStyleFingerprint(
        mean_planner_tokens=10.0,
        lex_diversity=0.6,
        tools_per_plan=1.5,
        punctuation_rate=0.04,
    )
    alert = StyleSwapAlert(
        session_id="S",
        detected=True,
        distance=1.2,
        threshold=1.0,
        first_half=fp,
        second_half=fp,
    )
    out = alert.model_dump()
    assert out["session_id"] == "S"
    assert out["detected"] is True
    assert out["distance"] == 1.2
    assert out["first_half"]["mean_planner_tokens"] == 10.0


def test_fingerprint_session_no_swap_detected_consistent_plans():
    plan = "Read the configuration file carefully and apply the migration step by step."
    events = [_plan(plan, i) for i in range(8)]
    auditor = ReasoningAuditor()
    report = auditor.fingerprint_session(events)

    assert report.session_id == "S"
    assert report.fingerprint.sample_size == 8
    assert report.swap_alert.detected is False
    assert report.swap_alert.distance == 0.0
    assert report.swap_alert.reason in {
        "insufficient_planner_signal",
        "below_threshold",
        "style_identical",
    }


def test_fingerprint_session_detects_style_swap():
    """When planner verbosity changes mid-session, detection should fire."""
    short_plans = [_plan("ok", i) for i in range(4)]
    verbose_plans = [
        _plan(
            "A carefully constructed plan that explores all the relevant "
            "details and considerations before committing to a course of "
            "action. Multiple sentences? Considerable detail!",
            i + 4,
        )
        for i in range(4)
    ]
    events = short_plans + verbose_plans
    auditor = ReasoningAuditor()
    report = auditor.fingerprint_session(events, distance_threshold=0.3)

    assert report.swap_alert.detected is True
    assert report.swap_alert.distance > 0.3
    assert report.swap_alert.threshold == 0.3
    assert report.swap_alert.reason is None  # detected -> no reason blob
    # Each half's sample_size is now populated from the half's planner count.
    assert report.swap_alert.first_half.sample_size == 4
    assert report.swap_alert.second_half.sample_size == 4


def test_fingerprint_session_short_circuit_records_reason():
    events = [_plan("one", 0), _tool(1)]
    auditor = ReasoningAuditor()
    report = auditor.fingerprint_session(events)
    assert report.swap_alert.detected is False
    assert report.swap_alert.reason == "insufficient_events"


def test_fingerprint_report_to_dict_roundtrip():
    plan = "A plan with multiple words and detail for testing serialization purposes."
    events = [_plan(plan, i) for i in range(6)]
    auditor = ReasoningAuditor()
    report = auditor.fingerprint_session(events)
    data = report.to_dict()
    assert "fingerprint" in data
    assert "swap_alert" in data
    assert isinstance(data["fingerprint"]["sample_size"], int)


@pytest.mark.parametrize(
    "events, expected_reason",
    [
        ([], "insufficient_events"),
        ([_plan("x", 0)], "insufficient_events"),
        ([_plan("plan", i) for i in range(5)], "insufficient_events"),
        (
            [_plan("single-plan-planner-alone", i) for i in range(6)],
            "style_identical",
        ),
    ],
)
def test_fingerprint_session_handles_edge_cases(events, expected_reason):
    auditor = ReasoningAuditor()
    report = auditor.fingerprint_session(events)
    assert report.swap_alert.reason == expected_reason
    # Detection is always False for the non-detection parameterised rows.
    assert report.swap_alert.detected is False
