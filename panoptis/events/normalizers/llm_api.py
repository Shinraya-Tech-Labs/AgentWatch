"""Shared request/response pairing for LLM provider API sensors."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from panoptis.events.model import DeclaredLink, Diagnostic, EntityRef, EventKind, EventStatus
from panoptis.events.normalize import (
    EventBuilder,
    NormalizeContext,
    Normalizer,
    NormalizeResult,
    source_order,
    temporal,
)
from panoptis.evidence.model import RawObservation


def token_logprobs(choice: Any) -> list[dict[str, Any]]:
    """Per-token logprobs of one OpenAI-style choice, trimmed to what analysis needs.

    Returns ``[]`` when the provider sent none (the common case): logprobs are opt-in on the
    request, so absence is normal and must not be treated as an error.

    Token text is left out on purpose: entropy only needs the numbers, and the text would sit in
    event attributes outside the artifact redaction path.
    """
    content = (choice.get("logprobs") or {}).get("content") if isinstance(choice, dict) else None
    out: list[dict[str, Any]] = []
    for item in content or []:
        if not isinstance(item, dict) or not isinstance(item.get("logprob"), int | float):
            continue
        top = [
            {"logprob": t["logprob"]}
            for t in item.get("top_logprobs") or []
            if isinstance(t, dict) and isinstance(t.get("logprob"), int | float)
        ]
        out.append({"logprob": item["logprob"], "top_logprobs": top})
    return out


class LLMApiNormalizer(Normalizer):
    provider = "unknown"
    maturity = "EXPERIMENTAL"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        return self._declared_key(obs, "run_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        calls: dict[str, dict[str, RawObservation]] = {}
        for obs in source_order(observations):
            cid = obs.declared("call_id")
            if not cid:
                result.diagnostics.append(
                    Diagnostic(
                        obs.obs_id,
                        "error",
                        "missing_call_id",
                        "provider observation without call_id",
                    )
                )
                continue
            calls.setdefault(cid, {})[obs.source_kind.rsplit(".", 1)[1]] = obs
        for cid, parts in calls.items():
            result.events.append(self._call(cid, parts, ctx, result))
        return result

    def _call(
        self,
        cid: str,
        parts: dict[str, RawObservation],
        ctx: NormalizeContext,
        result: NormalizeResult,
    ) -> Any:
        req = parts.get("request")
        end = parts.get("response") or parts.get("error")
        obs = [o for o in (req, end) if o]
        b = EventBuilder(self, ctx, obs)
        rq: dict[str, Any] = req.payload() if req else {}
        ep: dict[str, Any] = end.payload() if end else {}
        request = rq.get("request") or {}
        response = ep.get("response") or {}
        model = request.get("model") or (
            response.get("model") if isinstance(response, dict) else None
        )
        b.kind = EventKind.MODEL_INVOCATION
        b.operation = f"{rq.get('operation') or ep.get('operation')}:{model or 'unknown'}"
        if model:
            b.object = EntityRef("model", f"{self.provider}/{model}")
        else:
            b.extra_missing.append("model")
        self.map_io(b, request, response)
        anchor = req or end
        assert anchor is not None  # nosec B101 — invariant: group has request or response
        b.source_ids = [(f"{self.provider}.call", cid)]
        rid = end.declared("response_id") if end else None
        if rid:
            b.source_ids.append((f"{self.provider}.response", rid))
        for tc in (end.declared("tool_call_ids") or "").split(",") if end else []:
            if tc:
                b.source_ids.append(("llm.tool_call", tc))
        for tc in (anchor.declared("answers_tool_calls") or "").split(","):
            if tc:
                b.parents.append(DeclaredLink("depends_on", "llm.tool_call", tc))
        if anchor.declared("parent_span_id"):
            b.parents.append(
                DeclaredLink("parent", "native.span", anchor.declared("parent_span_id") or "")
            )
        elif anchor.declared("run_id"):
            b.parents.append(DeclaredLink("parent", "native.run", anchor.declared("run_id") or ""))
        if anchor.declared("run_id"):
            b.run_key = ("native.run", anchor.declared("run_id") or "")
        if end is None:
            b.status = EventStatus.UNKNOWN
            result.diagnostics.append(
                Diagnostic(
                    anchor.obs_id,
                    "warning",
                    "no_response",
                    f"{self.provider} call {cid} has no response",
                )
            )
        elif end.source_kind.endswith(".error"):
            b.status = EventStatus.ERROR
            b.error = ep.get("error")
        else:
            b.status = EventStatus.OK
        b.time = temporal(req, end) if req else temporal(end, None)
        return b.build()

    def map_io(self, b: EventBuilder, request: dict[str, Any], response: Any) -> None:
        raise NotImplementedError  # provider-specific


class OpenAINormalizer(LLMApiNormalizer):
    name = "openai"
    version = "1"
    provider = "openai"
    source_kinds = frozenset({"openai.request", "openai.response", "openai.error"})

    def map_io(self, b: EventBuilder, request: dict[str, Any], response: Any) -> None:
        if request.get("messages") is not None:
            b.input(request["messages"], role="prompt")
        if request.get("input") is not None:
            b.input(request["input"], role="input")
        if request.get("tools"):
            b.attributes["tools_offered"] = [
                ((t.get("function") or {}).get("name") or t.get("name"))
                for t in request["tools"]
                if isinstance(t, dict)
            ]
        if isinstance(response, dict):
            for ch in response.get("choices") or []:
                msg = ch.get("message") or {}
                if "logprobs" not in b.attributes and (lp := token_logprobs(ch)):
                    b.attributes["logprobs"] = lp  # first choice only: one token stream
                if msg.get("content") is not None:
                    b.output(msg["content"], role="completion")
                if msg.get("tool_calls"):
                    b.output(msg["tool_calls"], role="tool_calls")
                    b.facets.append("tool_call_request")
            usage = response.get("usage") or {}
            b.resources = {
                k: v
                for k, v in {
                    "tokens_in": usage.get("prompt_tokens"),
                    "tokens_out": usage.get("completion_tokens"),
                }.items()
                if v is not None
            }


class AnthropicNormalizer(LLMApiNormalizer):
    name = "anthropic"
    version = "1"
    provider = "anthropic"
    source_kinds = frozenset({"anthropic.request", "anthropic.response", "anthropic.error"})

    def map_io(self, b: EventBuilder, request: dict[str, Any], response: Any) -> None:
        prompt = (
            {"system": request.get("system"), "messages": request.get("messages")}
            if request.get("system")
            else request.get("messages")
        )
        if prompt is not None:
            b.input(prompt, role="prompt")
        if request.get("tools"):
            b.attributes["tools_offered"] = [
                t.get("name") for t in request["tools"] if isinstance(t, dict)
            ]
        if isinstance(response, dict):
            texts = [
                c.get("text", "")
                for c in response.get("content") or []
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            uses = [
                c
                for c in response.get("content") or []
                if isinstance(c, dict) and c.get("type") == "tool_use"
            ]
            if texts:
                b.output("\n".join(texts), role="completion")
            if uses:
                b.output(uses, role="tool_calls")
                b.facets.append("tool_call_request")
            usage = response.get("usage") or {}
            b.resources = {
                k: v
                for k, v in {
                    "tokens_in": usage.get("input_tokens"),
                    "tokens_out": usage.get("output_tokens"),
                }.items()
                if v is not None
            }
            if response.get("stop_reason"):
                b.attributes["stop_reason"] = response["stop_reason"]
