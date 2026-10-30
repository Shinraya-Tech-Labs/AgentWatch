"""
Tests for fix #133: asyncio.iscoroutinefunction replaced with inspect.iscoroutinefunction.

Verifies that:
- Async methods on wrapped agents are correctly detected and routed through the async path.
- No DeprecationWarning about asyncio.iscoroutinefunction is emitted from our adapters.
"""

from __future__ import annotations

import inspect
import warnings

from panoptis.adapters.autogen import AutoGenAdapter
from panoptis.adapters.langgraph import LangGraphAdapter
from panoptis.core.event_bus import EventBus
from panoptis.core.schema import AgentFramework
from panoptis.core.watcher import GenericAdapter

# ── Stub agents ──────────────────────────────────────────────────────────────


class _AsyncAgent:
    """Minimal agent with both sync and async entry points."""

    def run(self, prompt: str) -> str:
        return f"sync: {prompt}"

    async def arun(self, prompt: str) -> str:
        return f"async: {prompt}"


class _AsyncAutoGenAgent:
    __module__ = "autogen.agentchat.conversable_agent"

    def __init__(self) -> None:
        self.name = "async-test-agent"

    def generate_reply(self, messages=None, sender=None) -> str:
        return "sync reply"

    async def a_generate_reply(self, messages=None, sender=None) -> str:
        return "async reply"


class _AsyncLangGraphGraph:
    __module__ = "langgraph.graph.state"

    def invoke(self, inputs: dict) -> dict:
        return {"result": "sync"}

    async def ainvoke(self, inputs: dict) -> dict:
        return {"result": "async"}


# ── Tests ────────────────────────────────────────────────────────────────────


def test_inspect_detects_coroutine_correctly():
    """inspect.iscoroutinefunction must identify async methods as coroutines."""
    agent = _AsyncAgent()
    assert not inspect.iscoroutinefunction(agent.run)
    assert inspect.iscoroutinefunction(agent.arun)


def test_generic_adapter_wraps_async_method():
    """GenericAdapter._wrap must produce an async wrapper for async methods."""
    bus = EventBus()
    agent = _AsyncAgent()
    adapter = GenericAdapter(
        agent,
        framework=AgentFramework.CUSTOM,
        framework_label="custom",
        event_bus=bus,
    )
    adapter.attach()

    # After attach, the wrapped arun must still be a coroutine function
    assert inspect.iscoroutinefunction(agent.arun), "arun should be wrapped as a coroutine function"
    # The sync run wrapper must NOT be a coroutine
    assert not inspect.iscoroutinefunction(agent.run), "run should be wrapped as a regular function"


def test_autogen_adapter_wraps_async_method():
    """AutoGenAdapter._wrap must route async methods to the async wrapper."""
    bus = EventBus()
    agent = _AsyncAutoGenAgent()
    adapter = AutoGenAdapter(agent, event_bus=bus)
    adapter.attach()

    assert inspect.iscoroutinefunction(agent.a_generate_reply), (
        "a_generate_reply should be wrapped as a coroutine function"
    )
    assert not inspect.iscoroutinefunction(agent.generate_reply), (
        "generate_reply should be wrapped as a regular function"
    )


def test_langgraph_adapter_wraps_async_method():
    """LangGraphAdapter._wrap must route ainvoke to the async wrapper."""
    bus = EventBus()
    graph = _AsyncLangGraphGraph()
    adapter = LangGraphAdapter(graph, event_bus=bus)
    adapter.attach()

    assert inspect.iscoroutinefunction(graph.ainvoke), (
        "ainvoke should be wrapped as a coroutine function"
    )
    assert not inspect.iscoroutinefunction(graph.invoke), (
        "invoke should be wrapped as a regular function"
    )


def test_no_asyncio_iscoroutinefunction_deprecation_warning():
    """Wrapping agents must not emit DeprecationWarnings about asyncio.iscoroutinefunction."""
    bus = EventBus()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        agent = _AsyncAgent()
        adapter = GenericAdapter(agent, event_bus=bus)
        adapter.attach()

        autogen_agent = _AsyncAutoGenAgent()
        AutoGenAdapter(autogen_agent, event_bus=bus).attach()

        graph = _AsyncLangGraphGraph()
        LangGraphAdapter(graph, event_bus=bus).attach()

    deprecation_msgs = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning) and "iscoroutinefunction" in str(w.message)
    ]
    assert deprecation_msgs == [], (
        f"Unexpected DeprecationWarnings about iscoroutinefunction: {deprecation_msgs}"
    )


def test_generic_adapter_async_method_is_awaitable():
    """The wrapped async method must be an awaitable coroutine function."""
    import asyncio

    bus = EventBus()
    agent = _AsyncAgent()
    adapter = GenericAdapter(
        agent,
        framework=AgentFramework.CUSTOM,
        framework_label="custom",
        event_bus=bus,
    )
    adapter.attach()

    coro = agent.arun("hello")
    assert asyncio.iscoroutine(coro), "Calling arun() must return a coroutine object"
    # Clean up — close the coroutine to avoid 'never awaited' warnings
    coro.close()
