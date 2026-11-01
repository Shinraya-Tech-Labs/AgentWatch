"""
CMP-003 — HIPAA Compliance Mode.

PHI detection + redaction, plus per-resource access logging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

_PHI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # emails first, so no later pattern matches inside an address
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "email"),
    # US SSN: dashed form, or 9 digits right after an SSN keyword; area 000/666/9xx, group 00
    # and serial 0000 are never issued
    (re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), "ssn"),
    (re.compile(r"\b(?:SSN|social security(?: number)?)[:#]?\s*\d{9}\b", re.I), "ssn"),
    # record numbers: keyword, explicit separator, then an identifier containing a digit
    (
        re.compile(
            r"\b(?:patient(?:\s+id)?|MRN|medical record(?:\s+number)?)(?:\s*[:#]\s*|\s+)"
            r"(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}\b(?!@)",
            re.I,
        ),
        "mrn",
    ),
    (re.compile(r"\b(?:diagnosis|dx)[:\s]+[A-Za-z][\w \-/]{3,}\b", re.I), "diagnosis"),
    (re.compile(r"\bICD-?(?:10|9)[:\s]?[A-Z0-9.]{3,}", re.I), "icd_code"),
    (re.compile(r"\b[A-Z]{2}\d{6,10}\b"), "insurance_id"),
    (re.compile(r"\b(?:DOB|date of birth)[:\s]+\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", re.I), "dob"),
    (re.compile(r"\b(?:HIV|cancer|diabetes|depression|HBV|HCV)\b", re.I), "condition"),
]


def phi_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Public accessor for the compiled (pattern, label) PHI detectors."""
    return list(_PHI_PATTERNS)


@dataclass
class PHIFinding:
    label: str
    excerpt: str


@dataclass
class PHIRedaction:
    redacted: str
    findings: list[PHIFinding] = field(default_factory=list)


def detect_phi(text: str) -> list[PHIFinding]:
    out: list[PHIFinding] = []
    if not text:
        return out
    for pat, label in _PHI_PATTERNS:
        for m in pat.finditer(text):
            out.append(PHIFinding(label=label, excerpt=m.group()[:80]))
    return out


def redact_phi(text: str) -> PHIRedaction:
    findings: list[PHIFinding] = []
    s = text or ""
    for pat, label in _PHI_PATTERNS:
        for m in pat.finditer(s):
            findings.append(PHIFinding(label=label, excerpt=m.group()[:80]))
        s = pat.sub(f"[PHI:{label.upper()}]", s)
    return PHIRedaction(redacted=s, findings=findings)


@dataclass
class AccessLogEntry:
    resource: str
    user_id: str
    action: str  # read | write | delete
    when: datetime = field(default_factory=lambda: datetime.now(UTC))


class HIPAAEngine:
    """Track all PHI accesses for audit."""

    def __init__(self) -> None:
        self._log: list[AccessLogEntry] = []

    def log_access(self, resource: str, user_id: str, action: str) -> AccessLogEntry:
        entry = AccessLogEntry(resource=resource, user_id=user_id, action=action)
        self._log.append(entry)
        return entry

    def access_log(self, *, resource: str | None = None) -> list[AccessLogEntry]:
        if resource is None:
            return list(self._log)
        return [e for e in self._log if e.resource == resource]


__all__ = [
    "PHIFinding",
    "PHIRedaction",
    "AccessLogEntry",
    "HIPAAEngine",
    "detect_phi",
    "phi_patterns",
    "redact_phi",
]
