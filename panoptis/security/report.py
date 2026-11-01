"""
SAF-009 — Security Audit Report Generator.

Per-session and aggregate security posture reports. Exports as JSON / PDF.
PDF is built with `reportlab` if available; otherwise falls back to a
formatted plain-text report.
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from panoptis.core.schema import AgentEvent
from panoptis.security.owasp import (
    OwaspScan,
    validate_owasp,
)


@dataclass
class SecurityReport:
    session_id: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    owasp: OwaspScan = field(default_factory=OwaspScan)
    blocked_actions: int = 0
    exfil_attempts: int = 0
    injection_attempts: int = 0
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "generated_at": self.generated_at.isoformat(),
            "owasp": self.owasp.to_dict(),
            "blocked_actions": self.blocked_actions,
            "exfil_attempts": self.exfil_attempts,
            "injection_attempts": self.injection_attempts,
            "summary": self.summary,
        }


def generate(session_id: str, events: list[AgentEvent]) -> SecurityReport:
    from panoptis.core.injection import scan_text  # noqa: PLC0415
    from panoptis.security.exfiltration import detect as detect_exfil  # noqa: PLC0415

    report = SecurityReport(session_id=session_id)
    report.owasp = validate_owasp(events)

    blocked = sum(1 for e in events if e.is_blocked)
    exfil = sum(len(detect_exfil(e)) for e in events)
    injection = sum(
        1
        for e in events
        if (
            e.tool_result and e.tool_result.output and scan_text(str(e.tool_result.output)).detected
        )
        or (e.prompt_preview and scan_text(e.prompt_preview).detected)
    )

    report.blocked_actions = blocked
    report.exfil_attempts = exfil
    report.injection_attempts = injection

    report.summary = {
        "owasp_score": report.owasp.score,
        "n_events": len(events),
        "verdict": _verdict(report),
    }
    return report


def _verdict(report: SecurityReport) -> str:
    if report.owasp.score < 60 or report.exfil_attempts > 0:
        return "CRITICAL"
    if report.blocked_actions > 5 or report.injection_attempts > 0:
        return "WARN"
    return "OK"


def to_pdf_bytes(report: SecurityReport) -> bytes:
    """Render as PDF if reportlab is installed; else as text."""
    try:
        from reportlab.lib.pagesizes import letter  # noqa: PLC0415
        from reportlab.pdfgen import canvas  # noqa: PLC0415

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setTitle(f"AgentWatch Security Report — {report.session_id}")
        y = 750
        for line in _text_lines(report):
            c.drawString(50, y, line[:90])
            y -= 14
            if y < 50:
                c.showPage()
                y = 750
        c.save()
        return buf.getvalue()
    except ImportError:
        return ("\n".join(_text_lines(report))).encode("utf-8")


def _text_lines(report: SecurityReport) -> list[str]:
    lines = [
        "AgentWatch Security Report",
        f"Session: {report.session_id}",
        f"Generated: {report.generated_at.isoformat()}",
        f"Verdict: {report.summary.get('verdict')}",
        f"OWASP score: {report.owasp.score}",
        f"Blocked actions: {report.blocked_actions}",
        f"Exfil attempts: {report.exfil_attempts}",
        f"Injection attempts: {report.injection_attempts}",
        "",
        "OWASP findings:",
    ]
    by_v: Counter = Counter(f.vector.value for f in report.owasp.findings)
    for v, c in by_v.most_common():
        lines.append(f"  - {v}: {c}")
    return lines


__all__ = ["SecurityReport", "generate", "to_pdf_bytes"]
