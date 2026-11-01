"""
CMP-004 — EU AI Act Article 15 Package.

Technical documentation generator, decision logs, conformity assessment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class TechnicalDocumentation:
    system_name: str
    intended_purpose: str
    risk_category: str  # minimal | limited | high | unacceptable
    data_governance: dict[str, str] = field(default_factory=dict)
    training_data_summary: str = ""
    accuracy_metrics: dict[str, float] = field(default_factory=dict)
    robustness_evidence: list[str] = field(default_factory=list)
    human_oversight_description: str = ""
    transparency_disclosures: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["generated_at"] = self.generated_at.isoformat()
        return d


@dataclass
class DecisionLogEntry:
    when: datetime
    decision_id: str
    inputs_hash: str
    outputs_hash: str
    confidence: float
    safety_checks_passed: bool
    human_oversight_required: bool
    explanation: str


@dataclass
class ConformityAssessment:
    requirements_met: list[str] = field(default_factory=list)
    requirements_missing: list[str] = field(default_factory=list)
    score: float = 0.0
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirements_met": self.requirements_met,
            "requirements_missing": self.requirements_missing,
            "score": self.score,
            "verdict": self.verdict,
        }


_ART_15_REQUIREMENTS = [
    "risk_management_system",
    "data_governance",
    "technical_documentation",
    "record_keeping",
    "transparency",
    "human_oversight",
    "accuracy_robustness_cybersecurity",
]


class EUAIActPackage:
    def __init__(self) -> None:
        self._decision_log: list[DecisionLogEntry] = []
        self._doc: TechnicalDocumentation | None = None

    def set_documentation(self, doc: TechnicalDocumentation) -> None:
        self._doc = doc

    def log_decision(self, entry: DecisionLogEntry) -> None:
        self._decision_log.append(entry)

    def decision_log(self) -> list[DecisionLogEntry]:
        return list(self._decision_log)

    def assess(self) -> ConformityAssessment:
        if self._doc is None:
            return ConformityAssessment(
                requirements_missing=list(_ART_15_REQUIREMENTS),
                requirements_met=[],
                score=0.0,
                verdict="no_documentation",
            )

        met: list[str] = []
        missing: list[str] = []

        if self._doc.data_governance:
            met.append("data_governance")
        else:
            missing.append("data_governance")

        met.append("technical_documentation")

        if self._decision_log:
            met.append("record_keeping")
        else:
            missing.append("record_keeping")

        if self._doc.transparency_disclosures:
            met.append("transparency")
        else:
            missing.append("transparency")

        if self._doc.human_oversight_description:
            met.append("human_oversight")
        else:
            missing.append("human_oversight")

        if self._doc.accuracy_metrics and self._doc.robustness_evidence:
            met.append("accuracy_robustness_cybersecurity")
        else:
            missing.append("accuracy_robustness_cybersecurity")

        if self._doc.risk_category:
            met.append("risk_management_system")
        else:
            missing.append("risk_management_system")

        score = len(met) / len(_ART_15_REQUIREMENTS)
        verdict = "compliant" if score >= 0.85 else "partial" if score >= 0.6 else "non_compliant"

        return ConformityAssessment(
            requirements_met=met,
            requirements_missing=missing,
            score=score,
            verdict=verdict,
        )


__all__ = [
    "TechnicalDocumentation",
    "DecisionLogEntry",
    "ConformityAssessment",
    "EUAIActPackage",
]
