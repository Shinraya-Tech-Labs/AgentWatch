"""Bounded feedback learning for HITL thresholds.

This is deliberately a small, explainable heuristic rather than an opaque model:
it tracks, per risk level, how often humans approve vs. reject REVIEW requests.
When a level's approval rate is consistently very high over a meaningful sample,
that level is a candidate for *relaxation* (humans keep approving it, so blocking
review may be unnecessary overhead). When rejections are common, the level is a
candidate for *tightening*. The learner only ever *suggests* changes and can
optionally apply them within fixed bounds; it never silently escalates a level
past REVIEW or relaxes CRITICAL below REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from panoptis.core.schema import RiskLevel
from panoptis.hitl.decisions import DecisionOutcome, InteractionPattern
from panoptis.hitl.thresholds import HITLThresholds

# Escalation intensity, least to most intrusive. Relaxing moves left, tightening
# moves right. Exhaustive over all values of ``InteractionPattern | None``.
_PATTERN_ORDER: list[InteractionPattern | None] = [
    None,
    InteractionPattern.NOTIFY,
    InteractionPattern.QUESTION,
    InteractionPattern.REVIEW,
]


@dataclass
class _Tally:
    approvals: int = 0
    rejections: int = 0

    @property
    def total(self) -> int:
        return self.approvals + self.rejections

    @property
    def approval_rate(self) -> float:
        return self.approvals / self.total if self.total else 0.0


@dataclass
class ThresholdSuggestion:
    risk_level: RiskLevel
    current: InteractionPattern | None
    suggested: InteractionPattern | None
    reason: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "risk_level": self.risk_level.value,
            "current": self.current.value if self.current else None,
            "suggested": self.suggested.value if self.suggested else None,
            "reason": self.reason,
        }


@dataclass
class FeedbackLearner:
    """Learns per-risk escalation adjustments from human decisions.

    Attributes:
        min_samples: Minimum decisions at a level before a suggestion is made.
        relax_above: Approval rate at/above which a level is relaxed.
        tighten_below: Approval rate at/below which a level is tightened.
    """

    min_samples: int = 10
    relax_above: float = 0.95
    tighten_below: float = 0.5
    _tallies: dict[RiskLevel, _Tally] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= self.tighten_below < self.relax_above <= 1.0):
            raise ValueError("require 0.0 <= tighten_below < relax_above <= 1.0")
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")

    def record(self, risk_level: RiskLevel, outcome: DecisionOutcome) -> None:
        """Record a human decision. Only APPROVE/EDIT/REJECT affect learning."""
        tally = self._tallies.setdefault(risk_level, _Tally())
        if outcome in (DecisionOutcome.APPROVE, DecisionOutcome.EDIT):
            tally.approvals += 1
        elif outcome is DecisionOutcome.REJECT:
            tally.rejections += 1

    def approval_rate(self, risk_level: RiskLevel) -> float:
        return self._tallies.get(risk_level, _Tally()).approval_rate

    def suggest(self, thresholds: HITLThresholds) -> list[ThresholdSuggestion]:
        """Return relaxation/tightening suggestions for levels with enough data."""
        suggestions: list[ThresholdSuggestion] = []
        for level, tally in self._tallies.items():
            if tally.total < self.min_samples:
                continue
            current = thresholds.pattern_for(level)
            rate = tally.approval_rate

            if rate >= self.relax_above:
                relaxed = self._relaxed(current, level)
                if relaxed != current:
                    suggestions.append(
                        ThresholdSuggestion(
                            risk_level=level,
                            current=current,
                            suggested=relaxed,
                            reason=(
                                f"approval rate {rate:.0%} over {tally.total} decisions; "
                                "review may be unnecessary overhead"
                            ),
                        )
                    )
            elif rate <= self.tighten_below:
                tightened = self._tightened(current)
                if tightened != current:
                    suggestions.append(
                        ThresholdSuggestion(
                            risk_level=level,
                            current=current,
                            suggested=tightened,
                            reason=(
                                f"approval rate {rate:.0%} over {tally.total} decisions; "
                                "frequent rejections warrant stricter review"
                            ),
                        )
                    )
        return suggestions

    def apply(self, thresholds: HITLThresholds) -> list[ThresholdSuggestion]:
        """Apply all current suggestions to ``thresholds`` in place.

        Returns the list of suggestions that were applied.
        """
        applied = self.suggest(thresholds)
        for s in applied:
            thresholds.set_pattern(s.risk_level, s.suggested)
        return applied

    @staticmethod
    def _relaxed(current: InteractionPattern | None, level: RiskLevel) -> InteractionPattern | None:
        # CRITICAL is never relaxed below REVIEW regardless of approval rate.
        if level is RiskLevel.CRITICAL:
            return current
        # `order` is exhaustive over InteractionPattern | None, so index() is safe.
        idx = _PATTERN_ORDER.index(current)
        return _PATTERN_ORDER[idx - 1] if idx > 0 else current

    @staticmethod
    def _tightened(current: InteractionPattern | None) -> InteractionPattern | None:
        idx = _PATTERN_ORDER.index(current)
        return _PATTERN_ORDER[idx + 1] if idx < len(_PATTERN_ORDER) - 1 else current
