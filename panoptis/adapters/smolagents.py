"""
AgentWatch Smolagents Adapter (FOUND-004)

Hugging Face Smolagents — wraps CodeAgent / ToolCallingAgent.
Entry points: `run`, `step`. Tool calls happen inside `step`.
"""

from __future__ import annotations

import functools
import logging
import uuid
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    ToolCallData,
    ToolResultData,
)

logger = logging.getLogger(__name__)


class SmolagentsAdapter:
    """Wrap a smolagents CodeAgent / ToolCallingAgent."""

    def __init__(
        self,
        agent: Any,
        event_bus: EventBus | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ):
        self.agent = agent
        self.bus = event_bus or get_event_bus()
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"smolagents-{uuid.uuid4().hex[:8]}"
        self._step = 0

    def attach(self) -> Any:
        # Wrap entry points
        for name in ("run", "step", "execute"):
            if not hasattr(self.agent, name):
                continue
            original = getattr(self.agent, name)
            if not callable(original):
                continue
            if getattr(original, "_panoptis_wrapped", False):
                continue
            wrapped = self._wrap(name, original)
            wrapped._panoptis_wrapped = True  # type: ignore[attr-defined]
            try:
                setattr(self.agent, name, wrapped)
            except (AttributeError, TypeError):
                logger.debug("Smolagents: read-only method %s", name)

        # Wrap individual tools if exposed
        tools = getattr(self.agent, "tools", None)
        if isinstance(tools, dict):
            for tool_name, tool in list(tools.items()):
                self._wrap_tool(tool_name, tool)
        elif isinstance(tools, list):
            for tool in tools:
                self._wrap_tool(getattr(tool, "name", type(tool).__name__), tool)

        try:
            self.agent._panoptis_adapter = self
        except (AttributeError, TypeError):
            pass

        self._emit(EventType.SESSION_START, metadata={"adapter": "smolagents"})
        return self.agent

    def _wrap(self, name: str, original: Any) -> Any:
        @functools.wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
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

        return wrapped

    def _wrap_tool(self, tool_name: str, tool: Any) -> None:
        # Smolagents tools are callable objects with `forward` or `__call__`
        target_attr = "forward" if hasattr(tool, "forward") else "__call__"
        try:
            original = getattr(tool, target_attr)
        except AttributeError:
            return

        if getattr(original, "_panoptis_wrapped", False):
            return

        @functools.wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self._emit_tool_call(tool_name, args, kwargs)
            try:
                result = original(*args, **kwargs)
                self._emit_tool_result(tool_name, result, error=None)
                return result
            except Exception as exc:
                self._emit_tool_result(tool_name, None, error=str(exc))
                raise

        wrapped._panoptis_wrapped = True  # type: ignore[attr-defined]
        try:
            setattr(tool, target_attr, wrapped)
        except (AttributeError, TypeError):
            logger.debug("Smolagents: cannot wrap tool %s", tool_name)

    def _emit_tool_call(self, tool_name: str, args: tuple, kwargs: dict) -> None:
        try:
            event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                agent_name="smolagents",
                framework=AgentFramework.CUSTOM,
                event_type=EventType.TOOL_CALL,
                step_number=self._step,
                tool_call=ToolCallData(
                    tool_name=tool_name,
                    arguments={
                        "args": [str(a)[:200] for a in args],
                        "kwargs": {k: str(v)[:200] for k, v in kwargs.items()},
                    },
                ),
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Smolagents tool-call emit failed: %s", exc)

    def _emit_tool_result(self, tool_name: str, result: Any, *, error: str | None) -> None:
        try:
            event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                agent_name="smolagents",
                framework=AgentFramework.CUSTOM,
                event_type=EventType.TOOL_RESULT if error is None else EventType.TOOL_ERROR,
                status=ExecutionStatus.SUCCESS if error is None else ExecutionStatus.FAILURE,
                step_number=self._step,
                tool_result=ToolResultData(
                    tool_name=tool_name,
                    output=str(result)[:2000] if result is not None else None,
                    error=error,
                ),
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Smolagents tool-result emit failed: %s", exc)

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
                agent_name="smolagents",
                framework=AgentFramework.CUSTOM,
                event_type=event_type,
                status=status,
                step_number=self._step,
                metadata=metadata or {},
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Smolagents emit failed (suppressed): %s", exc)


__all__ = ["SmolagentsAdapter"]
