"""
PANOPTIS Trace Collector
Collects, stores, and retrieves agent execution traces.
Compatible with OpenTelemetry concepts (trace_id, span_id).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from panoptis.core.schema import AgentEvent, AgentSession, EventType, ExecutionStatus

logger = logging.getLogger(__name__)


class TraceSpan:
    """An individual span within a trace — maps to an agent event."""

    def __init__(self, event: AgentEvent):
        self.span_id = event.event_id
        self.trace_id = event.trace_id or event.session_id
        self.parent_span_id = event.parent_event_id
        self.name = event.event_type.value
        self.start_time = event.timestamp
        self.end_time = None
        self.status = event.status.value
        self.attributes: dict[str, Any] = {
            "agent.id": event.agent_id,
            "agent.framework": event.framework.value,
            "agent.step": event.step_number,
        }
        if event.tool_call:
            self.attributes["tool.name"] = event.tool_call.tool_name
            if event.tool_call.raw_command:
                self.attributes["tool.command"] = event.tool_call.raw_command[:200]
        if event.safety:
            self.attributes["safety.risk_level"] = event.safety.risk_level.value
            self.attributes["safety.blocked"] = event.safety.blocked
        if event.token_usage:
            self.attributes["tokens.total"] = event.token_usage.total_tokens

        self._event = event

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "status": self.status,
            "attributes": self.attributes,
        }


class Trace:
    """A complete agent execution trace — all spans for one session."""

    def __init__(self, session: AgentSession):
        self.trace_id = session.session_id
        self.session = session
        self.spans: list[TraceSpan] = []
        self._event_count = 0
        self.is_exported = False

    def add_event(self, event: AgentEvent) -> TraceSpan:
        span = TraceSpan(event)
        self.spans.append(span)
        self._event_count += 1
        return span

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def duration_seconds(self) -> float | None:
        if self.session.ended_at and self.session.started_at:
            return (self.session.ended_at - self.session.started_at).total_seconds()
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session": self.session.model_dump(mode="json"),
            "span_count": len(self.spans),
            "is_exported": self.is_exported,
            "spans": [s.to_dict() for s in self.spans],
        }


class TraceCollector:
    """
    Collects events from the event bus and organizes them into traces.
    Provides query interface for the API layer.
    """

    def __init__(
        self,
        max_traces: int = 500,
        storage_path: Path | None = None,
        flush_interval_seconds: float = 30.0,
    ):
        self._traces: dict[str, Trace] = {}
        self._session_index: dict[str, AgentSession] = {}
        self._max_traces = max_traces
        self._storage_path = storage_path
        self._flush_interval = flush_interval_seconds
        self._event_buffer: list[AgentEvent] = []
        self._lock = asyncio.Lock()
        self._stats: dict[str, int] = defaultdict(int)

    async def ingest(self, event: AgentEvent) -> None:
        """Process one event into the trace collection."""
        trace_to_export = None
        trace_obj = None

        async with self._lock:
            self._stats["ingested"] += 1

            # Get or create trace
            trace = self._traces.get(event.session_id)
            if trace is None:
                # Create a minimal session for orphan events
                session = AgentSession(
                    session_id=event.session_id,
                    agent_id=event.agent_id,
                    agent_name=event.agent_name,
                    framework=event.framework,
                )
                trace = Trace(session=session)
                self._traces[event.session_id] = trace
                self._session_index[event.session_id] = session

                # LRU eviction
                if len(self._traces) > self._max_traces:
                    oldest_key = next(iter(self._traces))
                    del self._traces[oldest_key]

            trace.add_event(event)
            trace.session.total_events = trace.event_count

            # Update session on lifecycle events
            if event.event_type == EventType.SESSION_START:
                trace.session.goal = event.goal
            elif event.event_type == EventType.SESSION_END:
                trace.session.ended_at = event.timestamp
                trace.session.status = event.status
            elif event.event_type == EventType.AGENT_ERROR:
                trace.session.status = ExecutionStatus.FAILURE

            if event.token_usage:
                trace.session.total_tokens += event.token_usage.total_tokens
                if event.token_usage.estimated_cost_usd:
                    trace.session.estimated_cost_usd += event.token_usage.estimated_cost_usd

            if not trace.is_exported and event.event_type in (
                EventType.SESSION_END,
                EventType.AGENT_ERROR,
            ):
                trace_to_export = trace.to_dict()
                trace_obj = trace

        if trace_to_export and trace_obj:
            try:
                from panoptis.telemetry.otel import get_telemetry

                if get_telemetry().export_reasoning_trace(trace_to_export):
                    trace_obj.is_exported = True
            except Exception as exc:
                logger.warning("Telemetry export failed: %s", exc)

    def register_session(self, session: AgentSession) -> None:
        """Register a session explicitly (before events arrive)."""
        if session.session_id not in self._traces:
            trace = Trace(session=session)
            self._traces[session.session_id] = trace
            self._session_index[session.session_id] = session

    def get_trace(self, session_id: str) -> Trace | None:
        return self._traces.get(session_id)

    def list_sessions(
        self,
        limit: int = 50,
        framework: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[AgentSession]:
        sessions = [t.session for t in self._traces.values()]

        if framework:
            sessions = [s for s in sessions if s.framework.value == framework]
        if status:
            sessions = [s for s in sessions if s.status.value == status]
        if since:
            sessions = [s for s in sessions if s.started_at >= since]

        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions[:limit]

    def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[AgentEvent]:
        trace = self._traces.get(session_id)
        if not trace:
            return []

        events = [s._event for s in trace.spans]

        if event_type:
            events = [e for e in events if e.event_type.value == event_type]

        return events[:limit]

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_traces": len(self._traces),
            "total_ingested": self._stats["ingested"],
            "active_sessions": sum(
                1 for t in self._traces.values() if t.session.status == ExecutionStatus.RUNNING
            ),
        }

    async def flush_to_disk(self) -> None:
        """Persist all traces to disk."""
        if not self._storage_path:
            return

        self._storage_path.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            for session_id, trace in self._traces.items():
                path = self._storage_path / f"{session_id}.json"
                try:
                    with open(path, "w") as f:
                        json.dump(trace.to_dict(), f, default=str)
                except Exception as exc:
                    logger.error("Failed to flush trace %s: %s", session_id, exc)

    async def load_from_disk(self) -> int:
        """Load persisted traces from disk. Returns count loaded."""
        if not self._storage_path or not self._storage_path.exists():
            return 0

        count = 0
        for path in self._storage_path.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                session = AgentSession(**data["session"])
                trace = Trace(session=session)
                trace.is_exported = data.get("is_exported", False)
                # Reconstruct minimal spans (events only, no full re-parse)
                self._traces[session.session_id] = trace
                self._session_index[session.session_id] = session
                count += 1
            except Exception as exc:
                logger.warning("Failed to load trace from %s: %s", path, exc)

        logger.info("Loaded %d traces from disk", count)
        return count
