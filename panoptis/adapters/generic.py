"""
Generic CLI Adapter for PANOPTIS.
Wraps any command-line agent (e.g. ollama, antigravity) and captures its output.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import UTC, datetime

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    AgentSession,
    EventType,
    ExecutionStatus,
    ToolResultData,
)

logger = logging.getLogger(__name__)

# Agent CLIs write colour and cursor escapes even to a pipe; keep them out of
# the captured event so every consumer gets readable text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class GenericCLIAdapter:
    def __init__(
        self,
        agent_cmd: str,
        event_bus: EventBus | None = None,
        safety_engine: SafetyEngine | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        working_dir: str | None = None,
    ):
        self.agent_cmd = agent_cmd
        self._bus = event_bus or get_event_bus()
        self._safety = safety_engine or SafetyEngine()
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"generic-{uuid.uuid4().hex[:8]}"
        self._working_dir = working_dir or os.getcwd()
        self._session: AgentSession | None = None
        self._events: list[AgentEvent] = []
        self._start_time: float = 0.0

    async def run(self, prompt: str, **kwargs) -> AgentSession:
        self._session = AgentSession(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name=self.agent_id,
            framework=AgentFramework.CUSTOM,
            goal=prompt[:500],
        )

        start_event = AgentEvent(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name=self.agent_id,
            framework=AgentFramework.CUSTOM,
            event_type=EventType.SESSION_START,
            goal=prompt[:500],
        )
        await self._emit(start_event)

        # The command is the agent_cmd + the prompt
        full_cmd = f'{self.agent_cmd} "{prompt}"'

        try:
            process = await asyncio.create_subprocess_shell(
                full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                self._session.status = ExecutionStatus.FAILURE

            # Create a result event
            res_event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                framework=AgentFramework.CUSTOM,
                event_type=EventType.TOOL_RESULT,
                status=ExecutionStatus.SUCCESS
                if process.returncode == 0
                else ExecutionStatus.FAILURE,
                tool_result=ToolResultData(
                    tool_name="cli_exec",
                    output=_ANSI_RE.sub("", stdout.decode(errors="replace")),
                    error=(_ANSI_RE.sub("", stderr.decode(errors="replace")) if stderr else None),
                ),
            )
            await self._emit(res_event)

        except Exception as e:
            error_event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                framework=AgentFramework.CUSTOM,
                event_type=EventType.AGENT_ERROR,
                status=ExecutionStatus.FAILURE,
                metadata={"error": str(e)},
            )
            await self._emit(error_event)
        finally:
            self._session.ended_at = datetime.now(UTC)
            if self._session.status == ExecutionStatus.RUNNING:
                self._session.status = ExecutionStatus.SUCCESS

            end_event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                framework=AgentFramework.CUSTOM,
                event_type=EventType.SESSION_END,
                status=self._session.status,
            )
            await self._emit(end_event)

        return self._session

    async def _emit(self, event: AgentEvent) -> None:
        self._events.append(event)
        if self._session is not None:
            self._session.total_events = len(self._events)
        await self._bus.publish(event)

    @property
    def events(self) -> list[AgentEvent]:
        return list(self._events)

    @property
    def session(self) -> AgentSession | None:
        return self._session
