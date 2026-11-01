"""
CST-009 — Cost-Aware Model Router (issue #375, CST-004 cost-intelligence scope).

Route a task to the cheapest model capable of handling its complexity: simple
subtasks go to cheap models (e.g. Gemini Flash), and expensive, advanced models
are reserved for genuinely complex work — optimizing the project budget.

Builds on the :data:`~panoptis.cost.comparator.DEFAULT_PRICING` table (CST-002)
as the single source of truth for rates; this module adds the complexity scoring
and capability-aware selection on top.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from panoptis.cost.comparator import DEFAULT_PRICING, estimate


class TaskComplexity(IntEnum):
    """How demanding a task is. Higher needs a more capable (costlier) model."""

    SIMPLE = 1
    STANDARD = 2
    COMPLEX = 3


# Capability rank per model: the highest TaskComplexity it can serve. A task of
# complexity C may be routed to any model whose rank is >= C.
DEFAULT_CAPABILITY: dict[str, TaskComplexity] = {
    "gemini-1.5-flash": TaskComplexity.SIMPLE,
    "gpt-4o-mini": TaskComplexity.SIMPLE,
    "claude-haiku-4-5": TaskComplexity.SIMPLE,
    "gemini-1.5-pro": TaskComplexity.STANDARD,
    "gpt-4o": TaskComplexity.STANDARD,
    "claude-sonnet-4-5": TaskComplexity.STANDARD,
    "claude-opus-4-5": TaskComplexity.COMPLEX,
}

# Rough heuristic: ~4 characters per token.
_CHARS_PER_TOKEN = 4


@dataclass
class TaskSignals:
    """Inputs the heuristic scorer uses to judge a task's complexity."""

    prompt: str = ""
    requires_tools: bool = False
    requires_reasoning: bool = False

    @property
    def input_tokens(self) -> int:
        return len(self.prompt) // _CHARS_PER_TOKEN


def score_complexity(signals: TaskSignals) -> TaskComplexity:
    """Heuristically classify a task into a :class:`TaskComplexity` tier.

    Multi-step reasoning or very large inputs → COMPLEX; short, tool-free,
    reasoning-free prompts → SIMPLE; everything else → STANDARD. Callers that
    have a better signal can pass an explicit complexity to :meth:`CostAwareRouter.route`.
    """
    tokens = signals.input_tokens
    if signals.requires_reasoning or tokens > 8000:
        return TaskComplexity.COMPLEX
    if tokens < 500 and not signals.requires_tools:
        return TaskComplexity.SIMPLE
    return TaskComplexity.STANDARD


@dataclass
class RoutingDecision:
    """The chosen model plus the reasoning and cheaper alternatives considered."""

    model: str
    complexity: TaskComplexity
    estimated_cost: float
    reason: str
    considered: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialize the decision to a JSON-friendly dict."""
        return {
            "model": self.model,
            "complexity": self.complexity.name.lower(),
            "estimated_cost": round(self.estimated_cost, 6),
            "reason": self.reason,
            "considered": [{"model": m, "estimated_cost": round(c, 6)} for m, c in self.considered],
        }


class CostAwareRouter:
    """Pick the cheapest model that can handle a task's complexity.

    Parameters
    ----------
    pricing:
        ``{model: (input_per_million, output_per_million)}`` rates. Defaults to
        the shared ``DEFAULT_PRICING`` table.
    capability:
        ``{model: max_complexity_rank}``. Models absent here are treated as
        SIMPLE-only so an unknown/cheap model never gets a complex task.
    budget_ceiling:
        Optional per-call USD cap. If the cheapest *capable* model exceeds it,
        the router downgrades to the cheapest model overall and flags it, rather
        than silently blowing the budget.
    """

    def __init__(
        self,
        *,
        pricing: dict[str, tuple[float, float]] | None = None,
        capability: dict[str, TaskComplexity] | None = None,
        budget_ceiling: float | None = None,
    ) -> None:
        # Explicit None checks so a caller can intentionally pass an empty table.
        self._pricing = pricing if pricing is not None else DEFAULT_PRICING
        self._capability = capability if capability is not None else DEFAULT_CAPABILITY
        self._budget_ceiling = budget_ceiling

    def route(
        self,
        signals: TaskSignals | None = None,
        *,
        complexity: TaskComplexity | None = None,
        input_tokens: int | None = None,
        output_tokens: int = 500,
        override_model: str | None = None,
        scorer: Callable[[TaskSignals], TaskComplexity] = score_complexity,
    ) -> RoutingDecision:
        """Choose a model for the task and return a :class:`RoutingDecision`.

        Complexity is taken from ``complexity`` if given, else from ``scorer``
        applied to ``signals``. ``override_model`` forces a specific model
        (still reporting its cost). Token counts drive the cost estimate.
        """
        signals = signals or TaskSignals()
        tier = complexity if complexity is not None else scorer(signals)
        tokens_in = input_tokens if input_tokens is not None else signals.input_tokens
        if tokens_in < 0 or output_tokens < 0:
            raise ValueError("token counts must be >= 0")

        # Per-model totals for this workload (cheapest first), via CST-002.
        # estimate() coerces a falsy pricing table back to DEFAULT_PRICING, so
        # reject an empty table here to honor the caller's explicit choice.
        if not self._pricing:
            raise ValueError("pricing table is empty; cannot route a task")
        report = estimate(tokens_in, output_tokens, pricing=self._pricing)
        totals = [(e.model, e.total) for e in report.estimates]
        cost_of = dict(totals)

        if override_model is not None:
            if override_model not in cost_of:
                raise ValueError(f"override_model {override_model!r} not in pricing table")
            return RoutingDecision(
                model=override_model,
                complexity=tier,
                estimated_cost=cost_of[override_model],
                reason="manual_override",
                considered=totals,
            )

        capable = [
            (m, c) for m, c in totals if self._capability.get(m, TaskComplexity.SIMPLE) >= tier
        ]
        if not capable:
            # No model is rated for this tier; use the most capable available.
            best_model = max(
                totals, key=lambda mc: self._capability.get(mc[0], TaskComplexity.SIMPLE)
            )[0]
            return RoutingDecision(
                model=best_model,
                complexity=tier,
                estimated_cost=cost_of[best_model],
                reason="no_model_rated_for_tier_using_most_capable",
                considered=totals,
            )

        # `capable` is already cost-sorted (totals is); take the cheapest.
        model, cost = capable[0]
        reason = f"cheapest_capable_for_{tier.name.lower()}"

        if self._budget_ceiling is not None and cost > self._budget_ceiling:
            # Even the cheapest capable model is over budget — downgrade.
            cheapest_model, cheapest_cost = totals[0]
            return RoutingDecision(
                model=cheapest_model,
                complexity=tier,
                estimated_cost=cheapest_cost,
                reason="budget_ceiling_exceeded_downgraded",
                considered=totals,
            )

        return RoutingDecision(
            model=model,
            complexity=tier,
            estimated_cost=cost,
            reason=reason,
            considered=totals,
        )


__all__ = [
    "TaskComplexity",
    "TaskSignals",
    "RoutingDecision",
    "CostAwareRouter",
    "score_complexity",
    "DEFAULT_CAPABILITY",
]
