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


@pytest.mark.parametrize(
    "verdict",
    [None, "allow", (None, []), ("false", []), (0, []), (False, "reason"), (False, [1]), (False,)],
)
def test_sync_invalid_verdict_blocks(verdict):
    class BadEngine(SafetyEngine):
        def check_tool_call_sync(self, tool_call: Any) -> Any:
            return verdict

    agent = _attach(CountingAgent(), BadEngine())
    with pytest.raises(AgentWatchBlockedError) as exc:
        agent.run("echo hello")
    assert agent.calls == 0
    assert exc.value.reasons == ["safety_check_failed: ValueError"]


@pytest.mark.parametrize("verdict", [None, "allow", {"blocked": False}])
def test_async_invalid_verdict_blocks(verdict):
    class BadEngine(SafetyEngine):
        async def check_event(self, event: Any) -> Any:
            return verdict

    agent = _attach(AsyncCountingAgent(), BadEngine())
    with pytest.raises(AgentWatchBlockedError):
        asyncio.run(agent.arun("echo hello"))
    assert agent.calls == 0


def test_async_event_without_safety_data_blocks():
    class NoDecisionEngine(SafetyEngine):
        async def check_event(self, event: Any) -> Any:
            return event  # returned unchanged: no safety decision attached

    agent = _attach(AsyncCountingAgent(), NoDecisionEngine())
    with pytest.raises(AgentWatchBlockedError):
        asyncio.run(agent.arun("echo hello"))
    assert agent.calls == 0


def test_policy_that_cannot_load_blocks_every_tool_call(monkeypatch):
    from agentwatch.core import watcher

    def broken_init(self: Any, *a: Any, **k: Any) -> None:
        raise OSError("policy file unreadable")

    monkeypatch.setattr(watcher.SafetyEngine, "__init__", broken_init)
    agent = watcher.watch(CountingAgent(), event_bus=EventBus())
    assert getattr(agent.run, "_agentwatch_wrapped", False), "the agent must still be wrapped"
    with pytest.raises(AgentWatchBlockedError) as exc:
        agent.run("echo hello")
    assert agent.calls == 0
    assert exc.value.reasons == ["safety_check_failed: RuntimeError"]

    async_agent = watcher.watch(AsyncCountingAgent(), event_bus=EventBus())
    with pytest.raises(AgentWatchBlockedError):
        asyncio.run(async_agent.arun("echo hello"))
    assert async_agent.calls == 0


class StreamingAgent:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, command: str):
        self.calls += 1
        yield f"chunk of {command}"


def test_allowed_calls_execute_exactly_once_on_every_path():
    sync_agent = _attach(CountingAgent())
    for i in range(3):
        sync_agent.run(f"echo {i}")
    assert sync_agent.calls == 3

    async_agent = _attach(AsyncCountingAgent())
    asyncio.run(async_agent.arun("echo hi"))
    assert async_agent.calls == 1

    streamer = _attach(StreamingAgent())
    assert list(streamer.stream("echo hi")) == ["chunk of echo hi"]
    assert streamer.calls == 1


def test_blocked_calls_never_execute_on_any_path():
    sync_agent = _attach(CountingAgent())
    with pytest.raises(AgentWatchBlockedError):
        sync_agent.run("rm -rf /")
    async_agent = _attach(AsyncCountingAgent())
    with pytest.raises(AgentWatchBlockedError):
        asyncio.run(async_agent.arun("rm -rf /"))
    streamer = _attach(StreamingAgent())
    with pytest.raises(AgentWatchBlockedError):
        list(streamer.stream("rm -rf /"))
    assert (sync_agent.calls, async_agent.calls, streamer.calls) == (0, 0, 0)


def test_watching_twice_does_not_double_wrap_or_double_execute():
    from agentwatch.core.watcher import watch

    checks = []

    class CountingEngine(SafetyEngine):
        def check_tool_call_sync(self, tool_call: Any) -> tuple[bool, list[str]]:
            checks.append(tool_call.tool_name)
            return False, []

    agent = CountingAgent()
    GenericAdapter(agent, event_bus=EventBus(), safety_engine=CountingEngine()).attach()
    watch(agent, event_bus=EventBus())
    agent.run("echo hi")
    assert agent.calls == 1
    assert checks == ["run"], "a second watch() must not stack another wrapper"
