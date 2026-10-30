"""
PRT-003 — Anonymized Failure Benchmark.

Opt-in anonymized failure aggregation. Public benchmark generation with
privacy-safe aggregation only — no per-user data ever exposed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class FailurePattern:
    pattern_id: str
    fingerprint: str  # SHA256 hash of the canonical pattern
    category: str  # tool_error | timeout | hallucination | safety_block
    occurrence_count: int = 0


@dataclass
class BenchmarkAggregate:
    generated_at: datetime
    n_contributors: int  # distinct opted-in sources
    n_failures: int
    patterns: list[FailurePattern]
    per_category: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "n_contributors": self.n_contributors,
            "n_failures": self.n_failures,
            "patterns": [p.__dict__ for p in self.patterns],
            "per_category": self.per_category,
        }


def _fingerprint(category: str, signature: str) -> str:
    return hashlib.sha256(f"{category}::{signature}".encode()).hexdigest()[:32]


class AnonymizedBenchmark:
    """
    Privacy-safe failure aggregator. Per-contributor data is rolled up
    into pattern counts only; raw text never leaves the box.

    Caller is expected to pass a stable contributor token (e.g. opaque
    install ID), not real identity.
    """

    def __init__(self, *, k_anonymity_threshold: int = 5):
        self.k_anonymity_threshold = k_anonymity_threshold
        self._patterns: dict[str, FailurePattern] = {}
        self._contributors: set[str] = set()
        # contributor → set of fingerprints they contributed (so we can
        # enforce k-anonymity at report time)
        self._contributor_fingerprints: dict[str, set[str]] = {}
        self._raw_failures: int = 0

    def submit(self, contributor: str, category: str, signature: str) -> FailurePattern:
        self._contributors.add(contributor)
        self._raw_failures += 1
        fp = _fingerprint(category, signature)
        pattern = self._patterns.get(fp)
        if pattern is None:
            pattern = FailurePattern(
                pattern_id=fp[:8],
                fingerprint=fp,
                category=category,
            )
            self._patterns[fp] = pattern
        pattern.occurrence_count += 1
        self._contributor_fingerprints.setdefault(contributor, set()).add(fp)
        return pattern

    def report(self) -> BenchmarkAggregate:
        # k-anonymity: only include patterns reported by at least k contributors
        contrib_count: Counter = Counter()
        for fps in self._contributor_fingerprints.values():
            for fp in fps:
                contrib_count[fp] += 1
        anonymized = [
            p
            for p in self._patterns.values()
            if contrib_count[p.fingerprint] >= self.k_anonymity_threshold
        ]
        per_cat: dict[str, int] = {}
        for p in anonymized:
            per_cat[p.category] = per_cat.get(p.category, 0) + p.occurrence_count
        return BenchmarkAggregate(
            generated_at=datetime.now(UTC),
            n_contributors=len(self._contributors),
            n_failures=self._raw_failures,
            patterns=sorted(anonymized, key=lambda p: p.occurrence_count, reverse=True),
            per_category=per_cat,
        )


__all__ = ["FailurePattern", "BenchmarkAggregate", "AnonymizedBenchmark"]
