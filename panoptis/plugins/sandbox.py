"""Plugin sandbox for AgentWatch.

Provides PermissionEnforcer: a lightweight runtime guard that enforces
plugin permission manifests by wrapping subprocess execution, filesystem
operations, and module imports so plugins cannot exceed their declared
permissions.

Import control uses an explicit ALLOW-LIST rather than a deny-list.
A deny-list must be kept exhaustively up-to-date as the standard library
grows; an allow-list is safe by default and only widens when a specific
permission is granted.
"""

from __future__ import annotations

import logging
from typing import Any

from panoptis.core.schema import PluginManifest, PluginPermissions

logger = logging.getLogger(__name__)


class SandboxViolationError(Exception):
    """Raised when a plugin attempts an operation outside its declared permissions."""


# Modules that are safe for all plugins regardless of permissions.
# Only pure-computation / data-structure / serialization modules are
# included here. Nothing that touches the filesystem, network, OS,
# or spawns processes is in this base set.
_ALLOWED_MODULES_BASE: frozenset[str] = frozenset(
    {
        "abc",
        "base64",
        "binascii",
        "collections",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "hmac",
        "itertools",
        "json",
        "math",
        "operator",
        "random",
        "re",
        "secrets",
        "statistics",
        "string",
        "textwrap",
        "typing",
        "uuid",
    }
)

# Additional modules unlocked by each permission flag.
_MODULES_BY_PERMISSION: dict[str, frozenset[str]] = {
    "filesystem_read": frozenset({"pathlib", "io", "mmap", "tempfile"}),
    "filesystem_write": frozenset({"pathlib", "io", "mmap", "tempfile"}),
    "network_outbound": frozenset({"urllib", "http", "socket", "ssl", "email"}),
    "subprocess_exec": frozenset({"subprocess", "shlex"}),
}


def _build_allowed_modules(perms: PluginPermissions) -> frozenset[str]:
    """Return the set of importable module names for a given permission set."""
    allowed = set(_ALLOWED_MODULES_BASE)
    for perm_attr, extra_modules in _MODULES_BY_PERMISSION.items():
        if getattr(perms, perm_attr, False):
            allowed |= extra_modules
    return frozenset(allowed)


class PermissionEnforcer:
    """Enforce plugin permission manifests at runtime.

    Wraps subprocess, filesystem, and import calls so plugins cannot
    exceed the permissions declared in their manifest.
    """

    def __init__(self, manifest: PluginManifest) -> None:
        self._perms = manifest.permissions
        self._plugin_id = manifest.plugin_id
        self._violations: list[str] = []
        self._accessed: list[str] = []
        self._allowed_modules = _build_allowed_modules(manifest.permissions)

    def _check(self, permission: str, context: str) -> None:
        allowed = getattr(self._perms, permission, False)
        if not allowed:
            msg = (
                f"Plugin '{self._plugin_id}' attempted '{context}' "
                f"without '{permission}' permission"
            )
            self._violations.append(msg)
            raise SandboxViolationError(msg)
        self._accessed.append(context)

    def safe_open(self, path: str, mode: str = "r", **kwargs: Any):
        """Permission-enforced open() replacement.

        Checks filesystem_read or filesystem_write permission before
        delegating to the real open() call.
        """
        is_write = any(c in mode for c in ("w", "a", "x", "+"))
        if is_write:
            self._check("filesystem_write", f"write:{path}")
        else:
            self._check("filesystem_read", f"read:{path}")
        return open(path, mode, **kwargs)  # noqa: SIM115

    def safe_exec(self, cmd: list[str], **kwargs: Any) -> Any:
        """Permission-enforced subprocess execution.

        cmd must be a list of strings. Passing a list instead of a shell
        string ensures the OS exec family is used directly, preventing
        shell metacharacter injection (semicolons, pipes, backticks, etc.)
        that would be interpreted by /bin/sh when shell=True is used.
        """
        if not isinstance(cmd, list) or not cmd:
            raise ValueError("cmd must be a non-empty list of strings")
        self._check("subprocess_exec", f"exec:{cmd[0]}")
        import subprocess  # nosec B404 — subprocess is gated behind an allow-list check above

        return subprocess.run(cmd, shell=False, **kwargs)  # noqa: S603  # nosec B603 — shell=False, cmd validated as list above

    def restricted_import(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Import gate based on the allow-list for this plugin's permissions.

        Only modules in _ALLOWED_MODULES_BASE plus those unlocked by the
        plugin's declared permissions can be imported. All others raise
        SandboxViolationError, including modules that bypass the open()
        enforcer (pathlib, io, mmap, tempfile) when filesystem_read is
        not granted.
        """
        base = name.split(".")[0]
        if base not in self._allowed_modules:
            msg = f"Plugin '{self._plugin_id}': import of '{name}' is not permitted"
            self._violations.append(msg)
            raise SandboxViolationError(msg)
        return __import__(name, *args, **kwargs)

    @property
    def violations(self) -> list[str]:
        return list(self._violations)

    @property
    def accessed_resources(self) -> list[str]:
        return list(self._accessed)
