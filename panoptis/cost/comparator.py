"""
CST-002 — Model Cost Comparator.

Given a token-count estimate (or input text), produce per-model cost
estimates across major providers. Reference pricing — kept in a constant
table; callers can override with their negotiated rates.
"""

from __future__ import annotations

from dataclasses import dataclass

# $ per 1M tokens. Updated to public list prices as of 2025-Q4.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_per_million, output_per_million)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-opus-4-5": (15.00, 75.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Gemini: list prices from ai.google.dev/gemini-api/docs/pricing (checked 2026-11-10),
    # not the launch promotions that end 2026-12-01 (Argon) and 2027-01-01 (3.8 Flash)
    "gemini-4-argon": (4.00, 20.00),
    "gemini-3.8-flash": (1.50, 7.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}


@dataclass
class CostEstimate:
    model: str
    input_cost: float
    output_cost: float
    total: float

    def to_dict(self) -> dict[str, str | float]:
        return {
            "model": self.model,
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "total": self.total,
        }


@dataclass
class ComparisonReport:
    estimates: list[CostEstimate]

    def cheapest(self) -> CostEstimate:
        return min(self.estimates, key=lambda e: e.total)

    def most_expensive(self) -> CostEstimate:
        return max(self.estimates, key=lambda e: e.total)


def estimate(
    input_tokens: int,
    output_tokens: int,
    *,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> ComparisonReport:
    p = pricing or DEFAULT_PRICING
    estimates: list[CostEstimate] = []
    for model, (in_rate, out_rate) in p.items():
        in_cost = (input_tokens / 1_000_000) * in_rate
        out_cost = (output_tokens / 1_000_000) * out_rate
        estimates.append(
            CostEstimate(
                model=model,
                input_cost=in_cost,
                output_cost=out_cost,
                total=in_cost + out_cost,
            )
        )
    return ComparisonReport(estimates=sorted(estimates, key=lambda e: e.total))


def estimate_for_text(input_text: str, output_text: str = "") -> ComparisonReport:
    # rough heuristic: 4 chars ≈ 1 token
    return estimate(len(input_text) // 4, len(output_text) // 4)


__all__ = ["CostEstimate", "ComparisonReport", "estimate", "estimate_for_text", "DEFAULT_PRICING"]
