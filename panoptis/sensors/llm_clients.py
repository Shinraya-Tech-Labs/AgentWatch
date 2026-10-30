"""OpenAI and Anthropic SDK client sensors.

``instrument_openai(client, sink)`` / ``instrument_anthropic(client, sink)`` wrap the
client's create methods to record the request and response verbatim. The wrapper:

* declares a per-call ``call_id`` (sensor bookkeeping, used only to pair request and
  response), the provider's response id, and tool-call ids present in either message;
* nests under the active native span/run when there is one (``parent_span_id``/``run_id``);
* never changes arguments or return values, and never raises because of recording.
"""

from __future__ import annotations

import functools
import logging
import uuid
from typing import Any

from panoptis.evidence.canonical import to_jsonable
from panoptis.sensors.base import ObservationSink, Sensor
from panoptis.sensors.native.recorder import current_run, current_span


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - fall through to generic conversion
            logging.getLogger(__name__).debug("model_dump failed", exc_info=True)
    try:
        return to_jsonable(obj)
    except (TypeError, ValueError):
        return repr(obj)


def _context_ids() -> dict[str, str | None]:
    span = current_span()
    run = current_run()
    return {"parent_span_id": span.span_id if span else None, "run_id": run.run_id if run else None}


class _ClientSensor(Sensor):
    provider = "unknown"

    def wrap(self, target: Any, attr: str, operation: str) -> None:
        original = getattr(target, attr)
        sensor = self

        @functools.wraps(original)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_id = uuid.uuid4().hex[:16]
            ids = {"call_id": call_id, **_context_ids(), **sensor.request_ids(kwargs)}
            sensor.ctx.emit(
                f"{sensor.provider}.request",
                {"operation": operation, "request": _dump(kwargs)},
                declared_ids=ids,
            )
            try:
                response = original(*args, **kwargs)
            except BaseException as exc:
                sensor.ctx.emit(
                    f"{sensor.provider}.error",
                    {
                        "operation": operation,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    },
                    declared_ids={"call_id": call_id},
                )
                raise
            body = _dump(response)
            sensor.ctx.emit(
                f"{sensor.provider}.response",
                {"operation": operation, "response": body},
                declared_ids={"call_id": call_id, **sensor.response_ids(body)},
            )
            return response

        setattr(target, attr, wrapper)

    def request_ids(self, kwargs: dict[str, Any]) -> dict[str, str]:
        return {}

    def response_ids(self, body: Any) -> dict[str, str]:
        return {}


class OpenAIClientSensor(_ClientSensor):
    sensor_type = "openai"
    version = "1"
    provider = "openai"

    def request_ids(self, kwargs: dict[str, Any]) -> dict[str, str]:
        answered = [
            str(m.get("tool_call_id"))
            for m in kwargs.get("messages") or []
            if isinstance(m, dict) and m.get("tool_call_id")
        ]
        return {"answers_tool_calls": ",".join(answered)} if answered else {}

    def response_ids(self, body: Any) -> dict[str, str]:
        ids: dict[str, str] = {}
        if isinstance(body, dict):
            if body.get("id"):
                ids["response_id"] = str(body["id"])
            calls = [
                str(tc.get("id"))
                for ch in body.get("choices") or []
                for tc in ((ch.get("message") or {}).get("tool_calls") or [])
                if tc.get("id")
            ]
            if calls:
                ids["tool_call_ids"] = ",".join(calls)
        return ids


class AnthropicClientSensor(_ClientSensor):
    sensor_type = "anthropic"
    version = "1"
    provider = "anthropic"

    def request_ids(self, kwargs: dict[str, Any]) -> dict[str, str]:
        answered: list[str] = []
        for m in kwargs.get("messages") or []:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, list):
                answered.extend(
                    str(b.get("tool_use_id"))
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                )
        return {"answers_tool_calls": ",".join(answered)} if answered else {}

    def response_ids(self, body: Any) -> dict[str, str]:
        ids: dict[str, str] = {}
        if isinstance(body, dict):
            if body.get("id"):
                ids["response_id"] = str(body["id"])
            uses = [
                str(b.get("id"))
                for b in body.get("content") or []
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if uses:
                ids["tool_call_ids"] = ",".join(uses)
        return ids


def instrument_openai(
    client: Any, sink: ObservationSink, tenant_id: str = "default"
) -> OpenAIClientSensor:
    sensor = OpenAIClientSensor(sink, tenant_id)
    chat = getattr(getattr(client, "chat", None), "completions", None)
    if chat is not None and hasattr(chat, "create"):
        sensor.wrap(chat, "create", "chat.completions")
    emb = getattr(client, "embeddings", None)
    if emb is not None and hasattr(emb, "create"):
        sensor.wrap(emb, "create", "embeddings")
    return sensor


def instrument_anthropic(
    client: Any, sink: ObservationSink, tenant_id: str = "default"
) -> AnthropicClientSensor:
    sensor = AnthropicClientSensor(sink, tenant_id)
    messages = getattr(client, "messages", None)
    if messages is not None and hasattr(messages, "create"):
        sensor.wrap(messages, "create", "messages")
    return sensor
