"""
MAG-006 — Shapley Attribution.

Credit / blame attribution across agents. When a task fails, surface
percentage contribution per agent. Uses an exact Shapley-value computation
on the marginal performance function — small N (≤8) is the realistic range.

For larger N, falls back to Monte Carlo sampling.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from itertools import permutations


@dataclass
class ShapleyResult:
    contributions: dict[str, float]  # absolute marginal contribution
    percentages: dict[str, float]  # normalized 0..100

    def ranked(self) -> list[tuple[str, float]]:
        return sorted(self.percentages.items(), key=lambda kv: kv[1], reverse=True)


def shapley_attribution(
    agents: list[str],
    value_fn: Callable[[list[str]], float],
    *,
    monte_carlo_cap: int = 8,
    n_samples: int = 2000,
) -> ShapleyResult:
    """
    `value_fn(coalition_agents)` returns the failure-blame value of running
    the task with that subset. Lower is better; higher means more blame.
    """
    n = len(agents)
    contributions = {a: 0.0 for a in agents}

    if n == 0:
        return ShapleyResult(contributions={}, percentages={})

    if n <= monte_carlo_cap:
        for perm in permutations(agents):
            prefix: list[str] = []
            prev = value_fn([])
            for a in perm:
                prefix.append(a)
                cur = value_fn(list(prefix))
                contributions[a] += cur - prev
                prev = cur
        total_perms = math.factorial(n)
        contributions = {a: v / total_perms for a, v in contributions.items()}
    else:
        rng = random.Random(0)  # noqa: S311  # nosec B311 — Monte Carlo sampling, not crypto
        for _ in range(n_samples):
            perm_list = list(agents)
            rng.shuffle(perm_list)
            prefix = []
            prev = value_fn([])
            for a in perm_list:
                prefix.append(a)
                cur = value_fn(list(prefix))
                contributions[a] += cur - prev
                prev = cur
        contributions = {a: v / n_samples for a, v in contributions.items()}

    total = sum(abs(v) for v in contributions.values()) or 1.0
    percentages = {a: (abs(v) / total) * 100 for a, v in contributions.items()}
    return ShapleyResult(contributions=contributions, percentages=percentages)


__all__ = ["ShapleyResult", "shapley_attribution"]
