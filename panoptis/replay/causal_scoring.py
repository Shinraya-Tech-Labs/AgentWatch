"""Empirical causal necessity and sufficiency scoring for counterfactual interventions.

Distinguishes measured empirical replay results from theoretical causal assertions.
Generates hypothesis records explicitly marked as EXPERIMENTAL with INTERVENTIONAL
evidence classification.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CausalScoringResult:
    """Empirical necessity and sufficiency evaluation from counterfactual replays."""

    necessity_score: float | None
    sufficiency_score: float | None
    trials_count: int
    baseline_outcomes: list[bool]
    counterfactual_outcomes: list[bool]
    maturity: str = "EXPERIMENTAL"
    evidence_class: str = "INTERVENTIONAL"
    summary: str = ""
    caveat: str = (
        "Empirical scores computed strictly from observed deterministic replay perturbations. "
        "Does not constitute an uncalibrated theoretical causal discovery."
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "necessity_score": self.necessity_score,
            "sufficiency_score": self.sufficiency_score,
            "trials_count": self.trials_count,
            "baseline_outcomes": self.baseline_outcomes,
            "counterfactual_outcomes": self.counterfactual_outcomes,
            "maturity": self.maturity,
            "evidence_class": self.evidence_class,
            "summary": self.summary,
            "caveat": self.caveat,
            "metadata": self.metadata,
        }


def compute_empirical_causal_scores(
    trials: Sequence[tuple[bool, bool]],
) -> CausalScoringResult:
    """Compute empirical necessity and sufficiency scores from (baseline_outcome, counterfactual_outcome) trials.

    Convention:
    - True represents presence of the target failure/event (E=1).
    - False represents absence of the target failure/event (E=0).

    Probability of Necessity (PN):
    Among trials where baseline had the failure (E_base=1), what proportion had the failure
    prevented under intervention (E_cf=0)?
    PN = count(E_base=1 and E_cf=0) / count(E_base=1).

    Probability of Sufficiency (PS):
    Among trials where baseline did NOT have the failure (E_base=0), what proportion had the failure
    triggered upon injecting the intervention (E_cf=1)?
    PS = count(E_base=0 and E_cf=1) / count(E_base=0).
    """
    if not trials:
        return CausalScoringResult(
            necessity_score=None,
            sufficiency_score=None,
            trials_count=0,
            baseline_outcomes=[],
            counterfactual_outcomes=[],
            summary="Zero trials evaluated; necessity and sufficiency undefined.",
        )

    base_list = [t[0] for t in trials]
    cf_list = [t[1] for t in trials]

    nec_trials = [t for t in trials if t[0] is True]
    suff_trials = [t for t in trials if t[0] is False]

    necessity: float | None = None
    if nec_trials:
        prevented = sum(1 for t in nec_trials if t[1] is False)
        necessity = round(prevented / len(nec_trials), 4)

    sufficiency: float | None = None
    if suff_trials:
        induced = sum(1 for t in suff_trials if t[1] is True)
        sufficiency = round(induced / len(suff_trials), 4)

    parts: list[str] = []
    if necessity is not None:
        parts.append(f"Empirical Necessity: {necessity:.2f} ({len(nec_trials)} trials)")
    else:
        parts.append("Empirical Necessity: N/A (no baseline failure trials)")

    if sufficiency is not None:
        parts.append(f"Empirical Sufficiency: {sufficiency:.2f} ({len(suff_trials)} trials)")
    else:
        parts.append("Empirical Sufficiency: N/A (no baseline success trials)")

    return CausalScoringResult(
        necessity_score=necessity,
        sufficiency_score=sufficiency,
        trials_count=len(trials),
        baseline_outcomes=base_list,
        counterfactual_outcomes=cf_list,
        summary="; ".join(parts),
    )


def generate_experimental_hypothesis(
    cause: str,
    effect: str,
    scoring_result: CausalScoringResult,
    *,
    statement: str | None = None,
    rationale: str | None = None,
    proposer: str = "analyzer",
    experiment_id: str | None = None,
    ws: Any = None,
) -> dict[str, Any]:
    """Build a hypothesis record explicitly labeled EXPERIMENTAL and INTERVENTIONAL.

    Uses `proposer="analyzer"` by default to ensure 100% compatibility with
    the allowed proposer taxonomy in `panoptis.causality.hypotheses`.
    """
    allowed_proposers = {"human", "analyzer", "llm", "benchmark"}
    if proposer not in allowed_proposers:
        raise ValueError(f"proposer must be one of {sorted(allowed_proposers)}, got {proposer!r}")

    stmt = statement or f"Intervention on '{cause}' empirically alters occurrence of '{effect}'"
    doc: dict[str, Any] = {
        "cause": cause,
        "effect": effect,
        "relation": "CAUSES",
        "statement": stmt,
        "proposer": proposer,
        "rationale": rationale or scoring_result.summary,
        "maturity": "EXPERIMENTAL",
        "evidence_class": "INTERVENTIONAL",
        "status": "PROPOSED",
        "scoring": scoring_result.to_dict(),
        "caveat": scoring_result.caveat,
        "metadata": {
            "engine": "counterfactual_replay_l3",
            "experiment_id": experiment_id,
        },
    }

    if ws is not None and hasattr(ws, "store") and hasattr(ws, "tenant_id"):
        hid = ws.store.put_experiment(ws.tenant_id, "hypothesis", doc, subject=cause)
        doc["hypothesis_id"] = hid

        # If an experiment_id is recorded, attach interventional evidence record
        if experiment_id:
            direction = (
                "supports"
                if (
                    scoring_result.necessity_score == 1.0 or scoring_result.sufficiency_score == 1.0
                )
                else "contradicts"
            )
            ev_doc = {
                "hypothesis_id": hid,
                "kind": "INTERVENTION",
                "direction": direction,
                "summary": scoring_result.summary,
                "refs": [],
                "experiment_id": experiment_id,
                "reproduction_confidence": 1.0,
                "statistics": scoring_result.to_dict(),
            }
            eid = ws.store.put_experiment(ws.tenant_id, "hypothesis_evidence", ev_doc, subject=hid)
            doc["evidence_id"] = eid

    return doc


__all__ = [
    "CausalScoringResult",
    "compute_empirical_causal_scores",
    "generate_experimental_hypothesis",
]
