"""CST-009 — Cost-aware model router tests (issue #375)."""

from __future__ import annotations

import pytest

from panoptis.cost.comparator import DEFAULT_PRICING
from panoptis.cost.complexity_router import (
    CostAwareRouter,
    TaskComplexity,
    TaskSignals,
    score_complexity,
)


def test_simple_task_routes_to_cheapest_model():
    """A trivial prompt is routed to the cheapest model overall."""
    d = CostAwareRouter().route(TaskSignals(prompt="format this date please"))
    assert d.complexity is TaskComplexity.SIMPLE
    assert d.model == "gpt-4o-mini"  # cheapest in DEFAULT_PRICING
    assert d.reason == "cheapest_capable_for_simple"


def test_complex_task_reserves_advanced_model():
    """Reasoning-heavy work goes to the cheapest COMPLEX-capable model."""
    d = CostAwareRouter().route(TaskSignals(prompt="x" * 200, requires_reasoning=True))
    assert d.complexity is TaskComplexity.COMPLEX
    assert d.model == "gemini-4-argon"  # cheaper than claude-opus-4-5, both COMPLEX


def test_standard_task_picks_cheapest_capable_mid_tier():
    """A standard task skips SIMPLE-only models and takes the cheapest mid-tier one."""
    d = CostAwareRouter().route(complexity=TaskComplexity.STANDARD, input_tokens=2000)
    assert d.model == "gemini-3.8-flash"  # cheapest with capability >= STANDARD


def test_explicit_complexity_overrides_scorer():
    """An explicit complexity argument bypasses the heuristic scorer."""
    d = CostAwareRouter().route(TaskSignals(prompt="hi"), complexity=TaskComplexity.COMPLEX)
    assert d.model == "gemini-4-argon"


def test_override_model_is_honored():
    """override_model forces a specific model and is flagged in the reason."""
    d = CostAwareRouter().route(TaskSignals(prompt="hi"), override_model="claude-opus-4-5")
    assert d.model == "claude-opus-4-5"
    assert d.reason == "manual_override"


def test_override_model_unknown_raises():
    """An override outside the pricing table is rejected."""
    with pytest.raises(ValueError, match="not in pricing table"):
        CostAwareRouter().route(TaskSignals(prompt="hi"), override_model="no-such-model")


def test_budget_ceiling_downgrades_when_capable_too_costly():
    """When the cheapest capable model exceeds the ceiling, downgrade to cheapest overall."""
    router = CostAwareRouter(budget_ceiling=0.001)
    d = router.route(complexity=TaskComplexity.COMPLEX, input_tokens=100_000, output_tokens=5_000)
    assert d.model == "gpt-4o-mini"
    assert d.reason == "budget_ceiling_exceeded_downgraded"


def test_considered_is_cost_sorted_and_complete():
    """The considered list covers every priced model, cheapest first."""
    d = CostAwareRouter().route(TaskSignals(prompt="hi"))
    costs = [c for _, c in d.considered]
    assert costs == sorted(costs)
    assert len(d.considered) == len(DEFAULT_PRICING)


def test_to_dict_shape():
    """to_dict emits the expected JSON-friendly keys."""
    d = CostAwareRouter().route(TaskSignals(prompt="hi"))
    out = d.to_dict()
    assert set(out) == {"model", "complexity", "estimated_cost", "reason", "considered"}
    assert out["complexity"] == "simple"


@pytest.mark.parametrize(
    "signals,expected",
    [
        (TaskSignals(prompt="short"), TaskComplexity.SIMPLE),
        (TaskSignals(prompt="x" * 400, requires_tools=True), TaskComplexity.STANDARD),
        (TaskSignals(prompt="x" * 40_000), TaskComplexity.COMPLEX),
        (TaskSignals(prompt="hi", requires_reasoning=True), TaskComplexity.COMPLEX),
    ],
)
def test_score_complexity_boundaries(signals, expected):
    """The heuristic scorer classifies representative tasks correctly."""
    assert score_complexity(signals) is expected


def test_empty_pricing_table_raises():
    """An empty pricing table is rejected with a clear error, not an IndexError."""
    with pytest.raises(ValueError, match="pricing table is empty"):
        CostAwareRouter(pricing={}).route(TaskSignals(prompt="hi"))


def test_custom_capability_table_is_respected():
    """A caller-supplied capability map changes which models qualify."""
    # Make haiku capable of COMPLEX and cheaper-than-opus haiku should win.
    router = CostAwareRouter(capability={"claude-haiku-4-5": TaskComplexity.COMPLEX})
    d = router.route(complexity=TaskComplexity.COMPLEX, input_tokens=1000)
    assert d.model == "claude-haiku-4-5"


def test_empty_capability_map_is_respected():
    """An explicit empty capability map is preserved (not replaced by defaults);
    with nothing rated for COMPLEX the router falls back to the most capable."""
    d = CostAwareRouter(capability={}).route(complexity=TaskComplexity.COMPLEX, input_tokens=1000)
    assert d.reason == "no_model_rated_for_tier_using_most_capable"


@pytest.mark.parametrize("input_tokens,output_tokens", [(-1, 1), (1, -1)])
def test_negative_tokens_rejected(input_tokens, output_tokens):
    """Negative token counts are rejected before any cost is computed."""
    with pytest.raises(ValueError, match="must be >= 0"):
        CostAwareRouter().route(
            complexity=TaskComplexity.SIMPLE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
