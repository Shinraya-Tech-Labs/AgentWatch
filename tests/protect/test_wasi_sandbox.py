"""Tests for WASI & Copy-on-Write (CoW) Micro-Worker Sandbox."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from panoptis.protect.wasi_sandbox import (
    SandboxExecutionError,
    WasiSandbox,
    WasiSandboxConfig,
    WorkspaceSnapshot,
)


def test_snapshot_detects_created_modified_deleted():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        initial_file = ws / "existing.txt"
        initial_file.write_text("v1", encoding="utf-8")

        snap = WorkspaceSnapshot(ws)

        # 1. Modify
        initial_file.write_text("v2", encoding="utf-8")
        # 2. Create
        new_file = ws / "created.txt"
        new_file.write_text("brand new", encoding="utf-8")

        diff = snap.compute_diff()
        assert "existing.txt" in diff.modified_files
        assert "created.txt" in diff.created_files
        assert diff.deleted_files == []

        # 3. Rollback
        snap.rollback()

        # Check restored state
        assert initial_file.read_text(encoding="utf-8") == "v1"
        assert not new_file.exists()
        snap.cleanup()


def test_wasi_sandbox_function_success_and_diff():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        cfg = WasiSandboxConfig(workspace_dir=ws)
        sandbox = WasiSandbox(cfg)

        def make_file():
            (ws / "artifact.json").write_text('{"ok": true}', encoding="utf-8")
            return 42

        res = sandbox.run_function(make_file)
        assert res.success is True
        assert res.output == 42
        assert "artifact.json" in res.diff.created_files
        assert res.rolled_back is False
        assert (ws / "artifact.json").exists()


def test_wasi_sandbox_function_error_triggers_cow_rollback():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        orig = ws / "database.db"
        orig.write_text("PRISTINE_DATA", encoding="utf-8")

        cfg = WasiSandboxConfig(workspace_dir=ws, enable_cow_rollback=True)
        sandbox = WasiSandbox(cfg)

        def malicious_worker():
            orig.write_text("CORRUPTED_DATA", encoding="utf-8")
            (ws / "backdoor.sh").write_text("evil", encoding="utf-8")
            raise PermissionError("Attempted unauthorized exfiltration")

        with pytest.raises(SandboxExecutionError) as exc_info:
            sandbox.run_function(malicious_worker, rollback_on_error=True)

        assert exc_info.value.result.rolled_back is True
        # Verify rollback reverted corrupted file and deleted backdoor
        assert orig.read_text(encoding="utf-8") == "PRISTINE_DATA"
        assert not (ws / "backdoor.sh").exists()


def test_wasi_sandbox_run_command_timeout():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        cfg = WasiSandboxConfig(workspace_dir=ws, timeout_seconds=0.1)
        sandbox = WasiSandbox(cfg)

        with pytest.raises(SandboxExecutionError) as exc_info:
            # Long sleep command exceeding 0.1s
            cmd = "import time; time.sleep(2)"
            sandbox.run_command(f'python -c "{cmd}"')

        assert exc_info.value.result.rolled_back is True


def test_pipeline_integration_with_wasi_sandbox_and_cow_rollback():
    from panoptis.protect.model import ToolAction
    from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        tracked_file = ws / "critical_data.txt"
        tracked_file.write_text("INITIAL_STATE", encoding="utf-8")

        p_cfg = ProtectionConfig(
            enable_wasi_sandbox=True,
            sandbox_config=WasiSandboxConfig(workspace_dir=ws, enable_cow_rollback=True),
        )
        pipeline = ProtectionPipeline(config=p_cfg)
        pipeline.register_agent("agent_alpha")
        pipeline.start_session("agent_alpha", "s1", objective="Perform verified database updates")

        # 1. Successful execution through pipeline
        act_ok = ToolAction(
            call_id="call_001",
            agent_id="agent_alpha",
            session_id="s1",
            tool="write_file",
            arguments={"path": str(tracked_file), "content": "NEW_DATA"},
        )

        def tool_fn(**args):
            tracked_file.write_text("NEW_DATA", encoding="utf-8")
            return "SUCCESS"

        res = pipeline.execute(act_ok, fn=tool_fn)
        assert res == "SUCCESS"
        assert tracked_file.read_text(encoding="utf-8") == "NEW_DATA"

        # 2. Failing execution with error inside sandbox triggers automatic rollback
        act_err = ToolAction(
            call_id="call_002",
            agent_id="agent_alpha",
            session_id="s1",
            tool="write_file",
            arguments={"path": str(tracked_file), "content": "BAD_DATA"},
        )

        def failing_fn(**args):
            tracked_file.write_text("TAMPERED_DATA", encoding="utf-8")
            (ws / "untrusted_payload.bin").write_text("malicious", encoding="utf-8")
            raise RuntimeError("Database constraint failed")

        with pytest.raises(SandboxExecutionError):
            pipeline.execute(act_err, fn=failing_fn)

        # Confirm rollback restored tracked_file to NEW_DATA and untrusted_payload was removed
        assert tracked_file.read_text(encoding="utf-8") == "NEW_DATA"
        assert not (ws / "untrusted_payload.bin").exists()
