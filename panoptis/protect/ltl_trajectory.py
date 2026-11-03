"""Linear Temporal Logic (LTL) Multi-Hop Trajectory Engine.

Implements multi-turn temporal trace verification (based on Siu, He, Song et al.,
UC Berkeley & Duke, 2026):
Prevents multi-hop split exfiltration attacks where an agent divides an exploit across
discrete, seemingly benign turns:
    Turn 1: Read sensitive credentials
    Turn 2: Stage data into scratchpad
    Turn 3: Exfiltrate staged data over network

Formally checks Linear Temporal Logic (LTL) safety properties over the session causal graph:
    Formula 1 (Confidentiality): G(ReadSensitive -> G(!NetworkEgress))
    Formula 2 (Integrity):       G(UntrustedIngest -> G(!UncheckedShellExecution))
    Formula 3 (Persistence):     G(ReadCredentials -> G(!WriteStartupScripts))
"""

from __future__ import annotations

import fnmatch
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from panoptis.protect.model import Finding, Severity, Stage, ToolAction, Verdict


@dataclass
class TrajectoryStep:
    call_id: str
    tool: str
    arguments: dict[str, Any]
    reads_sensitive: bool = False
    writes_staging: bool = False
    network_egress: bool = False
    untrusted_ingest: bool = False
    timestamp: float = field(default_factory=time.time)


_SENSITIVE_READ_TOOLS = {"read_file", "cat", "view_file", "get_secret", "fetch_credentials"}
_SENSITIVE_PATHS = ["*id_rsa*", "*.env*", "*credential*", "*/etc/shadow*", "*token*"]
_PERSISTENCE_PATHS = ["*.bashrc*", "*.profile*", "*/crontab*", "*/systemd/*", "*.zshrc*"]
_UNTRUSTED_INGEST_TOOLS = {"fetch_url", "web_search", "read_email", "get_issue"}
_NETWORK_EGRESS_TOOLS = {"http_post", "fetch_url", "curl", "wget", "send_email", "webhook"}
_SHELL_EXEC_TOOLS = {"execute_shell", "bash", "sh", "run_command", "run_python", "powershell"}


class LTLTrajectoryVerifier:
    """Verifies temporal safety invariants across multi-step agent trajectory traces."""

    def __init__(self, max_sessions: int = 5_000) -> None:
        self.max_sessions = max_sessions
        # scope (tenant_id/session_id) -> list of historical TrajectorySteps
        self.traces: OrderedDict[str, list[TrajectoryStep]] = OrderedDict()
        # scope -> {filepath: accumulated_content} for WLJ piecewise assembly tracking
        self.modified_files: OrderedDict[str, dict[str, str]] = OrderedDict()

    def _scope(self, action: ToolAction) -> str:
        tid = getattr(action, "tenant_id", "default") or "default"
        sid = getattr(action, "session_id", "default") or "default"
        return f"{tid}/{sid}"

    def record_step(self, action: ToolAction, result: Any = None) -> None:
        """Record an executed tool action into the session's causal trajectory graph."""
        scope = self._scope(action)
        if scope not in self.traces:
            if len(self.traces) >= self.max_sessions:
                self.traces.popitem(last=False)
            self.traces[scope] = []
        else:
            self.traces.move_to_end(scope)

        args_str = str(action.arguments).lower()
        reads_sens = False
        if action.tool in _SENSITIVE_READ_TOOLS or "read" in action.tool.lower():
            for pat in _SENSITIVE_PATHS:
                if fnmatch.fnmatch(args_str, f"*{pat.lower()}*"):
                    reads_sens = True
                    break

        writes_stag = action.tool in ("write_file", "append_file")
        net_egress = action.tool in _NETWORK_EGRESS_TOOLS
        untrusted = action.tool in _UNTRUSTED_INGEST_TOOLS

        step = TrajectoryStep(
            call_id=action.call_id,
            tool=action.tool,
            arguments=action.arguments,
            reads_sensitive=reads_sens,
            writes_staging=writes_stag,
            network_egress=net_egress,
            untrusted_ingest=untrusted,
        )
        self.traces[scope].append(step)

        # Track multi-file piecewise code assembly (Workflow-Level Jailbreak defense)
        if action.tool in ("write_file", "append_file", "edit_file"):
            path = str(action.arguments.get("path", action.arguments.get("file_path", "")))
            content = str(action.arguments.get("content", action.arguments.get("replacement", "")))
            if path and content:
                if scope not in self.modified_files:
                    if len(self.modified_files) >= self.max_sessions:
                        self.modified_files.popitem(last=False)
                    self.modified_files[scope] = {}
                else:
                    self.modified_files.move_to_end(scope)
                existing = self.modified_files[scope].get(path, "")
                self.modified_files[scope][path] = f"{existing}\n{content}"

    def has_occurred_within(
        self,
        session_id: str,
        predicate: Any,
        within_seconds: float,
        tenant_id: str = "default",
    ) -> bool:
        """Metric past-time diamond operator (diamond_[0, within_seconds] phi)."""
        now = time.time()
        scope = f"{tenant_id}/{session_id}"
        trace = self.traces.get(scope) or self.traces.get(session_id, [])
        for s in reversed(trace):
            if (now - s.timestamp) > within_seconds:
                break
            if predicate(s):
                return True
        return False

    def verify_action(self, action: ToolAction) -> list[Finding]:
        """Check all temporal LTL formulas against proposed action and session trace."""
        findings: list[Finding] = []
        scope = self._scope(action)
        history = self.traces.get(scope) or self.traces.get(action.session_id, [])
        if scope in self.traces:
            self.traces.move_to_end(scope)

        args_str = str(action.arguments).lower()

        # Check LTL Formula 1: G(ReadSensitive -> G(!NetworkEgress))
        has_read_sensitive = any(s.reads_sensitive for s in history)
        if has_read_sensitive and action.tool in _NETWORK_EGRESS_TOOLS:
            # Multi-turn exfiltration chain detected!
            sens_step = next(s for s in history if s.reads_sensitive)
            findings.append(
                Finding(
                    stage=Stage.THREAT,
                    code="LTL_MULTI_HOP_EXFILTRATION_DETECTED",
                    severity=Severity.CRITICAL,
                    message=(
                        f"LTL Invariant Violation G(ReadSensitive -> G(!NetworkEgress)): "
                        f"Session previously read sensitive data in turn '{sens_step.tool}' "
                        f"(call {sens_step.call_id[:12]}); subsequent network egress via '{action.tool}' is blocked"
                    ),
                    evidence={
                        "violated_ltl": "G(ReadSensitive -> G(!NetworkEgress))",
                        "antecedent_call_id": sens_step.call_id,
                        "antecedent_tool": sens_step.tool,
                        "consequent_tool": action.tool,
                    },
                    recommends=Verdict.BLOCK,
                )
            )

        # Check LTL Formula 2: G(UntrustedIngest -> G(!UncheckedShellExecution))
        has_untrusted_ingest = any(s.untrusted_ingest for s in history)
        if has_untrusted_ingest and action.tool in _SHELL_EXEC_TOOLS:
            ingest_step = next(s for s in history if s.untrusted_ingest)
            findings.append(
                Finding(
                    stage=Stage.THREAT,
                    code="LTL_UNTRUSTED_INPUT_TO_SHELL_CHAIN",
                    severity=Severity.HIGH,
                    message=(
                        f"LTL Invariant Warning G(UntrustedIngest -> G(!ShellExecution)): "
                        f"Session previously ingested untrusted external input via '{ingest_step.tool}'; "
                        f"subsequent code execution via '{action.tool}' requires human review"
                    ),
                    evidence={
                        "violated_ltl": "G(UntrustedIngest -> G(!ShellExecution))",
                        "antecedent_tool": ingest_step.tool,
                        "consequent_tool": action.tool,
                    },
                    recommends=Verdict.ESCALATE_HITL,
                )
            )

        # Check LTL Formula 3: G(ReadCredentials -> G(!WriteStartupScripts))
        if has_read_sensitive and action.tool in ("write_file", "append_file", "edit_file"):
            for p_pat in _PERSISTENCE_PATHS:
                if fnmatch.fnmatch(args_str, f"*{p_pat.lower()}*"):
                    findings.append(
                        Finding(
                            stage=Stage.THREAT,
                            code="LTL_PERSISTENCE_BACKDOOR_CHAIN",
                            severity=Severity.CRITICAL,
                            message=(
                                f"LTL Invariant Violation G(ReadCredentials -> G(!WriteStartupScripts)): "
                                f"Attempting to modify startup script '{args_str[:60]}' after reading credentials"
                            ),
                            evidence={"target": args_str[:80]},
                            recommends=Verdict.BLOCK,
                        )
                    )
                    break

        # Check LTL Formula 4: G(FilesAssembledWithExploitChain -> G(!ExecuteShell))
        # Defeats: Workflow-Level Jailbreak Construction (Alan Turing Inst. & Warwick 2026)
        if action.tool in _SHELL_EXEC_TOOLS:
            accumulated_files = self.modified_files.get(scope) or self.modified_files.get(
                action.session_id, {}
            )
            if len(accumulated_files) >= 1:
                all_text = " ".join(accumulated_files.values()).lower()
                has_subproc = any(
                    k in all_text
                    for k in (
                        "socket.socket",
                        "subprocess.popen",
                        "subprocess.run",
                        "subprocess",
                        "os.system",
                        "pty.spawn",
                        "/dev/tcp",
                        "/bin/sh",
                        "/bin/bash",
                    )
                )
                has_egress_or_crypto = any(
                    k in all_text
                    for k in (
                        "socket.socket",
                        "/dev/tcp",
                        "b64decode",
                        "urllib",
                        "requests",
                        "http.client",
                        "eval(",
                        "exec(",
                        "pty.spawn",
                    )
                )
                if has_subproc and has_egress_or_crypto:
                    findings.append(
                        Finding(
                            stage=Stage.THREAT,
                            code="LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED",
                            severity=Severity.CRITICAL,
                            message=(
                                f"LTL Workflow-Level Jailbreak (WLJ) Invariant Violation: "
                                f"Agent assembled dangerous code across {len(accumulated_files)} modified file(s) "
                                f"({list(accumulated_files.keys())[:3]}); execution via '{action.tool}' is blocked"
                            ),
                            evidence={
                                "violated_ltl": "G(FilesAssembledWithExploitChain -> G(!ExecuteShell))",
                                "assembled_files": list(accumulated_files.keys()),
                                "consequent_tool": action.tool,
                            },
                            recommends=Verdict.BLOCK,
                        )
                    )

        return findings

        return findings
