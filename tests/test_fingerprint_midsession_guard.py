"""Regression tests for RSN-008 fingerprint mid-session-change false positives.

Issue #538: detect_mid_session_change() must not flag a mid-session model change
when one half of the session simply has no PLANNER_OUTPUT events to fingerprint
(the all-zeros sentinel from fingerprint() means "no signal", not "changed").
"""

from __future__ import annotations

from panoptis.core.schema import AgentEvent, EventType
from panoptis.reasoning.fingerprint import detect_mid_session_change


def _plan(text: str) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        event_type=EventType.PLANNER_OUTPUT,
        planner_output_preview=text,
    )


def _tool() -> AgentEvent:
    return AgentEvent(session_id="S", agent_id="A", event_type=EventType.TOOL_CALL)


def test_planner_events_front_loaded_do_not_flag_change():
    # 3 consistent planner outputs, then 5 tool calls with no planner text.
    # The second half fingerprints to the all-zeros sentinel; that must NOT be
    # read as a mid-session model change (the bug in #538).
    plan = "I will read the file and then analyze the contents carefully."
    events = [_plan(plan) for _ in range(3)] + [_tool() for _ in range(5)]
    changed, dist = detect_mid_session_change(events)
    assert changed is False
    assert dist == 0.0


def test_planner_events_back_loaded_do_not_flag_change():
    # Symmetric case: tools first, planners second — the first half has no
    # planner output, so it must be treated as "no signal" too.
    plan = "Investigate the failure, form a hypothesis, and verify it."
    events = [_tool() for _ in range(5)] + [_plan(plan) for _ in range(3)]
    changed, dist = detect_mid_session_change(events)
    assert changed is False
    assert dist == 0.0


def test_even_planner_distribution_no_false_change():
    plan = "Read the config, then apply the migration step by step."
    events = [_plan(plan) for _ in range(6)]
    changed, _ = detect_mid_session_change(events)
    assert changed is False


def test_genuine_style_change_still_detected():
    # A real shift in planner style (terse -> verbose) must still be flagged, so
    # the guard does not mask true positives.
    short = [_plan("ok") for _ in range(5)]
    verbose = [
        _plan(
            "A very long and elaborate plan with many words and specific "
            "punctuation. Multiple sentences here."
        )
        for _ in range(5)
    ]
    changed, dist = detect_mid_session_change(short + verbose, distance_threshold=0.5)
    assert changed is True
    assert dist > 0.5


def test_short_session_short_circuits():
    events = [_plan("plan a"), _tool(), _plan("plan b")]
    assert detect_mid_session_change(events) == (False, 0.0)
