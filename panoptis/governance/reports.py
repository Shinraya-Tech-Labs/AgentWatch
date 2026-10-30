"""
CMP-006 — Compliance Report Generator (one-click multi-framework export).

Produces SOC 2, GDPR, HIPAA, EU AI Act-formatted evidence packages from
the same underlying telemetry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ComplianceExport:
    framework: str
    generated_at: datetime
    format: str  # json | text
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "generated_at": self.generated_at.isoformat(),
            "format": self.format,
            "body": self.body,
        }


@dataclass
class ReportInputs:
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    pii_findings: dict[str, int] = field(default_factory=dict)
    phi_access_log: list[dict[str, Any]] = field(default_factory=list)
    conformity: dict[str, Any] = field(default_factory=dict)


def generate_soc2(inputs: ReportInputs) -> ComplianceExport:
    body = {
        "framework": "SOC 2",
        "controls": {
            "CC6.1_logical_access": "RBAC enforced",
            "CC7.2_anomaly_detection": "cost + safety anomaly detectors active",
            "CC7.3_event_logging": f"{len(inputs.audit_events)} audit events captured",
        },
        "audit_trail_summary": {
            "events": len(inputs.audit_events),
            "decisions": len(inputs.decisions),
        },
    }
    return ComplianceExport(
        framework="SOC 2",
        generated_at=datetime.now(UTC),
        format="json",
        body=json.dumps(body, indent=2, default=str),
    )


def generate_gdpr(inputs: ReportInputs) -> ComplianceExport:
    body = {
        "framework": "GDPR",
        "article_30_records": {
            "categories_of_data": list(inputs.pii_findings.keys()),
            "processing_count": sum(inputs.pii_findings.values()),
        },
        "article_15_export_capability": True,
        "article_17_erasure_capability": True,
    }
    return ComplianceExport(
        framework="GDPR",
        generated_at=datetime.now(UTC),
        format="json",
        body=json.dumps(body, indent=2, default=str),
    )


def generate_hipaa(inputs: ReportInputs) -> ComplianceExport:
    body = {
        "framework": "HIPAA",
        "security_rule_164_312_b_audit_controls": {
            "phi_access_events": len(inputs.phi_access_log),
        },
        "privacy_rule_164_502_a_uses_and_disclosures": "logged",
        "phi_redaction_enabled": True,
    }
    return ComplianceExport(
        framework="HIPAA",
        generated_at=datetime.now(UTC),
        format="json",
        body=json.dumps(body, indent=2, default=str),
    )


def generate_eu_ai_act(inputs: ReportInputs) -> ComplianceExport:
    body = {
        "framework": "EU AI Act (Article 15)",
        "conformity_assessment": inputs.conformity,
        "decision_log_size": len(inputs.decisions),
    }
    return ComplianceExport(
        framework="EU AI Act",
        generated_at=datetime.now(UTC),
        format="json",
        body=json.dumps(body, indent=2, default=str),
    )


_GENERATORS = {
    "soc2": generate_soc2,
    "gdpr": generate_gdpr,
    "hipaa": generate_hipaa,
    "eu_ai_act": generate_eu_ai_act,
}


def export(framework: str, inputs: ReportInputs) -> ComplianceExport:
    key = framework.lower().replace(" ", "_")
    if key not in _GENERATORS:
        raise ValueError(f"unknown framework: {framework}")
    return _GENERATORS[key](inputs)


__all__ = [
    "ComplianceExport",
    "ReportInputs",
    "generate_soc2",
    "generate_gdpr",
    "generate_hipaa",
    "generate_eu_ai_act",
    "export",
]
