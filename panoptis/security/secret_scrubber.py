"""Multi-provider secret scrubber for evidence payloads."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_[REDACTED_SECRET]"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_[REDACTED_SECRET]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_[REDACTED_SECRET]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA[REDACTED_SECRET]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "sk-[REDACTED_SECRET]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "xox[REDACTED_SECRET]"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"), "https://hooks.slack.com/services/[REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]+", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED_JWT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
]

_DB_URI_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^:\s]+):([^@\s]+)@")


def scrub_string(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    value = _DB_URI_RE.sub(r"\1:[REDACTED_PASSWORD]@", value)
    return value


def scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(v) for v in value)
    return value