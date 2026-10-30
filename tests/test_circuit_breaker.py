"""Tests for the active circuit breaker (Issue #483)."""

from __future__ import annotations

import pytest

from panoptis.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitThresholds,
    TripReason,
)
from panoptis.core.schema import (
    AgentEvent,
    ConfidenceData,
    EventType,
    ExecutionStatus,
    TokenUsage,
)

# --------------------------------------------------------------------------- helpers


def make_event(
    *,
    session_id: str = "sess-1",
    event_type: EventType = EventType.TOOL_RESULT,
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    total_tokens: int = 0,
    confidence: float | None = None,
    anomaly_flags: list[str] | None = None,
    step_number: int = 0,
) -> AgentEvent:
    conf = None
    if confidence is not None or anomaly_flags is not None:
        conf = ConfidenceData(
            overall_score=1.0 if confidence is None else confidence,
            anomaly_flags=anomaly_flags or [],
        )
    token_usage = TokenUsage(total_tokens=total_tokens) if total_tokens else None
    return AgentEvent(
        session_id=session_id,
        agent_id="agent-1",
        event_type=event_type,
        status=status,
        step_number=step_number,
        confidence=conf,
        token_usage=token_usage,
    )


def error_event(**kwargs) -> AgentEvent:
    kwargs.setdefault("event_type", EventType.TOOL_ERROR)
    kwargs.setdefault("status", ExecutionStatus.FAILURE)
    return make_event(**kwargs)


def success_event(**kwargs) -> AgentEvent:
    kwargs.setdefault("event_type", EventType.TOOL_RESULT)
    kwargs.setdefault("status", ExecutionStatus.SUCCESS)
    return make_event(**kwargs)


class FakeCheckpoint:
    def __init__(self, checkpoint_id: str) -> None:
        self.checkpoint_id = checkpoint_id


class FakeRollbackEngine:
    """Records calls so tests can assert pause/resume delegate correctly."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.rolled_back: list[str] = []
        self._counter = 0

    async def create_checkpoint(self, **kwargs) -> FakeCheckpoint:
        self._counter += 1
        self.created.append(kwargs)
        return FakeCheckpoint(f"ckpt-{self._counter}")

    async def rollback(self, checkpoint_id: str) -> None:
        self.rolled_back.append(checkpoint_id)


class FakeEUAIAct:
    def __init__(self) -> None:
        self.entries: list = []

    def log_decision(self, entry) -> None:
        self.entries.append(entry)


# --------------------------------------------------------------------------- config


class TestThresholdsValidation:
    def test_defaults_are_valid(self):
        t = CircuitThresholds()
        assert t.max_total_tokens > 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_total_tokens": -1},
            {"max_consecutive_errors": -1},
            {"max_total_errors": -1},
            {"max_hallucinations": -1},
            {"min_confidence": 1.5},
            {"min_confidence": -0.1},
            {"half_open_probe_successes": 0},
        ],
    )
    def test_invalid_config_raises(self, kwargs):
        with pytest.raises(ValueError):
            CircuitThresholds(**kwargs)


# --------------------------------------------------------------------------- tripping


class TestTripping:
    def test_starts_closed(self):
        cb = CircuitBreaker("sess-1")
        assert cb.state is CircuitState.CLOSED
        assert cb.is_tripped is False

    def test_trips_on_total_tokens(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_total_tokens=100))
        cb.observe(make_event(total_tokens=60))
        assert cb.state is CircuitState.CLOSED
        cb.observe(make_event(total_tokens=60))  # cumulative 120 > 100
        assert cb.state is CircuitState.OPEN
        assert cb.history[-1].reason == TripReason.TOKEN_BUDGET.value

    def test_trips_on_consecutive_errors(self):
        cb = CircuitBreaker(
            "sess-1", CircuitThresholds(max_consecutive_errors=3, max_total_errors=0)
        )
        cb.observe(error_event())
        cb.observe(error_event())
        assert cb.state is CircuitState.CLOSED
        cb.observe(error_event())
        assert cb.state is CircuitState.OPEN
        assert cb.history[-1].reason == TripReason.CONSECUTIVE_ERRORS.value

    def test_consecutive_error_run_reset_by_success(self):
        cb = CircuitBreaker(
            "sess-1", CircuitThresholds(max_consecutive_errors=3, max_total_errors=0)
        )
        cb.observe(error_event())
        cb.observe(error_event())
        cb.observe(success_event())  # resets the consecutive run
        cb.observe(error_event())
        cb.observe(error_event())
        assert cb.state is CircuitState.CLOSED  # never hit 3 in a row

    def test_trips_on_total_errors_even_with_successes(self):
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=3, max_consecutive_errors=0),
        )
        cb.observe(error_event())
        cb.observe(success_event())
        cb.observe(error_event())
        cb.observe(success_event())
        assert cb.state is CircuitState.CLOSED
        cb.observe(error_event())  # 3 total
        assert cb.state is CircuitState.OPEN
        assert cb.history[-1].reason == TripReason.TOTAL_ERRORS.value

    def test_trips_on_hallucination_anomaly_flags(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_hallucinations=2))
        cb.observe(make_event(anomaly_flags=["contradiction"]))
        assert cb.state is CircuitState.CLOSED
        cb.observe(make_event(anomaly_flags=["fabricated_citation"]))
        assert cb.state is CircuitState.OPEN
        assert cb.history[-1].reason == TripReason.HALLUCINATIONS.value

    def test_trips_on_low_confidence(self):
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_hallucinations=1, min_confidence=0.5),
        )
        cb.observe(make_event(confidence=0.4))  # below 0.5 → hallucination
        assert cb.state is CircuitState.OPEN

    def test_high_confidence_does_not_trip(self):
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_hallucinations=1, min_confidence=0.5),
        )
        cb.observe(make_event(confidence=0.9))
        assert cb.state is CircuitState.CLOSED

    def test_min_confidence_zero_disables_confidence_check(self):
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_hallucinations=1, min_confidence=0.0),
        )
        cb.observe(make_event(confidence=0.01))  # would trip if enabled
        assert cb.state is CircuitState.CLOSED

    def test_disabled_threshold_never_trips(self):
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(
                max_total_tokens=0,
                max_consecutive_errors=0,
                max_total_errors=0,
                max_hallucinations=0,
                min_confidence=0.0,
            ),
        )
        for _ in range(50):
            cb.observe(error_event(total_tokens=10_000))
        assert cb.state is CircuitState.CLOSED

    def test_events_for_other_sessions_ignored(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_total_errors=1))
        cb.observe(error_event(session_id="other-session"))
        assert cb.state is CircuitState.CLOSED
        assert cb.status()["counters"]["events_observed"] == 0


# --------------------------------------------------------------------------- pause/resume


class TestPauseResume:
    async def test_pause_creates_checkpoint(self):
        engine = FakeRollbackEngine()
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1),
            rollback_engine=engine,
        )
        cb.observe(error_event())
        assert cb.state is CircuitState.OPEN
        ckpt = await cb.pause(step_number=7)
        assert cb.state is CircuitState.PAUSED
        assert ckpt == "ckpt-1"
        assert engine.created[0]["session_id"] == "sess-1"
        assert engine.created[0]["step_number"] == 7

    async def test_pause_without_engine_still_pauses(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_total_errors=1))
        cb.observe(error_event())
        ckpt = await cb.pause()
        assert cb.state is CircuitState.PAUSED
        assert ckpt is None

    async def test_pause_only_valid_from_open(self):
        cb = CircuitBreaker("sess-1")
        with pytest.raises(RuntimeError):
            await cb.pause()

    async def test_resume_from_paused_enters_half_open(self):
        engine = FakeRollbackEngine()
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1),
            rollback_engine=engine,
        )
        cb.observe(error_event())
        await cb.pause()
        state = await cb.resume()
        assert state is CircuitState.HALF_OPEN

    async def test_resume_with_restore_calls_rollback(self):
        engine = FakeRollbackEngine()
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1),
            rollback_engine=engine,
        )
        cb.observe(error_event())
        await cb.pause()
        await cb.resume(restore=True)
        assert engine.rolled_back == ["ckpt-1"]

    async def test_resume_from_open_without_pause(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_total_errors=1))
        cb.observe(error_event())
        assert cb.state is CircuitState.OPEN
        state = await cb.resume()
        assert state is CircuitState.HALF_OPEN

    async def test_resume_only_valid_from_paused_or_open(self):
        cb = CircuitBreaker("sess-1")
        with pytest.raises(RuntimeError):
            await cb.resume()


# --------------------------------------------------------------------------- half-open recovery


class TestHalfOpenRecovery:
    async def _open_and_probe(self, probe_successes: int = 2) -> CircuitBreaker:
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1, half_open_probe_successes=probe_successes),
        )
        cb.observe(error_event())
        await cb.resume()  # OPEN -> HALF_OPEN
        return cb

    async def test_probes_succeed_closes_circuit(self):
        cb = await self._open_and_probe(probe_successes=2)
        assert cb.state is CircuitState.HALF_OPEN
        cb.observe(success_event())
        assert cb.state is CircuitState.HALF_OPEN  # need 2
        cb.observe(success_event())
        assert cb.state is CircuitState.CLOSED
        assert cb.history[-1].reason == "recovery_probes_succeeded"

    async def test_probe_failure_reopens(self):
        cb = await self._open_and_probe(probe_successes=2)
        cb.observe(success_event())
        cb.observe(error_event())  # a failed probe
        assert cb.state is CircuitState.OPEN
        assert cb.history[-1].reason == TripReason.PROBE_FAILED.value

    async def test_probe_success_run_resets_on_failure(self):
        cb = await self._open_and_probe(probe_successes=3)
        cb.observe(success_event())
        cb.observe(success_event())
        cb.observe(error_event())  # reopens, probe count resets
        assert cb.state is CircuitState.OPEN
        await cb.resume()
        cb.observe(success_event())
        cb.observe(success_event())
        assert cb.state is CircuitState.HALF_OPEN  # only 2, need 3 again

    async def test_counters_reset_after_recovery(self):
        cb = await self._open_and_probe(probe_successes=1)
        cb.observe(success_event())  # closes
        assert cb.state is CircuitState.CLOSED
        assert cb.status()["counters"]["total_errors"] == 0
        assert cb.status()["counters"]["total_tokens"] == 0

    async def test_closed_transition_record_preserves_pre_reset_counters(self):
        # The audit record for a CLOSED transition must reflect the counters at
        # the moment of transition, not the post-reset zeros. This guards the
        # EU AI Act Article 12 trail: a recovery record should still show the
        # error count that triggered the original trip.
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=3, half_open_probe_successes=1),
        )
        cb.observe(error_event())
        cb.observe(error_event())
        cb.observe(error_event())  # trips OPEN with 3 total errors
        await cb.resume()
        cb.observe(success_event())  # HALF_OPEN -> CLOSED
        assert cb.state is CircuitState.CLOSED
        closed_record = cb.history[-1]
        assert closed_record.to_state is CircuitState.CLOSED
        assert closed_record.metadata["total_errors"] == 3  # not 0
        # Live counters are still reset for the next window.
        assert cb.status()["counters"]["total_errors"] == 0


# --------------------------------------------------------------------------- manual controls


class TestManualControls:
    def test_manual_trip_from_closed(self):
        cb = CircuitBreaker("sess-1")
        cb.trip()
        assert cb.state is CircuitState.OPEN
        assert cb.history[-1].reason == TripReason.MANUAL.value

    def test_manual_reset_from_open(self):
        cb = CircuitBreaker("sess-1")
        cb.trip()
        cb.reset()
        assert cb.state is CircuitState.CLOSED

    def test_trip_is_noop_when_already_open(self):
        cb = CircuitBreaker("sess-1")
        cb.trip()
        before = len(cb.history)
        cb.trip()  # no double transition
        assert len(cb.history) == before


# --------------------------------------------------------------------------- transitions / audit


class TestTransitionsAndAudit:
    def test_illegal_transition_raises(self):
        cb = CircuitBreaker("sess-1")
        # CLOSED -> PAUSED is not allowed
        with pytest.raises(RuntimeError):
            cb._transition(CircuitState.PAUSED, "illegal")

    def test_history_records_transitions(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_total_errors=1))
        cb.observe(error_event())
        cb.reset()
        assert len(cb.history) == 2
        assert cb.history[0].to_state is CircuitState.OPEN
        assert cb.history[1].to_state is CircuitState.CLOSED

    def test_transition_record_to_dict_roundtrips(self):
        cb = CircuitBreaker("sess-1", CircuitThresholds(max_total_errors=1))
        cb.observe(error_event())
        d = cb.history[-1].to_dict()
        assert d["from_state"] == "closed"
        assert d["to_state"] == "open"
        assert d["session_id"] == "sess-1"

    def test_status_snapshot_shape(self):
        cb = CircuitBreaker("sess-1")
        s = cb.status()
        assert s["state"] == "closed"
        assert "counters" in s
        assert set(s["counters"]) == {
            "total_tokens",
            "consecutive_errors",
            "total_errors",
            "hallucinations",
            "events_observed",
        }

    def test_eu_ai_act_logging_on_transition(self):
        eu = FakeEUAIAct()
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1),
            eu_ai_act_package=eu,
        )
        cb.observe(error_event())
        assert len(eu.entries) == 1
        entry = eu.entries[0]
        assert entry.human_oversight_required is True  # OPEN requires oversight
        assert "closed -> open" in entry.explanation

    def test_eu_ai_act_logging_failure_does_not_break(self):
        class BrokenEU:
            def log_decision(self, entry):
                raise RuntimeError("boom")

        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1),
            eu_ai_act_package=BrokenEU(),
        )
        # Should trip fine despite the logging sink raising.
        cb.observe(error_event())
        assert cb.state is CircuitState.OPEN


# --------------------------------------------------------------------------- full lifecycle


class TestFullLifecycle:
    async def test_closed_open_paused_halfopen_closed(self):
        engine = FakeRollbackEngine()
        eu = FakeEUAIAct()
        cb = CircuitBreaker(
            "sess-1",
            CircuitThresholds(max_total_errors=1, half_open_probe_successes=1),
            rollback_engine=engine,
            eu_ai_act_package=eu,
        )
        # CLOSED -> OPEN
        cb.observe(error_event())
        assert cb.state is CircuitState.OPEN
        # OPEN -> PAUSED
        await cb.pause(step_number=3)
        assert cb.state is CircuitState.PAUSED
        # PAUSED -> HALF_OPEN (with restore)
        await cb.resume(restore=True)
        assert cb.state is CircuitState.HALF_OPEN
        assert engine.rolled_back == ["ckpt-1"]
        # HALF_OPEN -> CLOSED
        cb.observe(success_event())
        assert cb.state is CircuitState.CLOSED
        # Full transition trail recorded for audit
        trail = [(r.from_state.value, r.to_state.value) for r in cb.history]
        assert trail == [
            ("closed", "open"),
            ("open", "paused"),
            ("paused", "half_open"),
            ("half_open", "closed"),
        ]
        # Every transition logged for EU AI Act Article 12
        assert len(eu.entries) == 4
