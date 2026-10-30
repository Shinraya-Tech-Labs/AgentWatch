"""
AgentWatch MCP CLI
Runs the AgentWatch Model Context Protocol (MCP) server over standard IO.
"""

from __future__ import annotations

import typer
from rich.console import Console

from panoptis.protocol.mcp_server import AgentWatchMCPServer

console = Console()
app = typer.Typer(name="mcp", help="Run the AgentWatch MCP server over stdio.")


@app.callback(invoke_without_command=True)
def mcp_server() -> None:
    """Run the AgentWatch MCP server over stdio for agents (e.g. Claude Desktop)."""
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        console.print("[red]mcp package not installed. Run: pip install mcp[/red]")
        raise typer.Exit(1)

    from panoptis.core.safety import SafetyEngine
    from panoptis.cost.tracker import CostTracker
    from panoptis.replay.engine import ReplayEngine
    from panoptis.scoring.confidence import ConfidenceScorer
    from panoptis.tracing.collector import TraceCollector

    collector = TraceCollector()
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

    def _memory(q: str) -> list[dict]:
        return []

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
    server.replay_provider = _replay
    server.safety_provider = _safety
    server.sessions_provider = _sessions
    server.cost_provider = _cost

    fastmcp = server.build_fastmcp()
    fastmcp.run(transport="stdio")
