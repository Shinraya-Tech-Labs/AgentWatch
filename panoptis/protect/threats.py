"""Threat classification of a proposed tool call (WP-01, pre-execution).

Combines three sources, each producing explainable findings:

1. structured argument analysis (:mod:`panoptis.protect.arguments`);
2. the existing PANOPTIS risk-pattern library (:class:`panoptis.core.safety.RiskScorer`),
   so rules users already wrote keep working;
3. content checks on the arguments themselves: secrets about to leave the process, and
   injection-shaped text an agent is forwarding (to a sub-agent, an email, a ticket).

It also proposes *argument mutations*: when a secret appears in an argument of a tool that
does not need it (a file write, a message), the secret is redacted and the call proceeds
(``MUTATE_AND_ALLOW``). A secret sent to the network is exfiltration and is blocked.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from panoptis.core.injection import scan_text
from panoptis.core.safety import RiskPattern, RiskScorer
from panoptis.core.schema import RiskLevel, ToolCallData
from panoptis.evidence.redaction import SECRET_PATTERNS, SENSITIVE_KEYS
from panoptis.protect.arguments import READ_ONLY_CMDS, ArgumentAnalyzer, ArgumentProfile
from panoptis.protect.model import Finding, Severity, Stage, Verdict
from panoptis.security.owasp import OwaspVector


def _rules_version() -> str:
    """Content hash of the rule code, so every decision names the exact rules that made it."""
    import panoptis.core.safety as legacy
    import panoptis.evidence.redaction as redaction
    import panoptis.protect.arguments as args_mod

    h = hashlib.sha256()
    for mod in (args_mod, sys.modules[__name__], legacy, redaction):
        h.update(Path(mod.__file__ or "").read_bytes())
    return f"sha256:{h.hexdigest()[:16]}"


_LEVEL = {
    RiskLevel.SAFE: Severity.INFO,
    RiskLevel.LOW: Severity.LOW,
    RiskLevel.MEDIUM: Severity.MEDIUM,
    RiskLevel.HIGH: Severity.HIGH,
    RiskLevel.CRITICAL: Severity.CRITICAL,
}

# argument keys whose value is meant to carry a credential to its destination
CREDENTIAL_ARGS = re.compile(
    r"(?i)^(api[_-]?key|token|access[_-]?token|authorization|auth|password|secret|credentials?|bearer|headers)$"
)


def find_secrets(text: str) -> list[tuple[str, str]]:
    """(category, matched value) for every high-precision secret format in ``text``."""
    hits = []
    for category, pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            hits.append((category, m.group(0)))
    return hits


def mask_secrets(text: str) -> tuple[str, int]:
    n = 0
    for category, pat in SECRET_PATTERNS:
        text, k = pat.subn(f"[REDACTED:{category}]", text)
        n += k
    return text, n


@dataclass
class ThreatReport:
    profile: ArgumentProfile
    findings: list[Finding] = field(default_factory=list)
    mutated_arguments: dict[str, Any] | None = None


class ThreatAnalyzer:
    def __init__(self, extra_patterns: list[RiskPattern] | None = None) -> None:
        self.arguments = ArgumentAnalyzer()
        self.scorer = RiskScorer(extra_patterns)

    def analyze(self, tool: str, arguments: dict[str, Any]) -> ThreatReport:
        profile, findings = self.arguments.analyze(tool, arguments)
        findings.extend(self._legacy_patterns(tool, arguments, profile))
        secret_findings, mutated = self._secrets(tool, arguments, profile)
        findings.extend(secret_findings)
        findings.extend(self._forwarded_injection(profile))
        return ThreatReport(profile=profile, findings=findings, mutated_arguments=mutated)

    def _legacy_patterns(
        self, tool: str, arguments: dict[str, Any], profile: ArgumentProfile
    ) -> list[Finding]:
        # Same input the SafetyEngine scores: the command text (not every string, so file
        # contents that merely mention a command are not flagged) plus the tool name.
        # read-only commands cannot act, so their arguments (grep 'rm -rf /' docs) are not commands
        acting = [
            c.text
            for c in profile.commands
            if c.name not in READ_ONLY_CMDS or c.name in ("git", "env")
        ]
        raw = " ; ".join(acting) or None
        if profile.commands and not acting:
            return []
        if raw is None:
            raw = next(
                (
                    v
                    for k, v in arguments.items()
                    if isinstance(v, str) and k.lower() in ("command", "cmd", "script")
                ),
                None,
            )
        tc = ToolCallData.model_construct(
            tool_name=tool, arguments={}, raw_command=raw, affected_resources=[]
        )
        level, _score, reasons, policy_ids = self.scorer.score(tc)
        if level is RiskLevel.SAFE or not reasons:
            return []
        sev = _LEVEL[level]
        rec = (
            Verdict.BLOCK
            if sev is Severity.CRITICAL
            else Verdict.ESCALATE_HITL
            if sev is Severity.HIGH
            else Verdict.ALLOW
        )
        return [
            Finding(
                Stage.THREAT,
                f"PATTERN_{policy_ids[0] if policy_ids else 'CUSTOM'}",
                sev,
                "; ".join(dict.fromkeys(reasons)),
                {"policy_ids": policy_ids, "library": "panoptis.core.safety"},
                OwaspVector.TOOL_ABUSE.value,
                rec,
            )
        ]

    def _secrets(
        self, tool: str, arguments: dict[str, Any], profile: ArgumentProfile
    ) -> tuple[list[Finding], dict[str, Any] | None]:
        findings: list[Finding] = []
        mutated = copy.deepcopy(arguments)
        changed = 0

        def walk(node: Any, path: str, key: str = "") -> Any:
            nonlocal changed
            if isinstance(node, dict):
                return {k: walk(v, f"{path}.{k}", str(k)) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, f"{path}[{i}]", key) for i, v in enumerate(node)]
            if not isinstance(node, str):
                return node
            hits = find_secrets(node)
            if not hits:
                return node
            categories = sorted({c for c, _ in hits})
            credential_slot = bool(CREDENTIAL_ARGS.match(key)) or bool(SENSITIVE_KEYS.match(key))
            if credential_slot and not profile.reads_sensitive:
                # a key passed in the parameter meant for it, to the tool that uses it
                findings.append(
                    Finding(
                        Stage.THREAT,
                        "SECRET_IN_CREDENTIAL_ARG",
                        Severity.INFO,
                        f"Credential passed in '{key}' as intended",
                        {"path": path, "categories": categories},
                        None,
                        Verdict.ALLOW,
                    )
                )
                return node
            if profile.network:
                findings.append(
                    Finding(
                        Stage.THREAT,
                        "SECRET_EXFILTRATION",
                        Severity.CRITICAL,
                        f"A secret ({', '.join(categories)}) would be sent over the network by '{tool}'",
                        {"path": path, "categories": categories, "hosts": sorted(profile.hosts)},
                        OwaspVector.DATA_EXFILTRATION.value,
                        Verdict.BLOCK,
                    )
                )
                return node
            masked, n = mask_secrets(node)
            changed += n
            findings.append(
                Finding(
                    Stage.THREAT,
                    "SECRET_IN_ARGUMENT",
                    Severity.MEDIUM,
                    f"A secret ({', '.join(categories)}) in '{path}' is redacted before '{tool}' runs",
                    {"path": path, "categories": categories, "redactions": n},
                    OwaspVector.DATA_EXFILTRATION.value,
                    Verdict.MUTATE_AND_ALLOW,
                )
            )
            return masked

        mutated = walk(mutated, "$")
        return findings, (mutated if changed else None)

    def _forwarded_injection(self, profile: ArgumentProfile) -> list[Finding]:
        if not profile.network and not profile.mutating:
            return []
        out = []
        for path, text in profile.strings:
            scan = scan_text(text)
            if scan.detected:
                out.append(
                    Finding(
                        Stage.THREAT,
                        "INJECTION_IN_ARGUMENT",
                        Severity.HIGH,
                        "Instruction-override text is being forwarded in a tool argument",
                        {"path": path, "patterns": sorted({f.pattern for f in scan.findings})},
                        OwaspVector.PROMPT_INJECTION.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
                break
        return out


THREAT_RULES_VERSION = _rules_version()
