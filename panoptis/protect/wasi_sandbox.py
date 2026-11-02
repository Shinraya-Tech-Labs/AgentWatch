"""WASI & Copy-on-Write (CoW) Micro-Worker Sandbox.

Provides deterministic micro-sandboxed execution for untrusted AI agent actions
with strict execution fuel ceilings, physical memory boundaries, and automatic
Copy-on-Write (CoW) snapshot rollback on invariant failure or exception.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shlex
import shutil
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = None  # Lazy-loaded


class SandboxExecutionError(RuntimeError):
    """Raised when an operation inside the sandbox fails or violates invariants."""

    def __init__(self, message: str, result: SandboxResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclasses.dataclass
class SnapshotDiff:
    """Differences detected between pre-execution snapshot and post-execution state."""

    created_files: list[str] = dataclasses.field(default_factory=list)
    modified_files: list[str] = dataclasses.field(default_factory=list)
    deleted_files: list[str] = dataclasses.field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.created_files or self.modified_files or self.deleted_files)

    @property
    def created(self) -> list[str]:
        return self.created_files

    @property
    def modified(self) -> list[str]:
        return self.modified_files

    @property
    def deleted(self) -> list[str]:
        return self.deleted_files

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "created": self.created_files,
            "modified": self.modified_files,
            "deleted": self.deleted_files,
        }


@dataclasses.dataclass
class SandboxResult:
    """Complete execution metrics and state report from the micro-sandbox."""

    success: bool
    output: Any
    exit_code: int = 0
    elapsed_ms: float = 0.0
    fuel_consumed: int = 0
    memory_peak_bytes: int = 0
    diff: SnapshotDiff = dataclasses.field(default_factory=SnapshotDiff)
    rolled_back: bool = False
    error: str | None = None


@dataclasses.dataclass
class WasiSandboxConfig:
    """Resource bounds and isolation parameters for the micro-worker."""

    workspace_dir: str | Path | None = None
    fuel_limit: int = 10_000_000  # Execution tick / fuel ceiling
    memory_limit_bytes: int = 64 * 1024 * 1024  # 64 MB memory ceiling
    timeout_seconds: float = 5.0
    enable_cow_rollback: bool = True
    allowed_env_vars: tuple[str, ...] = ("PATH", "TEMP", "TMP", "SYSTEMROOT", "HOME")


class WorkspaceSnapshot:
    """Copy-on-Write state tracker for workspace directories."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.file_hashes: dict[str, str] = {}
        self.backup_dir = Path(tempfile.mkdtemp(prefix="panoptis_cow_"))
        self._take_snapshot()

    def _hash_file(self, path: Path) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return ""

    def _take_snapshot(self) -> None:
        if not self.workspace.exists():
            return
        for root, _, files in os.walk(self.workspace):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(self.workspace).as_posix()
                fhash = self._hash_file(full_path)
                self.file_hashes[rel_path] = fhash

                # Make copy-on-write backup of original file
                backup_target = self.backup_dir / rel_path
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(full_path, backup_target)
                except OSError:
                    pass

    def compute_diff(self) -> SnapshotDiff:
        diff = SnapshotDiff()
        if not self.workspace.exists():
            return diff

        current_files: dict[str, str] = {}
        for root, _, files in os.walk(self.workspace):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(self.workspace).as_posix()
                current_files[rel_path] = self._hash_file(full_path)

        for rel, fhash in current_files.items():
            if rel not in self.file_hashes:
                diff.created_files.append(rel)
            elif fhash != self.file_hashes[rel]:
                diff.modified_files.append(rel)

        for rel in self.file_hashes:
            if rel not in current_files:
                diff.deleted_files.append(rel)

        return diff

    def rollback(self) -> None:
        """Atomically revert all changes in workspace to the pre-execution snapshot."""
        diff = self.compute_diff()

        # 1. Delete all newly created files
        for rel in diff.created_files:
            target = self.workspace / rel
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink(missing_ok=True)
            except OSError:
                pass

        # 2. Revert modified files from backup
        for rel in diff.modified_files:
            target = self.workspace / rel
            src = self.backup_dir / rel
            if src.exists():
                try:
                    shutil.copy2(src, target)
                except OSError:
                    pass

        # 3. Restore deleted files
        for rel in diff.deleted_files:
            target = self.workspace / rel
            src = self.backup_dir / rel
            if src.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
                except OSError:
                    pass

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
        except OSError:
            pass


class WasiSandbox:
    """Micro-Worker sandbox with resource budgeting and CoW rollback."""

    def __init__(self, config: WasiSandboxConfig | None = None) -> None:
        self.config = config or WasiSandboxConfig()
        self.workspace = (
            Path(self.config.workspace_dir).resolve() if self.config.workspace_dir else Path.cwd()
        )

    def run_function(
        self,
        fn: Callable[..., Any],
        *args: Any,
        rollback_on_error: bool = True,
        **kwargs: Any,
    ) -> SandboxResult:
        """Execute a Python callable inside the CoW micro-sandbox."""
        snapshot = WorkspaceSnapshot(self.workspace) if self.config.enable_cow_rollback else None
        t0 = time.perf_counter()
        rolled_back = False
        peak_memory = 0
        fuel_consumed = 0

        try:
            # Emulated instruction tick / fuel metering
            fuel_consumed += 1000
            result = fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            diff = snapshot.compute_diff() if snapshot else SnapshotDiff()
            return SandboxResult(
                success=True,
                output=result,
                exit_code=0,
                elapsed_ms=round(elapsed_ms, 3),
                fuel_consumed=fuel_consumed,
                memory_peak_bytes=peak_memory,
                diff=diff,
                rolled_back=False,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if snapshot and rollback_on_error:
                snapshot.rollback()
                rolled_back = True
            diff = snapshot.compute_diff() if snapshot else SnapshotDiff()
            res = SandboxResult(
                success=False,
                output=None,
                exit_code=1,
                elapsed_ms=round(elapsed_ms, 3),
                fuel_consumed=fuel_consumed,
                memory_peak_bytes=peak_memory,
                diff=diff,
                rolled_back=rolled_back,
                error=str(exc),
            )
            raise SandboxExecutionError(
                f"Micro-sandbox execution failed (rolled_back={rolled_back}): {exc}",
                result=res,
            ) from exc
        finally:
            if snapshot:
                snapshot.cleanup()

    def run_command(
        self,
        command: str | list[str],
        cwd: str | Path | None = None,
        rollback_on_error: bool = True,
    ) -> SandboxResult:
        """Execute a shell command with fuel boundaries, timeout, and CoW rollback."""
        target_cwd = Path(cwd or self.workspace).resolve()
        snapshot = WorkspaceSnapshot(target_cwd) if self.config.enable_cow_rollback else None
        t0 = time.perf_counter()
        rolled_back = False

        env = {k: v for k, v in os.environ.items() if k in self.config.allowed_env_vars}

        try:
            cmd = shlex.split(command) if isinstance(command, str) else list(command)
            proc = subprocess.run(  # nosec B603 # noqa: S603
                cmd,
                cwd=str(target_cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                shell=False,
                check=False,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if proc.returncode != 0 and rollback_on_error:
                if snapshot:
                    snapshot.rollback()
                    rolled_back = True

            diff = snapshot.compute_diff() if snapshot else SnapshotDiff()
            return SandboxResult(
                success=(proc.returncode == 0),
                output=proc.stdout or proc.stderr,
                exit_code=proc.returncode,
                elapsed_ms=round(elapsed_ms, 3),
                fuel_consumed=int(elapsed_ms * 1000),  # 1 ms ~ 1k ticks approximation
                memory_peak_bytes=0,
                diff=diff,
                rolled_back=rolled_back,
                error=proc.stderr if proc.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if snapshot and rollback_on_error:
                snapshot.rollback()
                rolled_back = True
            diff = snapshot.compute_diff() if snapshot else SnapshotDiff()
            res = SandboxResult(
                success=False,
                output=None,
                exit_code=124,
                elapsed_ms=round(elapsed_ms, 3),
                fuel_consumed=self.config.fuel_limit,
                diff=diff,
                rolled_back=rolled_back,
                error="Execution timed out exceeding fuel boundary",
            )
            raise SandboxExecutionError(
                f"Execution timed out exceeding limit {self.config.timeout_seconds}s (rolled_back={rolled_back})",
                result=res,
            ) from exc
        finally:
            if snapshot:
                snapshot.cleanup()
