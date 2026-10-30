from __future__ import annotations

import asyncio

import pytest

from panoptis.adapters.generic import GenericCLIAdapter
from panoptis.core.event_bus import EventBus
from panoptis.core.schema import EventType, ExecutionStatus


class _FakeProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"ok", b""


async def test_run_uses_the_configured_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_shell(cmd: str, **kwargs: object) -> _FakeProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    bus = EventBus()
    events = []
    bus.subscribe_fn(lambda event: events.append(event), handler_id="test.capture")

    adapter = GenericCLIAdapter(agent_cmd="ollama run llama3", event_bus=bus)
    await adapter.run("Write a python script")

    assert captured["cmd"] == 'ollama run llama3 "Write a python script"'
    assert [e.event_type for e in events] == [
        EventType.SESSION_START,
        EventType.TOOL_RESULT,
        EventType.SESSION_END,
    ]

    # The live renderer runs on every event the adapter emits; a stale event
    # type string here would abort an otherwise successful watch.
    from panoptis.cli.main import _print_live_event

    for event in events:
        _print_live_event(event)


async def test_missing_command_is_rejected_at_construction() -> None:
    with pytest.raises(TypeError):
        GenericCLIAdapter()  # type: ignore[call-arg]


class _FailingProcess:
    returncode = 1

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b"\x1b[91m\x1b[1mError: \x1b[0minsufficient credits"


async def test_failed_run_is_marked_failed_and_ansi_stripped(monkeypatch) -> None:
    async def fake_shell(cmd: str, **kwargs: object) -> _FailingProcess:
        return _FailingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    bus = EventBus()
    events = []
    bus.subscribe_fn(lambda event: events.append(event), handler_id="test.capture")

    session = await GenericCLIAdapter(agent_cmd="opencode run", event_bus=bus).run("hi")

    assert session.status is ExecutionStatus.FAILURE
    assert events[1].tool_result.error == "Error: insufficient credits"
