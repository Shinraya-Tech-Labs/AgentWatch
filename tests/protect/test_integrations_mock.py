"""Unit tests for panoptis.protect.integrations using mock and duck-typed objects.

Ensures >85% coverage of integrations.py even when third-party frameworks
(langchain_core, smolagents, autogen) are not installed in the execution environment.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from panoptis.protect import ActionBlocked, ProtectionPipeline
from panoptis.protect.enforce import protection_context
from panoptis.protect.integrations import (
    _action,
    _mark,
    _named,
    _wrap_async,
    _wrap_sync,
    protect_agent,
    protect_autogen_tool,
    protect_langchain_tool,
    protect_langgraph_tool_node,
    protect_smolagents_agent,
    protect_smolagents_tool,
    protect_tools,
)


class DummyPipe(ProtectionPipeline):
    def __init__(self, block_tools: set[str] | None = None):
        super().__init__()
        self.block_tools = block_tools or set()

    def evaluate(self, action: Any, **kw: Any) -> Any:
        if action.tool in self.block_tools:
            # Force block
            from panoptis.protect.model import Decision

            return Decision(verdict="deny", reason="Blocked by test", tool=action.tool)
        return super().evaluate(action, **kw)


def test_action_and_context_identity():
    # Test fallback default identity
    act = _action("calc", {"x": 1}, "test-source", {})
    assert act.tool == "calc"
    assert act.arguments == {"x": 1}
    assert act.agent_id == "default-agent"
    assert act.session_id == "default-session"
    assert act.tenant_id == "default"
    assert act.defaulted_identity is True

    # Test explicit identity in ident dict
    act2 = _action(
        "search",
        {"q": "hello"},
        "test-source",
        {
            "agent_id": "a-123",
            "session_id": "s-456",
            "tenant_id": "t-789",
            "objective": "find things",
        },
    )
    assert act2.agent_id == "a-123"
    assert act2.session_id == "s-456"
    assert act2.tenant_id == "t-789"
    assert act2.objective == "find things"
    assert act2.defaulted_identity is False

    # Test protection_context propagation
    with protection_context(
        agent_id="ctx-agent", session_id="ctx-sess", tenant_id="ctx-ten", trace_id="tr-1"
    ):
        act3 = _action("db_read", {}, "source-3", {})
        assert act3.agent_id == "ctx-agent"
        assert act3.session_id == "ctx-sess"
        assert act3.tenant_id == "ctx-ten"
        assert act3.trace_id == "tr-1"


def test_mark_idempotency_and_unmarkable_objects():
    class Normal:
        pass

    obj = Normal()
    assert _mark(obj) is False  # First time: marked
    assert _mark(obj) is True  # Second time: already marked

    # Slotted / frozen object that rejects attribute setting
    class Slotted:
        __slots__ = ()

    slotted = Slotted()
    assert _mark(slotted) is False


def test_named_bind_arguments_variations():
    def simple_fn(a: int, b: str = "default") -> str:
        return f"{a}:{b}"

    named, rebuild = _named(simple_fn, (10,), {"b": "custom"})
    assert named == {"a": 10, "b": "custom"}
    args, kwargs = rebuild(named)
    assert simple_fn(*args, **kwargs) == "10:custom"

    # Builtin / uninspectable callable
    named2, rebuild2 = _named(len, ("abc",), {})
    args2, kwargs2 = rebuild2(named2)
    assert len(*args2, **kwargs2) == 3


def test_wrap_sync_and_async():
    pipe = DummyPipe(block_tools={"evil_tool"})

    # Sync wrapping
    def sync_tool(x: int) -> int:
        return x * 2

    wrapped_ok = _wrap_sync(
        sync_tool, "good_tool", "unit", pipe, {"agent_id": "a1", "session_id": "s1"}
    )
    assert wrapped_ok(5) == 10

    wrapped_bad = _wrap_sync(
        sync_tool, "evil_tool", "unit", pipe, {"agent_id": "a1", "session_id": "s1"}
    )
    with pytest.raises(ActionBlocked):
        wrapped_bad(5)

    # Async wrapping
    async def async_tool(msg: str) -> str:
        return f"echo: {msg}"

    wrapped_async_ok = _wrap_async(
        async_tool, "good_async", "unit", pipe, {"agent_id": "a1", "session_id": "s1"}
    )
    res = asyncio.run(wrapped_async_ok("hello"))
    assert res == "echo: hello"

    wrapped_async_bad = _wrap_async(
        async_tool, "evil_tool", "unit", pipe, {"agent_id": "a1", "session_id": "s1"}
    )
    with pytest.raises(ActionBlocked):
        asyncio.run(wrapped_async_bad("hello"))


def test_protect_langchain_tool_mock():
    # Mock tool with .func
    class MockLangChainFuncTool:
        def __init__(self):
            self.name = "query"
            self.func = lambda q: f"queried {q}"

    t1 = MockLangChainFuncTool()
    res_t1 = protect_langchain_tool(t1, pipeline=ProtectionPipeline())
    assert res_t1 is t1
    assert t1.func("users") == "queried users"
    # Idempotent call
    assert protect_langchain_tool(t1) is t1

    # Mock tool with ._run and ._arun
    class MockLangChainClassTool:
        name = "exec_cmd"

        def _run(self, cmd: str) -> str:
            return f"executed {cmd}"

        async def _arun(self, cmd: str) -> str:
            return f"async executed {cmd}"

    t2 = MockLangChainClassTool()
    protect_langchain_tool(t2, pipeline=ProtectionPipeline())
    assert t2._run("whoami") == "executed whoami"
    assert asyncio.run(t2._arun("whoami")) == "async executed whoami"

    # Mock tool with .coroutine
    class MockLangChainCoroutineTool:
        name = "async_fetch"
        func = None

        async def coroutine(self, url: str) -> str:
            return f"fetched {url}"

    t3 = MockLangChainCoroutineTool()
    protect_langchain_tool(t3, pipeline=ProtectionPipeline())
    assert asyncio.run(t3.coroutine("https://example.com")) == "fetched https://example.com"


def test_protect_langgraph_tool_node_mock():
    class MockTool:
        name = "run_bash"

        @staticmethod
        def func(cmd: str) -> str:
            return f"ran {cmd}"

    class MockToolNode:
        def __init__(self):
            self.tools_by_name = {"run_bash": MockTool()}

    node = MockToolNode()
    protect_langgraph_tool_node(node)
    assert getattr(node.tools_by_name["run_bash"], "_panoptis_protected", False) is True

    # Check fallback _tools_by_name
    class MockToolNodePrivate:
        def __init__(self):
            self._tools_by_name = {"run_bash": MockTool()}

    node2 = MockToolNodePrivate()
    protect_langgraph_tool_node(node2)
    assert getattr(node2._tools_by_name["run_bash"], "_panoptis_protected", False) is True

    # Invalid node raises TypeError
    with pytest.raises(TypeError, match="expected a langgraph ToolNode"):
        protect_langgraph_tool_node(object())


def test_protect_smolagents_mock():
    # Sync forward
    class MockSmolToolSync:
        name = "smol_calc"

        def forward(self, expr: str) -> int:
            return len(expr)

    st1 = MockSmolToolSync()
    protect_smolagents_tool(st1)
    assert st1.forward("1+1") == 3
    # Idempotent
    assert protect_smolagents_tool(st1) is st1

    # Async forward
    class MockSmolToolAsync:
        name = "smol_async_calc"

        async def forward(self, expr: str) -> int:
            return len(expr) * 10

    st2 = MockSmolToolAsync()
    protect_smolagents_tool(st2)
    assert asyncio.run(st2.forward("abc")) == 30

    # Agent with tools dict and final_answer
    class MockSmolAgentDict:
        def __init__(self):
            fa = MockSmolToolSync()
            fa.name = "final_answer"
            self.tools = {
                "t1": MockSmolToolSync(),
                "final_answer": fa,
            }

    sa1 = MockSmolAgentDict()
    protect_smolagents_agent(sa1)
    assert getattr(sa1.tools["t1"], "_panoptis_protected", False) is True
    assert getattr(sa1.tools["final_answer"], "_panoptis_protected", False) is False

    # Agent with tools list
    class MockSmolAgentList:
        def __init__(self):
            self.tools = [MockSmolToolSync()]

    sa2 = MockSmolAgentList()
    protect_smolagents_agent(sa2)
    assert getattr(sa2.tools[0], "_panoptis_protected", False) is True


def test_protect_autogen_mock():
    class MockAutoGenTool:
        name = "autogen_query"

        async def run_json(
            self, args: dict[str, Any], cancellation_token: Any = None
        ) -> dict[str, Any]:
            return {"status": "ok", "got": args}

    ag = MockAutoGenTool()
    protect_autogen_tool(ag)
    res = asyncio.run(ag.run_json({"key": "val"}, None))
    assert res == {"status": "ok", "got": {"key": "val"}}
    # Idempotent
    assert protect_autogen_tool(ag) is ag


def test_protect_tools_dispatcher():
    class MockSmolTool:
        __module__ = "smolagents.tools"
        name = "st"

        def forward(self, x: Any) -> Any:
            return x

    class MockAutoGenTool:
        __module__ = "autogen_core.tools"
        name = "at"

        async def run_json(self, args: dict[str, Any], cancellation_token: Any = None) -> str:
            return "auto"

    class MockLangChainDuckTool:
        args_schema = dict
        name = "lt"

        def invoke(self, *a: Any) -> Any:
            return None

        @staticmethod
        def func(x: Any) -> Any:
            return x

    # List & dict dispatch
    t_smol = MockSmolTool()
    t_auto = MockAutoGenTool()
    t_lc = MockLangChainDuckTool()

    protect_tools([t_smol, t_auto])
    assert getattr(t_smol, "_panoptis_protected", False) is True
    assert getattr(t_auto, "_panoptis_protected", False) is True

    protect_tools({"k": t_lc})
    assert getattr(t_lc, "_panoptis_protected", False) is True

    # Unsupported tool raises TypeError
    with pytest.raises(TypeError, match="unsupported tool type"):
        protect_tools(object())


def test_protect_agent_dispatcher():
    # Langchain agent
    class MockLCAgent:
        tools = [type("T", (), {"func": lambda: 1, "name": "t1"})()]

    assert protect_agent(MockLCAgent(), "langchain") == 1

    # Langgraph agent
    class MockLGNode:
        tools_by_name = {"t": type("T", (), {"func": lambda: 1, "name": "t"})()}

    class MockLGAgent:
        nodes = {"node1": MockLGNode()}

    assert protect_agent(MockLGAgent(), "langgraph") == 1

    # AutoGen agent
    class MockAGAgent:
        _tools = [type("T", (), {"run_json": lambda *a, **k: 1, "name": "ag_t"})()]

    assert protect_agent(MockAGAgent(), "autogen") == 1

    # Smolagents agent
    class MockSmolAgent:
        tools = {"s1": type("T", (), {"forward": lambda: 1, "name": "s1"})()}

    assert protect_agent(MockSmolAgent(), "smolagents") == 1

    # Unknown framework
    with pytest.raises(ValueError, match="no tool-level enforcement"):
        protect_agent(MockLCAgent(), "unsupported_framework")

    # Zero tools found
    class EmptyAgent:
        tools = []

    with pytest.raises(ValueError, match="protection requested but no tools were found"):
        protect_agent(EmptyAgent(), "langchain")
