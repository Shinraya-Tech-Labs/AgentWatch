"""
SAF-008 — Exfiltration Attempt Detector.

Detect when agent tries to send data externally. Block and alert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from panoptis.core.schema import AgentEvent

_EXFIL_PATTERNS = [
    re.compile(r"curl\b[^\n]*?https?://(?!localhost|127\.|0\.0\.0\.0)", re.I),
    re.compile(r"\b(?:ncat|nc)\s+(?:-\S+\s+)*[a-z0-9.-]+\s+\d+", re.I),  # netcat / ncat
    re.compile(r"wget\b[^\n]*?--post-(?:data|file)", re.I),
    re.compile(r"requests\.(post|put)\(", re.I),
    re.compile(r"\b(scp|rsync)\s+\S+\s+\S+@[a-z0-9.-]+:", re.I),
    re.compile(r"aws\s+s3\s+cp\s+\S+\s+s3://(?!internal|local)", re.I),
    re.compile(r"discord(?:app)?\.com/api/webhooks/"),
    re.compile(r"hooks\.slack\.com/services/"),
    re.compile(r"raw\.githubusercontent\.com/[^\s]+\.(env|pem|key)"),
    # Detect base64 obfuscated http/https schemes
    re.compile(r"aHR0cHM6Ly[a-zA-Z0-9+/=]+", re.I),  # https://
    re.compile(r"aHR0cDovL[a-zA-Z0-9+/=]+", re.I),  # http://
]

_ALLOWLIST = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # nosec B104 — string literal in an allow-list, not a socket bind


@dataclass
class ExfilFinding:
    raw: str
    pattern: str
    destination: str


def detect(event: AgentEvent) -> list[ExfilFinding]:
    findings: list[ExfilFinding] = []
    if not event.tool_call:
        return findings
    # Use raw_command exclusively for pattern matching.
    raw = event.tool_call.raw_command or ""
    for pat in _EXFIL_PATTERNS:
        m = pat.search(raw)
        if m:
            destination = _extract_host(raw)
            if destination in _ALLOWLIST:
                continue
            findings.append(
                ExfilFinding(
                    raw=raw[:200],
                    pattern=pat.pattern,
                    destination=destination,
                )
            )
    return findings


def _parse_obfuscated_ip(host: str) -> str | None:
    """Parse octal, hex, decimal, or base64 encoded host representations."""
    if not host:
        return None
    # 1. Decimal IP (e.g., 2130706433 -> 127.0.0.1)
    if host.isdigit():
        try:
            val = int(host)
            if 0 <= val <= 0xFFFFFFFF:
                return f"{(val >> 24) & 255}.{(val >> 16) & 255}.{(val >> 8) & 255}.{val & 255}"
        except ValueError:
            pass
    # 2. Hex IP (e.g., 0x7f000001 -> 127.0.0.1)
    if host.lower().startswith("0x"):
        try:
            val = int(host, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return f"{(val >> 24) & 255}.{(val >> 16) & 255}.{(val >> 8) & 255}.{val & 255}"
        except ValueError:
            pass
    # 3. Base64 encoded domain check
    if len(host) >= 8 and len(host) % 4 == 0:
        import base64
        import binascii

        try:
            decoded = base64.b64decode(host).decode("utf-8", errors="ignore")
            if re.match(r"^[a-zA-Z0-9.-]+$", decoded):
                return decoded
        except (binascii.Error, ValueError):
            pass
    return None


def _extract_host(raw: str) -> str:
    # First search for normal url format
    m = re.search(r"https?://([a-z0-9.-]+)", raw, re.I)
    if m:
        host = m.group(1)
        parsed = _parse_obfuscated_ip(host)
        return parsed or host
    m = re.search(r"@([a-z0-9.-]+):", raw)
    if m:
        host = m.group(1)
        parsed = _parse_obfuscated_ip(host)
        return parsed or host

    # Try parsing base64 string matching direct schemes
    for pat in [r"aHR0cHM6Ly([a-zA-Z0-9+/=]+)", r"aHR0cDovL([a-zA-Z0-9+/=]+)"]:
        bm = re.search(pat, raw, re.I)
        if bm:
            import base64
            import binascii

            try:
                decoded = base64.b64decode(bm.group(0)).decode("utf-8", errors="ignore")
                host_m = re.search(r"https?://([a-z0-9.-]+)", decoded, re.I)
                if host_m:
                    return host_m.group(1)
            except (binascii.Error, ValueError):
                pass
    return "unknown"


__all__ = ["ExfilFinding", "detect"]
