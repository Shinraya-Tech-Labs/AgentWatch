"""Configurable thresholds that govern when the circuit breaker trips."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CircuitThresholds:
    """Thresholds that decide when the breaker trips from CLOSED to OPEN.

    All thresholds are evaluated over the window of events observed since the
    breaker last entered the CLOSED state (a successful recovery resets the
    counters). A threshold of ``0`` or ``None`` disables that particular check.

    Attributes:
        max_total_tokens: Trip once cumulative tokens across observed events
            exceed this value. Guards runaway generation cost.
        max_consecutive_errors: Trip once this many error/failure events occur
            in a row (a success resets the run).
        max_total_errors: Trip once this many error/failure events occur in
            total within the window, even if interspersed with successes.
        max_hallucinations: Trip once this many low-confidence / anomaly-flagged
            events are observed within the window.
        min_confidence: Any single event whose confidence ``overall_score`` is
            strictly below this value counts as a hallucination signal. Set to
            ``0.0`` to disable per-event confidence checking.
        half_open_probe_successes: Number of consecutive successful probe events
            required in HALF_OPEN before the breaker closes again.
        cooldown_seconds: How long the breaker stays OPEN before it moves to
            HALF_OPEN on its own and starts probing recovery. ``0`` disables the
            automatic move, so recovery only starts through ``resume()``.
    """

    max_total_tokens: int = 100_000
    max_consecutive_errors: int = 3
    max_total_errors: int = 5
    max_hallucinations: int = 3
    min_confidence: float = 0.35
    half_open_probe_successes: int = 2
    max_tool_repeats: int = 3
    max_parameter_oscillations: int = 2
    loop_window_size: int = 10
    cooldown_seconds: float = 0.0

    def __post_init__(self) -> None:
        # Fail loudly on nonsensical configuration rather than silently
        # producing a breaker that can never trip or never recover.
        if self.max_total_tokens < 0:
            raise ValueError("max_total_tokens must be >= 0")
        if self.max_consecutive_errors < 0:
            raise ValueError("max_consecutive_errors must be >= 0")
        if self.max_total_errors < 0:
            raise ValueError("max_total_errors must be >= 0")
        if self.max_hallucinations < 0:
            raise ValueError("max_hallucinations must be >= 0")
        if not (0.0 <= self.min_confidence <= 1.0):
            raise ValueError("min_confidence must be within [0.0, 1.0]")
        if self.half_open_probe_successes < 1:
            raise ValueError("half_open_probe_successes must be >= 1")
        if self.max_tool_repeats < 0:
            raise ValueError("max_tool_repeats must be >= 0")
        if self.max_parameter_oscillations < 0:
            raise ValueError("max_parameter_oscillations must be >= 0")
        if self.loop_window_size < 2:
            raise ValueError("loop_window_size must be >= 2")
