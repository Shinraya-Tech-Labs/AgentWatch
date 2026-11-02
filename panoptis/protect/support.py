"""What each integration actually does to a tool call (TO-011, TO-035).

``PRE_EXECUTION_ENFORCEMENT``  the call is decided before the tool runs; a blocked call
                               never executes; the result is sanitized before the agent
                               sees it.
``POST_EXECUTION_OBSERVATION`` events are recorded; nothing is prevented.
``UNSUPPORTED``                no integration.

This table is the single source for the support matrix in docs/ADAPTER_SUPPORT.md and
is checked by tests/protect/test_support_matrix.py: every adapter module must appear, and
every enforcing entry must name an integration test that exists.
"""

from __future__ import annotations

from dataclasses import dataclass

PRE = "PRE_EXECUTION_ENFORCEMENT"
POST = "POST_EXECUTION_OBSERVATION"
UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class Support:
    integration: str
    stage: str
    entry_point: str
    how: str
    test: str | None = None
    limitations: str = ""


MATRIX: tuple[Support, ...] = (
    Support(
        "protect_tool() (any Python callable)",
        PRE,
        "panoptis.protect.protect_tool",
        "wraps the callable",
        "tests/protect/test_protect_tool.py",
        "only calls made through the wrapper are covered",
    ),
    Support(
        "watch(agent, protection=...) generic agents",
        PRE,
        "panoptis.core.watcher.GenericAdapter",
        "tool-like methods (run, invoke, execute, …) go through enforce_call",
        "tests/protect/test_enforce.py",
        "only methods whose name looks tool-like; generator results are inspected item by item",
    ),
    Support(
        "watch(agent) generic agents, no protection",
        PRE,
        "panoptis.core.watcher.GenericAdapter",
        "legacy SafetyEngine pattern gate, fail-closed",
        "tests/test_watcher_fail_closed.py",
        "pattern rules only: no RBAC, loops, intent, approvals or output sanitization",
    ),
    Support(
        "LangChain tools",
        PRE,
        "panoptis.protect.integrations.protect_langchain_tool",
        "StructuredTool.func/coroutine or BaseTool._run/_arun",
        "tests/protect/test_framework_integrations.py",
        "tools must be protected (directly or via watch(protection=)) before the agent runs them",
    ),
    Support(
        "LangGraph ToolNode / compiled graphs",
        PRE,
        "panoptis.protect.integrations.protect_langgraph_tool_node",
        "every tool of each ToolNode",
        "tests/protect/test_framework_integrations.py",
        "tools executed outside a ToolNode (custom nodes calling functions directly) are not covered",
    ),
    Support(
        "smolagents tools / agents",
        PRE,
        "panoptis.protect.integrations.protect_smolagents_agent",
        "Tool.forward",
        "tests/protect/test_framework_integrations.py",
        "CodeAgent's Python interpreter executes code that is not a Tool call and is not covered",
    ),
    Support(
        "AutoGen (agentchat 0.4+) tools / agents",
        PRE,
        "panoptis.protect.integrations.protect_autogen_tool",
        "FunctionTool.run_json (agent tools and workbench)",
        "tests/protect/test_framework_integrations.py",
        "MCP workbenches route through the MCP gateway instead",
    ),
    Support(
        "Claude Code (PreToolUse hook)",
        PRE,
        "panoptis.protect.claude_hook",
        "PreToolUse deny/ask/updatedInput; PostToolUse updatedToolOutput",
        "tests/protect/test_claude_hook.py",
        "a hook command that cannot start or times out lets the tool run unless installed "
        "with onFailure=block (Claude Code 2.1.295+); per-process loop/rate state",
    ),
    Support(
        "LangChain callback adapter",
        POST,
        "panoptis.adapters.langchain",
        "callbacks record events",
        None,
        "observation only; use protect_langchain_tool for enforcement",
    ),
    Support(
        "LangGraph adapter (observability)",
        POST,
        "panoptis.adapters.langgraph",
        "records graph invocations",
        None,
        "observation only",
    ),
    Support(
        "AutoGen adapter (observability)",
        POST,
        "panoptis.adapters.autogen",
        "records message/reply events",
        None,
        "observation only",
    ),
    Support(
        "smolagents adapter (observability)",
        POST,
        "panoptis.adapters.smolagents",
        "records run/step/tool events",
        None,
        "observation only",
    ),
    Support(
        "CrewAI",
        POST,
        "panoptis.adapters.crewai",
        "lifecycle callbacks",
        None,
        "observation only; no tool-level enforcement yet",
    ),
    Support(
        "OpenAI Agents SDK",
        POST,
        "panoptis.adapters.openai_agents",
        "lifecycle hooks",
        None,
        "observation only",
    ),
    Support(
        "AutoGPT", POST, "panoptis.adapters.autogpt", "event translation", None, "observation only"
    ),
    Support(
        "OpenClaw",
        POST,
        "panoptis.adapters.openclaw",
        "event translation",
        None,
        "observation only",
    ),
    Support(
        "Generic CLI agent",
        POST,
        "panoptis.adapters.generic",
        "captures the process output",
        None,
        "observation only: the subprocess runs its own tools",
    ),
    Support(
        "Claude Code (stream-json adapter)",
        POST,
        "panoptis.adapters.claude_code",
        "parses the CLI's event stream",
        None,
        "the CLI has already run a tool when its event is read; use the PreToolUse hook for enforcement",
    ),
)


def by_module() -> dict[str, list[Support]]:
    out: dict[str, list[Support]] = {}
    for s in MATRIX:
        out.setdefault(
            s.entry_point.rsplit(".", 1)[0] if s.entry_point.count(".") > 2 else s.entry_point, []
        ).append(s)
    return out
