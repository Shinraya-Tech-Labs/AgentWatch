"""Tests for plugin sandbox permission enforcement.

Covers the bypass vectors identified in issues #97 and #98:
  - shell=True command injection via metacharacters
  - filesystem modules (pathlib, io, mmap, tempfile) bypassing
    the open() enforcer when filesystem_read=False
"""

from __future__ import annotations

import sys

import pytest

from panoptis.core.schema import PluginManifest, PluginPermissions
from panoptis.plugins.sandbox import (
    PermissionEnforcer,
    SandboxViolationError,
    _build_allowed_modules,
)


def _make_enforcer(**perms: bool) -> PermissionEnforcer:
    """Helper: build a PermissionEnforcer with only the named perms set."""
    permissions = PluginPermissions(**perms)
    manifest = PluginManifest(
        plugin_id="test-plugin",
        name="Test Plugin",
        version="0.1.0",
        author="test",
        description="Test plugin for sandbox tests",
        permissions=permissions,
    )
    return PermissionEnforcer(manifest)


# ─── safe_exec ───────────────────────────────────────────────────────────────


def test_safe_exec_requires_list():
    enforcer = _make_enforcer(subprocess_exec=True)
    with pytest.raises(ValueError, match="non-empty list"):
        enforcer.safe_exec("echo hello")  # type: ignore[arg-type]


def test_safe_exec_empty_list_raises():
    enforcer = _make_enforcer(subprocess_exec=True)
    with pytest.raises(ValueError, match="non-empty list"):
        enforcer.safe_exec([])


def test_safe_exec_without_permission_raises():
    enforcer = _make_enforcer(subprocess_exec=False)
    with pytest.raises(SandboxViolationError, match="subprocess_exec"):
        enforcer.safe_exec(["echo", "hello"])


def test_safe_exec_with_permission_runs():
    enforcer = _make_enforcer(subprocess_exec=True)
    result = enforcer.safe_exec(
        [sys.executable, "-c", "print('hello')"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_safe_exec_shell_metacharacters_not_interpreted():
    """Semicolon must be treated as a literal argument, not a command separator."""
    enforcer = _make_enforcer(subprocess_exec=True)
    # If shell=True were used, 'echo safe; echo injected' would run two commands.
    # With shell=False it is passed as a single argv element to echo.
    result = enforcer.safe_exec(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "safe; echo injected"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "injected" not in result.stdout.splitlines()
    # The semicolon appears literally in the output
    assert "safe; echo injected" in result.stdout


# ─── restricted_import allow-list ────────────────────────────────────────────


def test_base_modules_always_allowed():
    enforcer = _make_enforcer()
    # json and math are in _ALLOWED_MODULES_BASE
    mod = enforcer.restricted_import("json")
    assert mod.__name__ == "json"


def test_os_blocked_by_default():
    enforcer = _make_enforcer()
    with pytest.raises(SandboxViolationError, match="'os'"):
        enforcer.restricted_import("os")


def test_sys_blocked_by_default():
    enforcer = _make_enforcer()
    with pytest.raises(SandboxViolationError, match="'sys'"):
        enforcer.restricted_import("sys")


# ─── pathlib bypass (issue #98) ──────────────────────────────────────────────


def test_pathlib_blocked_without_filesystem_read():
    """pathlib can read files without calling open(); must be blocked."""
    enforcer = _make_enforcer(filesystem_read=False)
    with pytest.raises(SandboxViolationError, match="'pathlib'"):
        enforcer.restricted_import("pathlib")


def test_pathlib_allowed_with_filesystem_read():
    enforcer = _make_enforcer(filesystem_read=True)
    mod = enforcer.restricted_import("pathlib")
    assert mod.__name__ == "pathlib"


# ─── io bypass (issue #98) ───────────────────────────────────────────────────


def test_io_blocked_without_filesystem_read():
    """io.open() bypasses the safe_open enforcer; must be blocked."""
    enforcer = _make_enforcer(filesystem_read=False)
    with pytest.raises(SandboxViolationError, match="'io'"):
        enforcer.restricted_import("io")


def test_io_allowed_with_filesystem_read():
    enforcer = _make_enforcer(filesystem_read=True)
    mod = enforcer.restricted_import("io")
    assert mod.__name__ == "io"


# ─── mmap bypass (issue #98) ─────────────────────────────────────────────────


def test_mmap_blocked_without_filesystem_read():
    """mmap maps file descriptors directly; must be blocked."""
    enforcer = _make_enforcer(filesystem_read=False)
    with pytest.raises(SandboxViolationError, match="'mmap'"):
        enforcer.restricted_import("mmap")


def test_mmap_allowed_with_filesystem_read():
    enforcer = _make_enforcer(filesystem_read=True)
    mod = enforcer.restricted_import("mmap")
    assert mod.__name__ == "mmap"


# ─── tempfile bypass (issue #98) ─────────────────────────────────────────────


def test_tempfile_blocked_without_filesystem_read():
    """tempfile creates and reads temporary files; must be blocked."""
    enforcer = _make_enforcer(filesystem_read=False)
    with pytest.raises(SandboxViolationError, match="'tempfile'"):
        enforcer.restricted_import("tempfile")


def test_tempfile_allowed_with_filesystem_read():
    enforcer = _make_enforcer(filesystem_read=True)
    mod = enforcer.restricted_import("tempfile")
    assert mod.__name__ == "tempfile"


# ─── network modules controlled by network_outbound ──────────────────────────


def test_urllib_blocked_without_network_outbound():
    enforcer = _make_enforcer(network_outbound=False)
    with pytest.raises(SandboxViolationError, match="'urllib'"):
        enforcer.restricted_import("urllib")


def test_socket_blocked_without_network_outbound():
    enforcer = _make_enforcer(network_outbound=False)
    with pytest.raises(SandboxViolationError, match="'socket'"):
        enforcer.restricted_import("socket")


def test_urllib_allowed_with_network_outbound():
    enforcer = _make_enforcer(network_outbound=True)
    mod = enforcer.restricted_import("urllib")
    assert mod.__name__ == "urllib"


# ─── subprocess controlled by subprocess_exec ────────────────────────────────


def test_subprocess_blocked_without_subprocess_exec():
    enforcer = _make_enforcer(subprocess_exec=False)
    with pytest.raises(SandboxViolationError, match="'subprocess'"):
        enforcer.restricted_import("subprocess")


def test_subprocess_allowed_with_subprocess_exec():
    enforcer = _make_enforcer(subprocess_exec=True)
    mod = enforcer.restricted_import("subprocess")
    assert mod.__name__ == "subprocess"


# ─── violation tracking ──────────────────────────────────────────────────────


def test_violations_recorded():
    enforcer = _make_enforcer()
    with pytest.raises(SandboxViolationError):
        enforcer.restricted_import("os")
    assert len(enforcer.violations) == 1
    assert "os" in enforcer.violations[0]


def test_accessed_resources_recorded():
    enforcer = _make_enforcer(filesystem_read=True)
    enforcer.restricted_import("pathlib")
    # restricted_import does not append to _accessed; only _check does
    # when called via safe_open / safe_exec. Verify no false positives.
    assert enforcer.violations == []


# ─── _build_allowed_modules ──────────────────────────────────────────────────


def test_build_allowed_modules_base_only():
    perms = PluginPermissions()
    allowed = _build_allowed_modules(perms)
    assert "json" in allowed
    assert "pathlib" not in allowed
    assert "os" not in allowed


def test_build_allowed_modules_filesystem_adds_pathlib():
    perms = PluginPermissions(filesystem_read=True)
    allowed = _build_allowed_modules(perms)
    assert "pathlib" in allowed
    assert "io" in allowed
    assert "mmap" in allowed
    assert "tempfile" in allowed
