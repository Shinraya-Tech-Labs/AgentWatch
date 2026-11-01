"""
PLT-005 — Prompt Version Management.

Version-control system prompts inside AgentWatch.
A/B test prompt versions against production traffic.
Roll back on confidence drop.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class PromptVersion:
    name: str
    version: int
    text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


@dataclass
class ABTestResult:
    name: str
    variants: dict[int, dict[str, float]]
    winner_version: int | None
    confidence_delta: float


class PromptRegistry:
    """Versioned prompt store with A/B testing and rollback."""

    def __init__(self) -> None:
        self._versions: dict[str, list[PromptVersion]] = defaultdict(list)
        self._active: dict[str, int] = {}
        # variant -> [(confidence, success)]
        self._scores: dict[tuple[str, int], list[tuple[float, bool]]] = defaultdict(list)
        # A/B split: name -> {version: weight}
        self._splits: dict[str, dict[int, float]] = {}

    def register(self, name: str, text: str, *, activate: bool = True) -> PromptVersion:
        versions = self._versions[name]
        next_v = max((v.version for v in versions), default=0) + 1
        pv = PromptVersion(name=name, version=next_v, text=text)
        versions.append(pv)
        if activate:
            self._active[name] = next_v
        return pv

    def latest(self, name: str) -> PromptVersion | None:
        versions = self._versions.get(name, [])
        return versions[-1] if versions else None

    def active(self, name: str) -> PromptVersion | None:
        v = self._active.get(name)
        if v is None:
            return None
        for pv in self._versions.get(name, []):
            if pv.version == v:
                return pv
        return None

    def rollback(self, name: str) -> PromptVersion | None:
        versions = self._versions.get(name, [])
        if len(versions) < 2:
            return None
        new_active = versions[-2].version
        self._active[name] = new_active
        return versions[-2]

    def set_ab_split(self, name: str, weights: dict[int, float]) -> None:
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("weights must sum to positive total")
        self._splits[name] = {v: w / total for v, w in weights.items()}

    def select(self, name: str) -> PromptVersion | None:
        split = self._splits.get(name)
        if not split:
            return self.active(name)
        r = random.random()  # noqa: S311  # nosec B311 — A/B routing, not crypto
        cumulative = 0.0
        for version, weight in split.items():
            cumulative += weight
            if r <= cumulative:
                for pv in self._versions[name]:
                    if pv.version == version:
                        return pv
        return self.active(name)

    def record_outcome(self, name: str, version: int, *, confidence: float, success: bool) -> None:
        self._scores[(name, version)].append((confidence, success))

    def evaluate_ab(self, name: str) -> ABTestResult:
        variants: dict[int, dict[str, float]] = {}
        for (n, v), outcomes in self._scores.items():
            if n != name or not outcomes:
                continue
            confidences = [c for c, _ in outcomes]
            successes = sum(1 for _, s in outcomes if s)
            variants[v] = {
                "n": len(outcomes),
                "mean_confidence": sum(confidences) / len(confidences),
                "success_rate": successes / len(outcomes),
            }
        if not variants:
            return ABTestResult(name=name, variants={}, winner_version=None, confidence_delta=0.0)

        ranked = sorted(variants.items(), key=lambda kv: kv[1]["mean_confidence"], reverse=True)
        winner = ranked[0][0]
        delta = ranked[0][1]["mean_confidence"] - ranked[-1][1]["mean_confidence"]
        return ABTestResult(
            name=name, variants=variants, winner_version=winner, confidence_delta=delta
        )

    def auto_rollback_on_drop(
        self, name: str, *, min_confidence: float = 0.55
    ) -> PromptVersion | None:
        active = self.active(name)
        if active is None:
            return None
        outcomes = self._scores.get((name, active.version), [])
        if len(outcomes) < 5:
            return None
        mean_conf = sum(c for c, _ in outcomes) / len(outcomes)
        if mean_conf < min_confidence:
            return self.rollback(name)
        return None


__all__ = ["PromptVersion", "ABTestResult", "PromptRegistry"]
