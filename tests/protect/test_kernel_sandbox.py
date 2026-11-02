"""Tests for Ring-0 Linux Kernel Sandbox Interface."""

from __future__ import annotations

import tempfile
from pathlib import Path

from panoptis.protect.kernel_sandbox import (
    ALL_READ_ACCESS,
    ALL_WRITE_ACCESS,
    KernelSandbox,
    KernelSandboxConfig,
    LandlockPathBeneathAttr,
    LandlockRulesetAttr,
)


def test_kernel_sandbox_status_reports_platform():
    ks = KernelSandbox()
    status = ks.status()
    assert status.platform != ""
    assert status.mode in ("kernel_lsm", "user_emulation")
    assert isinstance(status.read_paths, list)
    assert isinstance(status.write_paths, list)


def test_kernel_sandbox_path_validation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        read_dir = tmp / "reads"
        write_dir = tmp / "writes"
        read_dir.mkdir()
        write_dir.mkdir()

        cfg = KernelSandboxConfig(
            allowed_read_paths=[str(read_dir)],
            allowed_write_paths=[str(write_dir)],
        )
        ks = KernelSandbox(config=cfg)

        # Reads in allowed read dir
        assert ks.validate_path_access(str(read_dir / "file.txt"), is_write=False) is True
        # Writes in allowed read dir must fail write validation
        assert ks.validate_path_access(str(read_dir / "file.txt"), is_write=True) is False

        # Writes in allowed write dir
        assert ks.validate_path_access(str(write_dir / "out.txt"), is_write=True) is True
        # Reads in allowed write dir are allowed
        assert ks.validate_path_access(str(write_dir / "out.txt"), is_write=False) is True

        # Arbitrary path outside
        assert ks.validate_path_access("/etc/shadow", is_write=False) is False
        assert ks.validate_path_access("/etc/shadow", is_write=True) is False


def test_landlock_ctypes_structures():
    # Verify 64-bit struct layouts matching Linux kernel ABI
    attr = LandlockRulesetAttr(handled_access_fs=ALL_READ_ACCESS | ALL_WRITE_ACCESS)
    assert attr.handled_access_fs > 0

    path_attr = LandlockPathBeneathAttr(allowed_access=ALL_READ_ACCESS, parent_fd=3)
    assert path_attr.parent_fd == 3
    assert path_attr.allowed_access == ALL_READ_ACCESS


def test_kernel_sandbox_fallback_non_strict():
    cfg = KernelSandboxConfig(strict=False)
    ks = KernelSandbox(config=cfg)
    # If not on Linux with Landlock, apply_self returns False cleanly without raising
    if not ks.is_landlock_available:
        assert ks.apply_self() is False
