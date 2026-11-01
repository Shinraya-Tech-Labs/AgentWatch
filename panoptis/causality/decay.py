"""Compounding Multi-Step Confidence Decay Tracker.

Tracks cumulative confidence decay across deep multi-step agent executions using
numerically stable log-space arithmetic, applies penalties for low-certainty
tool invocations and ambiguous extractions, and emits CUMULATIVE_CONFIDENCE_COLLAPSE
warnings when cumulative confidence falls below a critical floor.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CUMULATIVE_CONFIDENCE_COLLAPSE: str = "CUMULATIVE_CONFIDENCE_COLLAPSE"
DEFAULT_CRITICAL_FLOOR: float = 0.40
DEFAULT_LOW_CERTAINTY_TOOL_PENALTY: float = 0.85
DEFAULT_AMBIGUOUS_EXTRACTION_PENALTY: float = 0.80


@dataclass(frozen=True, slots=True)
class ConfidenceCollapseWarning:
    """Warning emitted when compounding confidence drops below critical floor."""

    code: str
    step_index: int
    cumulative_confidence: float
    critical_floor: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "step_index": self.step_index,
            "cumulative_confidence": self.cumulative_confidence,
            "critical_floor": self.critical_floor,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class StepRecord:
    """Record of an individual confidence step and the resulting compounding state."""

    step_index: int
    raw_confidence: float
    effective_confidence: float
    cumulative_confidence: float
    log_cumulative_confidence: float
    penalties_applied: tuple[float, ...] = ()
    warning: ConfidenceCollapseWarning | None = None
    step_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "raw_confidence": self.raw_confidence,
            "effective_confidence": self.effective_confidence,
            "cumulative_confidence": self.cumulative_confidence,
            "log_cumulative_confidence": self.log_cumulative_confidence,
            "penalties_applied": list(self.penalties_applied),
            "warning": self.warning.to_dict() if self.warning else None,
            "step_id": self.step_id,
        }


def _validate_score(value: Any, name: str = "confidence") -> float:
    """Validate that value is a finite float in [0.0, 1.0]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a numeric float, got {type(value).__name__}")
    fval = float(value)
    if math.isnan(fval) or math.isinf(fval):
        raise ValueError(f"{name} must be finite, got {fval}")
    if fval < 0.0 or fval > 1.0:
        raise ValueError(f"{name} must be within [0.0, 1.0], got {fval}")
    return fval


def _validate_penalty(penalty: Any, name: str = "penalty") -> float:
    """Validate that penalty multiplier is a finite float in (0.0, 1.0]."""
    if isinstance(penalty, bool) or not isinstance(penalty, (int, float)):
        raise TypeError(f"{name} must be a numeric float, got {type(penalty).__name__}")
    pval = float(penalty)
    if math.isnan(pval) or math.isinf(pval):
        raise ValueError(f"{name} must be finite, got {pval}")
    if pval <= 0.0 or pval > 1.0:
        raise ValueError(f"{name} multiplier must be within (0.0, 1.0], got {pval}")
    return pval


def _extract_confidence_score(item: Any) -> float:
    """Extract a confidence float from a float, int, Confidence, or ComputationalEvent."""
    if hasattr(item, "confidence_observation"):
        obs_conf = getattr(item, "confidence_observation")
        if hasattr(obs_conf, "value"):
            return _validate_score(obs_conf.value, name="event.confidence_observation.value")
    if hasattr(item, "value"):
        return _validate_score(getattr(item, "value"), name="confidence.value")
    return _validate_score(item, name="confidence")


def calculate_cumulative_confidence(
    scores: Iterable[Any],
    *,
    penalties: Iterable[float] | None = None,
) -> float:
    """Compute cumulative confidence across scores in log-space.

    Args:
        scores: Sequence of confidence scores in [0.0, 1.0].
        penalties: Optional sequence of penalty multipliers in (0.0, 1.0].

    Returns:
        Cumulative confidence in [0.0, 1.0].
    """
    score_list = [_extract_confidence_score(s) for s in scores]
    penalty_list = [_validate_penalty(p) for p in (penalties or ())]

    if not score_list and not penalty_list:
        return 1.0

    if any(s == 0.0 for s in score_list):
        return 0.0

    log_sum = sum(math.log(s) for s in score_list) + sum(math.log(p) for p in penalty_list)
    return max(0.0, min(1.0, math.exp(log_sum)))


class ConfidenceDecayTracker:
    """Sequential compounding confidence decay tracker.

    Tracks step confidences in log space to guarantee numerical stability across
    arbitrarily deep agent traces. Emits CUMULATIVE_CONFIDENCE_COLLAPSE when
    cumulative confidence drops below critical_floor.
    """

    def __init__(
        self,
        *,
        critical_floor: float = DEFAULT_CRITICAL_FLOOR,
        low_certainty_tool_penalty: float = DEFAULT_LOW_CERTAINTY_TOOL_PENALTY,
        ambiguous_extraction_penalty: float = DEFAULT_AMBIGUOUS_EXTRACTION_PENALTY,
        on_collapse: Callable[[ConfidenceCollapseWarning], None] | None = None,
    ) -> None:
        self._critical_floor = _validate_score(critical_floor, name="critical_floor")
        self._tool_penalty = _validate_penalty(
            low_certainty_tool_penalty, name="low_certainty_tool_penalty"
        )
        self._extraction_penalty = _validate_penalty(
            ambiguous_extraction_penalty, name="ambiguous_extraction_penalty"
        )
        self._on_collapse = on_collapse

        self._steps: list[StepRecord] = []
        self._warnings: list[ConfidenceCollapseWarning] = []
        self._step_count: int = 0
        self._has_zero: bool = False
        self._log_cumulative: float = 0.0

    @property
    def critical_floor(self) -> float:
        return self._critical_floor

    @property
    def low_certainty_tool_penalty(self) -> float:
        return self._tool_penalty

    @property
    def ambiguous_extraction_penalty(self) -> float:
        return self._extraction_penalty

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def steps(self) -> list[StepRecord]:
        return list(self._steps)

    @property
    def warnings(self) -> list[ConfidenceCollapseWarning]:
        return list(self._warnings)

    @property
    def log_cumulative_confidence(self) -> float:
        """Log-space cumulative confidence score (ln(C))."""
        if self._has_zero:
            return -float("inf")
        return self._log_cumulative

    @property
    def cumulative_confidence(self) -> float:
        """Cumulative confidence score in [0.0, 1.0]."""
        if self._has_zero:
            return 0.0
        if self._step_count == 0:
            return 1.0
        return max(0.0, min(1.0, math.exp(self._log_cumulative)))

    @property
    def is_collapsed(self) -> bool:
        """Check if cumulative confidence is currently below critical floor."""
        if self._step_count == 0:
            return False
        return self.cumulative_confidence < self._critical_floor

    def add_step(
        self,
        confidence: Any,
        *,
        is_low_certainty_tool: bool = False,
        is_ambiguous_extraction: bool = False,
        custom_penalties: Sequence[float] | None = None,
        step_id: str | None = None,
    ) -> StepRecord:
        """Record a single step and update cumulative confidence in log space.

        Args:
            confidence: Base confidence in [0.0, 1.0], Confidence, or ComputationalEvent.
            is_low_certainty_tool: Whether to apply tool penalty multiplier.
            is_ambiguous_extraction: Whether to apply ambiguous extraction penalty multiplier.
            custom_penalties: Additional custom penalty multipliers in (0.0, 1.0].
            step_id: Optional step identifier.

        Returns:
            The recorded StepRecord.
        """
        raw_score = _extract_confidence_score(confidence)

        applied_penalties: list[float] = []
        if is_low_certainty_tool:
            applied_penalties.append(self._tool_penalty)
        if is_ambiguous_extraction:
            applied_penalties.append(self._extraction_penalty)
        if custom_penalties:
            for p in custom_penalties:
                applied_penalties.append(_validate_penalty(p, name="custom_penalty"))

        effective_step = raw_score
        for p in applied_penalties:
            effective_step *= p

        step_idx = self._step_count
        self._step_count += 1

        if effective_step == 0.0:
            self._has_zero = True
            self._log_cumulative = -float("inf")
        elif not self._has_zero:
            step_log = math.log(raw_score) + sum(math.log(p) for p in applied_penalties)
            self._log_cumulative += step_log

        current_cum = self.cumulative_confidence

        warning: ConfidenceCollapseWarning | None = None
        if current_cum < self._critical_floor:
            warning = ConfidenceCollapseWarning(
                code=CUMULATIVE_CONFIDENCE_COLLAPSE,
                step_index=step_idx,
                cumulative_confidence=current_cum,
                critical_floor=self._critical_floor,
                message=(
                    f"Cumulative confidence collapsed to {current_cum:.4f} "
                    f"below critical floor {self._critical_floor:.4f} at step {step_idx}."
                ),
            )
            self._warnings.append(warning)
            logger.warning(warning.message)
            if self._on_collapse:
                self._on_collapse(warning)

        record = StepRecord(
            step_index=step_idx,
            raw_confidence=raw_score,
            effective_confidence=effective_step,
            cumulative_confidence=current_cum,
            log_cumulative_confidence=self.log_cumulative_confidence,
            penalties_applied=tuple(applied_penalties),
            warning=warning,
            step_id=step_id,
        )
        self._steps.append(record)
        return record

    def add_steps(
        self,
        steps: Iterable[Any],
    ) -> list[StepRecord]:
        """Add multiple steps in sequence."""
        return [self.add_step(s) for s in steps]

    def reset(self) -> None:
        """Reset the tracker to initial empty state."""
        self._steps.clear()
        self._warnings.clear()
        self._step_count = 0
        self._has_zero = False
        self._log_cumulative = 0.0
