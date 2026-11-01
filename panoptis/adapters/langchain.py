# pylint: disable=unused-argument,protected-access,broad-exception-caught,too-many-arguments,too-many-instance-attributes,too-few-public-methods,too-many-return-statements,no-else-return,missing-function-docstring,keyword-arg-before-vararg,too-many-positional-arguments
"""
AgentWatch LangChain Adapter
Hooks into LangChain's callback system to emit normalized AgentWatch events.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    TokenUsage,
    ToolCallData,
    ToolResultData,
)

logger = logging.getLogger(__name__)


class AgentWatchCallbackHandler:
    """
    LangChain callback handler that emits AgentWatch events.
    Compatible with LangChain's BaseCallbackHandler interface.

    Usage:
        from panoptis.adapters.langchain import AgentWatchCallbackHandler

        handler = AgentWatchCallbackHandler(session_id="my-session")
        llm = ChatOpenAI(callbacks=[handler])
        agent = AgentExecutor(agent=..., tools=..., callbacks=[handler])
    """

    def __init__(
        self,
        session_id: str | None = None,
        agent_id: str | None = None,
        event_bus: EventBus | None = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"langchain-{uuid.uuid4().hex[:8]}"
        self._bus = event_bus or get_event_bus()
        self._step = 0
        self._run_map: dict[str, str] = {}  # run_id -> event_id for correlation

    def _step_up(self) -> int:
        self._step += 1
        return self._step

    def _base(self, event_type: EventType, run_id: str | None = None) -> AgentEvent:
        event = AgentEvent(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name="langchain",
            framework=AgentFramework.LANGCHAIN,
            event_type=event_type,
            step_number=self._step_up(),
        )
        if run_id:
            self._run_map[str(run_id)] = event.event_id
        return event

    def _emit_sync(self, event: AgentEvent) -> None:
        self._bus.publish_sync(event)

    # ── LLM callbacks ──────────────────────────────────────────────────────

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.PLANNER_INPUT, run_id=str(run_id))
        if prompts:
            event.prompt_preview = prompts[0][:500]
        event.metadata = {
            "model": serialized.get("name", serialized.get("id", ["?"])[-1]),
            **(metadata or {}),
        }
        self._emit_sync(event)

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.PLANNER_OUTPUT)
        event.status = ExecutionStatus.SUCCESS

        # Extract usage if available
        try:
            if hasattr(response, "llm_output") and response.llm_output:
                usage_meta = response.llm_output.get(
                    "usage", response.llm_output.get("token_usage", {})
                )
                if usage_meta:
                    event.token_usage = TokenUsage(
                        prompt_tokens=usage_meta.get("prompt_tokens", 0),
                        completion_tokens=usage_meta.get("completion_tokens", 0),
                        total_tokens=usage_meta.get("total_tokens", 0),
                    )
        except Exception:  # noqa: S110  # nosec B110 — malformed usage metadata is safe to skip
            pass  # Malformed usage metadata — safe to ignore, event still emits

        self._emit_sync(event)

    def on_llm_error(
        self,
        error: Exception | KeyboardInterrupt,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.AGENT_ERROR)
        event.status = ExecutionStatus.FAILURE
        event.metadata["error"] = str(error)
        self._emit_sync(event)

    # ── Chain callbacks ──────────────────────────────────────────────────

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.AGENT_START, run_id=str(run_id))
        chain_type = serialized.get("name", serialized.get("id", ["?"])[-1])
        event.metadata["chain_type"] = chain_type
        if "input" in inputs:
            event.goal = str(inputs["input"])[:500]
        self._emit_sync(event)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.AGENT_END)
        event.status = ExecutionStatus.SUCCESS
        if "output" in outputs:
            event.metadata["output_preview"] = str(outputs["output"])[:500]
        self._emit_sync(event)

    def on_chain_error(
        self,
        error: Exception | KeyboardInterrupt,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.AGENT_ERROR)
        event.status = ExecutionStatus.FAILURE
        event.metadata["error"] = str(error)
        self._emit_sync(event)

    # ── Tool callbacks ──────────────────────────────────────────────────

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.TOOL_CALL, run_id=str(run_id))
        tool_name = serialized.get("name", serialized.get("id", ["?"])[-1])

        # Detect bash/shell tools for raw_command
        raw_command = None
        if any(kw in tool_name.lower() for kw in ("bash", "shell", "terminal", "run")):
            raw_command = input_str

        event.tool_call = ToolCallData(
            tool_name=tool_name,
            arguments={"input": input_str},
            raw_command=raw_command,
        )
        self._emit_sync(event)

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.TOOL_RESULT)
        event.status = ExecutionStatus.SUCCESS
        event.tool_result = ToolResultData(
            tool_name="unknown",
            output=output[:2000] if output else None,
        )
        self._emit_sync(event)

    def on_tool_error(
        self,
        error: Exception | KeyboardInterrupt,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.TOOL_ERROR)
        event.status = ExecutionStatus.FAILURE
        event.tool_result = ToolResultData(
            tool_name="unknown",
            error=str(error),
        )
        self._emit_sync(event)

    # ── Agent action callbacks ────────────────────────────────────────────

    def on_agent_action(
        self,
        action: Any,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Called when agent decides on an action."""
        event = self._base(EventType.PLANNER_OUTPUT)
        tool = getattr(action, "tool", "")
        tool_input = getattr(action, "tool_input", "")
        log = getattr(action, "log", "")

        if log:
            event.planner_output_preview = log[:500]

        event.metadata["planned_tool"] = tool
        event.metadata["planned_input"] = str(tool_input)[:200]
        self._emit_sync(event)

    def on_agent_finish(
        self,
        finish: Any,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        event = self._base(EventType.AGENT_END)
        event.status = ExecutionStatus.SUCCESS
        output = getattr(finish, "return_values", {})
        if "output" in output:
            event.metadata["final_output"] = str(output["output"])[:500]
        self._emit_sync(event)


def create_langchain_handler(
    session_id: str | None = None,
    agent_id: str | None = None,
) -> AgentWatchCallbackHandler:
    """Factory function for easy handler creation."""
    return AgentWatchCallbackHandler(session_id=session_id, agent_id=agent_id)
