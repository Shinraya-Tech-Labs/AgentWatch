from __future__ import annotations

import shutil

import pytest

from panoptis.adapters.claude_code import ClaudeCodeAdapter


async def _empty_stream():
    if False:  # pragma: no cover - makes this an async generator
        yield b""


class _FakeProcess:
    returncode = 0
    stderr = None

    def __init__(self) -> None:
        self.stdout = _empty_stream()

    async def wait(self) -> int:
        return 0


async def test_missing_cli_reports_a_clear_error(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="not found on PATH"):
        await ClaudeCodeAdapter()._run_subprocess(["claude", "-p", "hi"])


async def test_spawns_the_shim_resolved_from_path(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: rf"C:\nodejs\{name}.CMD")
    captured: dict[str, object] = {}

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        return _FakeProcess()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await ClaudeCodeAdapter()._run_subprocess(["claude", "-p", "hi"])

    # Windows npm shims are .CMD files; the bare name is not executable.
    assert captured["argv"] == (r"C:\nodejs\claude.CMD", "-p", "hi")


async def _lines(*lines: bytes):
    for line in lines:
        yield line


async def test_a_violation_in_the_stream_is_a_detection_not_a_block(monkeypatch) -> None:
    """TO-012: the CLI has already run the tool when its stream event is read."""
    import json

    from panoptis.core.schema import EventType, ExecutionStatus

    monkeypatch.setattr(shutil, "which", lambda name: name)
    call = {"type": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "rm -rf /"}}

    class Proc(_FakeProcess):
        def __init__(self) -> None:
            self.stdout = _lines(json.dumps(call).encode() + b"\n")

    async def fake_exec(*argv, **kwargs):
        return Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = ClaudeCodeAdapter()
    await adapter._run_subprocess(["claude", "-p", "hi"])

    assert not any(e.is_blocked for e in adapter._events), "nothing was prevented"
    assert not any(e.event_type == EventType.SAFETY_BLOCK for e in adapter._events)
    tool = next(e for e in adapter._events if e.event_type == EventType.TOOL_CALL)
    assert tool.metadata["policy_violation"] is True
    assert tool.metadata["enforcement"] == "observation_only"
    check = next(e for e in adapter._events if e.event_type == EventType.SAFETY_CHECK)
    assert check.status == ExecutionStatus.FAILURE
    assert check.metadata["detected_after_execution"] is True
