"""
PRT-004 — MCP Server Integration.

Expose AgentWatch as an MCP (Model Context Protocol) server.
Claude agents can query their own observability data via MCP tool calls.

Exposed tools:
    - confidence_history(session_id)
    - memory_query(question)
    - session_replay(session_id, step?)
    - safety_status()
    - list_sessions()
    - cost_report(session_id)

The implementation is transport-agnostic — it can be wired into the actual
MCP stdio or HTTP transport at the API layer. Here we expose the tool
catalog + a synchronous `dispatch(tool, args)` entry point.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any] = field(repr=False)


@dataclass
class MCPResponse:
    ok: bool
    result: Any = None
    error: str | None = None


class AgentWatchMCPServer:
    """In-process MCP server skeleton — implements the tool catalog."""

    def __init__(self) -> None:
        self._tools: dict[str, MCPTool] = {}
        self._register_default_tools()

    # ── tool registration ──────────────────────────────────────────────
    def register(self, tool: MCPTool) -> None:
        self._tools[tool.name] = tool

    def tool_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    # ── dispatch ───────────────────────────────────────────────────────
    def dispatch(self, tool_name: str, args: dict[str, Any] | None = None) -> MCPResponse:
        tool = self._tools.get(tool_name)
        if tool is None:
            return MCPResponse(ok=False, error=f"unknown tool: {tool_name}")
        try:
            result = tool.handler(args or {})
            return MCPResponse(ok=True, result=result)
        except Exception as exc:  # noqa: BLE001
            return MCPResponse(ok=False, error=f"{type(exc).__name__}: {exc}")

    # ── built-in tools ─────────────────────────────────────────────────
    def _register_default_tools(self) -> None:
        # confidence_history
        self.register(
            MCPTool(
                name="panoptis_confidence_history",
                description="Return the confidence score history for a session.",
                input_schema={
                    "type": "object",
                    "required": ["session_id"],
                    "properties": {"session_id": {"type": "string"}},
                },
                handler=self._confidence_history,
            )
        )
        # memory_query
        self.register(
            MCPTool(
                name="panoptis_memory_query",
                description="Query AgentWatch's persistent memory in natural language.",
                input_schema={
                    "type": "object",
                    "required": ["question"],
                    "properties": {"question": {"type": "string"}},
                },
                handler=self._memory_query,
            )
        )
        # session_replay
        self.register(
            MCPTool(
                name="panoptis_session_replay",
                description="Retrieve a stored session for step-by-step inspection.",
                input_schema={
                    "type": "object",
                    "required": ["session_id"],
                    "properties": {
                        "session_id": {"type": "string"},
                        "step": {"type": "integer", "minimum": 0},
                    },
                },
                handler=self._session_replay,
            )
        )
        # safety_status
        self.register(
            MCPTool(
                name="panoptis_safety_status",
                description="Return the current safety engine status and recent blocks.",
                input_schema={"type": "object", "properties": {}},
                handler=self._safety_status,
            )
        )

        # list_sessions
        self.register(
            MCPTool(
                name="panoptis_list_sessions",
                description="Return active or recently failed sessions.",
                input_schema={"type": "object", "properties": {}},
                handler=self._list_sessions,
            )
        )
        # cost_report
        self.register(
            MCPTool(
                name="panoptis_cost_report",
                description="Retrieves token usage and estimated cost for a session.",
                input_schema={
                    "type": "object",
                    "required": ["session_id"],
                    "properties": {"session_id": {"type": "string"}},
                },
                handler=self._cost_report,
            )
        )

    # ── default handlers (overridable) ─────────────────────────────────
    # These are stubs operating against in-memory state. Wire them to the
    # real EventBus / DB / store at the API layer.
    confidence_provider: Callable[[str], list[float]] | None = None
    memory_provider: Callable[[str], list[dict[str, Any]]] | None = None
    replay_provider: Callable[[str, int | None], dict[str, Any]] | None = None
    safety_provider: Callable[[], dict[str, Any]] | None = None
    sessions_provider: Callable[[], list[dict[str, Any]]] | None = None
    cost_provider: Callable[[str], dict[str, Any]] | None = None

    def _confidence_history(self, args: dict[str, Any]) -> list[float]:
        sid = args["session_id"]
        if self.confidence_provider:
            return self.confidence_provider(sid)
        return []

    def _memory_query(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        q = args["question"]
        if self.memory_provider:
            return self.memory_provider(q)
        return []

    def _session_replay(self, args: dict[str, Any]) -> dict[str, Any]:
        sid = args["session_id"]
        step = args.get("step")
        if self.replay_provider:
            return self.replay_provider(sid, step)
        return {"session_id": sid, "step": step, "events": []}

    def _safety_status(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.safety_provider:
            return self.safety_provider()
        return {"status": "ok", "blocks_last_hour": 0}

    def _list_sessions(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        if self.sessions_provider:
            return self.sessions_provider()
        return []

    def _cost_report(self, args: dict[str, Any]) -> dict[str, Any]:
        sid = args["session_id"]
        if self.cost_provider:
            return self.cost_provider(sid)
        return {"session_id": sid, "tokens": 0, "cost_usd": 0.0}

    # ── fastmcp integration ────────────────────────────────────────────
    def build_fastmcp(self) -> Any:
        """Create a FastMCP server that wraps the existing tool registry."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("AgentWatch")

        @mcp.tool()
        def get_confidence_report(session_id: str) -> list[float]:
            """Return the confidence score history for a session."""
            res = self.dispatch("panoptis_confidence_history", {"session_id": session_id})
            if not res.ok:
                raise ValueError(res.error)
            return res.result

        @mcp.tool()
        def memory_query(question: str) -> list[dict[str, Any]]:
            """Query AgentWatch's persistent memory in natural language."""
            res = self.dispatch("panoptis_memory_query", {"question": question})
            if not res.ok:
                raise ValueError(res.error)
            return res.result

        @mcp.tool()
        def get_session_replay(session_id: str, step: int | None = None) -> dict[str, Any]:
            """Retrieve a stored session for step-by-step inspection."""
            args = {"session_id": session_id}
            if step is not None:
                args["step"] = step
            res = self.dispatch("panoptis_session_replay", args)
            if not res.ok:
                raise ValueError(res.error)
            return res.result

        @mcp.tool()
        def get_safety_events() -> dict[str, Any]:
            """Return the current safety engine status and recent blocks."""
            res = self.dispatch("panoptis_safety_status", {})
            if not res.ok:
                raise ValueError(res.error)
            return res.result

        @mcp.tool()
        def list_active_sessions() -> list[dict[str, Any]]:
            """Return active or recently failed sessions."""
            res = self.dispatch("panoptis_list_sessions", {})
            if not res.ok:
                raise ValueError(res.error)
            return res.result

        @mcp.tool()
        def get_cost_report(session_id: str) -> dict[str, Any]:
            """Retrieves token usage and estimated cost for a session."""
            res = self.dispatch("panoptis_cost_report", {"session_id": session_id})
            if not res.ok:
                raise ValueError(res.error)
            return res.result

        return mcp


__all__ = ["AgentWatchMCPServer", "MCPTool", "MCPResponse"]
