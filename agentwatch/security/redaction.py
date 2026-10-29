"""
CMP-003 / CMP-004 — PII/PHI Redaction for telemetry (issue #398).

Unified scrubber that masks PII and PHI as ``[REDACTED]`` before telemetry is
persisted, so SSNs, emails, medical diagnoses, and similar sensitive data never
reach the database or exported reports.

Detection uses Microsoft Presidio (``presidio-analyzer`` / ``presidio-anonymizer``)
when it is installed for richer NER-based coverage, and otherwise falls back to
the regex detectors already maintained by the GDPR (CMP-001) and HIPAA (CMP-003)
engines — so there is a single source of truth for the patterns and no hard
dependency on the heavyweight Presidio stack.
"""

from __future__ import annotations

import re
from typing import Any

from agentwatch.governance.gdpr import pii_patterns
from agentwatch.governance.hipaa import phi_patterns

MASK = "[REDACTED]"
SECRET_MASK = "[REDACTED_API_KEY]"

# Pre-compiled secret patterns preserving format prefix identifiers (Issue #744)
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(Bearer\s+)(?!\[REDACTED_API_KEY\])[A-Za-z0-9\-_=.]+\b"),
        r"\g<1>[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"\b(sk-ant-)(?!\[REDACTED_API_KEY\])[A-Za-z0-9_\-]{20,}\b"),
        r"\g<1>[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"\b(sk-proj-)(?!\[REDACTED_API_KEY\])[A-Za-z0-9_\-]{20,}\b"),
        r"\g<1>[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"\b(sk-)(?!ant-|proj-|\[REDACTED_API_KEY\])[A-Za-z0-9_\-]{20,}\b"),
        r"\g<1>[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr)_)(?!\[REDACTED_API_KEY\])[A-Za-z0-9]{20,}\b"),
        r"\g<1>[REDACTED_API_KEY]",
    ),
    (
        re.compile(r"\b(AKIA|ASIA)(?!\[REDACTED_API_KEY\])[0-9A-Z]{16}\b"),
        r"\g<1>[REDACTED_API_KEY]",
    ),
)


def redact_secrets(payload: Any) -> Any:
    """Recursively scrub API keys, auth tokens, and environment secrets from payloads.

    Replaces matched credentials with ``[REDACTED_API_KEY]`` while preserving
    informative format prefix identifiers (e.g. ``sk-proj-[REDACTED_API_KEY]``).
    """
    if isinstance(payload, str):
        out = payload
        for pattern, replacement in SECRET_PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    if isinstance(payload, dict):
        return {redact_secrets(k): redact_secrets(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_secrets(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_secrets(item) for item in payload)
    if isinstance(payload, set):
        return {redact_secrets(item) for item in payload}
    return payload

# Regex fallback: PII (email/SSN/credit-card/phone/IP/keys) + PHI (MRN, ICD,
# diagnosis, conditions), reusing the governance engines' public pattern
# accessors so there is a single source of truth.
_FALLBACK_PATTERNS = pii_patterns() + phi_patterns()


def _load_presidio() -> tuple[Any, Any] | None:
    """Return (analyzer, anonymizer) if Presidio is importable, else None."""
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
    except Exception:  # noqa: BLE001 — any import/initialization failure → fallback
        return None
    try:
        return AnalyzerEngine(), AnonymizerEngine()
    except Exception:  # noqa: BLE001
        return None


class Redactor:
    """Mask PII/PHI in text and nested payloads.

    Parameters
    ----------
    use_presidio:
        When True (default) use Presidio if it is installed; otherwise fall back
        to the regex detectors. Pass False to force the regex path.
    mask:
        The replacement token. Defaults to ``[REDACTED]``.
    """

    def __init__(self, *, use_presidio: bool = True, mask: str = MASK) -> None:
        self.mask = mask
        self._engines = _load_presidio() if use_presidio else None

    @property
    def backend(self) -> str:
        return "presidio" if self._engines else "regex"

    def redact_text(self, text: str | None) -> str | None:
        if not text:
            return text
        if self._engines:
            analyzer, anonymizer = self._engines
            from presidio_anonymizer.entities import OperatorConfig

            results = analyzer.analyze(text=text, language="en")
            if not results:
                return text
            return anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators={"DEFAULT": OperatorConfig("replace", {"new_value": self.mask})},
            ).text
        out = text
        for pattern, _label in _FALLBACK_PATTERNS:
            out = pattern.sub(self.mask, out)
        return out

    def redact_payload(self, payload: Any) -> Any:
        """Recursively redact every string in a dict/list/scalar payload.

        Both keys and values are scrubbed, since a key can itself be sensitive
        (e.g. an email address used as a map key).
        """
        if isinstance(payload, str):
            return self.redact_text(payload)
        if isinstance(payload, dict):
            return {self.redact_payload(k): self.redact_payload(v) for k, v in payload.items()}
        if isinstance(payload, (list, tuple)):
            return type(payload)(self.redact_payload(v) for v in payload)
        return payload


_default_redactor: Redactor | None = None


def default_redactor() -> Redactor:
    """Process-wide default redactor (lazily constructed)."""
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = Redactor()
    return _default_redactor


def redact(text: str | None) -> str | None:
    """Convenience wrapper around :meth:`Redactor.redact_text`."""
    return default_redactor().redact_text(text)


def redact_payload(payload: Any) -> Any:
    """Convenience wrapper around :meth:`Redactor.redact_payload`."""
    return default_redactor().redact_payload(payload)


def redact_tool_call(tool_call: Any) -> Any:
    """Return a copy of a ``ToolCallData`` with its command/arguments scrubbed."""
    r = default_redactor()
    return tool_call.model_copy(
        update={
            "raw_command": r.redact_text(tool_call.raw_command),
            "arguments": r.redact_payload(tool_call.arguments),
        }
    )


__all__ = [
    "MASK",
    "SECRET_MASK",
    "SECRET_PATTERNS",
    "Redactor",
    "default_redactor",
    "redact",
    "redact_payload",
    "redact_secrets",
    "redact_tool_call",
]
