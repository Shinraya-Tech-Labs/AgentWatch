"""
OBS-001 — Full-Span Trace Collection.

Typed spans for the four span kinds AgentWatch tracks:
    REASONING   — model "thinking" / planner output
    TOOL_CALL   — outbound side-effecting calls
    MEMORY_READ — episodic/semantic/procedural memory access
    MODEL_CALL  — actual LLM API request/response

Each Span captures: input, output, latency, token count, retry count,
error state. This is the minimum schema required to debug any failure mode.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from panoptis.core.schema import AgentEvent, EventType


class SpanKind(str, Enum):
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    MEMORY_READ = "memory_read"
    MODEL_CALL = "model_call"
    GENERIC = "generic"


@dataclass
class Span:
    """Typed span — one unit of observable agent work."""

    span_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    parent_span_id: str | None = None
    kind: SpanKind = SpanKind.GENERIC
    name: str = ""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = None

    input: Any = None
    output: Any = None
    error: str | None = None

    token_count: int = 0
    retry_count: int = 0

    attributes: dict[str, Any] = field(default_factory=dict)
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def finish(self, output: Any = None, error: str | None = None) -> None:
        self.end_time = datetime.now(UTC)
        if output is not None:
            self.output = output
        if error is not None:
            self.error = error

    @property
    def latency_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() * 1000.0

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "kind": self.kind.value,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "latency_ms": self.latency_ms,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "token_count": self.token_count,
            "retry_count": self.retry_count,
            "attributes": self.attributes,
        }


_EVENT_KIND_MAP: dict[EventType, SpanKind] = {
    EventType.PLANNER_INPUT: SpanKind.REASONING,
    EventType.PLANNER_OUTPUT: SpanKind.REASONING,
    EventType.TOOL_CALL: SpanKind.TOOL_CALL,
    EventType.TOOL_RESULT: SpanKind.TOOL_CALL,
    EventType.TOOL_ERROR: SpanKind.TOOL_CALL,
    EventType.TOOL_RETRY: SpanKind.TOOL_CALL,
    EventType.MEMORY_READ: SpanKind.MEMORY_READ,
    EventType.MEMORY_WRITE: SpanKind.MEMORY_READ,
}


def span_kind_for(event: AgentEvent) -> SpanKind:
    return _EVENT_KIND_MAP.get(event.event_type, SpanKind.GENERIC)


def event_to_span(event: AgentEvent) -> Span:
    """Convert an AgentEvent into a Span."""
    kind = span_kind_for(event)
    span = Span(
        span_id=event.event_id,
        trace_id=event.trace_id or event.session_id,
        parent_span_id=event.parent_event_id,
        kind=kind,
        name=event.event_type.value,
        start_time=event.timestamp,
        token_count=event.token_usage.total_tokens if event.token_usage else 0,
        attributes={
            "agent.id": event.agent_id,
            "agent.framework": event.framework.value,
            "agent.step": event.step_number,
        },
    )

    if event.tool_call:
        span.input = {
            "tool_name": event.tool_call.tool_name,
            "arguments": event.tool_call.arguments,
            "raw_command": event.tool_call.raw_command,
        }
    if event.tool_result:
        span.output = {
            "output": event.tool_result.output,
            "error": event.tool_result.error,
            "exit_code": event.tool_result.exit_code,
        }
        if event.tool_result.error:
            span.error = event.tool_result.error
    if event.planner_output_preview:
        span.output = event.planner_output_preview
    if event.prompt_preview:
        span.input = event.prompt_preview
    if event.duration_ms is not None:
        span.end_time = event.timestamp

    # Retry detection: tool retry events flag this
    if event.event_type == EventType.TOOL_RETRY:
        span.retry_count = int(event.metadata.get("retry_count", 1))

    return span


class SpanRegistry:
    """In-memory store of spans indexed by trace_id and kind."""

    def __init__(self) -> None:
        self._by_trace: dict[str, list[Span]] = {}
        self._by_kind: dict[SpanKind, list[Span]] = {k: [] for k in SpanKind}

    def add(self, span: Span) -> None:
        self._by_trace.setdefault(span.trace_id, []).append(span)
        self._by_kind[span.kind].append(span)

    def ingest_event(self, event: AgentEvent) -> Span:
        span = event_to_span(event)
        self.add(span)
        return span

    def by_trace(self, trace_id: str) -> list[Span]:
        return list(self._by_trace.get(trace_id, []))

    def by_kind(self, kind: SpanKind) -> list[Span]:
        return list(self._by_kind.get(kind, []))

    def count(self) -> int:
        return sum(len(v) for v in self._by_trace.values())

    def clear(self) -> None:
        self._by_trace.clear()
        for k in self._by_kind:
            self._by_kind[k].clear()


__all__ = ["Span", "SpanKind", "SpanRegistry", "event_to_span", "span_kind_for"]
