"""Per-risk-level thresholds that decide how an action is escalated."""

from __future__ import annotations

from dataclasses import dataclass, field

from panoptis.core.schema import RiskLevel
from panoptis.hitl.decisions import InteractionPattern

# The default policy: low-risk actions are auto-approved (or merely notified),
# and escalation intensifies with risk. REVIEW (blocking approve/reject/edit) is
# reserved for the two highest tiers by default.
_DEFAULT_POLICY: dict[RiskLevel, InteractionPattern | None] = {
    RiskLevel.SAFE: None,  # None => auto-approve, never surfaced
    RiskLevel.LOW: InteractionPattern.NOTIFY,
    RiskLevel.MEDIUM: InteractionPattern.NOTIFY,
    RiskLevel.HIGH: InteractionPattern.REVIEW,
    RiskLevel.CRITICAL: InteractionPattern.REVIEW,
}


@dataclass
class HITLThresholds:
    """Maps each risk level to the interaction pattern used to surface it.

    A value of ``None`` for a level means actions at that level are auto-approved
    and never surfaced to a human. Use :meth:`pattern_for` to resolve a level.
    """

    policy: dict[RiskLevel, InteractionPattern | None] = field(
        default_factory=lambda: dict(_DEFAULT_POLICY)
    )
    approval_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if self.approval_timeout_seconds <= 0:
            raise ValueError("approval_timeout_seconds must be > 0")
        # Ensure every risk level has an explicit entry so lookups never KeyError.
        for level in RiskLevel:
            self.policy.setdefault(level, _DEFAULT_POLICY[level])

    def pattern_for(self, risk_level: RiskLevel) -> InteractionPattern | None:
        return self.policy.get(risk_level)

    def requires_human(self, risk_level: RiskLevel) -> bool:
        """True when the level is surfaced for a blocking human decision."""
        return self.pattern_for(risk_level) in (
            InteractionPattern.QUESTION,
            InteractionPattern.REVIEW,
        )

    def set_pattern(self, risk_level: RiskLevel, pattern: InteractionPattern | None) -> None:
        self.policy[risk_level] = pattern
