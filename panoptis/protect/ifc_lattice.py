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
from collections import OrderedDict
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


class IntegrityLabel(IntEnum):
    """Biba Dual-Lattice Integrity model for agentic workflow verification."""

    UNTRUSTED = 0  # External web, unverified input, untrusted emails, scraped webhooks
    COMMUNITY = 1  # Public packages, unverified open-source repos
    INTERNAL = 2  # Internal company documentation, internal notes
    TRUSTED = 3  # Authenticated developer commands, approved task directives
    AUTHORITATIVE = 4  # Root security policies, enclave attestation, cryptographic keys

    def can_flow_to(self, target: IntegrityLabel) -> bool:
        """Biba's flows-to relation: I1 >= I2 (cannot write up to higher integrity without declassification)."""
        return self.value >= target.value

    def join(self, other: IntegrityLabel) -> IntegrityLabel:
        """LUB in integrity: the resulting integrity is bounded by the weakest link: min(I1, I2)."""
        return IntegrityLabel(min(self.value, other.value))

    def meet(self, other: IntegrityLabel) -> IntegrityLabel:
        """GLB in integrity: max(I1, I2)."""
        return IntegrityLabel(max(self.value, other.value))


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

# Minimum integrity required to execute consequential actions
DEFAULT_INTEGRITY_REQUIREMENTS: dict[str, IntegrityLabel] = {
    "execute_shell": IntegrityLabel.INTERNAL,
    "bash": IntegrityLabel.INTERNAL,
    "sh": IntegrityLabel.INTERNAL,
    "subprocess": IntegrityLabel.INTERNAL,
    "write_file": IntegrityLabel.INTERNAL,
    "append_file": IntegrityLabel.INTERNAL,
    "delete_file": IntegrityLabel.TRUSTED,
    "database_query": IntegrityLabel.INTERNAL,
    "deploy_service": IntegrityLabel.TRUSTED,
    "modify_config": IntegrityLabel.TRUSTED,
}

# Tools that produce untrusted integrity by default
UNTRUSTED_OUTPUT_TOOLS: set[str] = {
    "fetch_url",
    "http_get",
    "curl",
    "wget",
    "web_search",
    "read_email",
    "scrape",
    "read_github_issue",
    "read_github_pr",
}

# Tools that read private or internal data
PRIVATE_DATA_TOOLS: set[str] = {
    "read_file",
    "cat",
    "database_query",
    "vault_get",
    "get_secret",
    "read_env",
}

_SHELL_EGRESS_PATTERNS = re.compile(
    r"(?i)\b(curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|ftp|telnet|invoke-webrequest|invoke-restmethod|fetch|urllib|requests|socket\.connect)\b|/dev/(?:tcp|udp)/"
)

# Regex for common high-confidence PII/confidential data
_PII_PATTERNS = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("JWT", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}")),
]


@dataclass
class SessionTrifectaState:
    """Tracks the Lethal Trifecta (Private Data + Untrusted Input + Egress)."""

    has_read_private: bool = False
    has_untrusted_input: bool = False
    active_integrity: IntegrityLabel = IntegrityLabel.TRUSTED


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
    """Rigorous information flow tracking, non-interference, and Lethal Trifecta verifier."""

    def __init__(
        self,
        sink_clearances: dict[str, SecurityLabel] | None = None,
        default_sink_clearance: SecurityLabel = SecurityLabel.PUBLIC,
        taint_tracker: Any = None,
        max_sessions: int = 5_000,
    ) -> None:
        self.sink_clearances = dict(sink_clearances or DEFAULT_SINK_CLEARANCES)
        self.default_sink_clearance = default_sink_clearance
        self.max_sessions = max_sessions
        self.trifecta_states: OrderedDict[str, SessionTrifectaState] = OrderedDict()
        if taint_tracker is not None:
            self.taint_tracker = taint_tracker
        else:
            from panoptis.protect.taint_tracker import DynamicTaintTracker

            self.taint_tracker = DynamicTaintTracker()

    def _get_or_create_trifecta(self, scope: str | None) -> SessionTrifectaState:
        key = scope or "default"
        if key in self.trifecta_states:
            self.trifecta_states.move_to_end(key)
            return self.trifecta_states[key]
        if len(self.trifecta_states) >= self.max_sessions:
            self.trifecta_states.popitem(last=False)
        state = SessionTrifectaState()
        self.trifecta_states[key] = state
        return state

    def resolve_sink_clearance(self, tool: str, arguments: dict[str, Any]) -> SecurityLabel:
        """Resolve dynamic sink clearance (e.g. shell command executing network egress)."""
        base_clearance = self.sink_clearances.get(tool.lower(), self.default_sink_clearance)
        if tool.lower() in ("execute_shell", "bash", "sh", "terminal", "powershell", "cmd"):
            for val in arguments.values():
                if val and _SHELL_EGRESS_PATTERNS.search(str(val)):
                    return SecurityLabel.PUBLIC
        return base_clearance

    def infer_label(self, value: Any, scope: str | None = None) -> SecurityLabel:
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
            dyn_label = self.taint_tracker.evaluate_taint(
                value, default_label=base_label, scope=scope
            )
            return base_label.join(dyn_label)

        return base_label

    def record_result(self, tool: str, result: Any, scope: str | None = None) -> None:
        """Dynamically propagate confidentiality and integrity labels when a tool execution completes."""
        if self.taint_tracker is not None and result is not None:
            self.taint_tracker.register_from_payload(f"{tool}:output", result, scope=scope)

        state = self._get_or_create_trifecta(scope)
        t_lower = tool.lower()
        if t_lower in UNTRUSTED_OUTPUT_TOOLS:
            state.has_untrusted_input = True
            state.active_integrity = state.active_integrity.join(IntegrityLabel.UNTRUSTED)

        if t_lower in PRIVATE_DATA_TOOLS:
            state.has_read_private = True
        elif result is not None:
            text = str(result)
            for _, pat in SECRET_PATTERNS:
                if pat.search(text):
                    state.has_read_private = True
                    break

    def verify_flow(
        self,
        source_label: SecurityLabel,
        sink_tool: str,
        channel_name: str = "",
        declassified_with: str | None = None,
        effective_sink_clearance: SecurityLabel | None = None,
    ) -> tuple[bool, Finding | None]:
        """Verify whether data from source_label can legally flow into sink_tool."""
        sink_label = (
            effective_sink_clearance
            if effective_sink_clearance is not None
            else self.sink_clearances.get(sink_tool.lower(), self.default_sink_clearance)
        )

        # Explicit declassification hook
        if declassified_with:
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
        scope: str | None = None,
    ) -> tuple[SecurityLabel, list[Finding]]:
        """Compute taint, verify Denning non-interference, Biba integrity, and Lethal Trifecta invariant."""
        current_label = context_label
        findings: list[Finding] = []
        state = self._get_or_create_trifecta(scope)

        t_lower = tool.lower()
        if t_lower in PRIVATE_DATA_TOOLS:
            state.has_read_private = True

        effective_sink_clearance = self.resolve_sink_clearance(tool, arguments)
        is_egress = effective_sink_clearance == SecurityLabel.PUBLIC

        # 1. Lethal Trifecta Invariant Enforcement
        if is_egress and state.has_read_private and state.has_untrusted_input:
            findings.append(
                Finding(
                    stage=Stage.THREAT,
                    code="LETHAL_TRIFECTA_CONVERGENCE",
                    severity=Severity.CRITICAL,
                    message=(
                        f"Lethal Trifecta Invariant Violation: Agent holds private/confidential data "
                        f"AND has ingested untrusted content, and is now attempting outbound egress '{tool}'. "
                        f"Potential autonomous indirect exfiltration."
                    ),
                    evidence={
                        "tool": tool,
                        "has_read_private": state.has_read_private,
                        "has_untrusted_input": state.has_untrusted_input,
                        "scope": scope,
                    },
                    recommends=Verdict.BLOCK,
                )
            )

        # 2. Biba Integrity Verification
        if t_lower in DEFAULT_INTEGRITY_REQUIREMENTS:
            req_integrity = DEFAULT_INTEGRITY_REQUIREMENTS[t_lower]
            if not state.active_integrity.can_flow_to(req_integrity):
                findings.append(
                    Finding(
                        stage=Stage.THREAT,
                        code="IFC_INTEGRITY_VIOLATION",
                        severity=Severity.HIGH,
                        message=(
                            f"Information Flow Integrity Violation: {state.active_integrity.name} input "
                            f"cannot invoke {req_integrity.name} execution sink '{tool}' without sanitization."
                        ),
                        evidence={
                            "tool": tool,
                            "active_integrity": state.active_integrity.name,
                            "required_integrity": req_integrity.name,
                            "scope": scope,
                        },
                        recommends=Verdict.BLOCK,
                    )
                )

        # 3. Denning Non-Interference Confidentiality Flow Verification
        for arg_key, arg_val in arguments.items():
            arg_label = self.infer_label(arg_val, scope=scope)
            current_label = current_label.join(arg_label)

            if not arg_label.can_flow_to(effective_sink_clearance):
                allowed, finding = self.verify_flow(
                    arg_label,
                    tool,
                    channel_name=arg_key,
                    effective_sink_clearance=effective_sink_clearance,
                )
                if not allowed and finding:
                    findings.append(finding)

        return current_label, findings
