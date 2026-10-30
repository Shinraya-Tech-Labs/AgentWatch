"""Unit tests for the Panopti Guard decorator and pre-execution interceptor."""

from __future__ import annotations

import pytest

from panoptis.circuit_breaker.loop_detector import AgentLoopError
from panoptis.core.watcher import AgentWatchBlockedError
from panopti import guard
from panopti.guard import PanoptiGuard


def test_guard_decision_allowed() -> None:
    """Safe tool call returns an allowed GuardDecision."""
    g = PanoptiGuard()
    decision = g.check("file_read", {"path": "/app/data/sample.txt"})
    assert decision.allowed is True
    assert decision.blocked is False
    assert decision.reason is None


def test_guard_decision_blocked_destructive_command() -> None:
    """Destructive shell command triggers a blocked decision."""
    g = PanoptiGuard()
    decision = g.check("bash", {"cmd": "rm -rf / --no-preserve-root"})
    assert decision.blocked is True
    assert decision.allowed is False
    assert decision.risk_level.upper() in ("HIGH", "CRITICAL")
    assert len(decision.reasons) > 0


def test_guard_tool_decorator_normal_execution() -> None:
    """Decorated tool executes cleanly on valid arguments."""
    g = PanoptiGuard()

    @g.tool()
    def calculate_tax(amount: float, rate: float = 0.1) -> float:
        return amount * rate

    result = calculate_tax(100.0, rate=0.15)
    assert result == pytest.approx(15.0)


def test_guard_tool_decorator_catches_repetitive_loop() -> None:
    """Repeated tool execution with identical arguments raises AgentLoopError."""
    g = PanoptiGuard()

    @g.tool(max_repeats=3)
    def fetch_user_data(user_id: int) -> str:
        return f"user_{user_id}"

    assert fetch_user_data(101) == "user_101"
    assert fetch_user_data(101) == "user_101"

    with pytest.raises(AgentLoopError) as exc_info:
        fetch_user_data(101)

    assert "Repetitive loop detected" in str(exc_info.value)


def test_guard_tool_decorator_no_raise_mode() -> None:
    """Decorator in raise_on_block=False returns an error dict instead of raising."""
    g = PanoptiGuard()

    @g.tool(raise_on_block=False)
    def run_command(cmd: str) -> str:
        return "executed"

    res = run_command(cmd="rm -rf /")
    assert isinstance(res, dict)
    assert res.get("blocked") is True


def test_guard_confidence_decay_collapse() -> None:
    """PanoptiGuard blocks tool calls when cumulative confidence collapses below critical floor."""
    g = PanoptiGuard(critical_confidence_floor=0.50)

    # Step 1: 0.80 -> 0.80 >= 0.50 -> allowed
    d1 = g.check("search", {"q": "query1"}, confidence=0.80)
    assert d1.allowed is True

    # Step 2: 0.80 * 0.50 = 0.40 < 0.50 -> blocked by collapse warning
    d2 = g.check("search", {"q": "query2"}, confidence=0.50)
    assert d2.blocked is True
    assert "CUMULATIVE_CONFIDENCE_COLLAPSE" in (d2.reason or "")


def test_guard_watch_passthrough() -> None:
    """guard.watch attaches universal instrumentation to arbitrary targets."""
    dummy_agent = object()
    wrapped = guard.watch(dummy_agent)
    assert wrapped is not None
