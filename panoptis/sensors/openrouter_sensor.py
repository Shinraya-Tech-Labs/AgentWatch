"""OpenRouter SDK sensor for LLM call interception.

Wraps OpenAI-compatible client ``chat.completions.create`` (sync and async) when
configured for OpenRouter (base_url contains 'openrouter.ai') to emit AGENT_INTENT
and TOOL_CALL events to the EventBus with latency, token usage, and tool call data.
"""

from __future__ import annotations

import functools
import time
import uuid
from typing import Any

from panoptis.core.event_bus import get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    TokenUsage,
    ToolCallData,
)

_OPENAI_AVAILABLE = False
try:
    import openai  # noqa: F401

    _OPENAI_AVAILABLE = True
except Exception:  # noqa: BLE001,S110  # nosec B110 — optional dependency not installed
    pass  # pragma: no cover - defensive import


_OPENROUTER_BASE_URLS = ("openrouter.ai", "openrouter.com")


def _is_openrouter_client(client: Any) -> bool:
    """Check if the client is configured for OpenRouter."""
    try:
        base_url = getattr(client, "base_url", None)
        if base_url:
            base_url_str = str(base_url)
            return any(domain in base_url_str for domain in _OPENROUTER_BASE_URLS)
    except Exception:  # noqa: BLE001,S110  # nosec B110 — malformed client, assume not OpenRouter
        pass
    return False


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:  # noqa: BLE001,S110  # nosec B110 — fall through to generic conversion
            pass
    if hasattr(obj, "__dict__"):
        try:
            return {k: _dump(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
        except Exception:  # noqa: BLE001,S110  # nosec B110 — fall through to generic conversion
            pass
    if isinstance(obj, (list, tuple)):
        return [_dump(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    try:
        return obj
    except Exception:  # noqa: BLE001,S110  # nosec B110 — fall through to repr
        return repr(obj)


def _extract_tool_calls(response: Any) -> list[ToolCallData]:
    """Extract tool calls from an OpenRouter/OpenAI response."""
    tool_calls: list[ToolCallData] = []
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return tool_calls
        for choice in choices:
            message = getattr(choice, "message", None)
            if not message:
                continue
            tc_list = getattr(message, "tool_calls", None)
            if not tc_list:
                continue
            for tc in tc_list:
                func = getattr(tc, "function", None)
                if not func:
                    continue
                name = getattr(func, "name", None)
                if not name:
                    continue
                args_str = getattr(func, "arguments", "{}")
                try:
                    import json

                    arguments = json.loads(args_str)
                except Exception:  # noqa: BLE001,S110  # nosec B110 — invalid JSON, preserve raw
                    arguments = {"raw": args_str}
                tool_calls.append(
                    ToolCallData(
                        tool_name=name,
                        tool_id=getattr(tc, "id", None),
                        arguments=arguments,
                    )
                )
    except Exception:  # noqa: BLE001,S110  # nosec B110 — malformed response, return empty
        pass
    return tool_calls


def _extract_usage(response: Any) -> TokenUsage | None:
    """Extract token usage from an OpenRouter/OpenAI response."""
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return None
        return TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
        )
    except Exception:  # noqa: BLE001,S110  # nosec B110 — malformed usage, return None
        return None


def _get_model_name(response: Any, kwargs: dict[str, Any]) -> str:
    """Get model name from response or request."""
    try:
        model = getattr(response, "model", None)
        if model:
            return str(model)
    except Exception:  # noqa: BLE001,S110  # nosec B110 — malformed response, fall back to request
        pass
    return str(kwargs.get("model", "unknown"))


class OpenRouterSensor:
    """Sensor that wraps an OpenAI-compatible client (configured for OpenRouter)
    to emit events to the EventBus.
    """

    def __init__(
        self,
        client: Any,
        session_id: str,
        agent_id: str,
        agent_name: str | None = None,
        framework: AgentFramework = AgentFramework.CUSTOM,
    ) -> None:
        if not _OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI SDK not installed. Install with: pip install openai")
        if not _is_openrouter_client(client):
            raise ValueError(
                "Client does not appear to be configured for OpenRouter. "
                "Ensure base_url contains 'openrouter.ai' or 'openrouter.com'."
            )
        self.client = client
        self.session_id = session_id
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.framework = framework
        self.bus = get_event_bus()
        self._wrap_methods()

    def _wrap_methods(self) -> None:
        chat = getattr(getattr(self.client, "chat", None), "completions", None)
        if chat is not None:
            if hasattr(chat, "create"):
                self._wrap_sync(chat, "create", "chat.completions.create")
            if hasattr(chat, "acreate"):
                self._wrap_async(chat, "acreate", "chat.completions.create")

    def _wrap_sync(self, target: Any, attr: str, operation: str) -> None:
        original = getattr(target, attr)
        sensor = self

        @functools.wraps(original)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            call_id = uuid.uuid4().hex[:16]

            # Emit AGENT_INTENT event on call start
            event = AgentEvent(
                session_id=sensor.session_id,
                agent_id=sensor.agent_id,
                agent_name=sensor.agent_name,
                framework=sensor.framework,
                event_type=EventType.AGENT_INTENT,
                status=ExecutionStatus.RUNNING,
                prompt_preview=_preview_prompt(kwargs.get("messages")),
                metadata={
                    "operation": operation,
                    "call_id": call_id,
                    "model": kwargs.get("model"),
                    "temperature": kwargs.get("temperature"),
                    "max_tokens": kwargs.get("max_tokens"),
                    "provider": "openrouter",
                },
            )
            sensor.bus.publish_sync(event)

            try:
                response = original(*args, **kwargs)
            except BaseException as exc:
                duration_ms = (time.perf_counter() - start) * 1000
                error_event = AgentEvent(
                    session_id=sensor.session_id,
                    agent_id=sensor.agent_id,
                    agent_name=sensor.agent_name,
                    framework=sensor.framework,
                    event_type=EventType.AGENT_ERROR,
                    status=ExecutionStatus.FAILURE,
                    duration_ms=duration_ms,
                    metadata={
                        "operation": operation,
                        "call_id": call_id,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "provider": "openrouter",
                    },
                )
                sensor.bus.publish_sync(error_event)
                raise

            duration_ms = (time.perf_counter() - start) * 1000
            model = _get_model_name(response, kwargs)
            usage = _extract_usage(response)
            tool_calls = _extract_tool_calls(response)

            # Emit TOOL_CALL events for each tool call
            for tc in tool_calls:
                tc_event = AgentEvent(
                    session_id=sensor.session_id,
                    agent_id=sensor.agent_id,
                    agent_name=sensor.agent_name,
                    framework=sensor.framework,
                    event_type=EventType.TOOL_CALL,
                    status=ExecutionStatus.RUNNING,
                    duration_ms=duration_ms,
                    tool_call=tc,
                    token_usage=usage,
                    metadata={
                        "operation": operation,
                        "call_id": call_id,
                        "model": model,
                        "response_id": getattr(response, "id", None),
                        "provider": "openrouter",
                    },
                )
                sensor.bus.publish_sync(tc_event)

            # Emit completion event
            completion_event = AgentEvent(
                session_id=sensor.session_id,
                agent_id=sensor.agent_id,
                agent_name=sensor.agent_name,
                framework=sensor.framework,
                event_type=EventType.AGENT_INTENT,
                status=ExecutionStatus.SUCCESS,
                duration_ms=duration_ms,
                token_usage=usage,
                prompt_preview=_preview_prompt(kwargs.get("messages")),
                metadata={
                    "operation": operation,
                    "call_id": call_id,
                    "model": model,
                    "response_id": getattr(response, "id", None),
                    "tool_call_count": len(tool_calls),
                    "provider": "openrouter",
                },
            )
            sensor.bus.publish_sync(completion_event)

            return response

        setattr(target, attr, wrapper)

    def _wrap_async(self, target: Any, attr: str, operation: str) -> None:
        original = getattr(target, attr)
        sensor = self

        @functools.wraps(original)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            call_id = uuid.uuid4().hex[:16]

            # Emit AGENT_INTENT event on call start
            event = AgentEvent(
                session_id=sensor.session_id,
                agent_id=sensor.agent_id,
                agent_name=sensor.agent_name,
                framework=sensor.framework,
                event_type=EventType.AGENT_INTENT,
                status=ExecutionStatus.RUNNING,
                prompt_preview=_preview_prompt(kwargs.get("messages")),
                metadata={
                    "operation": operation,
                    "call_id": call_id,
                    "model": kwargs.get("model"),
                    "temperature": kwargs.get("temperature"),
                    "max_tokens": kwargs.get("max_tokens"),
                    "provider": "openrouter",
                },
            )
            await sensor.bus.publish(event)

            try:
                response = await original(*args, **kwargs)
            except BaseException as exc:
                duration_ms = (time.perf_counter() - start) * 1000
                error_event = AgentEvent(
                    session_id=sensor.session_id,
                    agent_id=sensor.agent_id,
                    agent_name=sensor.agent_name,
                    framework=sensor.framework,
                    event_type=EventType.AGENT_ERROR,
                    status=ExecutionStatus.FAILURE,
                    duration_ms=duration_ms,
                    metadata={
                        "operation": operation,
                        "call_id": call_id,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "provider": "openrouter",
                    },
                )
                await sensor.bus.publish(error_event)
                raise

            duration_ms = (time.perf_counter() - start) * 1000
            model = _get_model_name(response, kwargs)
            usage = _extract_usage(response)
            tool_calls = _extract_tool_calls(response)

            # Emit TOOL_CALL events for each tool call
            for tc in tool_calls:
                tc_event = AgentEvent(
                    session_id=sensor.session_id,
                    agent_id=sensor.agent_id,
                    agent_name=sensor.agent_name,
                    framework=sensor.framework,
                    event_type=EventType.TOOL_CALL,
                    status=ExecutionStatus.RUNNING,
                    duration_ms=duration_ms,
                    tool_call=tc,
                    token_usage=usage,
                    metadata={
                        "operation": operation,
                        "call_id": call_id,
                        "model": model,
                        "response_id": getattr(response, "id", None),
                        "provider": "openrouter",
                    },
                )
                await sensor.bus.publish(tc_event)

            # Emit completion event
            completion_event = AgentEvent(
                session_id=sensor.session_id,
                agent_id=sensor.agent_id,
                agent_name=sensor.agent_name,
                framework=sensor.framework,
                event_type=EventType.AGENT_INTENT,
                status=ExecutionStatus.SUCCESS,
                duration_ms=duration_ms,
                token_usage=usage,
                prompt_preview=_preview_prompt(kwargs.get("messages")),
                metadata={
                    "operation": operation,
                    "call_id": call_id,
                    "model": model,
                    "response_id": getattr(response, "id", None),
                    "tool_call_count": len(tool_calls),
                    "provider": "openrouter",
                },
            )
            await sensor.bus.publish(completion_event)

            return response

        setattr(target, attr, async_wrapper)


def _preview_prompt(messages: Any) -> str | None:
    """Extract a preview of the prompt from messages."""
    if not messages:
        return None
    try:
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if content and isinstance(content, str):
                        return content[:500]
    except Exception:  # noqa: BLE001,S110  # nosec B110 — malformed messages, return None
        pass
    return None


def instrument_openrouter(
    client: Any,
    session_id: str,
    agent_id: str,
    agent_name: str | None = None,
    framework: AgentFramework = AgentFramework.CUSTOM,
) -> OpenRouterSensor:
    """Instrument an OpenAI-compatible client configured for OpenRouter.

    Args:
        client: An ``openai.OpenAI`` or ``openai.AsyncOpenAI`` client with
            ``base_url`` set to an OpenRouter endpoint.
        session_id: Session identifier for event correlation.
        agent_id: Agent identifier.
        agent_name: Optional human-readable agent name.
        framework: Source framework (default: CUSTOM).

    Returns:
        The sensor instance (keeps wrappers alive).

    Raises:
        RuntimeError: If the OpenAI SDK is not installed.
        ValueError: If the client is not configured for OpenRouter.
    """
    return OpenRouterSensor(client, session_id, agent_id, agent_name, framework)
