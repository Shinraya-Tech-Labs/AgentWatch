"""
AgentWatch LangGraph Adapter (FOUND-004)

LangGraph compiles agent graphs into `CompiledGraph` objects exposing
`invoke`, `ainvoke`, `stream`, and `astream`. We intercept those calls and
emit one AgentEvent per node transition when streaming.
"""

from __future__ import annotations

import functools
import inspect
import logging
import uuid
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


class LangGraphAdapter:
    """Wrap a LangGraph CompiledGraph with AgentWatch observability."""

    def __init__(
        self,
        graph: Any,
        event_bus: EventBus | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ):
        self.graph = graph
        self.bus = event_bus or get_event_bus()
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"langgraph-{uuid.uuid4().hex[:8]}"
        self._step = 0

    def attach(self) -> Any:
        # Wrap each entry point if present
        for name in ("invoke", "ainvoke", "stream", "astream"):
            if not hasattr(self.graph, name):
                continue
            original = getattr(self.graph, name)
            if getattr(original, "_panoptis_wrapped", False):
                continue
            wrapped = self._wrap(name, original)
            wrapped._panoptis_wrapped = True  # type: ignore[attr-defined]
            try:
                setattr(self.graph, name, wrapped)
            except (AttributeError, TypeError):
                logger.debug("LangGraph: read-only method %s", name)

        try:
            self.graph._panoptis_adapter = self
        except (AttributeError, TypeError):
            pass

        self._emit(EventType.SESSION_START, metadata={"adapter": "langgraph"})
        return self.graph

    def _wrap(self, name: str, original: Any) -> Any:
        is_stream = "stream" in name
        is_async = inspect.iscoroutinefunction(original) or name == "astream"

        if is_async and is_stream:

            @functools.wraps(original)
            async def async_stream_wrapped(*args: Any, **kwargs: Any):
                self._step += 1
                self._emit(EventType.AGENT_START, metadata={"method": name})
                try:
                    async for chunk in original(*args, **kwargs):
                        self._handle_chunk(chunk)
                        yield chunk
                    self._emit(EventType.AGENT_END, status=ExecutionStatus.SUCCESS)
                except Exception as exc:
                    self._emit(
                        EventType.AGENT_ERROR,
                        status=ExecutionStatus.FAILURE,
                        metadata={"error": str(exc)},
                    )
                    raise

            return async_stream_wrapped

        if is_async:

            @functools.wraps(original)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                self._step += 1
                self._emit(EventType.AGENT_START, metadata={"method": name})
                try:
                    result = await original(*args, **kwargs)
                    self._emit(EventType.AGENT_END, status=ExecutionStatus.SUCCESS)
                    return result
                except Exception as exc:
                    self._emit(
                        EventType.AGENT_ERROR,
                        status=ExecutionStatus.FAILURE,
                        metadata={"error": str(exc)},
                    )
                    raise

            return async_wrapped

        if is_stream:

            @functools.wraps(original)
            def sync_stream_wrapped(*args: Any, **kwargs: Any):
                self._step += 1
                self._emit(EventType.AGENT_START, metadata={"method": name})
                try:
                    for chunk in original(*args, **kwargs):
                        self._handle_chunk(chunk)
                        yield chunk
                    self._emit(EventType.AGENT_END, status=ExecutionStatus.SUCCESS)
                except Exception as exc:
                    self._emit(
                        EventType.AGENT_ERROR,
                        status=ExecutionStatus.FAILURE,
                        metadata={"error": str(exc)},
                    )
                    raise

            return sync_stream_wrapped

        @functools.wraps(original)
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            self._step += 1
            self._emit(EventType.AGENT_START, metadata={"method": name})
            try:
                result = original(*args, **kwargs)
                self._emit(EventType.AGENT_END, status=ExecutionStatus.SUCCESS)
                return result
            except Exception as exc:
                self._emit(
                    EventType.AGENT_ERROR,
                    status=ExecutionStatus.FAILURE,
                    metadata={"error": str(exc)},
                )
                raise

        return sync_wrapped

    def _handle_chunk(self, chunk: Any) -> None:
        # LangGraph stream yields { node_name: state } dicts
        if isinstance(chunk, dict):
            for node, state in chunk.items():
                self._emit(
                    EventType.PLANNER_OUTPUT,
                    metadata={
                        "node": node,
                        "state_keys": list(state) if isinstance(state, dict) else None,
                    },
                )
        else:
            self._emit(
                EventType.PLANNER_OUTPUT,
                metadata={"chunk_type": type(chunk).__name__},
            )

    def _emit(
        self,
        event_type: EventType,
        *,
        status: ExecutionStatus = ExecutionStatus.RUNNING,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                agent_name="langgraph",
                framework=AgentFramework.CUSTOM,
                event_type=event_type,
                status=status,
                step_number=self._step,
                metadata=metadata or {},
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("LangGraph emit failed (suppressed): %s", exc)


__all__ = ["LangGraphAdapter"]
