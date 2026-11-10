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
from panoptis.governance.hipaa import redact_phi

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
        self._by_event_id: dict[str, TraceSpan] = {}
        self._event_count = 0

    def add_event(self, event: AgentEvent) -> TraceSpan:
        """Add an event; a retried event (same ``event_id``) is recorded once."""
        existing = self._by_event_id.get(event.event_id)
        if existing is not None:
            return existing
        span = TraceSpan(event)
        self.spans.append(span)
        self._by_event_id[event.event_id] = span
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
        hipaa_compliance_mode: bool = False,
    ):
        self._traces: dict[str, Trace] = {}
        self._session_index: dict[str, AgentSession] = {}
        self._max_traces = max_traces
        self._storage_path = storage_path
        self._flush_interval = flush_interval_seconds
        self._event_buffer: list[AgentEvent] = []
        self._lock = asyncio.Lock()
        self._stats: dict[str, int] = defaultdict(int)
        self.hipaa_compliance_mode = hipaa_compliance_mode

    async def ingest(self, event: AgentEvent) -> None:
        """Process one event into the trace collection."""
        if self.hipaa_compliance_mode:
            # Redact a private copy; never mutate the caller's (possibly shared) event object.
            event = event.model_copy(deep=True)
            # Redact common text fields
            if event.goal:
                event.goal = redact_phi(event.goal).redacted
            if event.tool_call and event.tool_call.raw_command:
                event.tool_call.raw_command = redact_phi(event.tool_call.raw_command).redacted
            if event.prompt_preview:
                event.prompt_preview = redact_phi(event.prompt_preview).redacted
            if event.planner_output_preview:
                event.planner_output_preview = redact_phi(event.planner_output_preview).redacted
            if event.tool_result and isinstance(event.tool_result.output, str):
                event.tool_result.output = redact_phi(event.tool_result.output).redacted
            if event.agent_message and getattr(event.agent_message, "content", None):
                if isinstance(event.agent_message.content, dict):
                    # Shallow copy and redact string values
                    event.agent_message.content = {
                        k: redact_phi(v).redacted if isinstance(v, str) else v
                        for k, v in event.agent_message.content.items()
                    }
                elif isinstance(event.agent_message.content, str):
                    event.agent_message.content = redact_phi(event.agent_message.content).redacted
            if event.metadata:
                # Shallow copy to avoid mutating original
                event.metadata = {
                    k: redact_phi(v).redacted if isinstance(v, str) else v
                    for k, v in event.metadata.items()
                }

        async with self._lock:
            self._stats["ingested"] += 1

            trace = self._traces.get(event.session_id)
            if trace is None:
                session = AgentSession(
                    session_id=event.session_id,
                    agent_id=event.agent_id,
                    agent_name=event.agent_name,
                    framework=event.framework,
                )
                trace = Trace(session=session)
                self._traces[event.session_id] = trace
                self._session_index[event.session_id] = session

                # Seed session metadata from the first event's metadata
                if event.metadata:
                    trace.session.metadata.update(event.metadata)

                if len(self._traces) > self._max_traces:
                    oldest_key = next(iter(self._traces))
                    del self._traces[oldest_key]
                    self._session_index.pop(oldest_key, None)

            event_tenant = (event.metadata or {}).get("tenant_id")
            session_tenant = trace.session.metadata.get("tenant_id") or "default"
            if event_tenant is not None and event_tenant != session_tenant:
                # Decided under the lock, so two tenants racing to the same new session id
                # cannot interleave: the session belongs to whoever created it.
                self._stats["rejected_cross_tenant"] = (
                    self._stats.get("rejected_cross_tenant", 0) + 1
                )
                logger.warning(
                    "dropped an event for session %s from another tenant", event.session_id
                )
                return
            counted = trace.event_count
            trace.add_event(event)
            if trace.event_count == counted:
                return  # a retry: its tokens and cost are already in the totals
            trace.session.total_events = trace.event_count

            if event.event_type == EventType.SESSION_START:
                trace.session.goal = event.goal
            elif event.event_type == EventType.SESSION_END:
                trace.session.ended_at = event.timestamp
                trace.session.status = event.status
            elif event.event_type == EventType.AGENT_ERROR:
                trace.session.status = ExecutionStatus.FAILURE

            if event.tool_call and event.tool_call.tool_name:
                trace.session.metadata["current_tool"] = event.tool_call.tool_name

            if event.token_usage:
                trace.session.total_tokens += event.token_usage.total_tokens
                if event.token_usage.estimated_cost_usd:
                    trace.session.estimated_cost_usd += event.token_usage.estimated_cost_usd

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
                self._traces[session.session_id] = trace
                self._session_index[session.session_id] = session
                count += 1
            except Exception as exc:
                logger.warning("Failed to load trace from %s: %s", path, exc)

        logger.info("Loaded %d traces from disk", count)
        return count

    async def prune(self, session_ids: list[str], dry_run: bool = False) -> int:
        """Remove traces for specific sessions from memory and disk.

        Args:
            session_ids (list[str]): The IDs of the sessions to delete.
            dry_run (bool): If True, only return the count of files that would be deleted.

        Returns:
            int: The number of files that were (or would be) deleted.
        """
        count = 0
        paths_to_check: list[Path] = []

        async with self._lock:
            for session_id in session_ids:
                if not dry_run:
                    self._traces.pop(session_id, None)
                    self._session_index.pop(session_id, None)

                if self._storage_path:
                    base = self._storage_path.resolve()
                    path = (base / f"{session_id}.json").resolve()
                    if base not in path.parents:
                        logger.warning(
                            "Skipping suspicious session_id during prune: %s", session_id
                        )
                        continue
                    paths_to_check.append(path)

        for path in paths_to_check:
            if path.exists():
                if not dry_run:
                    try:
                        path.unlink()
                        count += 1
                    except Exception as exc:
                        logger.error("Failed to delete trace file %s: %s", path, exc)
                else:
                    count += 1

        return count

    async def get_sessions_older_than(self, cutoff: datetime) -> list[str]:
        """Find IDs of sessions that started before the cutoff time.

        This checks the modification time of the trace files on disk to determine age.

        Args:
            cutoff (datetime): The threshold date/time.

        Returns:
            list[str]: A list of session IDs older than the cutoff.
        """
        session_ids: list[str] = []
        if not self._storage_path or not self._storage_path.exists():
            return session_ids

        cutoff_timestamp = cutoff.timestamp()
        for path in self._storage_path.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff_timestamp:
                    session_ids.append(path.stem)
            except Exception as exc:
                logger.warning("Failed to check modification time for %s: %s", path, exc)

        return session_ids
