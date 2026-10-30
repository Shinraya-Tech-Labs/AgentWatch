"""
CMP-008 — Compliance attribution over an asserted decision graph (v0.2, deprecated).

Despite the historical name, the "causal_chain" below is an upstream walk over edges that
callers *asserted* in :class:`~panoptis.memory.causal_graph.CausalGraph`; no causal
evidence is involved. Reports now say so explicitly (``evidence_class``). Use v3
``panoptis causes`` for dependency/correlation/intervention evidence kept separate.

Given an adverse outcome: full causal chain.
Which policy → which action → what remediation. Machine-readable report.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from panoptis.memory.causal_graph import CausalGraph


@dataclass
class AdverseOutcome:
    outcome_id: str
    description: str
    severity: str  # low | medium | high | critical
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AttributionStep:
    actor: str
    action: str
    policy_id: str | None
    timestamp: str
    excerpt: str


@dataclass
class AttributionReport:
    outcome: AdverseOutcome
    chain: list[AttributionStep]
    remediation: list[str]
    machine_readable: dict[str, Any]
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return self.machine_readable


def attribute(
    outcome: AdverseOutcome,
    graph: CausalGraph,
    *,
    remediation: list[str] | None = None,
) -> AttributionReport:
    chain: list[AttributionStep] = []
    # Walk upstream from the outcome's node id if it exists
    nodes = list(graph.explain(outcome.outcome_id))
    for n in nodes:
        chain.append(
            AttributionStep(
                actor=n.metadata.get("agent_id", "system"),
                action=n.text,
                policy_id=n.metadata.get("policy_id"),
                timestamp=n.timestamp.isoformat(),
                excerpt=n.text[:120],
            )
        )

    remediation_steps = remediation or _default_remediation(outcome.severity)
    machine = {
        "outcome_id": outcome.outcome_id,
        "description": outcome.description,
        "severity": outcome.severity,
        "occurred_at": outcome.occurred_at.isoformat(),
        "causal_chain": [step.__dict__ for step in chain],
        "evidence_class": "ASSERTED_EDGES_UNVALIDATED",
        "note": "chain = upstream walk over caller-asserted edges; not evidence of causation",
        "remediation": remediation_steps,
    }
    body = json.dumps(machine, sort_keys=True, default=str).encode()
    sig = "sha256:" + hashlib.sha256(body).hexdigest()
    machine["signature"] = sig
    return AttributionReport(
        outcome=outcome,
        chain=chain,
        remediation=remediation_steps,
        machine_readable=machine,
        signature=sig,
    )


def _default_remediation(severity: str) -> list[str]:
    base = ["preserve_evidence", "notify_security", "open_incident_ticket"]
    if severity in ("high", "critical"):
        base += ["rotate_secrets", "review_recent_sessions", "notify_dpo"]
    return base


__all__ = [
    "AdverseOutcome",
    "AttributionStep",
    "AttributionReport",
    "attribute",
]
