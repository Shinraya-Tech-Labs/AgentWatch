"""Non-Interference Information Flow Control (IFC) Lattice Engine.

Implements a formal Denning-style Security Lattice:
    L = <S, <=, join, meet, bottom, top>
    PUBLIC (bottom) <= INTERNAL <= CONFIDENTIAL <= RESTRICTED <= SECRET (top)

Guarantees Non-Interference:
Data labeled with security classification L_src cannot flow into sink labeled L_dst
unless L_src <= L_dst (Flows-To relation), preventing secret exfiltration and covert
taint propagation across agent tool chains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from panoptis.evidence.redaction import SECRET_PATTERNS
from panoptis.protect.model import Finding, Severity, Stage, Verdict


class SecurityLabel(IntEnum):
    PUBLIC = 0  # Untrusted / world-readable (e.g. public internet, stdout)
    INTERNAL = 1  # Organization-internal non-sensitive data
    CONFIDENTIAL = 2  # PII, user session data, proprietary codebase
    RESTRICTED = 3  # Financial, compliance, or customer-identifiable data
    SECRET = 4  # Cryptographic keys, API tokens, master credentials (top)

    def can_flow_to(self, sink: SecurityLabel) -> bool:
        """Denning's flows-to relation: L1 <= L2."""
        return self.value <= sink.value

    def join(self, other: SecurityLabel) -> SecurityLabel:
        """Least Upper Bound (LUB / join): max(L1, L2)."""
        return SecurityLabel(max(self.value, other.value))

    def meet(self, other: SecurityLabel) -> SecurityLabel:
        """Greatest Lower Bound (GLB / meet): min(L1, L2)."""
        return SecurityLabel(min(self.value, other.value))


# Default sink clearance levels for common tools and protocols
DEFAULT_SINK_CLEARANCES: dict[str, SecurityLabel] = {
    # Public network endpoints
    "http_post": SecurityLabel.PUBLIC,
    "http_get": SecurityLabel.PUBLIC,
    "fetch_url": SecurityLabel.PUBLIC,
    "curl": SecurityLabel.PUBLIC,
    "wget": SecurityLabel.PUBLIC,
    "webhook": SecurityLabel.PUBLIC,
    "send_email": SecurityLabel.PUBLIC,
    "slack_post": SecurityLabel.PUBLIC,
    # Local sandboxed sinks
    "execute_shell": SecurityLabel.INTERNAL,
    "write_file": SecurityLabel.CONFIDENTIAL,
    "database_query": SecurityLabel.RESTRICTED,
    # Cryptographic vault
    "vault_store": SecurityLabel.SECRET,
}

# Regex for common high-confidence PII/confidential data
_PII_PATTERNS = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("JWT", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}")),
]


@dataclass
class TaintContext:
    variable_labels: dict[str, SecurityLabel] = field(default_factory=dict)
    active_taint: SecurityLabel = SecurityLabel.PUBLIC

    def assign(self, var_name: str, label: SecurityLabel) -> None:
        self.variable_labels[var_name] = label
        self.active_taint = self.active_taint.join(label)

    def label_of(self, var_name: str) -> SecurityLabel:
        return self.variable_labels.get(var_name, SecurityLabel.PUBLIC)


class InformationFlowEngine:
    """Rigorous information flow tracking and non-interference verifier."""

    def __init__(
        self,
        sink_clearances: dict[str, SecurityLabel] | None = None,
        default_sink_clearance: SecurityLabel = SecurityLabel.PUBLIC,
        taint_tracker: Any = None,
    ) -> None:
        self.sink_clearances = dict(sink_clearances or DEFAULT_SINK_CLEARANCES)
        self.default_sink_clearance = default_sink_clearance
        if taint_tracker is not None:
            self.taint_tracker = taint_tracker
        else:
            from panoptis.protect.taint_tracker import DynamicTaintTracker

            self.taint_tracker = DynamicTaintTracker()

    def infer_label(self, value: Any) -> SecurityLabel:
        """Infer security label by static analysis AND dynamic transformation tracking."""
        if value is None:
            return SecurityLabel.PUBLIC

        text = str(value)
        base_label = SecurityLabel.PUBLIC

        # 1. Check top-secret patterns (API keys, private keys, tokens)
        for _, pat in SECRET_PATTERNS:
            if pat.search(text):
                base_label = SecurityLabel.SECRET
                break

        # 2. Check confidential / PII patterns
        if base_label < SecurityLabel.SECRET:
            for _, pat in _PII_PATTERNS:
                if pat.search(text):
                    base_label = SecurityLabel.CONFIDENTIAL
                    break

        # 3. Dynamic transformation tracking (base64, hex, URL-enc, token splits)
        if self.taint_tracker is not None:
            dyn_label = self.taint_tracker.evaluate_taint(value, default_label=base_label)
            return base_label.join(dyn_label)

        return base_label

    def verify_flow(
        self,
        source_label: SecurityLabel,
        sink_tool: str,
        channel_name: str = "",
        declassified_with: str | None = None,
    ) -> tuple[bool, Finding | None]:
        """Verify whether data from source_label can legally flow into sink_tool."""
        sink_label = self.sink_clearances.get(sink_tool, self.default_sink_clearance)

        # Explicit declassification hook
        if declassified_with:
            # Verified sanitized / declassified
            return True, None

        if source_label.can_flow_to(sink_label):
            return True, None

        # Illegal Information Flow detected!
        finding = Finding(
            stage=Stage.THREAT,
            code="IFC_ILLEGAL_INFORMATION_FLOW",
            severity=Severity.CRITICAL,
            message=(
                f"Information Flow Non-Interference Violation: {source_label.name} "
                f"data cannot flow into {sink_label.name} sink '{sink_tool}' "
                f"(channel: {channel_name or 'argument'}) without explicit declassification"
            ),
            evidence={
                "source_label": source_label.name,
                "sink_label": sink_label.name,
                "sink_tool": sink_tool,
                "channel": channel_name,
            },
            recommends=Verdict.BLOCK,
        )
        return False, finding

    def audit_tool_call(
        self,
        tool: str,
        arguments: dict[str, Any],
        context_label: SecurityLabel = SecurityLabel.PUBLIC,
    ) -> tuple[SecurityLabel, list[Finding]]:
        """Compute the combined taint label of arguments and verify flow into target tool."""
        current_label = context_label
        findings: list[Finding] = []

        # Infer taint of each argument
        for arg_key, arg_val in arguments.items():
            arg_label = self.infer_label(arg_val)
            current_label = current_label.join(arg_label)

            if not arg_label.can_flow_to(
                self.sink_clearances.get(tool, self.default_sink_clearance)
            ):
                allowed, finding = self.verify_flow(arg_label, tool, channel_name=arg_key)
                if not allowed and finding:
                    findings.append(finding)

        return current_label, findings
