"""Multi-provider secret scrubber for evidence payloads."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_[REDACTED_SECRET]"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_[REDACTED_SECRET]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_[REDACTED_SECRET]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(postgres|mysql|mongodb)://([^:\s]+):([^@\s]+)@"), lambda m: f"{m.group(1)}://{m.group(2)}:[REDACTED_PASSWORD]@"),
]


def scrub_string(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        if callable(replacement):
            value = pattern.sub(replacement, value)
        else:
            value = pattern.sub(replacement, value)
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