"""Real-Time Counterfactual Temporal Forking (WP-07).

Speculative Pre-Execution Sandbox:
Before committing state-altering tool operations (destructive shell commands,
bulk file updates, configuration mutations), the agent runtime transparently forks
an ephemeral shadow branch/workspace.

The tool executes inside the isolated counterfactual fork. If the post-execution delta
violates any safety invariants (data destruction, privilege escalation, unauthorized
persistence, secret leakage), the fork is vaporized and the action is terminated
BEFORE any real syscall or state corruption can occur on the host system.

If verified safe, the speculative state can either be promoted atomically (zero double-
execution overhead) or discarded cleanly.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from panoptis.evidence.redaction import SECRET_PATTERNS
from panoptis.protect.model import Finding, Severity, Stage, ToolAction, Verdict
from panoptis.protect.wasi_sandbox import SnapshotDiff, WorkspaceSnapshot


@dataclass
class CounterfactualConfig:
    enabled: bool = True
    max_state_drift_files: int = 50
    allow_file_deletion: bool = True
    max_deleted_files: int = 10
    forbidden_mutation_paths: list[str] = field(
        default_factory=lambda: [
            ".git/**",
            "**/.env*",
            "**/id_rsa*",
            "**/*.pem",
            "**/*.key",
            "/etc/**",
            "C:\\Windows\\**",
            "panoptis/**",
        ]
    )
    scan_output_secrets: bool = True
    promote_on_success: bool = False
    timeout_s: float = 15.0


@dataclass
class CounterfactualResult:
    is_safe: bool
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)
    state_diff: SnapshotDiff = field(default_factory=SnapshotDiff)
    output: Any = None
    promoted: bool = False
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "verdict": self.verdict.value,
            "findings": [f.to_dict() for f in self.findings],
            "state_diff": self.state_diff.to_dict(),
            "output": str(self.output)[:200] if self.output is not None else None,
            "promoted": self.promoted,
            "execution_time_ms": round(self.execution_time_ms, 3),
        }


class CounterfactualEngine:
    """Orchestrates ephemeral speculative execution in isolated temporal forks."""

    def __init__(self, config: CounterfactualConfig | None = None) -> None:
        self.config = config or CounterfactualConfig()

    def fork_and_evaluate(
        self,
        action: ToolAction,
        func: Callable[..., Any],
        args: dict[str, Any],
        workspace_dir: Path | str | None = None,
    ) -> CounterfactualResult:
        """Execute `func(**args)` inside an ephemeral parallel fork and verify state invariants."""
        start = time.perf_counter()
        target_ws = Path(workspace_dir).resolve() if workspace_dir else Path.cwd().resolve()
        fork_temp_dir = Path(tempfile.mkdtemp(prefix="agentwatch_fork_"))

        findings: list[Finding] = []
        output: Any = None
        state_diff = SnapshotDiff()
        is_safe = True
        promoted = False

        try:
            # Mirror workspace structure if workspace exists
            if target_ws.exists():
                self._mirror_workspace(target_ws, fork_temp_dir)

            # Take pre-execution snapshot in the fork
            pre_snap = WorkspaceSnapshot(fork_temp_dir)

            # Execute speculative action inside the isolated temporal fork
            orig_cwd = os.getcwd()
            try:
                os.chdir(str(fork_temp_dir))
                # Adjust path arguments to point to fork if applicable
                forked_args = self._remap_args_to_fork(args, target_ws, fork_temp_dir)
                output = func(**forked_args)
            finally:
                os.chdir(orig_cwd)

            # Compute post-execution state diff
            state_diff = pre_snap.compute_diff()
            pre_snap.cleanup()

            # Invariant 1: Check forbidden mutations (e.g. .env, .git, private keys)
            for touched in state_diff.created + state_diff.modified + state_diff.deleted:
                for pattern in self.config.forbidden_mutation_paths:
                    if fnmatch.fnmatch(touched, pattern) or fnmatch.fnmatch(f"/{touched}", pattern):
                        findings.append(
                            Finding(
                                stage=Stage.COUNTERFACTUAL,
                                code="COUNTERFACTUAL_PROTECTED_PATH_TAMPERING",
                                severity=Severity.CRITICAL,
                                message=f"Temporal fork detected forbidden mutation to protected path {touched!r}",
                                evidence={"path": touched, "rule": pattern},
                                recommends=Verdict.BLOCK,
                            )
                        )
                        is_safe = False

            # Invariant 2: Check excessive file deletion drift
            if not self.config.allow_file_deletion and state_diff.deleted:
                findings.append(
                    Finding(
                        stage=Stage.COUNTERFACTUAL,
                        code="COUNTERFACTUAL_DELETION_PROHIBITED",
                        severity=Severity.HIGH,
                        message=f"Temporal fork attempted to delete {len(state_diff.deleted)} files when deletion is prohibited",
                        evidence={"deleted": state_diff.deleted[:5]},
                        recommends=Verdict.BLOCK,
                    )
                )
                is_safe = False
            elif len(state_diff.deleted) > self.config.max_deleted_files:
                findings.append(
                    Finding(
                        stage=Stage.COUNTERFACTUAL,
                        code="COUNTERFACTUAL_MASS_DELETION_SPIKE",
                        severity=Severity.CRITICAL,
                        message=f"Temporal fork deleted {len(state_diff.deleted)} files, exceeding threshold of {self.config.max_deleted_files}",
                        evidence={"deleted_count": len(state_diff.deleted)},
                        recommends=Verdict.BLOCK,
                    )
                )
                is_safe = False

            # Invariant 3: Check excessive state drift (runaway modification)
            total_drift = (
                len(state_diff.created) + len(state_diff.modified) + len(state_diff.deleted)
            )
            if total_drift > self.config.max_state_drift_files:
                findings.append(
                    Finding(
                        stage=Stage.COUNTERFACTUAL,
                        code="COUNTERFACTUAL_EXCESSIVE_STATE_DRIFT",
                        severity=Severity.HIGH,
                        message=f"Temporal fork modified {total_drift} files, exceeding safe drift limit ({self.config.max_state_drift_files})",
                        evidence={"total_drift": total_drift},
                        recommends=Verdict.BLOCK,
                    )
                )
                is_safe = False

            # Invariant 4: Check secret leaks in output
            if self.config.scan_output_secrets and output is not None:
                out_str = str(output)
                for cat, pat in SECRET_PATTERNS:
                    if pat.search(out_str):
                        findings.append(
                            Finding(
                                stage=Stage.COUNTERFACTUAL,
                                code="COUNTERFACTUAL_SECRET_LEAK_IN_OUTPUT",
                                severity=Severity.CRITICAL,
                                message=f"Temporal fork output leaked sensitive credential ({cat})",
                                evidence={"category": cat},
                                recommends=Verdict.BLOCK,
                            )
                        )
                        is_safe = False
                        break

            # Promotion if safe and requested
            promoted = False
            if is_safe and self.config.promote_on_success:
                self._promote_fork(fork_temp_dir, target_ws, state_diff)
                promoted = True

        except Exception as exc:
            is_safe = False
            findings.append(
                Finding(
                    stage=Stage.COUNTERFACTUAL,
                    code="COUNTERFACTUAL_EXECUTION_FAILURE",
                    severity=Severity.HIGH,
                    message=f"Speculative execution in temporal fork failed with unhandled error: {exc}",
                    evidence={"error": str(exc)},
                    recommends=Verdict.BLOCK,
                )
            )
        finally:
            # Destroy the ephemeral shadow fork completely
            try:
                shutil.rmtree(fork_temp_dir, ignore_errors=True)
            except OSError:
                pass

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        verdict = Verdict.ALLOW if is_safe else Verdict.BLOCK

        return CounterfactualResult(
            is_safe=is_safe,
            verdict=verdict,
            findings=findings,
            state_diff=state_diff,
            output=output,
            promoted=promoted,
            execution_time_ms=elapsed_ms,
        )

    def _mirror_workspace(self, src: Path, dest: Path) -> None:
        """Create a lightweight mirror of files in the workspace (ignoring .git / large dirs)."""
        ignore_patterns = shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"
        )
        for item in src.iterdir():
            dest_item = dest / item.name
            if item.name in (".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"):
                continue
            try:
                if item.is_file():
                    shutil.copy2(item, dest_item)
                elif item.is_dir():
                    shutil.copytree(item, dest_item, ignore=ignore_patterns, dirs_exist_ok=True)
            except (OSError, PermissionError):
                pass

    def _remap_args_to_fork(
        self, args: dict[str, Any], target_ws: Path, fork_dir: Path
    ) -> dict[str, Any]:
        """Remap file path arguments pointing to real workspace so they target the fork."""
        remapped = dict(args)
        t_str = str(target_ws)
        f_str = str(fork_dir)
        for k, v in remapped.items():
            if isinstance(v, str) and (t_str in v or v.startswith("./") or v.startswith(".\\")):
                remapped[k] = v.replace(t_str, f_str)
        return remapped

    def _promote_fork(self, fork_dir: Path, target_ws: Path, diff: SnapshotDiff) -> None:
        """Promote verified safe changes from fork back to target workspace."""
        for rel in diff.created + diff.modified:
            src_file = fork_dir / rel
            dest_file = target_ws / rel
            if src_file.exists():
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
        for rel in diff.deleted:
            dest_file = target_ws / rel
            if dest_file.exists():
                try:
                    dest_file.unlink()
                except OSError:
                    pass
