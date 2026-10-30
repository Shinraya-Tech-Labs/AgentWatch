"""
CMP-009 — ISO 42001 AI Management System.

AI risk assessments, documented governance, incident tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskAssessment:
    risk_id: str
    description: str
    likelihood: float  # 0..1
    impact: float  # 0..1
    treatment: str = ""
    owner: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def score(self) -> float:
        return round(self.likelihood * self.impact, 3)


@dataclass
class Incident:
    incident_id: str
    description: str
    severity: IncidentSeverity
    occurred_at: datetime
    resolved_at: datetime | None = None
    remediation: str = ""

    @property
    def open(self) -> bool:
        return self.resolved_at is None


@dataclass
class GovernanceDoc:
    title: str
    version: str
    summary: str
    last_reviewed: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ISO42001Report:
    risks: list[RiskAssessment]
    incidents: list[Incident]
    governance_docs: list[GovernanceDoc]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risks": [
                {**r.__dict__, "created_at": r.created_at.isoformat(), "score": r.score}
                for r in self.risks
            ],
            "incidents": [
                {
                    **i.__dict__,
                    "severity": i.severity.value,
                    "occurred_at": i.occurred_at.isoformat(),
                    "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                    "open": i.open,
                }
                for i in self.incidents
            ],
            "governance_docs": [
                {**d.__dict__, "last_reviewed": d.last_reviewed.isoformat()}
                for d in self.governance_docs
            ],
            "metrics": self.metrics,
        }


class ISO42001AMS:
    """In-memory AI Management System per ISO/IEC 42001."""

    def __init__(self) -> None:
        self._risks: list[RiskAssessment] = []
        self._incidents: list[Incident] = []
        self._docs: list[GovernanceDoc] = []

    def add_risk(self, risk: RiskAssessment) -> None:
        self._risks.append(risk)

    def add_incident(self, incident: Incident) -> None:
        self._incidents.append(incident)

    def add_governance_doc(self, doc: GovernanceDoc) -> None:
        self._docs.append(doc)

    def report(self) -> ISO42001Report:
        open_inc = sum(1 for i in self._incidents if i.open)
        high_risks = sum(1 for r in self._risks if r.score >= 0.5)
        return ISO42001Report(
            risks=list(self._risks),
            incidents=list(self._incidents),
            governance_docs=list(self._docs),
            metrics={
                "open_incidents": open_inc,
                "total_incidents": len(self._incidents),
                "high_risks": high_risks,
                "total_risks": len(self._risks),
                "governance_docs": len(self._docs),
            },
        )


__all__ = [
    "RiskAssessment",
    "Incident",
    "IncidentSeverity",
    "GovernanceDoc",
    "ISO42001Report",
    "ISO42001AMS",
]
