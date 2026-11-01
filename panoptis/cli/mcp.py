"""
AgentWatch MCP CLI
Runs the AgentWatch Model Context Protocol (MCP) server over standard IO.
"""

from __future__ import annotations

import contextlib
import json

import typer
from rich.console import Console

from panoptis.protocol.mcp_server import AgentWatchMCPServer

console = Console()
app = typer.Typer(name="mcp", help="Run the AgentWatch MCP server over stdio.")


@app.callback(invoke_without_command=True)
def mcp_server() -> None:
    """Run the AgentWatch MCP server over stdio for agents (e.g. Claude Desktop)."""
    try:
        try:
            import mcp.server.fastmcp  # noqa: F401
        except (ImportError, ModuleNotFoundError):
            import mcp.server.mcpserver  # noqa: F401
    except ImportError:
        console.print("[red]mcp package not installed. Run: pip install mcp[/red]")
        raise typer.Exit(1)

    import asyncio
    from datetime import UTC, datetime

    from panoptis.core.safety import SafetyEngine
    from panoptis.cost.tracker import CostTracker
    from panoptis.memory.nlquery import query as nlquery
    from panoptis.replay.engine import ReplayEngine
    from panoptis.scoring.confidence import ConfidenceScorer
    from panoptis.storage.store import panoptis_home
    from panoptis.tracing.collector import TraceCollector

    home = panoptis_home()
    memory_file = home / "memories.json"
    traces_dir = home / "traces"

    collector = TraceCollector(storage_path=traces_dir)
    if traces_dir.exists():
        with contextlib.suppress(Exception):
            asyncio.run(collector.load_from_disk())

    replay_engine = ReplayEngine()
    safety_engine = SafetyEngine()
    confidence_scorer = ConfidenceScorer()
    cost_tracker = CostTracker()

    server = AgentWatchMCPServer()

    def _confidence(sid: str) -> list[float]:
        events = collector.get_events(sid, limit=2000)
        if not events:
            raise ValueError(f"No events for session {sid}")
        trace = collector.get_trace(sid)
        goal = trace.session.goal if trace else None
        res = confidence_scorer.score(events, goal=goal)
        return [res.overall_score, res.goal_alignment, res.consistency_score]

    def _load_memories() -> list[dict]:
        memories: list[dict] = []
        if memory_file.exists():
            with contextlib.suppress(Exception):
                with open(memory_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        memories.extend(data)
        for session in collector.list_sessions(limit=50):
            for ev in collector.get_events(session.session_id):
                if ev.event_type.value in ("memory_read", "memory_write", "tool_result"):
                    val = ev.metadata.get("content") or ev.goal
                    if not val and ev.tool_result and ev.tool_result.output:
                        val = str(ev.tool_result.output)[:500]
                    if val:
                        memories.append(
                            {
                                "key": ev.event_id[:8],
                                "value": val,
                                "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
                            }
                        )
        return memories

    def _memory(q: str) -> list[dict]:
        memories = _load_memories()
        if not memories:
            return []
        results = nlquery(q, memories, limit=10)
        return [r.to_dict() for r in results]

    def _store_mem(key: str, value: str) -> dict:
        memories: list[dict] = []
        if memory_file.exists():
            with contextlib.suppress(Exception):
                with open(memory_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        memories = data
        entry = {
            "key": key,
            "value": value,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        memories.append(entry)
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(memory_file, "w", encoding="utf-8") as f:
                json.dump(memories, f, indent=2)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        return {"status": "stored", "key": key, "total_memories": len(memories)}

    def _replay(sid: str, step: int | None = None) -> dict:
        events = collector.get_events(sid, limit=5000)
        trace = collector.get_trace(sid)
        if not events or not trace:
            raise ValueError(f"Session {sid} not found")
        replay = replay_engine.load_from_events(trace.session, events)
        payload = replay.to_dict()
        if step is None:
            return payload
        steps = payload.get("steps", [])
        if step < 0 or step >= len(steps):
            raise ValueError(f"Step {step} out of range for session {sid}")
        payload["steps"] = [steps[step]]
        payload["selected_step"] = step
        return payload

    def _safety() -> dict:
        return safety_engine.stats()

    def _sessions() -> list[dict]:
        sessions = collector.list_sessions(limit=50)
        return [s.model_dump(mode="json") for s in sessions]

    def _cost(sid: str) -> dict:
        budget = cost_tracker.get_session(sid)
        if not budget:
            events = collector.get_events(sid, limit=5000)
            if not events:
                raise ValueError(f"Session {sid} not found")
            for event in events:
                cost_tracker.ingest_event(event)
            budget = cost_tracker.get_session(sid)
        if budget:
            return budget.to_dict()
        budget_dict = {
            "session_id": sid,
            "usd_budget": 0.0,
            "tokens_used": 0,
            "usd_used": 0.0,
            "exceeded": False,
            "warnings": [],
        }
        budget_dict["token_budget"] = 0
        return budget_dict

    server.confidence_provider = _confidence
    server.memory_provider = _memory
    server.store_memory_provider = _store_mem
    server.replay_provider = _replay
    server.safety_provider = _safety
    server.sessions_provider = _sessions
    server.cost_provider = _cost

    import sys

    if sys.stdin.isatty():
        from rich.panel import Panel

        err_console = Console(stderr=True)
        err_console.print(
            Panel(
                "[bold cyan]⚡ Panoptis MCP Server Active[/bold cyan] [dim](stdio transport)[/dim]\n\n"
                "[bold green]Ready and listening[/bold green] for JSON-RPC messages from MCP clients (Claude Desktop, Cursor, etc.).\n\n"
                "[bold white]Registered Tools:[/bold white]\n"
                "  • [cyan]get_confidence_report[/cyan]  — Confidence score history for a session\n"
                "  • [cyan]memory_query[/cyan]           — Natural language semantic memory search\n"
                "  • [cyan]store_memory[/cyan]           — Save key facts or decisions to persistent memory\n"
                "  • [cyan]get_session_replay[/cyan]     — Full step-by-step trace replay\n"
                "  • [cyan]list_active_sessions[/cyan]   — Currently monitored sessions\n"
                "  • [cyan]get_safety_events[/cyan]      — Intercepted blocked & warned actions\n"
                "  • [cyan]get_cost_report[/cyan]        — Token spend and budget tracking\n\n"
                "[dim]To connect with Claude Desktop, add to claude_desktop_config.json:\n"
                '  "panoptis": { "command": "panoptis", "args": ["mcp"] }[/dim]\n\n'
                "[yellow]Press Ctrl+C to stop the server.[/yellow]",
                title="[bold green]AGENTWATCH MCP SERVER[/bold green]",
                border_style="cyan",
            )
        )

    fastmcp = server.build_fastmcp()
    fastmcp.run(transport="stdio")
