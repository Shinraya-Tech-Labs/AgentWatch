"""Relations (hyperedges) and the three graph views.

* EXECUTION  — what happened and what structurally depended on what.
* INFORMATION — how information (artifacts) moved and was transformed.
* CAUSAL     — what appears to have influenced what; every relation carries an
  evidence class and is only ever created from hypotheses/experiments, never from
  parent-child structure or temporal order.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

REL_NAMESPACE = uuid.UUID("9a1b2c3d-4e5f-5a6b-8c7d-0e1f2a3b4c5d")


class View(StrEnum):
    EXECUTION = "EXECUTION"
    INFORMATION = "INFORMATION"
    CAUSAL = "CAUSAL"


class Basis(StrEnum):
    DECLARED = "DECLARED"  # the source declared it (parent span id, explicit link, input/output)
    CONTENT_MATCH = "CONTENT_MATCH"  # identical or contained content
    KEY_MATCH = "KEY_MATCH"  # same declared key (e.g. memory store + key)
    HEURISTIC = "HEURISTIC"  # rule over observed facts, labelled as such
    STATISTICAL = "STATISTICAL"
    INTERVENTIONAL = "INTERVENTIONAL"
    HYPOTHESIS = "HYPOTHESIS"


class EvidenceClass(StrEnum):
    CORRELATIONAL = "CORRELATIONAL"
    OBSERVATIONAL = "OBSERVATIONAL"
    QUASI_CAUSAL = "QUASI_CAUSAL"
    INTERVENTIONAL = "INTERVENTIONAL"
    VERIFIED = "VERIFIED"


EVIDENCE_RANK = {
    None: 0,
    EvidenceClass.CORRELATIONAL: 1,
    EvidenceClass.OBSERVATIONAL: 2,
    EvidenceClass.QUASI_CAUSAL: 3,
    EvidenceClass.INTERVENTIONAL: 4,
    EvidenceClass.VERIFIED: 5,
}


class RelType(StrEnum):
    # execution
    CONTAINS = "CONTAINS"
    DEPENDS_ON = "DEPENDS_ON"
    RESPONDS_TO = "RESPONDS_TO"
    RETRIES = "RETRIES"
    PERFORMED_BY = "PERFORMED_BY"
    TARGETS = "TARGETS"
    DELEGATES_TO = "DELEGATES_TO"
    # information
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    DERIVES_FROM = "DERIVES_FROM"
    # identical/contained content in an artifact that entered from outside the system
    # (retrieval, external I/O, external input): a similarity, not an information flow
    MATCHES_CONTENT = "MATCHES_CONTENT"
    CONTAINS_ITEM = "CONTAINS_ITEM"
    # a possible source of a value that the evidence does not single out (ambiguous
    # provenance, ADR-0017): NOT an information flow, excluded from traversal by default
    CANDIDATE_SOURCE = "CANDIDATE_SOURCE"
    WRITES_TO = "WRITES_TO"
    READS_FROM = "READS_FROM"
    TRANSFERS = "TRANSFERS"
    # causal
    CAUSES = "CAUSES"
    INFLUENCES = "INFLUENCES"
    ENABLES = "ENABLES"
    PREVENTS = "PREVENTS"


class InfoEvidence(StrEnum):
    """What supports an INFORMATION relation (ADR-0017), strongest first."""

    DECLARED_OUTPUT = "DECLARED_OUTPUT"  # the event declared it produced the value
    DECLARED_INPUT = "DECLARED_INPUT"  # the event declared it consumed the value
    DECLARED_REFERENCE = "DECLARED_REFERENCE"  # sensor-declared source (incl. runtime identity)
    CORRELATION_LINEAGE = "CORRELATION_LINEAGE"  # declared parent/linked span had the value
    MEMORY_REFERENCE = "MEMORY_REFERENCE"  # memory read returned the written value
    MESSAGE_REFERENCE = "MESSAGE_REFERENCE"  # same value produced on the same channel
    ARTIFACT_REFERENCE = "ARTIFACT_REFERENCE"  # same value written to the same named object
    TEMPORAL_CONTENT_MATCH = "TEMPORAL_CONTENT_MATCH"  # identical value produced earlier
    CONTENT_CONTAINMENT = "CONTENT_CONTAINMENT"  # constructed value contains earlier text
    CONTENT_MATCH_ONLY = "CONTENT_MATCH_ONLY"  # similarity only; never an information flow


# Deterministic evidence levels (not probabilities). STRONG relations come from what a sensor
# declared ("high-fidelity mode"); the others are inferred ("best-effort mode").
_STRENGTH = {
    InfoEvidence.DECLARED_OUTPUT: "STRONG",
    InfoEvidence.DECLARED_INPUT: "STRONG",
    InfoEvidence.DECLARED_REFERENCE: "STRONG",
    InfoEvidence.CORRELATION_LINEAGE: "MEDIUM",
    InfoEvidence.MEMORY_REFERENCE: "MEDIUM",
    InfoEvidence.MESSAGE_REFERENCE: "MEDIUM",
    InfoEvidence.ARTIFACT_REFERENCE: "MEDIUM",
    InfoEvidence.TEMPORAL_CONTENT_MATCH: "WEAK",
    InfoEvidence.CONTENT_CONTAINMENT: "WEAK",
    InfoEvidence.CONTENT_MATCH_ONLY: "NONE",
}
STRENGTH_RANK = {"NONE": 0, "WEAK": 1, "MEDIUM": 2, "STRONG": 3}


def strength_of(evidence: InfoEvidence) -> str:
    return _STRENGTH[evidence]


def node_event(event_id: str) -> str:
    return f"event:{event_id}"


def node_artifact(artifact_id: str) -> str:
    return f"artifact:{artifact_id}"


def node_entity(canonical: str) -> str:
    return f"entity:{canonical}"


def split_node(node: str) -> tuple[str, str]:
    kind, _, rest = node.partition(":")
    return kind, rest


def make_relation(
    view: View,
    rtype: RelType,
    tail: list[str],
    head: list[str],
    *,
    basis: Basis,
    confidence: float = 1.0,
    run_id: str | None,
    evidence: list[str] | None = None,
    derived_by: str,
    evidence_class: EvidenceClass | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if view == View.CAUSAL and evidence_class is None:
        raise ValueError("CAUSAL relations require an evidence class")
    if view != View.CAUSAL and rtype in (
        RelType.CAUSES,
        RelType.INFLUENCES,
        RelType.ENABLES,
        RelType.PREVENTS,
    ):
        raise ValueError(f"{rtype} is a causal relation type and belongs to the CAUSAL view")
    rel_id = str(
        uuid.uuid5(
            REL_NAMESPACE, f"{view}|{rtype}|{','.join(tail)}|{','.join(head)}|{derived_by}|{run_id}"
        )
    )
    return {
        "rel_id": rel_id,
        "view": view.value,
        "type": rtype.value,
        "tail": tail,
        "head": head,
        "basis": basis.value,
        "evidence_class": evidence_class.value if evidence_class else None,
        "confidence": round(float(confidence), 4),
        "run_id": run_id,
        "evidence": evidence or [],
        "derived_by": derived_by,
        "attributes": attributes or {},
    }
