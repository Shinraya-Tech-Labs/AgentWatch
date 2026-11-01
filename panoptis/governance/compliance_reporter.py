"""Compliance reporting over governance and session data."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from panoptis.governance.engine import AuditEventType, GovernanceEngine

if TYPE_CHECKING:
    # Imported lazily to break the governance <-> tracing import cycle that
    # otherwise breaks a cold `import panoptis.cli.main`. Only used as a
    # type annotation, so the runtime import is unnecessary.
    from panoptis.tracing.collector import TraceCollector


@dataclass
class ComplianceReport:
    generated_at: str
    summary: dict[str, Any]
    findings: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "findings": self.findings,
        }

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "audit_id",
                "timestamp",
                "principal_id",
                "event_type",
                "resource",
                "action",
                "allowed",
                "session_id",
                "details",
            ]
        )
        for entry in self.findings.get("sample_denials", []):
            writer.writerow(
                [
                    entry.get("audit_id", ""),
                    entry.get("timestamp", ""),
                    entry.get("principal_id", ""),
                    entry.get("event_type", ""),
                    entry.get("resource", ""),
                    entry.get("action", ""),
                    entry.get("allowed", ""),
                    entry.get("session_id", ""),
                    entry.get("details", ""),
                ]
            )
        return buf.getvalue()


class ComplianceReporter:
    def __init__(self, governance: GovernanceEngine, collector: TraceCollector | None = None):
        self._governance = governance
        self._collector = collector

    def generate(self) -> ComplianceReport:
        audit_log = self._governance.get_audit_log(limit=10_000)
        denied = [entry for entry in audit_log if not entry.allowed]
        overrides = [
            entry for entry in audit_log if entry.event_type == AuditEventType.SAFETY_OVERRIDE
        ]
        sessions = self._collector.list_sessions(limit=10_000) if self._collector else []
        findings = {
            "permission_denials": len(denied),
            "safety_overrides": len(overrides),
            "active_sessions": sum(1 for session in sessions if session.status.value == "running"),
            "sample_denials": [entry.to_dict() for entry in denied[:20]],
        }
        summary = {
            "total_audit_entries": len(audit_log),
            "total_sessions": len(sessions),
        }
        return ComplianceReport(
            generated_at=datetime.now(UTC).isoformat(),
            summary=summary,
            findings=findings,
        )

    def generate_csv(self, *, include_allowed: bool = False) -> str:
        total = len(self._governance._audit_log)
        audit_log = self._governance.get_audit_log(limit=total)
        if not include_allowed:
            audit_log = [entry for entry in audit_log if not entry.allowed]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "audit_id",
                "timestamp",
                "principal_id",
                "event_type",
                "resource",
                "action",
                "allowed",
                "session_id",
                "details",
            ]
        )
        for entry in audit_log:
            writer.writerow(
                [
                    entry.audit_id,
                    entry.timestamp.isoformat(),
                    entry.principal_id or "",
                    entry.event_type.value,
                    entry.resource,
                    entry.action,
                    entry.allowed,
                    entry.session_id or "",
                    entry.details,
                ]
            )
        return buf.getvalue()
