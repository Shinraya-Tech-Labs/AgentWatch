"""Active circuit breaker state machine.

State model
----------
    CLOSED     normal operation; events flow through and thresholds are watched
    OPEN       a threshold tripped; the agent should stop issuing new actions
    PAUSED     state has been checkpointed and the agent is safely suspended
    HALF_OPEN  probing recovery; a limited number of events are allowed through
    CLOSED     probes succeeded; counters reset and normal operation resumes

Allowed transitions::

    CLOSED    -> OPEN        (threshold breach)
    OPEN      -> PAUSED      (pause(): checkpoint saved)
    PAUSED    -> HALF_OPEN   (resume(): begin probing)
    OPEN      -> HALF_OPEN   (resume() without an intervening pause)
    OPEN      -> HALF_OPEN   (cooldown_seconds elapsed, when cooldown is enabled)
    HALF_OPEN -> CLOSED      (enough consecutive successful probes)
    HALF_OPEN -> OPEN        (a probe failed)
    any       -> CLOSED      (manual reset)

The breaker is deliberately framework-agnostic: it consumes ``AgentEvent``
objects and never calls into a specific agent runtime. Pausing delegates to the
existing :class:`~panoptis.rollback.engine.RollbackEngine` for checkpointing,
and every transition is recorded for EU AI Act Article 12 record-keeping via the
existing :class:`~panoptis.governance.eu_ai_act.EUAIActPackage`.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from panoptis.circuit_breaker.loop_detector import LoopDetector
from panoptis.circuit_breaker.thresholds import CircuitThresholds
from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    PAUSED = "paused"
    HALF_OPEN = "half_open"


class TripReason(str, Enum):
    TOKEN_BUDGET = "token_budget_exceeded"  # noqa: S105  # nosec B105 — enum label, not a secret
    CONSECUTIVE_ERRORS = "consecutive_errors_exceeded"
    TOTAL_ERRORS = "total_errors_exceeded"
    HALLUCINATIONS = "hallucination_threshold_exceeded"
    PROBE_FAILED = "half_open_probe_failed"
    MANUAL = "manual_trip"
    REPETITIVE_LOOP = "repetitive_tool_loop_detected"
    PARAMETER_OSCILLATION = "parameter_oscillation_detected"


# Which allowed target states each state may move to. Used to reject invalid
# transitions loudly instead of silently corrupting the breaker's state.
_ALLOWED: dict[CircuitState, set[CircuitState]] = {
    CircuitState.CLOSED: {CircuitState.OPEN, CircuitState.CLOSED},
    CircuitState.OPEN: {CircuitState.PAUSED, CircuitState.HALF_OPEN, CircuitState.CLOSED},
    CircuitState.PAUSED: {CircuitState.HALF_OPEN, CircuitState.CLOSED},
    CircuitState.HALF_OPEN: {CircuitState.CLOSED, CircuitState.OPEN},
}


@dataclass
class TransitionRecord:
    """A single audited state transition."""

    when: datetime
    from_state: CircuitState
    to_state: CircuitState
    reason: str
    session_id: str
    checkpoint_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "when": self.when.isoformat(),
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "session_id": self.session_id,
            "checkpoint_id": self.checkpoint_id,
            "metadata": dict(self.metadata),
        }


class CircuitBreaker:
    """Active circuit breaker for a single agent session."""

    def __init__(
        self,
        session_id: str,
        thresholds: CircuitThresholds | None = None,
        *,
        rollback_engine: Any | None = None,
        eu_ai_act_package: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_id = session_id
        self.thresholds = thresholds or CircuitThresholds()
        self._rollback_engine = rollback_engine
        self._eu_ai_act = eu_ai_act_package
        # Injectable so tests can move time forward without sleeping.
        self._clock = clock

        self._state = CircuitState.CLOSED
        self._history: list[TransitionRecord] = []
        # Monotonic time of the last move into OPEN, used for the cooldown.
        self._opened_at: float | None = None

        # Window counters, reset whenever the breaker (re)enters CLOSED.
        self._total_tokens = 0
        self._consecutive_errors = 0
        self._total_errors = 0
        self._hallucinations = 0
        self._events_observed = 0

        # HALF_OPEN probe tracking.
        self._probe_successes = 0

        # Loop and oscillation detector
        self._loop_detector = LoopDetector(
            window_size=self.thresholds.loop_window_size,
            max_repeats=self.thresholds.max_tool_repeats or 999999,
            max_oscillations=self.thresholds.max_parameter_oscillations or 999999,
        )
        self._last_loop_warning: str | None = None

        # The most recent checkpoint created by pause(), used by resume().
        self._last_checkpoint_id: str | None = None
        self._last_step_number = 0

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_tripped(self) -> bool:
        """True when the agent should not be issuing new actions."""
        return self._state in (CircuitState.OPEN, CircuitState.PAUSED)

    @property
    def history(self) -> list[TransitionRecord]:
        return list(self._history)

    def status(self) -> dict[str, Any]:
        """A snapshot of the breaker suitable for a dashboard payload."""
        return {
            "session_id": self.session_id,
            "state": self._state.value,
            "is_tripped": self.is_tripped,
            "counters": {
                "total_tokens": self._total_tokens,
                "consecutive_errors": self._consecutive_errors,
                "total_errors": self._total_errors,
                "hallucinations": self._hallucinations,
                "events_observed": self._events_observed,
            },
            "probe_successes": self._probe_successes,
            "last_checkpoint_id": self._last_checkpoint_id,
            "transitions": len(self._history),
            "cooldown_seconds": self.thresholds.cooldown_seconds,
            "cooldown_remaining": self.cooldown_remaining(),
        }

    def cooldown_remaining(self) -> float | None:
        """Seconds left before an OPEN breaker starts probing on its own.

        ``None`` when the breaker is not OPEN or the cooldown is disabled.
        """
        cooldown = self.thresholds.cooldown_seconds
        if self._state is not CircuitState.OPEN or not cooldown or self._opened_at is None:
            return None
        return max(0.0, cooldown - (self._clock() - self._opened_at))

    # ------------------------------------------------------------- observe

    def observe(self, event: AgentEvent) -> CircuitState:
        """Feed a single event to the breaker and return the resulting state.

        In CLOSED state, thresholds are evaluated and the breaker may trip to
        OPEN. In HALF_OPEN state, the event is treated as a recovery probe. In
        OPEN/PAUSED state, events are recorded in the counters but do not change
        state (the agent is expected to have stopped; callers drive recovery via
        :meth:`pause` / :meth:`resume`). The one exception is an OPEN breaker
        whose ``cooldown_seconds`` has elapsed: it moves to HALF_OPEN first and
        the event is evaluated as the first recovery probe. PAUSED is never left
        automatically, since a pause is an explicit operator decision.
        """
        if event.session_id != self.session_id:
            # Ignore events for other sessions rather than corrupting counters.
            return self._state

        self._events_observed += 1
        self._last_step_number = event.step_number
        self._accumulate(event)

        if self.cooldown_remaining() == 0.0:
            self._probe_successes = 0
            self._transition(CircuitState.HALF_OPEN, "cooldown_elapsed")

        if self._state is CircuitState.CLOSED:
            reason = self._evaluate_thresholds()
            if reason is not None:
                self._transition(CircuitState.OPEN, reason.value)
        elif self._state is CircuitState.HALF_OPEN:
            self._evaluate_probe(event)

        return self._state

    def _accumulate(self, event: AgentEvent) -> None:
        if event.token_usage is not None:
            self._total_tokens += int(getattr(event.token_usage, "total_tokens", 0) or 0)

        if self._is_error(event):
            self._total_errors += 1
            self._consecutive_errors += 1
        elif self._is_success(event):
            self._consecutive_errors = 0

        if self._is_hallucination(event):
            self._hallucinations += 1

        if event.tool_call is not None:
            self._last_loop_warning = self._loop_detector.record(
                event.tool_call.tool_name,
                event.tool_call.arguments,
            )

    @staticmethod
    def _is_error(event: AgentEvent) -> bool:
        return event.status in (
            ExecutionStatus.FAILURE,
            ExecutionStatus.TIMEOUT,
        ) or event.event_type in (
            EventType.AGENT_ERROR,
            EventType.TOOL_ERROR,
            EventType.TASK_FAIL,
        )

    @staticmethod
    def _is_success(event: AgentEvent) -> bool:
        return event.status is ExecutionStatus.SUCCESS or event.event_type in (
            EventType.AGENT_END,
            EventType.TOOL_RESULT,
            EventType.TASK_COMPLETE,
        )

    def _is_hallucination(self, event: AgentEvent) -> bool:
        if event.confidence is None:
            return False
        if event.confidence.anomaly_flags:
            return True
        if self.thresholds.min_confidence <= 0.0:
            return False
        return event.confidence.overall_score < self.thresholds.min_confidence

    def _evaluate_thresholds(self) -> TripReason | None:
        t = self.thresholds
        if t.max_total_tokens and self._total_tokens > t.max_total_tokens:
            return TripReason.TOKEN_BUDGET
        if t.max_consecutive_errors and self._consecutive_errors >= t.max_consecutive_errors:
            return TripReason.CONSECUTIVE_ERRORS
        if t.max_total_errors and self._total_errors >= t.max_total_errors:
            return TripReason.TOTAL_ERRORS
        if t.max_hallucinations and self._hallucinations >= t.max_hallucinations:
            return TripReason.HALLUCINATIONS
        if self._last_loop_warning is not None:
            if "oscillation" in self._last_loop_warning.lower():
                return TripReason.PARAMETER_OSCILLATION
            return TripReason.REPETITIVE_LOOP
        return None

    def _evaluate_probe(self, event: AgentEvent) -> None:
        """In HALF_OPEN, count successful probes; a failure re-opens."""
        if self._is_error(event):
            self._probe_successes = 0
            self._transition(CircuitState.OPEN, TripReason.PROBE_FAILED.value)
            return
        if self._is_success(event):
            self._probe_successes += 1
            if self._probe_successes >= self.thresholds.half_open_probe_successes:
                self._transition(CircuitState.CLOSED, "recovery_probes_succeeded")

    # --------------------------------------------------------- pause/resume

    async def pause(
        self,
        *,
        step_number: int | None = None,
        working_dir: str | None = None,
        memory_snapshot: dict[str, Any] | None = None,
    ) -> str | None:
        """Checkpoint current state and move to PAUSED.

        Only valid from OPEN. Delegates checkpoint creation to the rollback
        engine if one was supplied; otherwise the breaker still transitions to
        PAUSED but without a durable snapshot (and records that fact).

        Returns the checkpoint id, or ``None`` if no rollback engine is wired.
        """
        if self._state is not CircuitState.OPEN:
            raise RuntimeError(f"pause() is only valid from OPEN, not {self._state.value}")

        step = step_number if step_number is not None else self._last_step_number
        checkpoint_id: str | None = None

        if self._rollback_engine is not None:
            cp = await self._rollback_engine.create_checkpoint(
                session_id=self.session_id,
                step_number=step,
                working_dir=working_dir,
                memory_snapshot=memory_snapshot,
                label=f"circuit-breaker-pause-{self.session_id}",
            )
            checkpoint_id = cp.checkpoint_id
            self._last_checkpoint_id = checkpoint_id
        else:
            logger.warning(
                "CircuitBreaker.pause() with no rollback engine: session %s paused "
                "without a durable checkpoint",
                self.session_id,
            )

        self._transition(
            CircuitState.PAUSED,
            "paused_with_checkpoint" if checkpoint_id else "paused_without_checkpoint",
            checkpoint_id=checkpoint_id,
        )
        return checkpoint_id

    async def resume(self, *, restore: bool = False) -> CircuitState:
        """Begin probing recovery by moving to HALF_OPEN.

        Valid from PAUSED or OPEN. When ``restore`` is True and a checkpoint was
        saved by :meth:`pause`, the rollback engine restores that checkpoint
        before probing begins.
        """
        if self._state not in (CircuitState.PAUSED, CircuitState.OPEN):
            raise RuntimeError(f"resume() is only valid from PAUSED/OPEN, not {self._state.value}")

        if restore and self._last_checkpoint_id and self._rollback_engine is not None:
            await self._rollback_engine.rollback(self._last_checkpoint_id)

        self._probe_successes = 0
        self._transition(
            CircuitState.HALF_OPEN,
            "resume_probing",
            checkpoint_id=self._last_checkpoint_id if restore else None,
        )
        return self._state

    def trip(self, reason: str = TripReason.MANUAL.value) -> CircuitState:
        """Manually force the breaker OPEN (e.g. operator kill-switch)."""
        if self._state is CircuitState.CLOSED or self._state is CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN, reason)
        return self._state

    def reset(self) -> CircuitState:
        """Manually force the breaker back to CLOSED and clear the window."""
        self._transition(CircuitState.CLOSED, "manual_reset")
        return self._state

    # --------------------------------------------------------- transitions

    def _transition(
        self,
        to_state: CircuitState,
        reason: str,
        *,
        checkpoint_id: str | None = None,
    ) -> None:
        from_state = self._state
        if to_state not in _ALLOWED[from_state]:
            raise RuntimeError(f"illegal circuit transition {from_state.value} -> {to_state.value}")

        # Snapshot the counters *before* a CLOSED transition resets the window, so
        # the audit record reflects the state at the moment of transition (e.g. a
        # recovery record shows the error count that triggered the trip, not 0).
        transition_metadata = {
            "total_tokens": self._total_tokens,
            "total_errors": self._total_errors,
            "hallucinations": self._hallucinations,
        }

        self._state = to_state
        self._opened_at = self._clock() if to_state is CircuitState.OPEN else None
        if to_state is CircuitState.CLOSED:
            self._reset_window()

        record = TransitionRecord(
            when=datetime.now(UTC),
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            session_id=self.session_id,
            checkpoint_id=checkpoint_id,
            metadata=transition_metadata,
        )
        self._history.append(record)
        self._record_for_eu_ai_act(record)

        logger.info(
            "Circuit %s: %s -> %s (%s)",
            self.session_id,
            from_state.value,
            to_state.value,
            reason,
        )

    def _reset_window(self) -> None:
        self._total_tokens = 0
        self._consecutive_errors = 0
        self._total_errors = 0
        self._hallucinations = 0
        self._events_observed = 0
        self._probe_successes = 0
        self._loop_detector.reset()
        self._last_loop_warning = None

    def _record_for_eu_ai_act(self, record: TransitionRecord) -> None:
        """Log the transition for EU AI Act Article 12 record-keeping.

        Best-effort: a governance package is optional, and a logging failure
        must never break the breaker's control flow.
        """
        if self._eu_ai_act is None:
            return
        try:
            from panoptis.governance.eu_ai_act import DecisionLogEntry

            payload = record.to_dict()
            entry = DecisionLogEntry(
                when=record.when,
                decision_id=f"cb-{uuid.uuid4().hex[:12]}",
                inputs_hash=self._hash(record.from_state.value + record.reason),
                outputs_hash=self._hash(record.to_state.value),
                confidence=1.0,
                safety_checks_passed=record.to_state is not CircuitState.OPEN,
                human_oversight_required=record.to_state
                in (CircuitState.OPEN, CircuitState.PAUSED),
                explanation=(
                    f"Circuit breaker transitioned {record.from_state.value} -> "
                    f"{record.to_state.value} for session {record.session_id} "
                    f"(reason: {record.reason})"
                ),
            )
            self._eu_ai_act.log_decision(entry)
            logger.debug("Recorded circuit transition to EU AI Act log: %s", payload["reason"])
        except Exception as exc:  # noqa: BLE001 - logging must not break control flow
            logger.warning("Failed to record circuit transition for EU AI Act: %s", exc)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
