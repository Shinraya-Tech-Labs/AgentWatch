# pylint: disable=unused-argument,protected-access,broad-exception-caught,too-many-arguments,too-many-instance-attributes,too-few-public-methods,too-many-return-statements,no-else-return,missing-function-docstring,keyword-arg-before-vararg,too-many-positional-arguments
"""
PANOPTIS AutoGen Adapter (FOUND-004)

Wraps Microsoft AutoGen's ConversableAgent / GroupChat objects.
AutoGen emits messages through `generate_reply`, `send`, and `receive`.
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
    AgentMessageData,
    EventType,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


class AutoGenAdapter:
    """Wrap an AutoGen agent or GroupChat with PANOPTIS observability."""

    INTERCEPT = (
        "generate_reply",
        "a_generate_reply",
        "send",
        "a_send",
        "receive",
        "a_receive",
        "initiate_chat",
        "a_initiate_chat",
        "run",
    )

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
        agent_name = getattr(agent, "name", None) or type(agent).__name__
        self.agent_id = agent_id or f"autogen-{agent_name}-{uuid.uuid4().hex[:6]}"
        self.agent_name = agent_name
        self._step = 0

    def attach(self) -> Any:
        for name in self.INTERCEPT:
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
                logger.debug("AutoGen: read-only method %s", name)

        try:
            self.agent._panoptis_adapter = self
        except (AttributeError, TypeError):
            pass

        self._emit(
            EventType.SESSION_START, metadata={"adapter": "autogen", "name": self.agent_name}
        )
        return self.agent

    def _wrap(self, name: str, original: Any) -> Any:
        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                self._step += 1
                event_type = self._classify(name)
                self._emit(event_type, metadata={"method": name})
                try:
                    result = await original(*args, **kwargs)
                    self._maybe_emit_message(name, args, kwargs, result)
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

        @functools.wraps(original)
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            self._step += 1
            event_type = self._classify(name)
            self._emit(event_type, metadata={"method": name})
            try:
                result = original(*args, **kwargs)
                self._maybe_emit_message(name, args, kwargs, result)
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

    def _classify(self, method_name: str) -> EventType:
        if "send" in method_name or "receive" in method_name:
            return EventType.AGENT_MESSAGE
        if "generate" in method_name:
            return EventType.PLANNER_OUTPUT
        if "chat" in method_name or method_name == "run":
            return EventType.AGENT_START
        return EventType.AGENT_START

    def _maybe_emit_message(self, method_name: str, args: tuple, kwargs: dict, result: Any) -> None:
        if "send" not in method_name and "receive" not in method_name:
            return
        try:
            # AutoGen send signature: send(message, recipient, ...)
            message = args[0] if args else kwargs.get("message")
            recipient = args[1] if len(args) > 1 else kwargs.get("recipient")
            recipient_name = getattr(recipient, "name", str(recipient)) if recipient else "unknown"
            content = message.get("content", "") if isinstance(message, dict) else str(message)

            event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                framework=AgentFramework.CUSTOM,
                event_type=EventType.AGENT_MESSAGE,
                step_number=self._step,
                agent_message=AgentMessageData(
                    sender_agent_id=self.agent_id,
                    receiver_agent_id=recipient_name,
                    message_type="task" if "send" in method_name else "result",
                    content={"text": str(content)[:500]},
                ),
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AutoGen message emit failed: %s", exc)

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
                agent_name=self.agent_name,
                framework=AgentFramework.CUSTOM,
                event_type=event_type,
                status=status,
                step_number=self._step,
                metadata=metadata or {},
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AutoGen emit failed (suppressed): %s", exc)


__all__ = ["AutoGenAdapter"]
