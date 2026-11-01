"""TO-001: the generic watch() adapter fails closed.

Every test counts the wrapped tool's side effects. Whenever enforcement cannot reach a
valid decision, the counter must stay at zero; allowed calls must run exactly once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentwatch.core.event_bus import EventBus
from agentwatch.core.safety import SafetyEngine
from agentwatch.core.watcher import AgentWatchBlockedError, GenericAdapter


class CountingAgent:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, command: str) -> str:
        self.calls += 1
        return f"ran {command}"


class RaisingEngine(SafetyEngine):
    def check_tool_call_sync(self, tool_call: Any) -> tuple[bool, list[str]]:
        raise RuntimeError("policy backend unavailable")


def _attach(agent: Any, engine: SafetyEngine | None = None) -> Any:
    GenericAdapter(agent, event_bus=EventBus(), safety_engine=engine).attach()
    return agent


def test_sync_safety_check_exception_blocks_and_tool_never_runs():
    agent = _attach(CountingAgent(), RaisingEngine())
    with pytest.raises(AgentWatchBlockedError) as exc:
        agent.run("echo hello")
    assert agent.calls == 0
    assert "fails closed" in str(exc.value)
    assert exc.value.reasons == ["safety_check_failed: RuntimeError"]


def test_sync_allowed_call_runs_exactly_once():
    agent = _attach(CountingAgent())
    assert agent.run("echo hello") == "ran echo hello"
    assert agent.calls == 1


def test_sync_telemetry_failure_does_not_block_an_allowed_call():
    class BrokenBus(EventBus):
        def publish_sync(self, event: Any) -> None:
            raise OSError("collector down")

    agent = CountingAgent()
    GenericAdapter(agent, event_bus=BrokenBus()).attach()
    assert agent.run("echo hello") == "ran echo hello"
    assert agent.calls == 1


class AsyncCountingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def arun(self, command: str) -> str:
        self.calls += 1
        return f"ran {command}"


class AsyncRaisingEngine(SafetyEngine):
    async def check_event(self, event: Any) -> Any:
        raise RuntimeError("policy backend unavailable")


def test_async_safety_check_exception_blocks_and_tool_never_runs():
    agent = _attach(AsyncCountingAgent(), AsyncRaisingEngine())
    with pytest.raises(AgentWatchBlockedError) as exc:
        asyncio.run(agent.arun("echo hello"))
    assert agent.calls == 0
    assert exc.value.reasons == ["safety_check_failed: RuntimeError"]


def test_async_allowed_call_runs_exactly_once():
    agent = _attach(AsyncCountingAgent())
    assert asyncio.run(agent.arun("echo hello")) == "ran echo hello"
    assert agent.calls == 1


def test_async_safety_check_timeout_blocks():
    class HangingEngine(SafetyEngine):
        async def check_event(self, event: Any) -> Any:
            await asyncio.sleep(10)

    agent = AsyncCountingAgent()
    GenericAdapter(
        agent, event_bus=EventBus(), safety_engine=HangingEngine(), safety_timeout_s=0.05
    ).attach()
    with pytest.raises(AgentWatchBlockedError) as exc:
        asyncio.run(agent.arun("echo hello"))
    assert agent.calls == 0
    assert "TimeoutError" in exc.value.reasons[0]


def test_sync_safety_check_timeout_blocks():
    import threading

    release = threading.Event()

    class HangingEngine(SafetyEngine):
        def check_tool_call_sync(self, tool_call: Any) -> tuple[bool, list[str]]:
            release.wait(5)
            return False, []

    agent = CountingAgent()
    GenericAdapter(
        agent, event_bus=EventBus(), safety_engine=HangingEngine(), safety_timeout_s=0.05
    ).attach()
    try:
        with pytest.raises(AgentWatchBlockedError) as exc:
            agent.run("echo hello")
        assert agent.calls == 0
        assert "TimeoutError" in exc.value.reasons[0]
    finally:
        release.set()


def test_default_async_deadline_exceeds_the_approval_wait():
    adapter = GenericAdapter(AsyncCountingAgent(), event_bus=EventBus())
    assert adapter._async_deadline() > adapter._safety_engine.policy.approval_timeout_seconds
