"""Circuit breaker cooldown and the --cooldown / --max-failures CLI flags (Issue #773)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from panopti.cli import main as panopti_main
from panoptis.circuit_breaker import CircuitBreaker, CircuitState, CircuitThresholds
from panoptis.cli.main import app
from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus

runner = CliRunner()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _event(status: ExecutionStatus, event_type: EventType) -> AgentEvent:
    return AgentEvent(session_id="sess-1", agent_id="agent-1", event_type=event_type, status=status)


def error_event() -> AgentEvent:
    return _event(ExecutionStatus.FAILURE, EventType.TOOL_ERROR)


def success_event() -> AgentEvent:
    return _event(ExecutionStatus.SUCCESS, EventType.TOOL_RESULT)


def tripped_breaker(cooldown: float, clock: FakeClock) -> CircuitBreaker:
    breaker = CircuitBreaker(
        "sess-1",
        CircuitThresholds(max_consecutive_errors=2, cooldown_seconds=cooldown),
        clock=clock,
    )
    breaker.observe(error_event())
    breaker.observe(error_event())
    assert breaker.state is CircuitState.OPEN
    return breaker


# --------------------------------------------------------------- breaker cooldown


def test_open_breaker_stays_open_until_cooldown_elapses() -> None:
    clock = FakeClock()
    breaker = tripped_breaker(30, clock)

    clock.now += 29
    assert breaker.observe(success_event()) is CircuitState.OPEN
    assert breaker.cooldown_remaining() == pytest.approx(1.0)


def test_cooldown_elapsed_moves_to_half_open_and_counts_event_as_probe() -> None:
    clock = FakeClock()
    breaker = tripped_breaker(30, clock)

    clock.now += 30
    assert breaker.observe(success_event()) is CircuitState.HALF_OPEN
    assert breaker.history[-1].reason == "cooldown_elapsed"
    # Default half_open_probe_successes is 2: the triggering event was the first.
    assert breaker.observe(success_event()) is CircuitState.CLOSED


def test_failed_probe_after_cooldown_reopens_and_restarts_cooldown() -> None:
    clock = FakeClock()
    breaker = tripped_breaker(30, clock)

    clock.now += 31
    assert breaker.observe(error_event()) is CircuitState.OPEN
    assert breaker.cooldown_remaining() == pytest.approx(30.0)


def test_zero_cooldown_keeps_manual_resume_only() -> None:
    clock = FakeClock()
    breaker = tripped_breaker(0, clock)

    clock.now += 10_000
    assert breaker.observe(success_event()) is CircuitState.OPEN
    assert breaker.cooldown_remaining() is None


async def test_paused_breaker_is_never_resumed_by_cooldown() -> None:
    clock = FakeClock()
    breaker = tripped_breaker(30, clock)
    await breaker.pause()

    clock.now += 10_000
    assert breaker.observe(success_event()) is CircuitState.PAUSED


def test_status_reports_cooldown() -> None:
    clock = FakeClock()
    breaker = tripped_breaker(30, clock)
    clock.now += 10

    status = breaker.status()
    assert status["cooldown_seconds"] == 30
    assert status["cooldown_remaining"] == pytest.approx(20.0)


# ------------------------------------------------------------- panopti top / live


@pytest.mark.parametrize("command", ["top", "live"])
def test_panopti_live_shows_default_breaker_config(command, capsys) -> None:
    assert panopti_main([command]) == 0
    out = capsys.readouterr().out
    assert "Circuit Breaker:    CLOSED" in out
    assert "Breaker Cooldown:   60s" in out
    assert "Max Failures:       3 consecutive" in out


def test_panopti_live_uses_flag_values(capsys) -> None:
    assert panopti_main(["live", "--cooldown", "30", "--max-failures", "5"]) == 0
    out = capsys.readouterr().out
    assert "Breaker Cooldown:   30s" in out
    assert "Max Failures:       5 consecutive" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["live", "--cooldown", "-1"], "--cooldown must be at least 0"),
        (["live", "--cooldown", "1.5"], "--cooldown must be a whole number"),
        (["live", "--max-failures", "0"], "--max-failures must be at least 1"),
        (["live", "--max-failures", "abc"], "--max-failures must be a whole number"),
        (["live", "--cooldown"], "--cooldown needs a value"),
    ],
)
def test_panopti_live_rejects_invalid_flags(argv, message, capsys) -> None:
    assert panopti_main(argv) == 2
    assert message in capsys.readouterr().err


# ------------------------------------------------------------------ panoptis top


def _mock_top_api() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"top_sessions": []}
    client = AsyncMock()
    client.get.return_value = resp
    return MagicMock(__aenter__=AsyncMock(return_value=client), __aexit__=AsyncMock())


@patch("asyncio.sleep", side_effect=RuntimeError("stop polling"))
def test_panoptis_top_shows_breaker_config(mock_sleep) -> None:
    with patch("httpx.AsyncClient", return_value=_mock_top_api()):
        result = runner.invoke(app, ["server", "top", "--cooldown", "30", "--max-failures", "5"])

    assert "breaker: cooldown 30s, max failures 5" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--cooldown", "-1"],
        ["--cooldown", "abc"],
        ["--max-failures", "0"],
        ["--max-failures", "2.5"],
    ],
)
def test_panoptis_top_rejects_invalid_flags(args) -> None:
    result = runner.invoke(app, ["server", "top", *args])
    assert result.exit_code == 2
