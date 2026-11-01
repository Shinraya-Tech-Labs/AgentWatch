"""Conservative causal infrastructure.

AgentWatch does not *discover* causes from a single trace. It keeps four kinds of
statements strictly apart:

* **dependency**  — the effect is in the data/control path of the cause (OBSERVATIONAL
  evidence at most: influence was possible and present, not shown necessary);
* **correlation** — cross-run co-occurrence statistics (CORRELATIONAL);
* **hypothesis**  — a proposed causal claim with status and accumulated evidence;
* **intervention**— a branch/counterfactual experiment that changed the candidate cause and
  observed the effect (INTERVENTIONAL; VERIFIED after independent replication).

Evidence class of a hypothesis is *computed* from its evidence records; no caller —
including an LLM — can set it directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from panoptis.graph.model import EvidenceClass
from panoptis.query.workspace import Workspace


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceKind(StrEnum):
    DEPENDENCY = "DEPENDENCY"
    CORRELATION = "CORRELATION"
    QUASI_EXPERIMENT = "QUASI_EXPERIMENT"
    INTERVENTION = "INTERVENTION"


KIND_CLASS = {
    EvidenceKind.CORRELATION: EvidenceClass.CORRELATIONAL,
    EvidenceKind.DEPENDENCY: EvidenceClass.OBSERVATIONAL,
    EvidenceKind.QUASI_EXPERIMENT: EvidenceClass.QUASI_CAUSAL,
    EvidenceKind.INTERVENTION: EvidenceClass.INTERVENTIONAL,
}
PROPOSERS = {"human", "analyzer", "llm", "benchmark"}


@dataclass
class HypothesisView:
    record: dict[str, Any]
    evidence: list[dict[str, Any]]

    def assess(self) -> dict[str, Any]:
        sup = [e for e in self.evidence if e["direction"] == "supports"]
        con = [e for e in self.evidence if e["direction"] == "contradicts"]
        sup_int = [e for e in sup if e["kind"] == EvidenceKind.INTERVENTION]
        con_int = [e for e in con if e["kind"] == EvidenceKind.INTERVENTION]
        best: EvidenceClass | None = None
        order = [
            EvidenceClass.CORRELATIONAL,
            EvidenceClass.OBSERVATIONAL,
            EvidenceClass.QUASI_CAUSAL,
            EvidenceClass.INTERVENTIONAL,
        ]
        for e in sup:
            c = KIND_CLASS[EvidenceKind(e["kind"])]
            if best is None or order.index(c) > order.index(best):
                best = c
        independent = {e.get("experiment_id") or e["evidence_id"] for e in sup_int}
        if (
            best == EvidenceClass.INTERVENTIONAL
            and len(independent) >= 2
            and not con_int
            and all(e.get("reproduction_confidence", 0) >= 0.8 for e in sup_int)
        ):
            best = EvidenceClass.VERIFIED
        if con_int and not sup_int:
            status = HypothesisStatus.REFUTED
        elif sup_int and con_int:
            status = HypothesisStatus.INCONCLUSIVE
        elif sup_int:
            status = HypothesisStatus.SUPPORTED
        else:
            status = (
                HypothesisStatus.PROPOSED
            )  # non-interventional evidence never settles a causal claim
        n = len(sup) + len(con)
        conf = None
        if sup_int or con_int:
            conf = round(
                (len(sup_int) + 1) / (len(sup_int) + len(con_int) + 2), 3
            )  # Laplace; uncalibrated
        return {
            **self.record,
            "status": status.value,
            "evidence_class": best.value if best else None,
            "confidence": {
                "value": conf,
                "basis": "Laplace-smoothed share of supporting interventions",
                "calibrated": False,
            }
            if conf is not None
            else None,
            "supporting": sup,
            "contradicting": con,
            "evidence_count": n,
        }


def propose(
    ws: Workspace,
    *,
    cause: str,
    effect: str,
    statement: str,
    proposer: str = "human",
    relation: str = "CAUSES",
    rationale: str | None = None,
) -> dict[str, Any]:
    if proposer not in PROPOSERS:
        raise ValueError(f"proposer must be one of {sorted(PROPOSERS)}")
    doc = {
        "cause": cause,
        "effect": effect,
        "relation": relation,
        "statement": statement,
        "proposer": proposer,
        "rationale": rationale,
        "note": "LLM-proposed hypotheses start PROPOSED with no evidence class"
        if proposer == "llm"
        else None,
    }
    hid = ws.store.put_experiment(ws.tenant_id, "hypothesis", doc, subject=cause)
    return get(ws, hid)


def add_evidence(
    ws: Workspace,
    hypothesis_id: str,
    *,
    kind: EvidenceKind | str,
    direction: str,
    summary: str,
    refs: list[str] | None = None,
    experiment_id: str | None = None,
    reproduction_confidence: float | None = None,
    statistics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if direction not in ("supports", "contradicts"):
        raise ValueError("direction must be 'supports' or 'contradicts'")
    kind = EvidenceKind(kind)
    if kind == EvidenceKind.INTERVENTION and not experiment_id:
        raise ValueError("interventional evidence must reference a recorded experiment")
    if experiment_id and ws.store.experiment(experiment_id) is None:
        raise ValueError(f"experiment {experiment_id} does not exist")
    doc = {
        "hypothesis_id": hypothesis_id,
        "kind": kind.value,
        "direction": direction,
        "summary": summary,
        "refs": refs or [],
        "experiment_id": experiment_id,
        "reproduction_confidence": reproduction_confidence,
        "statistics": statistics,
    }
    eid = ws.store.put_experiment(ws.tenant_id, "hypothesis_evidence", doc, subject=hypothesis_id)
    return {**doc, "evidence_id": eid}


def get(ws: Workspace, hypothesis_id: str) -> dict[str, Any]:
    rec = ws.store.experiment(hypothesis_id)
    if rec is None or rec.get("record_type") != "hypothesis":
        raise LookupError(f"hypothesis {hypothesis_id} not found")
    ev = [
        {**e, "evidence_id": e["record_id"]}
        for e in ws.store.experiments(ws.tenant_id, "hypothesis_evidence", subject=hypothesis_id)
    ]
    return HypothesisView({**rec, "hypothesis_id": hypothesis_id}, ev).assess()


def list_hypotheses(ws: Workspace, about: str | None = None) -> list[dict[str, Any]]:
    out = []
    for rec in ws.store.experiments(ws.tenant_id, "hypothesis"):
        if about and about not in (rec.get("cause"), rec.get("effect")):
            continue
        out.append(get(ws, rec["record_id"]))
    return out


def causal_relations(ws: Workspace) -> list[dict[str, Any]]:
    """CAUSAL-view relations derived from hypotheses that have supporting evidence."""
    from panoptis.graph.model import Basis, RelType, View, make_relation

    rels = []
    for h in list_hypotheses(ws):
        if not h["evidence_class"] or h["status"] == HypothesisStatus.REFUTED:
            continue
        rtype = RelType(h["relation"]) if h["relation"] in RelType.__members__ else RelType.CAUSES
        rels.append(
            make_relation(
                View.CAUSAL,
                rtype,
                [h["cause"]],
                [h["effect"]],
                basis=Basis.INTERVENTIONAL
                if h["evidence_class"] in ("INTERVENTIONAL", "VERIFIED")
                else Basis.HYPOTHESIS,
                confidence=(h["confidence"] or {}).get("value") or 0.0,
                run_id=None,
                evidence=[e["evidence_id"] for e in h["supporting"]],
                derived_by="causality.hypotheses@1",
                evidence_class=EvidenceClass(h["evidence_class"]),
                attributes={"hypothesis_id": h["hypothesis_id"], "status": h["status"]},
            )
        )
    return rels
