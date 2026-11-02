"""Edge redaction and payload policy.

Redaction runs *before* an observation is stored and produces a new payload plus a
:class:`RedactionManifest`. The input object is never modified (it is deep-copied via
canonical JSON first), which removes the class of bug where redaction mutated an event
shared with other handlers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from panoptis.evidence.canonical import sha256_hex, to_jsonable
from panoptis.evidence.model import RedactionManifest

DETECTOR = "panoptis.edge_redactor"
DETECTOR_VERSION = "1"

# Secret patterns: high-precision formats only. Each maps to a category label.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "secret.private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("secret.aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("secret.anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("secret.openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("secret.github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("secret.slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("secret.bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*")),
    (
        "secret.jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    ),
    # a credential assigned to a credential-named key: KEY=value, "key": "value", key: value
    (
        "secret.credential_assignment",
        re.compile(
            r"(?i)\b(?:aws_secret_access_key|secret_access_key|client_secret|api[_-]?secret|"
            r"api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?token|password|passwd)"
            r"[\"']?\s*[:=]\s*[\"']?(?!\[REDACTED)[^\s\"',;]{8,}"
        ),
    ),
)

PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pii.email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("pii.us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("pii.credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
)

# Keys whose values are always treated as secrets regardless of content.
SENSITIVE_KEYS = re.compile(
    r"(?i)^(api[_-]?key|authorization|password|passwd|secret|token|access[_-]?token|x-api-key|cookie)$"
)


@dataclass(frozen=True)
class PayloadPolicy:
    """How much of a payload is captured.

    capture:
      * ``full``   — everything (after redaction)
      * ``preview``— strings truncated to ``max_string_chars``
      * ``hash``   — strings replaced by ``{"$sha256": ..., "$len": ...}``; structure kept
    """

    capture: str = "full"
    max_string_chars: int = 2000
    redact_secrets: bool = True
    redact_pii: bool = False
    exempt_keys: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.capture not in {"full", "preview", "hash"}:
            raise ValueError(f"unknown capture mode {self.capture!r}")


DEFAULT_POLICY = PayloadPolicy()


class _Counter:
    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], int] = {}

    def add(self, path: str, category: str, n: int = 1) -> None:
        self.hits[(path, category)] = self.hits.get((path, category), 0) + n


def _scrub_string(value: str, path: str, policy: PayloadPolicy, counter: _Counter) -> Any:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    if policy.redact_secrets:
        patterns.extend(SECRET_PATTERNS)
    if policy.redact_pii:
        patterns.extend(PII_PATTERNS)
    for category, pattern in patterns:
        value, n = pattern.subn(f"[REDACTED:{category}]", value)
        if n:
            counter.add(path, category, n)
    if policy.capture == "hash":
        return {"$sha256": sha256_hex(value), "$len": len(value)}
    if policy.capture == "preview" and len(value) > policy.max_string_chars:
        counter.add(path, "policy.truncated")
        return (
            value[: policy.max_string_chars] + f"…[+{len(value) - policy.max_string_chars} chars]"
        )
    return value


def _walk(node: Any, path: str, policy: PayloadPolicy, counter: _Counter) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, val in node.items():
            child = f"{path}.{key}"
            if (
                policy.redact_secrets
                and SENSITIVE_KEYS.match(str(key))
                and key not in policy.exempt_keys
                and val not in (None, "")
            ):
                out[key] = "[REDACTED:secret.sensitive_key]"
                counter.add(child, "secret.sensitive_key")
            else:
                out[key] = _walk(val, child, policy, counter)
        return out
    if isinstance(node, list):
        return [_walk(v, f"{path}[{i}]", policy, counter) for i, v in enumerate(node)]
    if isinstance(node, str):
        return _scrub_string(node, path, policy, counter)
    return node


def redact_payload(
    payload: Any, policy: PayloadPolicy = DEFAULT_POLICY
) -> tuple[Any, RedactionManifest | None]:
    """Return ``(new_payload, manifest)``. ``payload`` itself is never modified."""
    copy = to_jsonable(payload)  # deep, independent copy in plain JSON types
    counter = _Counter()
    result = _walk(copy, "$", policy, counter)
    if not counter.hits:
        return result, None
    manifest = RedactionManifest(
        detector=DETECTOR,
        detector_version=DETECTOR_VERSION,
        redactions=tuple(sorted((p, c, n) for (p, c), n in counter.hits.items())),
    )
    return result, manifest
