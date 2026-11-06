"""
AgentWatch Rollback Engine
Framework-agnostic rollback support via filesystem snapshots,
git-backed checkpoints, and transactional execution tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A checkpoint with no tenant belongs to ``default``, as in ``panoptis.api.tenancy``.
_DEFAULT_TENANT = "default"


class CheckpointType(str, Enum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    MEMORY = "memory"
    COMPOSITE = "composite"  # git + memory


class RollbackStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Checkpoint:
    checkpoint_id: str
    session_id: str
    step_number: int
    checkpoint_type: CheckpointType
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    snapshot_path: Path | None = None
    git_stash_ref: str | None = None
    git_commit_ref: str | None = None
    memory_snapshot: dict[str, Any] | None = None
    working_dir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tenant_id: str | None = None

    def owned_by(self, tenant_id: str | None) -> bool:
        """Whether this tenant owns it. None is a local caller, which has no tenant to check."""
        return tenant_id is None or (self.tenant_id or _DEFAULT_TENANT) == tenant_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "step_number": self.step_number,
            "checkpoint_type": self.checkpoint_type.value,
            "created_at": self.created_at.isoformat(),
            "snapshot_path": str(self.snapshot_path) if self.snapshot_path else None,
            "git_stash_ref": self.git_stash_ref,
            "git_commit_ref": self.git_commit_ref,
            "working_dir": self.working_dir,
            "metadata": self.metadata,
        }


@dataclass
class RollbackResult:
    checkpoint_id: str
    status: RollbackStatus
    rolled_back_files: list[str] = field(default_factory=list)
    rolled_back_git_ref: str | None = None
    error: str | None = None
    duration_seconds: float | None = None
    completed_at: datetime | None = None


class FilesystemSnapshot:
    """Creates and restores tar-based filesystem snapshots."""

    @staticmethod
    async def create(
        source_path: Path,
        snapshot_dir: Path,
        checkpoint_id: str,
        exclude_patterns: list[str] | None = None,
    ) -> Path:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        out_path = snapshot_dir / f"{checkpoint_id}.tar.gz"

        exclude = exclude_patterns or [
            ".git",
            "__pycache__",
            "*.pyc",
            "node_modules",
            ".agentwatch",
            "*.log",
            "*.tmp",
        ]

        def _create() -> None:
            with tarfile.open(out_path, "w:gz") as tar:

                def filter_fn(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
                    for pat in exclude:
                        if pat.lstrip("*.") in member.name:
                            return None
                    return member

                tar.add(str(source_path), arcname=".", filter=filter_fn)

        await asyncio.get_event_loop().run_in_executor(None, _create)
        logger.info(
            "Created filesystem snapshot at %s (%.1f MB)",
            out_path,
            out_path.stat().st_size / 1_048_576,
        )
        return out_path

    @staticmethod
    async def restore(snapshot_path: Path, target_path: Path) -> list[str]:
        restored: list[str] = []

        def _restore() -> None:
            with tarfile.open(snapshot_path, "r:gz") as tar:
                members = tar.getmembers()
                tar.extractall(str(target_path), filter="data")
                restored.extend([m.name for m in members if m.isfile()])

        await asyncio.get_event_loop().run_in_executor(None, _restore)
        logger.info("Restored %d files from snapshot %s", len(restored), snapshot_path)
        return restored


class GitCheckpointer:
    """Manages git-based checkpoints via stash and branch operations."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    async def _run_git(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.repo_path),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    async def is_git_repo(self) -> bool:
        try:
            await self._run_git("rev-parse", "--git-dir")
            return True
        except RuntimeError:
            return False

    async def current_commit(self) -> str:
        return await self._run_git("rev-parse", "HEAD")

    async def stash(self, message: str) -> str | None:
        """Stash current changes. Returns stash ref or None if nothing to stash."""
        status = await self._run_git("status", "--porcelain")
        if not status:
            return None  # Nothing to stash

        await self._run_git("stash", "push", "-m", message)
        stash_list = await self._run_git("stash", "list", "--format=%H %s")
        if stash_list:
            return stash_list.split()[0]  # Return stash commit hash
        return None

    async def restore_stash(self, stash_ref: str) -> None:
        """Restore a specific stash."""
        await self._run_git("stash", "pop")

    async def create_checkpoint_branch(self, checkpoint_id: str) -> str:
        """Create a branch at current HEAD for the checkpoint."""
        branch = f"panoptis/checkpoint/{checkpoint_id[:12]}"
        await self._run_git("branch", branch)
        return branch

    async def restore_to_commit(self, commit_ref: str) -> None:
        """Hard reset to a specific commit (destructive)."""
        await self._run_git("reset", "--hard", commit_ref)

    async def restore_to_branch(self, branch: str) -> None:
        """Restore working tree to the state of a checkpoint branch."""
        commit = await self._run_git("rev-parse", branch)
        await self.restore_to_commit(commit)


# ─────────────────────────────────────────────
# Rollback Engine
# ─────────────────────────────────────────────


class RollbackEngine:
    """
    Framework-agnostic rollback engine.

    Creates checkpoints before risky operations and restores
    filesystem/git state when rollback is triggered.
    """

    def __init__(
        self,
        checkpoints_dir: Path | None = None,
        auto_checkpoint_on_risk: bool = True,
    ):
        self._checkpoints_dir = checkpoints_dir or Path(".agentwatch/checkpoints")
        self._auto_checkpoint = auto_checkpoint_on_risk
        self._checkpoints: dict[str, Checkpoint] = {}
        self._session_checkpoints: dict[str, list[str]] = {}  # session_id -> [checkpoint_ids]

    def _checkpoint_snapshot_dir(self, checkpoint_id: str) -> Path:
        return self._checkpoints_dir / "snapshots" / checkpoint_id

    async def create_checkpoint(
        self,
        session_id: str,
        step_number: int,
        working_dir: str | None = None,
        memory_snapshot: dict[str, Any] | None = None,
        checkpoint_type: CheckpointType = CheckpointType.COMPOSITE,
        label: str | None = None,
        tenant_id: str | None = None,
    ) -> Checkpoint:
        """Create a checkpoint of current system state."""
        checkpoint_id = f"ckpt-{uuid.uuid4().hex[:12]}"
        cwd = Path(working_dir or os.getcwd())

        cp = Checkpoint(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            step_number=step_number,
            checkpoint_type=checkpoint_type,
            working_dir=str(cwd),
            memory_snapshot=memory_snapshot,
            metadata={"label": label or f"Step {step_number}"},
            tenant_id=tenant_id,
        )

        snapshot_dir = self._checkpoint_snapshot_dir(checkpoint_id)

        # Git checkpoint
        if checkpoint_type in (CheckpointType.GIT, CheckpointType.COMPOSITE):
            git = GitCheckpointer(cwd)
            if await git.is_git_repo():
                try:
                    cp.git_commit_ref = await git.current_commit()
                    branch = await git.create_checkpoint_branch(checkpoint_id)
                    cp.metadata["git_branch"] = branch
                    logger.debug("Git checkpoint: %s @ %s", checkpoint_id, cp.git_commit_ref[:8])
                except Exception as exc:
                    logger.warning("Git checkpoint failed: %s", exc)

        # Filesystem snapshot
        if checkpoint_type in (CheckpointType.FILESYSTEM, CheckpointType.COMPOSITE):
            try:
                snapshot_path = await FilesystemSnapshot.create(
                    source_path=cwd,
                    snapshot_dir=snapshot_dir,
                    checkpoint_id=checkpoint_id,
                )
                cp.snapshot_path = snapshot_path
                cp.metadata["snapshot_size_bytes"] = snapshot_path.stat().st_size
            except Exception as exc:
                logger.warning("Filesystem snapshot failed: %s", exc)

        # Persist checkpoint metadata
        self._checkpoints[checkpoint_id] = cp
        if session_id not in self._session_checkpoints:
            self._session_checkpoints[session_id] = []
        self._session_checkpoints[session_id].append(checkpoint_id)

        await self._save_checkpoint_meta(cp)

        logger.info(
            "Created checkpoint %s for session %s step %d",
            checkpoint_id,
            session_id,
            step_number,
        )
        return cp

    async def rollback(
        self,
        checkpoint_id: str,
        restore_filesystem: bool = True,
        restore_git: bool = True,
        *,
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> RollbackResult:
        """Rollback to a specific checkpoint.

        A checkpoint id is not a credential. A caller acting for a tenant passes the session
        it proved it owns and that tenant, and a checkpoint outside them answers exactly as an
        unknown id does, before anything is restored. Only local callers may omit them.
        """
        import time

        start = time.monotonic()

        cp = self._checkpoints.get(checkpoint_id)
        if (
            cp is None
            or (session_id is not None and cp.session_id != session_id)
            or not cp.owned_by(tenant_id)
        ):
            return RollbackResult(
                checkpoint_id=checkpoint_id,
                status=RollbackStatus.FAILED,
                error="Checkpoint not found",
            )

        result = RollbackResult(
            checkpoint_id=checkpoint_id,
            status=RollbackStatus.IN_PROGRESS,
        )

        try:
            cwd = Path(cp.working_dir or os.getcwd())

            # Git rollback
            if restore_git and cp.git_commit_ref:
                git = GitCheckpointer(cwd)
                if await git.is_git_repo():
                    await git.restore_to_commit(cp.git_commit_ref)
                    result.rolled_back_git_ref = cp.git_commit_ref
                    logger.info("Rolled back git to %s", cp.git_commit_ref[:8])

            # Filesystem rollback
            if restore_filesystem and cp.snapshot_path and cp.snapshot_path.exists():
                restored = await FilesystemSnapshot.restore(
                    snapshot_path=cp.snapshot_path,
                    target_path=cwd,
                )
                result.rolled_back_files = restored
                logger.info("Restored %d files from snapshot", len(restored))

            result.status = RollbackStatus.COMPLETED

        except Exception as exc:
            result.status = RollbackStatus.FAILED
            result.error = str(exc)
            logger.error("Rollback failed for checkpoint %s: %s", checkpoint_id, exc)

        result.duration_seconds = time.monotonic() - start
        result.completed_at = datetime.now(UTC)
        return result

    async def rollback_session(
        self,
        session_id: str,
        to_step: int | None = None,
        *,
        tenant_id: str | None = None,
    ) -> RollbackResult:
        """
        Rollback a session to its latest checkpoint (or to a specific step).
        """
        checkpoints = self.list_checkpoints(session_id, tenant_id=tenant_id)
        if not checkpoints:
            return RollbackResult(
                checkpoint_id="",
                status=RollbackStatus.FAILED,
                error=f"No checkpoints found for session {session_id}",
            )

        if to_step is not None:
            # Find closest checkpoint at or before the requested step
            candidates = [cp for cp in checkpoints if cp.step_number <= to_step]
            if not candidates:
                return RollbackResult(
                    checkpoint_id="",
                    status=RollbackStatus.FAILED,
                    error=f"No checkpoint found at or before step {to_step}",
                )
            target = max(candidates, key=lambda c: c.step_number)
        else:
            # Use the most recent checkpoint
            target = checkpoints[-1]

        return await self.rollback(target.checkpoint_id, session_id=session_id, tenant_id=tenant_id)

    def list_checkpoints(
        self, session_id: str, *, tenant_id: str | None = None
    ) -> list[Checkpoint]:
        ids = self._session_checkpoints.get(session_id, [])
        found = [self._checkpoints[i] for i in ids if i in self._checkpoints]
        return [cp for cp in found if cp.owned_by(tenant_id)]

    def sessions_of(self, tenant_id: str) -> set[str]:
        """Sessions with a checkpoint this tenant owns, in memory or on disk."""
        owned = {cp.session_id for cp in self._checkpoints.values() if cp.owned_by(tenant_id)}
        for session_id, checkpoints in self._checkpoints_on_disk().items():
            if any((tenant or _DEFAULT_TENANT) == tenant_id for _, tenant in checkpoints):
                owned.add(session_id)
        return owned

    def unattributed(self, session_ids: list[str]) -> int:
        """How many checkpoints of these sessions carry no tenant, in memory or on disk."""
        on_disk = self._checkpoints_on_disk()
        found: set[str] = set()
        for session_id in session_ids:
            found.update(cid for cid, tenant in on_disk.get(session_id, []) if tenant is None)
            for cid in self._session_checkpoints.get(session_id, []):
                checkpoint = self._checkpoints.get(cid)
                if checkpoint is not None and checkpoint.tenant_id is None:
                    found.add(cid)
        return len(found)

    def _checkpoints_on_disk(self) -> dict[str, list[tuple[str, str | None]]]:
        """session id -> [(checkpoint id, tenant)] from the metadata files."""
        found: dict[str, list[tuple[str, str | None]]] = {}
        meta_dir = self._checkpoints_dir / "meta"
        if meta_dir.exists():
            for path in meta_dir.glob("*.json"):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    sid = data.get("session_id")
                    if sid:
                        found.setdefault(sid, []).append((path.stem, data.get("tenant_id")))
                except Exception as exc:
                    logger.warning("Failed to read checkpoint meta %s: %s", path, exc)
        return found

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        return self._checkpoints.get(checkpoint_id)

    async def _save_checkpoint_meta(self, cp: Checkpoint) -> None:
        meta_path = self._checkpoints_dir / "meta" / f"{cp.checkpoint_id}.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(meta_path, "w") as f:
            json.dump(cp.to_dict(), f, indent=2)

    async def load_checkpoint_meta(self, checkpoint_id: str) -> Checkpoint | None:
        meta_path = self._checkpoints_dir / "meta" / f"{checkpoint_id}.json"
        if not meta_path.exists():
            return None
        with open(meta_path) as f:
            data = json.load(f)
        cp = Checkpoint(
            checkpoint_id=data["checkpoint_id"],
            session_id=data["session_id"],
            step_number=data["step_number"],
            checkpoint_type=CheckpointType(data["checkpoint_type"]),
            working_dir=data.get("working_dir"),
            metadata=data.get("metadata", {}),
            tenant_id=data.get("tenant_id"),
        )
        if data.get("snapshot_path"):
            cp.snapshot_path = Path(data["snapshot_path"])
        cp.git_commit_ref = data.get("git_commit_ref")
        return cp

    async def prune_checkpoints(
        self,
        session_ids: list[str],
        dry_run: bool = False,
        tenant_id: str | None = None,
    ) -> int:
        """Remove checkpoints for specific sessions from memory and disk.

        Args:
            session_ids (list[str]): The IDs of the sessions to delete checkpoints for.
            dry_run (bool): If True, only return the count of files that would be deleted.
            tenant_id (str | None): When given, only this tenant's checkpoints go. One with
                no tenant belongs to ``default``.

        Returns:
            int: The number of files (metadata and snapshots) that were (or would be) deleted.
        """
        import shutil

        # Scan all meta files to discover checkpoints on disk not in memory
        disk_checkpoints = self._checkpoints_on_disk()

        count = 0
        for session_id in session_ids:
            tenants = dict(disk_checkpoints.get(session_id, []))
            for cid in self._session_checkpoints.get(session_id, []):
                if cid in self._checkpoints:
                    tenants[cid] = self._checkpoints[cid].tenant_id
                else:
                    tenants.setdefault(cid, None)
            kept = {
                cid
                for cid, stamped in tenants.items()
                if tenant_id is not None and (stamped or _DEFAULT_TENANT) != tenant_id
            }

            for cid in set(tenants) - kept:
                # Delete snapshot directory and tarball
                snapshot_dir = self._checkpoints_dir / "snapshots" / cid
                snapshot_file = snapshot_dir / f"{cid}.tar.gz"
                gone = True
                if snapshot_dir.exists():
                    has_tarball = snapshot_file.exists()
                    if not dry_run:
                        try:
                            shutil.rmtree(snapshot_dir)
                        except Exception as exc:
                            gone = False
                            logger.error(
                                "Failed to delete snapshot directory %s: %s", snapshot_dir, exc
                            )
                    if has_tarball and gone:
                        count += 1

                # Delete metadata JSON last: it is what finds a snapshot that would not delete
                meta_path = self._checkpoints_dir / "meta" / f"{cid}.json"
                if meta_path.exists() and gone:
                    if not dry_run:
                        try:
                            meta_path.unlink()
                            count += 1
                        except Exception as exc:
                            gone = False
                            logger.error("Failed to delete checkpoint meta %s: %s", meta_path, exc)
                    else:
                        count += 1

                if not dry_run and gone:
                    self._checkpoints.pop(cid, None)

            if not dry_run:
                remaining = [
                    cid
                    for cid in self._session_checkpoints.get(session_id, [])
                    if cid in self._checkpoints
                ]
                if remaining:
                    self._session_checkpoints[session_id] = remaining
                else:
                    self._session_checkpoints.pop(session_id, None)

        return count

    async def get_sessions_older_than(self, cutoff: datetime) -> list[str]:
        """Find IDs of sessions that have checkpoints older than the cutoff time.

        This checks the modification time of the checkpoint metadata files on disk.

        Args:
            cutoff (datetime): The threshold date/time.

        Returns:
            list[str]: A list of session IDs older than the cutoff.
        """
        session_ids = set()
        meta_dir = self._checkpoints_dir / "meta"
        if not meta_dir.exists():
            return list(session_ids)

        cutoff_timestamp = cutoff.timestamp()
        for path in meta_dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff_timestamp:
                    with open(path) as f:
                        data = json.load(f)
                    sid = data.get("session_id")
                    if sid:
                        session_ids.add(sid)
            except Exception as exc:
                logger.warning("Failed to check modification time for %s: %s", path, exc)

        return list(session_ids)
