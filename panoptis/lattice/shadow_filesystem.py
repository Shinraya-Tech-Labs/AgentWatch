"""In-memory filesystem simulation for the v2 state lattice.

The lattice decides whether an action is safe by working out what it *would* do, rather than by
pattern-matching the command that requests it. This module is the filesystem half of that: given a
:class:`FileAction`, it reports the path that would change, how it would change, and whether the
target sits inside a critical system directory.

Nothing here touches the disk. That is the point of the class, not an implementation detail, so two
things are worth spelling out:

* The set of paths believed to exist is supplied by the caller and held in memory. The simulator
  never scans, stats, opens or writes anything.
* Path normalisation goes through :func:`os.path.normpath`, which is pure string work.
  :meth:`pathlib.Path.resolve` is deliberately avoided: it stats the filesystem to follow symlinks,
  which would be real I/O.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

__all__ = [
    "CRITICAL_SYSTEM_PATHS",
    "FileAction",
    "FileOperation",
    "MutationResult",
    "MutationType",
    "ShadowFilesystem",
]


# Directories where a write or delete is a system-level concern rather than a workspace edit.
# This mirrors the set used by `panoptis.core.blast_radius`; see the note in the PR about
# hoisting a single shared constant once the v2 lattice settles.
CRITICAL_SYSTEM_PATHS: frozenset[str] = frozenset(
    {"/etc", "/var", "/boot", "/root", "/home", "/usr", "/bin", "/sbin"}
)


class FileOperation(str, Enum):
    """What an agent is asking to do to a path."""

    WRITE = "write"
    APPEND = "append"
    DELETE = "delete"
    MKDIR = "mkdir"


class MutationType(str, Enum):
    """How the shadow tree would change as a result."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


@dataclass(frozen=True)
class FileAction:
    """A requested filesystem operation.

    `path` may be relative, in which case it is interpreted against the shadow root.
    """

    operation: FileOperation
    path: Path


@dataclass(frozen=True)
class MutationResult:
    """What :meth:`ShadowFilesystem.simulate` worked out about an action.

    Attributes:
        target_path: The normalised absolute path the action would affect.
        mutation: Whether the target would be created, modified or deleted.
        is_critical_path: The target is a critical system directory or lives inside one.
        existed_before: The shadow tree already knew about the target. A delete with this set to
            False is a no-op rather than a real removal, and a write with it set to True is an
            overwrite rather than a new file.
        escapes_root: The target lies outside the shadow root, so the action reaches beyond the
            workspace it was scoped to.
    """

    target_path: Path
    mutation: MutationType
    is_critical_path: bool
    existed_before: bool
    escapes_root: bool


@dataclass
class ShadowFilesystem:
    """A filesystem model that answers "what would this action do?" without doing it.

    Args:
        root: The workspace the agent is scoped to. Relative action paths resolve against it.
        known_paths: Paths the caller believes already exist. Supplied rather than discovered,
            because discovering them would mean reading the disk.

    Example:
        >>> fs = ShadowFilesystem(Path("/srv/app"), known_paths=[Path("/srv/app/main.py")])
        >>> fs.simulate(FileAction(FileOperation.WRITE, Path("main.py"))).mutation
        <MutationType.MODIFY: 'modify'>
        >>> fs.simulate(FileAction(FileOperation.DELETE, Path("/etc/passwd"))).is_critical_path
        True
    """

    root: Path
    known_paths: set[Path] = field(default_factory=set)

    def __init__(self, root: Path, known_paths: Iterable[Path] | None = None) -> None:
        root = Path(root)
        if not self._is_posix_absolute(root):
            raise ValueError(f"ShadowFilesystem root must be an absolute path, got {root!r}")
        # root is now known absolute, so this call cannot take the relative branch of
        # _normalise and cannot double up on itself — it only collapses `.`/`..` segments.
        self.root = self._normalise(root, root=root)
        self.known_paths = {self._normalise(Path(p), root=self.root) for p in (known_paths or ())}

    # ------------------------------------------------------------------ public API

    def simulate(self, action: FileAction) -> MutationResult:
        """Work out what `action` would do. Leaves the shadow tree untouched."""
        target = self._normalise(Path(action.path), root=self.root)
        existed = target in self.known_paths

        if action.operation is FileOperation.DELETE:
            mutation = MutationType.DELETE
        else:
            mutation = MutationType.MODIFY if existed else MutationType.CREATE

        return MutationResult(
            target_path=target,
            mutation=mutation,
            is_critical_path=self._is_critical(target),
            existed_before=existed,
            escapes_root=self._escapes_root(target),
        )

    def apply(self, action: FileAction) -> MutationResult:
        """Simulate `action` and fold the outcome into the shadow tree.

        Use this to model a *sequence* of actions, where a file created by an earlier step should
        read as an overwrite when a later step writes to it again. Still no disk access.
        """
        result = self.simulate(action)
        if result.mutation is MutationType.DELETE:
            self.known_paths.discard(result.target_path)
        else:
            self.known_paths.add(result.target_path)
        return result

    def exists(self, path: Path) -> bool:
        """Whether the shadow tree believes `path` exists. Never consults the disk."""
        return self._normalise(Path(path), root=self.root) in self.known_paths

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _is_posix_absolute(path: Path) -> bool:
        """Whether `path` is absolute, using POSIX semantics regardless of platform.

        `Path.is_absolute()` alone is wrong here: the paths this simulator reasons about —
        critical directories like ``/etc``, workspace roots, targets from an agent's action —
        are POSIX paths regardless of the OS the lattice happens to run on, but
        `PureWindowsPath("/etc/passwd").is_absolute()` is ``False`` (there is no drive letter).
        Trusting that alone would make `root / path` join a real absolute path as if it were
        relative — silently folding it underneath the workspace root, and making
        `_is_critical` and `_escapes_root` wrong on Windows. A leading-slash string test is
        checked first and is enough on its own to cover the POSIX case correctly everywhere.
        """
        return str(path).startswith(("/", "\\")) or path.is_absolute()

    @staticmethod
    def _normalise(path: Path, *, root: Path) -> Path:
        """Make `path` absolute and collapse `.` and `..`, without touching the filesystem.

        `Path.resolve()` is not used on purpose: it stats the filesystem to follow symlinks, and
        this class must never perform I/O. `os.path.normpath` collapses the same segments purely
        textually, which is what a simulation wants anyway — the answer should not depend on what
        happens to be on the machine running it. See :meth:`_is_posix_absolute` for why
        absoluteness itself is not checked with `Path.is_absolute()` alone.
        """
        candidate = path if ShadowFilesystem._is_posix_absolute(path) else root / path
        return Path(os.path.normpath(str(candidate)))

    @staticmethod
    def _is_critical(target: Path) -> bool:
        """Whether `target` is a critical system directory or sits beneath one."""
        parents = set(target.parents)
        return any(
            target == Path(prefix) or Path(prefix) in parents for prefix in CRITICAL_SYSTEM_PATHS
        )

    def _escapes_root(self, target: Path) -> bool:
        """Whether `target` falls outside the workspace the simulator is scoped to."""
        return target != self.root and self.root not in target.parents
