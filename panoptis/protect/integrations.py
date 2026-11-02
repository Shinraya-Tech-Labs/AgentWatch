"""Framework tool integrations: route the code that actually runs a tool through the
shared enforcement boundary (TO-011, TO-013, TO-014, TO-015).

Each function wraps the framework's *execution* entry point, not its agent loop, so a
blocked call never reaches the tool, and the agent receives the sanitized result:

=================  =====================================  ==============================
framework          execution entry point wrapped           blocked call surfaces as
=================  =====================================  ==============================
LangChain          ``StructuredTool.func/coroutine``,      ``ActionBlocked`` raised from
                   else ``BaseTool._run/_arun``            ``tool.invoke``
LangGraph          every tool of a ``ToolNode``            ``ActionBlocked`` (the node's
                   (via the LangChain wrapper)             error handling decides)
smolagents         ``Tool.forward``                        ``ActionBlocked`` from the tool
AutoGen            ``FunctionTool.run_json`` (async)       ``ActionBlocked`` from run_json
=================  =====================================  ==============================

Identity (agent, session, tenant, objective) comes from the arguments given here or from
:func:`panoptis.protect.enforce.protection_context`. Wrapping is idempotent.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from panoptis.protect.enforce import (
    aenforce_call,
    current_context,
    enforce_call,
    get_default_pipeline,
)
from panoptis.protect.model import ToolAction
from panoptis.protect.pipeline import ProtectionPipeline

_MARK = "_panoptis_protected"


def _action(name: str, arguments: dict[str, Any], source: str, ident: dict[str, Any]) -> ToolAction:
    ctx = current_context()
    agent = ident.get("agent_id") or ctx.get("agent_id")
    session = ident.get("session_id") or ctx.get("session_id")
    return ToolAction(
        agent_id=agent or "default-agent",
        session_id=session or "default-session",
        tool=name,
        arguments=dict(arguments),
        objective=ident.get("objective") or ctx.get("objective"),
        tenant_id=ident.get("tenant_id") or ctx.get("tenant_id") or "default",
        trace_id=ctx.get("trace_id"),
        source=source,
        defaulted_identity=not (agent and session),
    )


def _mark(obj: Any) -> bool:
    """True if ``obj`` was already protected; marks it otherwise."""
    if getattr(obj, _MARK, False):
        return True
    try:
        object.__setattr__(obj, _MARK, True)
    except (AttributeError, TypeError):
        pass
    return False


def _named(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], Callable[[dict[str, Any]], tuple[tuple[Any, ...], dict[str, Any]]]]:
    from panoptis.protect.enforce import bind_arguments

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None
    return bind_arguments(sig, args, kwargs)


def _wrap_sync(
    fn: Callable[..., Any],
    name: str,
    source: str,
    pipeline: ProtectionPipeline | None,
    ident: dict[str, Any],
) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        named, rebuild = _named(fn, args, kwargs)

        def invoke(arguments: dict[str, Any]) -> Any:
            a, k = rebuild(arguments)
            return fn(*a, **k)

        return enforce_call(
            pipeline or get_default_pipeline(), _action(name, named, source, ident), invoke
        )

    return wrapper


def _wrap_async(
    fn: Callable[..., Any],
    name: str,
    source: str,
    pipeline: ProtectionPipeline | None,
    ident: dict[str, Any],
) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        named, rebuild = _named(fn, args, kwargs)

        async def invoke(arguments: dict[str, Any]) -> Any:
            a, k = rebuild(arguments)
            return await fn(*a, **k)

        return await aenforce_call(
            pipeline or get_default_pipeline(), _action(name, named, source, ident), invoke
        )

    return wrapper


# ── LangChain / LangGraph ──────────────────────────────────────────────────────────────


def protect_langchain_tool(
    tool: Any, *, pipeline: ProtectionPipeline | None = None, **ident: Any
) -> Any:
    """Protect a ``langchain_core`` tool in place and return it.

    Tool schemas, names, descriptions and callbacks are untouched: only the callable the
    tool executes is wrapped, so ``tool.invoke`` / ``ainvoke`` / ``run`` all enforce.
    """
    if _mark(tool):
        return tool
    name = getattr(tool, "name", type(tool).__name__)
    if getattr(tool, "func", None) is not None:
        object.__setattr__(
            tool, "func", _wrap_sync(tool.func, name, "adapter:langchain", pipeline, ident)
        )
    elif getattr(type(tool), "_run", None) is not None and type(tool)._run is not _base_run():
        object.__setattr__(
            tool, "_run", _wrap_sync(tool._run, name, "adapter:langchain", pipeline, ident)
        )
    if getattr(tool, "coroutine", None) is not None:
        object.__setattr__(
            tool,
            "coroutine",
            _wrap_async(tool.coroutine, name, "adapter:langchain", pipeline, ident),
        )
    elif (
        getattr(type(tool), "_arun", None) is not None
        and type(tool)._arun is not _base_arun()
        and getattr(tool, "func", None) is None
    ):
        object.__setattr__(
            tool, "_arun", _wrap_async(tool._arun, name, "adapter:langchain", pipeline, ident)
        )
    return tool


def _base_run() -> Any:
    try:
        from langchain_core.tools import BaseTool

        return BaseTool._run
    except ImportError:
        return None


def _base_arun() -> Any:
    try:
        from langchain_core.tools import BaseTool

        return BaseTool._arun
    except ImportError:
        return None


def protect_langgraph_tool_node(
    node: Any, *, pipeline: ProtectionPipeline | None = None, **ident: Any
) -> Any:
    """Protect every tool of a LangGraph ``ToolNode`` (the node that executes tool calls)."""
    tools = getattr(node, "tools_by_name", None) or getattr(node, "_tools_by_name", None)
    if not isinstance(tools, dict):
        raise TypeError("expected a langgraph ToolNode (with tools_by_name)")
    for t in tools.values():
        protect_langchain_tool(t, pipeline=pipeline, **ident)
    return node


# ── smolagents ─────────────────────────────────────────────────────────────────────────


def protect_smolagents_tool(
    tool: Any, *, pipeline: ProtectionPipeline | None = None, **ident: Any
) -> Any:
    """Protect a ``smolagents.Tool`` in place (its ``forward`` runs the tool)."""
    if _mark(tool):
        return tool
    name = getattr(tool, "name", type(tool).__name__)
    fwd = tool.forward
    wrapped = (
        _wrap_async(fwd, name, "adapter:smolagents", pipeline, ident)
        if inspect.iscoroutinefunction(fwd)
        else _wrap_sync(fwd, name, "adapter:smolagents", pipeline, ident)
    )
    object.__setattr__(tool, "forward", wrapped)
    return tool


def protect_smolagents_agent(
    agent: Any, *, pipeline: ProtectionPipeline | None = None, **ident: Any
) -> Any:
    """Protect every tool an agent can call (``agent.tools`` is a dict or a list)."""
    tools = getattr(agent, "tools", None)
    for t in tools.values() if isinstance(tools, dict) else tools or []:
        if getattr(t, "name", "") == "final_answer":
            continue  # returning the answer is not a side effect
        protect_smolagents_tool(t, pipeline=pipeline, **ident)
    return agent


# ── AutoGen (autogen-agentchat / autogen-core 0.4+) ─────────────────────────────────────


def protect_autogen_tool(
    tool: Any, *, pipeline: ProtectionPipeline | None = None, **ident: Any
) -> Any:
    """Protect an AutoGen ``BaseTool`` / ``FunctionTool`` in place (``run_json`` runs it)."""
    if _mark(tool):
        return tool
    name = getattr(tool, "name", type(tool).__name__)
    original = tool.run_json

    @functools.wraps(original)
    async def run_json(args: dict[str, Any], cancellation_token: Any, *rest: Any, **kw: Any) -> Any:
        async def invoke(arguments: dict[str, Any]) -> Any:
            return await original(arguments, cancellation_token, *rest, **kw)

        return await aenforce_call(
            pipeline or get_default_pipeline(),
            _action(name, args, "adapter:autogen", ident),
            invoke,
        )

    object.__setattr__(tool, "run_json", run_json)
    return tool


def protect_tools(tools: Any, *, pipeline: ProtectionPipeline | None = None, **ident: Any) -> Any:
    """Protect a tool or a collection of tools from any supported framework."""
    if isinstance(tools, dict):
        for t in tools.values():
            protect_tools(t, pipeline=pipeline, **ident)
        return tools
    if isinstance(tools, (list, tuple)):
        for t in tools:
            protect_tools(t, pipeline=pipeline, **ident)
        return tools
    mod = type(tools).__module__
    if mod.startswith("langchain") or hasattr(tools, "args_schema") and hasattr(tools, "invoke"):
        return protect_langchain_tool(tools, pipeline=pipeline, **ident)
    if mod.startswith("smolagents"):
        return protect_smolagents_tool(tools, pipeline=pipeline, **ident)
    if mod.startswith("autogen"):
        return protect_autogen_tool(tools, pipeline=pipeline, **ident)
    raise TypeError(
        f"unsupported tool type {type(tools).__qualname__}; use protect_tool() for plain callables"
    )


# ── whole agents (used by watch(agent, protection=...)) ────────────────────────────────


def protect_agent(
    agent: Any, framework: str, *, pipeline: ProtectionPipeline | None = None, **ident: Any
) -> int:
    """Protect every tool an agent of ``framework`` can execute. Returns how many.

    Raises ``ValueError`` when no tool could be found, so a caller that asked for
    enforcement is never left with observation only.
    """
    count = 0
    if framework == "langchain":
        for t in getattr(agent, "tools", None) or []:
            protect_langchain_tool(t, pipeline=pipeline, **ident)
            count += 1
    elif framework == "langgraph":
        for node in (getattr(agent, "nodes", None) or {}).values():
            bound = getattr(node, "bound", node)
            if getattr(bound, "tools_by_name", None):
                protect_langgraph_tool_node(bound, pipeline=pipeline, **ident)
                count += len(bound.tools_by_name)
    elif framework == "autogen":
        seen: set[int] = set()
        workbenches = getattr(agent, "_workbench", None) or []
        for holder in [agent, *(workbenches if isinstance(workbenches, list) else [workbenches])]:
            for t in getattr(holder, "_tools", None) or []:
                if id(t) not in seen and hasattr(t, "run_json"):
                    seen.add(id(t))
                    protect_autogen_tool(t, pipeline=pipeline, **ident)
                    count += 1
    elif framework == "smolagents":
        tools = getattr(agent, "tools", None)
        items = tools.values() if isinstance(tools, dict) else tools or []
        count = sum(1 for t in items if getattr(t, "name", "") != "final_answer")
        protect_smolagents_agent(agent, pipeline=pipeline, **ident)
    else:
        raise ValueError(
            f"no tool-level enforcement for framework {framework!r}; use protect_tool()"
        )
    if count == 0:
        raise ValueError(f"protection requested but no tools were found on this {framework} agent")
    return count
