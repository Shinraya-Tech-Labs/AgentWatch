# pylint: disable=unused-argument,protected-access,broad-exception-caught,too-many-arguments,too-many-instance-attributes,too-few-public-methods,too-many-return-statements,no-else-return,missing-function-docstring,keyword-arg-before-vararg,too-many-positional-arguments
"""
PANOPTIS CrewAI Adapter
Converts CrewAI lifecycle callbacks into PANOPTIS events.
"""

from __future__ import annotations

import logging
import uuid

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


class AgentWatchCrewAIAdapter:
    """
    Minimal CrewAI adapter following LangChain adapter pattern.
    """

    def __init__(
        self,
        session_id: str | None = None,
        agent_id: str | None = None,
        event_bus: EventBus | None = None,
    ):
        """
        Initialize CrewAI adapter with session, agent and event bus.
        """
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"crewai-{uuid.uuid4().hex[:8]}"
        self._bus = event_bus or get_event_bus()
        self._step = 0

    # ── helpers ───────────────────────────────

    def _next_step(self) -> int:
        """Increment and return execution step counter."""
        self._step += 1
        return self._step

    def _emit(self, event: AgentEvent) -> None:
        """Publish event to PANOPTIS event bus."""
        self._bus.publish_sync(event)

    def _base(self, event_type: EventType) -> AgentEvent:
        """Create base AgentEvent with shared metadata."""
        return AgentEvent(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name="crewai",
            framework=AgentFramework.CREWAI,
            event_type=event_type,
            step_number=self._next_step(),
        )

    # ── CrewAI callbacks ──────────────────────

    def on_agent_start(self, *args, **kwargs):
        """
        Handles CrewAI agent start event.
        Emits SESSION_START event.
        """
        event = self._base(EventType.SESSION_START)
        event.metadata = {"args": str(args), "kwargs": str(kwargs)}
        self._emit(event)

    def on_agent_finish(self, *args, **kwargs):
        """
        Handles CrewAI agent finish event.
        Emits SESSION_END event.
        """
        event = self._base(EventType.SESSION_END)
        event.status = ExecutionStatus.SUCCESS
        event.metadata = {"result": str(kwargs.get("result"))}
        self._emit(event)

    def on_tool_start(self, tool_name, *args, **kwargs):
        """
        Handles tool start event.
        Emits TOOL_CALL event.
        """
        event = self._base(EventType.TOOL_CALL)
        event.metadata = {
            "tool_name": tool_name,
            "args": str(args),
            "kwargs": str(kwargs),
        }
        self._emit(event)

    def on_tool_end(self, tool_name, result=None, *args, **kwargs):
        """
        Handles tool end event.
        Emits TOOL_RESULT event.
        """
        event = self._base(EventType.TOOL_RESULT)
        event.status = ExecutionStatus.SUCCESS
        event.metadata = {
            "tool_name": tool_name,
            "result": str(result),
        }
        self._emit(event)

    def on_chain_error(self, error, *args, **kwargs):
        """
        Handles chain error event.
        Emits AGENT_ERROR event.
        """
        event = self._base(EventType.AGENT_ERROR)
        event.status = ExecutionStatus.FAILURE
        event.metadata = {"error": str(error)}
        self._emit(event)
