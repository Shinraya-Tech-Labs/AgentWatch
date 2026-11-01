"""
CST-004 — Task Cost Predictor.

Before a task starts, estimate total cost based on similar historical tasks.
Uses a simple k-NN over (goal_embedding, total_cost) tuples plus a baseline
heuristic when history is sparse.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from panoptis.scoring.drift import cosine, embed


@dataclass
class HistoricalRun:
    goal: str
    total_tokens: int
    total_usd: float
    vector: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.vector:
            self.vector = embed(self.goal)


@dataclass
class Prediction:
    expected_tokens: int
    expected_usd: float
    lower_usd: float
    upper_usd: float
    n_neighbors: int
    confidence: float  # 0..1, higher with more matches


class TaskCostPredictor:
    """In-memory k-NN cost predictor."""

    def __init__(self, k: int = 5, fallback_usd: float = 0.50, fallback_tokens: int = 5000):
        self.k = k
        self.fallback_usd = fallback_usd
        self.fallback_tokens = fallback_tokens
        self._history: list[HistoricalRun] = []

    def add(self, goal: str, total_tokens: int, total_usd: float) -> HistoricalRun:
        run = HistoricalRun(goal=goal, total_tokens=total_tokens, total_usd=total_usd)
        self._history.append(run)
        return run

    def predict(self, goal: str) -> Prediction:
        if not self._history:
            return Prediction(
                expected_tokens=self.fallback_tokens,
                expected_usd=self.fallback_usd,
                lower_usd=0.0,
                upper_usd=self.fallback_usd * 3,
                n_neighbors=0,
                confidence=0.0,
            )

        query = embed(goal)
        ranked = sorted(
            self._history,
            key=lambda r: cosine(query, r.vector),
            reverse=True,
        )
        neighbors = ranked[: self.k]
        sims = [max(0.0, cosine(query, r.vector)) for r in neighbors]

        # Similarity-weighted means
        weights = sims if any(s > 0 for s in sims) else [1.0] * len(neighbors)
        wsum = sum(weights) or 1.0
        exp_tokens = (
            sum(r.total_tokens * w for r, w in zip(neighbors, weights, strict=False)) / wsum
        )
        exp_usd = sum(r.total_usd * w for r, w in zip(neighbors, weights, strict=False)) / wsum

        usds = [r.total_usd for r in neighbors]
        stdev = statistics.stdev(usds) if len(usds) > 1 else 0.0
        lower = max(0.0, exp_usd - stdev)
        upper = exp_usd + stdev
        confidence = (sum(sims) / max(1, len(sims))) if sims else 0.0

        return Prediction(
            expected_tokens=int(exp_tokens),
            expected_usd=float(exp_usd),
            lower_usd=float(lower),
            upper_usd=float(upper),
            n_neighbors=len(neighbors),
            confidence=confidence,
        )


__all__ = ["HistoricalRun", "Prediction", "TaskCostPredictor"]
