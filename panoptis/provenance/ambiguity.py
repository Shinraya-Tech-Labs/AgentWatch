"""Ambiguous provenance and information-evidence summaries (ADR-0017).

Two derived record types, rebuilt with the interpretation:

* ``ambiguous_provenance`` — one per value whose source the evidence does not single out:
  the candidate producers, the evidence for each, the alternatives that make it ambiguous,
  and the sources that are certain regardless (``resolution_status`` AMBIGUOUS).
* ``information_evidence`` — one per run: how its information relations are supported
  (declared vs inferred, by evidence type), and how many consumed values are resolved,
  ambiguous or of unknown origin. It says how far the run's lineage can be trusted.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from typing import Any

from panoptis.analysis.base import AnalysisInput, Analyzer
from panoptis.analysis.maturity import Maturity
from panoptis.graph.information import instance_event

_NS = uuid.UUID("5b8e1f7a-9c2d-4e3f-8a1b-2c3d4e5f6a7b")


class ProvenanceEvidenceAnalyzer(Analyzer):
    name = "provenance.evidence"
    version = "1"
    maturity = Maturity.EXPERIMENTAL
    record_type = "ambiguous_provenance"

    def analyze(self, data: AnalysisInput) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        by_run: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for r in data.relations:
            if r["view"] == "INFORMATION":
                by_run[r.get("run_id")].append(r)
        for run_id, rels in sorted(by_run.items(), key=lambda kv: kv[0] or ""):
            if run_id is None:
                continue
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in rels:
                if r["type"] == "CANDIDATE_SOURCE":
                    groups[r["head"][0]].append(r)
            for target, cands in sorted(groups.items()):
                certain = sorted({c for r in cands for c in r["attributes"].get("certain") or []})
                out.append(
                    {
                        "record_id": str(uuid.uuid5(_NS, f"amb|{run_id}|{target}")),
                        "record_type": "ambiguous_provenance",
                        "scope": run_id,
                        "analyzer": self.ref,
                        "maturity": self.maturity.value,
                        "target": target,
                        "target_event": instance_event(target) or target.partition(":")[2],
                        "resolution_status": "AMBIGUOUS",
                        "candidates": [
                            {
                                "node": r["tail"][0],
                                "source_event": r["attributes"].get("source_event"),
                                "evidence_type": r["attributes"].get("evidence_type"),
                                "containment": r["attributes"].get("containment"),
                                "alternatives": r["attributes"].get("alternatives") or [],
                                "relation": r["rel_id"],
                            }
                            for r in sorted(cands, key=lambda r: r["tail"][0])
                        ],
                        "certain_sources": certain,
                        "confidence": "categorical: candidates are not ranked (no calibrated probabilities)",
                    }
                )
            by_type = Counter(r["attributes"].get("evidence_type") for r in rels)
            by_strength = Counter(r["attributes"].get("strength") for r in rels)
            by_mode = Counter(
                r["attributes"].get("mode")
                for r in rels
                if r["type"] not in ("PRODUCES", "CONTAINS_ITEM")
                and r["attributes"].get("strength") != "NONE"
            )
            inputs = Counter(
                r["attributes"].get("resolution")
                for r in rels
                if r["type"] == "CONSUMES" and r["attributes"].get("resolution")
            )
            links = sum(by_mode.values())
            out.append(
                {
                    "record_id": str(uuid.uuid5(_NS, f"sum|{run_id}")),
                    "record_type": "information_evidence",
                    "scope": run_id,
                    "analyzer": self.ref,
                    "maturity": self.maturity.value,
                    "relations_by_evidence_type": dict(sorted(by_type.items(), key=str)),
                    "relations_by_strength": dict(sorted(by_strength.items(), key=str)),
                    "links_by_mode": dict(sorted(by_mode.items(), key=str)),
                    "high_fidelity_share": round(by_mode.get("HIGH_FIDELITY", 0) / links, 3)
                    if links
                    else None,
                    "consumed_values": dict(sorted(inputs.items(), key=str)),
                    "ambiguous_values": len(groups),
                }
            )
        return out
