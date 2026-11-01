"""Unit tests for the Sliding Window Tool-Call Loop & Oscillation Detector (Issue #746)."""

from __future__ import annotations

from panoptis.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitThresholds,
    LoopDetector,
    TripReason,
)
from panoptis.core.schema import (
    AgentEvent,
    EventType,
    ExecutionStatus,
    ToolCallData,
)


def test_loop_detector_standalone_repetition() -> None:
    """Detects consecutive identical tool calls reaching threshold."""
    detector = LoopDetector(window_size=10, max_repeats=3, max_oscillations=2)

    assert detector.record("read_file", {"path": "/app/config.json"}) is None
    assert detector.record("read_file", {"path": "/app/config.json"}) is None
    warning = detector.record("read_file", {"path": "/app/config.json"})

    assert warning is not None
    assert "Repetitive loop detected" in warning
    assert "read_file" in warning
    assert "3 times" in warning


def test_loop_detector_standalone_oscillation() -> None:
    """Detects periodic 2-state oscillating sequences (A -> B -> A -> B)."""
    detector = LoopDetector(window_size=10, max_repeats=5, max_oscillations=2)

    # Sequence: A -> B -> A -> B
    assert detector.record("search", {"q": "python"}) is None
    assert detector.record("fetch", {"id": 42}) is None
    assert detector.record("search", {"q": "python"}) is None
    warning = detector.record("fetch", {"id": 42})

    assert warning is not None
    assert "Parameter oscillation detected" in warning
    assert "search -> fetch" in warning


def test_loop_detector_healthy_calls_pass() -> None:
    """Varied, non-repeating calls proceed cleanly without warnings."""
    detector = LoopDetector(window_size=10, max_repeats=3, max_oscillations=2)

    for i in range(10):
        warning = detector.record("query", {"offset": i, "limit": 10})
        assert warning is None


def test_loop_detector_reset() -> None:
    """Resetting the detector clears window history."""
    detector = LoopDetector(window_size=10, max_repeats=3, max_oscillations=2)

    assert detector.record("retry_task", {"attempt": 1}) is None
    assert detector.record("retry_task", {"attempt": 1}) is None
    detector.reset()
    assert detector.record("retry_task", {"attempt": 1}) is None


def test_circuit_breaker_trips_on_repetitive_tool_loop() -> None:
    """CircuitBreaker trips OPEN with REPETITIVE_LOOP reason on repeated tool calls."""
    cb = CircuitBreaker("sess-cb-loop", thresholds=CircuitThresholds(max_tool_repeats=3))
    assert cb.state is CircuitState.CLOSED

    for i in range(2):
        ev = AgentEvent(
            session_id="sess-cb-loop",
            agent_id="agent-loop",
            event_type=EventType.TOOL_CALL,
            status=ExecutionStatus.RUNNING,
            step_number=i + 1,
            tool_call=ToolCallData(
                tool_name="bash_exec",
                arguments={"cmd": "grep -r pattern"},
                raw_command="grep -r pattern",
            ),
        )
        assert cb.observe(ev) is CircuitState.CLOSED

    # 3rd identical call trips the breaker
    trip_ev = AgentEvent(
        session_id="sess-cb-loop",
        agent_id="agent-loop",
        event_type=EventType.TOOL_CALL,
        status=ExecutionStatus.RUNNING,
        step_number=3,
        tool_call=ToolCallData(
            tool_name="bash_exec",
            arguments={"cmd": "grep -r pattern"},
            raw_command="grep -r pattern",
        ),
    )
    assert cb.observe(trip_ev) is CircuitState.OPEN
    assert cb.is_tripped is True
    assert cb.history[-1].reason == TripReason.REPETITIVE_LOOP.value


def test_circuit_breaker_trips_on_parameter_oscillation() -> None:
    """CircuitBreaker trips OPEN with PARAMETER_OSCILLATION on cyclic calls."""
    cb = CircuitBreaker(
        "sess-cb-osc",
        thresholds=CircuitThresholds(max_tool_repeats=10, max_parameter_oscillations=2),
    )

    calls = [
        ("read_db", {"table": "users"}),
        ("write_db", {"table": "logs"}),
        ("read_db", {"table": "users"}),
        ("write_db", {"table": "logs"}),
    ]

    for step, (tool, args) in enumerate(calls[:3], start=1):
        ev = AgentEvent(
            session_id="sess-cb-osc",
            agent_id="agent-osc",
            event_type=EventType.TOOL_CALL,
            status=ExecutionStatus.RUNNING,
            step_number=step,
            tool_call=ToolCallData(tool_name=tool, arguments=args),
        )
        assert cb.observe(ev) is CircuitState.CLOSED

    # 4th call completes the 2nd period of A -> B -> A -> B
    final_ev = AgentEvent(
        session_id="sess-cb-osc",
        agent_id="agent-osc",
        event_type=EventType.TOOL_CALL,
        status=ExecutionStatus.RUNNING,
        step_number=4,
        tool_call=ToolCallData(tool_name=calls[3][0], arguments=calls[3][1]),
    )
    assert cb.observe(final_ev) is CircuitState.OPEN
    assert cb.is_tripped is True
    assert cb.history[-1].reason == TripReason.PARAMETER_OSCILLATION.value
