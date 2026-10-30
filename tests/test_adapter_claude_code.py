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
