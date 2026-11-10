"""
SAF-008 — Automated Red-Team Safety Test Harness.

Drives a curated corpus of simulated attacks — prompt injection, path
traversal, credential scans, and tool misuse — through PANOPTIS's detection
layer and scores how well the agent's defenses hold up.

Payloads are only ever *scored* by the existing detectors
(:class:`~panoptis.core.safety.RiskScorer` and
:func:`~panoptis.core.injection.scan_text`); they are **never executed**, so
the harness is safe to run anywhere, including CI. A scenario is "defended"
when its payload is detected/blocked and "bypassed" when it slips through —
bypassed scenarios are the actionable gaps in the agent's defenses.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from panoptis.core.injection import scan_text
from panoptis.core.safety import RiskScorer
from panoptis.core.schema import RiskLevel, ToolCallData
from panoptis.security.owasp import OwaspVector

# Bundled attack corpus, externalized so it can be edited/extended without code
# changes and shared with the scheduled red-team runner.
_PAYLOADS_PATH = Path(__file__).parent / "payloads.json"


class AttackCategory(str, Enum):
    """Families of simulated attack the harness exercises."""

    PROMPT_INJECTION = "prompt_injection"
    PATH_TRAVERSAL = "path_traversal"
    CREDENTIAL_SCAN = "credential_scan"
    TOOL_MISUSE = "tool_misuse"


# A risk level at or above this counts as "defended" (the command would be
# blocked or escalated for approval rather than run silently).
_BLOCKING_LEVELS: frozenset[RiskLevel] = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})


@dataclass(frozen=True)
class AttackScenario:
    """A single simulated attack: a payload and the vector it targets."""

    id: str
    category: AttackCategory
    vector: OwaspVector
    payload: str
    description: str


@dataclass
class AttackResult:
    """Outcome of running one scenario through the detection layer."""

    scenario: AttackScenario
    defended: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to a JSON-friendly dict."""
        return {
            "id": self.scenario.id,
            "category": self.scenario.category.value,
            "vector": self.scenario.vector.value,
            "defended": self.defended,
            "detail": self.detail,
        }


@dataclass
class ResilienceReport:
    """Aggregate outcome of a red-team run across all scenarios."""

    results: list[AttackResult]

    @property
    def total(self) -> int:
        """Number of scenarios run."""
        return len(self.results)

    @property
    def defended_count(self) -> int:
        """Number of scenarios whose payload was detected/blocked."""
        return sum(1 for r in self.results if r.defended)

    @property
    def bypassed(self) -> list[AttackResult]:
        """Scenarios whose payload slipped past the detectors."""
        return [r for r in self.results if not r.defended]

    @property
    def resilience_score(self) -> float:
        """Fraction of attacks defended, in [0.0, 1.0]. Empty corpus → 1.0."""
        return self.defended_count / self.total if self.total else 1.0

    def by_category(self) -> dict[str, dict[str, int]]:
        """Per-category {defended, total} breakdown.

        All categories are always present (zero-initialized) so the schema is
        stable across default, custom, and empty corpora.
        """
        out: dict[str, dict[str, int]] = {
            c.value: {"defended": 0, "total": 0} for c in AttackCategory
        }
        for r in self.results:
            bucket = out[r.scenario.category.value]
            bucket["total"] += 1
            if r.defended:
                bucket["defended"] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full report (score, per-category, results) to a dict."""
        return {
            "resilience_score": round(self.resilience_score, 4),
            "defended": self.defended_count,
            "total": self.total,
            "by_category": self.by_category(),
            "bypassed": [r.to_dict() for r in self.bypassed],
            "results": [r.to_dict() for r in self.results],
        }


def load_corpus(path: str | Path | None = None) -> list[AttackScenario]:
    """Load attack scenarios from a JSON payload file.

    Defaults to the bundled ``payloads.json``. Each entry must provide ``id``,
    ``category`` (an :class:`AttackCategory` value), ``vector`` (an
    :class:`~panoptis.security.owasp.OwaspVector` value), ``payload``, and an
    optional ``description``.
    """
    src = Path(path) if path is not None else _PAYLOADS_PATH
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        AttackScenario(
            id=item["id"],
            category=AttackCategory(item["category"]),
            vector=OwaspVector(item["vector"]),
            payload=item["payload"],
            description=item.get("description", ""),
        )
        for item in raw
    ]


def default_corpus() -> list[AttackScenario]:
    """Curated attack scenarios spanning the four red-team categories.

    Loaded from the bundled ``payloads.json`` (single source of truth). The set
    deliberately includes attacks the current detectors catch and a few that
    slip through, so the resilience score reflects real coverage.
    """
    return load_corpus()


class RedTeamHarness:
    """Run a corpus of simulated attacks against PANOPTIS's detection layer.

    The default target is the offline, deterministic :class:`RiskScorer` /
    prompt-injection scanner — no live agent or LLM is invoked and nothing is
    executed. Inject a custom ``scorer`` to point the harness at a different
    detection backend.
    """

    def __init__(
        self,
        scenarios: Sequence[AttackScenario] | None = None,
        *,
        scorer: RiskScorer | None = None,
        blocking_levels: frozenset[RiskLevel] = _BLOCKING_LEVELS,
    ) -> None:
        """Configure the corpus, scorer, and which risk levels count as defended."""
        self.scenarios: list[AttackScenario] = (
            list(scenarios) if scenarios is not None else default_corpus()
        )
        self._scorer = scorer or RiskScorer()
        self._blocking = blocking_levels

    def run(self) -> ResilienceReport:
        """Score every scenario and return the resilience report."""
        return ResilienceReport([self._evaluate(s) for s in self.scenarios])

    def _evaluate(self, scenario: AttackScenario) -> AttackResult:
        """Run one scenario through the matching detector and record the outcome."""
        if scenario.category is AttackCategory.PROMPT_INJECTION:
            scan = scan_text(scenario.payload)
            if scan.detected:
                names = ", ".join(f.pattern for f in scan.findings)
                return AttackResult(scenario, True, f"injection detected: {names}")
            return AttackResult(scenario, False, "no injection pattern matched")

        level, score, reasons, _ = self._scorer.score(
            ToolCallData(
                tool_name="bash",
                raw_command=scenario.payload,
                arguments={"command": scenario.payload},
            )
        )
        defended = level in self._blocking
        detail = f"risk={level.value} score={score:.2f}"
        if reasons:
            detail += f" - {reasons[0]}"
        return AttackResult(scenario, defended, detail)


__all__ = [
    "AttackCategory",
    "AttackScenario",
    "AttackResult",
    "ResilienceReport",
    "RedTeamHarness",
    "default_corpus",
    "load_corpus",
]
