"""Normalizer for OpenTelemetry spans, including GenAI semantic conventions.

Mapping table (``otel-genai@1``):

* ``gen_ai.operation.name`` in chat / text_completion / generate_content / embeddings → MODEL_INVOCATION
* ``execute_tool`` or ``gen_ai.tool.name`` → TOOL_INVOCATION
* ``invoke_agent`` / ``create_agent`` → OPERATION (actor ``agent:<gen_ai.agent.name>``)
* ``db.system`` → RETRIEVAL for read operations, STATE_MUTATION otherwise
* ``messaging.system`` → MESSAGE
* ``http.*`` / ``url.full`` with client kind → EXTERNAL_IO
* anything else → OPERATION (a named unit of work); attributes are preserved verbatim

OTel status UNSET is the protocol default for spans that completed without an error
being recorded; it is mapped to OK and ``attributes["otel.status"] = "UNSET"`` keeps
that distinction visible.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from panoptis.events.model import (
    DeclaredLink,
    Effect,
    EffectKind,
    EntityRef,
    EventKind,
    EventStatus,
    TemporalInfo,
)
from panoptis.events.normalize import (
    EventBuilder,
    NormalizeContext,
    Normalizer,
    NormalizeResult,
    source_order,
)
from panoptis.evidence.model import RawObservation
from panoptis.telemetry.otel import AW_SPAN_KIND_ATTR, normalize_otel_span

MODEL_OPS = {"chat", "text_completion", "generate_content", "embeddings", "completion"}
READ_DB_OPS = {"select", "get", "find", "query", "search", "read", "mget", "hget", "scan"}


# AgentWatch OpenTelemetry extension attributes (ADR-0018). These are AgentWatch's own
# convention, namespaced to say so; they are not part of the OpenTelemetry specification.
AW_ARTIFACT_OPERATION = "panoptis.artifact.operation"  # create | write | update | delete | read
AW_ARTIFACT_ID = "panoptis.artifact.id"  # the artifact's name or path
AW_ARTIFACT_CONTENT = "panoptis.artifact.content"  # content written or read (optional)
AW_ARTIFACT_PARENT = "panoptis.artifact.parent"  # the artifact this one derives from
AW_INSTANCE_ID = "panoptis.information.instance_id"  # id(s) of the value(s) this span produced
AW_SOURCE = "panoptis.information.source"  # instance id(s) this span's input was built from


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _ns(v: Any) -> datetime | None:
    if v in (None, "", "0", 0):
        return None
    return datetime.fromtimestamp(int(v) / 1e9, tz=UTC)


class OTelNormalizer(Normalizer):
    name = "otel"
    # 2: AgentWatch OTel extension attributes (ADR-0018)
    # 3: OpenInference spans are bridged to gen_ai.* before mapping (normalize_otel_span)
    version = "3"
    source_kinds = frozenset({"otel.span"})
    maturity = "EXPERIMENTAL"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        return self._declared_key(obs, "trace_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        for obs in source_order(observations):
            result.events.append(self._span(obs, ctx))
        return result

    def _span(self, obs: RawObservation, ctx: NormalizeContext) -> Any:
        sp: dict[str, Any] = obs.payload()
        raw_attrs: dict[str, Any] = sp.get("attributes") or {}
        a: dict[str, Any] = normalize_otel_span(raw_attrs)
        res: dict[str, Any] = sp.get("resource") or {}
        b = EventBuilder(self, ctx, [obs])
        trace = sp.get("trace_id") or ""
        name = str(sp.get("name") or "span")
        op = str(a.get("gen_ai.operation.name") or "").lower()
        service = res.get("service.name")
        if service:
            b.actor = EntityRef("service", str(service))
        if a.get("gen_ai.agent.name"):
            b.actor = EntityRef("agent", str(a["gen_ai.agent.name"]))

        if op in MODEL_OPS:
            b.kind = EventKind.MODEL_INVOCATION
            model = a.get("gen_ai.request.model") or a.get("gen_ai.response.model")
            system = a.get("gen_ai.system") or a.get("gen_ai.provider.name")
            b.operation = f"{op}:{model}" if model else op
            if model:
                b.object = EntityRef("model", f"{system}/{model}" if system else str(model))
            else:
                b.extra_missing.append("model")
        elif op == "execute_tool" or a.get("gen_ai.tool.name"):
            b.kind = EventKind.TOOL_INVOCATION
            tool = a.get("gen_ai.tool.name") or name
            b.operation = str(tool)
            b.object = EntityRef("tool", str(tool))
        elif op in ("invoke_agent", "create_agent"):
            b.kind = EventKind.OPERATION
            b.operation = name
            b.facets.append("agent")
        elif a.get("db.system") or a.get("db.system.name"):
            system = a.get("db.system") or a.get("db.system.name")
            dbop = str(a.get("db.operation") or a.get("db.operation.name") or "").lower()
            b.kind = EventKind.RETRIEVAL if dbop in READ_DB_OPS else EventKind.STATE_MUTATION
            b.operation = f"{system}:{dbop or name}"
            b.object = EntityRef(
                "db", f"{system}/{a.get('db.name') or a.get('db.namespace') or 'default'}"
            )
            b.effects.append(
                Effect(
                    EffectKind.READ if b.kind == EventKind.RETRIEVAL else EffectKind.WRITE,
                    b.object.canonical,
                )
            )
            if a.get("db.statement") or a.get("db.query.text"):
                b.input(a.get("db.statement") or a.get("db.query.text"), role="query")
        elif a.get("messaging.system"):
            b.kind = EventKind.MESSAGE
            dest = (
                a.get("messaging.destination.name") or a.get("messaging.destination") or "unknown"
            )
            b.operation = f"{a.get('messaging.operation') or 'message'}:{dest}"
            b.object = EntityRef("queue", f"{a['messaging.system']}/{dest}")
        elif (
            a.get("http.method")
            or a.get("http.request.method")
            or a.get("url.full")
            or a.get("http.url")
        ):
            kind = str(sp.get("kind", "")).upper()
            if "SERVER" in kind or kind == "2":
                b.kind = EventKind.OPERATION
                b.operation = name
                b.facets.append("http_server")
            else:
                b.kind = EventKind.EXTERNAL_IO
                url = str(a.get("url.full") or a.get("http.url") or "")
                host = (
                    a.get("server.address")
                    or a.get("net.peer.name")
                    or (url.split("/")[2] if "://" in url else "unknown")
                )
                b.operation = f"{a.get('http.request.method') or a.get('http.method')} {host}"
                b.object = EntityRef("http", str(host))
        elif a.get(AW_SPAN_KIND_ATTR) == "RETRIEVAL":  # OpenInference RETRIEVER
            b.kind = EventKind.RETRIEVAL
            b.operation = name
        else:
            b.kind = EventKind.OPERATION
            b.operation = name

        # AgentWatch extension (ADR-0018; NOT an OpenTelemetry convention): artifact operations
        art_op = str(a.get(AW_ARTIFACT_OPERATION) or "").lower()
        if art_op:
            art_id = str(a.get(AW_ARTIFACT_ID) or name)
            b.object = EntityRef("file", art_id)
            if art_op in ("create", "write", "update", "delete"):
                b.kind = EventKind.STATE_MUTATION
                b.operation = f"{art_op}:{art_id}"
                if art_op != "delete":
                    b.facets.append("artifact_creation")
                b.effects.append(Effect(EffectKind.WRITE, b.object.canonical))
                if a.get(AW_ARTIFACT_CONTENT) is not None:
                    b.output(a[AW_ARTIFACT_CONTENT], role="artifact", label=art_id)
            elif art_op == "read":
                b.kind = EventKind.EXTERNAL_IO
                b.operation = f"read:{art_id}"
                b.effects.append(Effect(EffectKind.READ, b.object.canonical))
                if a.get(AW_ARTIFACT_CONTENT) is not None:
                    b.output(a[AW_ARTIFACT_CONTENT], role="content", label=art_id)
            if a.get(AW_ARTIFACT_PARENT):
                b.attributes["artifact.parent"] = a[AW_ARTIFACT_PARENT]

        # GenAI content (attribute and span-event conventions)
        if a.get("gen_ai.prompt") is not None:
            b.input(a["gen_ai.prompt"], role="prompt")
        if a.get("gen_ai.input.messages") is not None:
            b.input(a["gen_ai.input.messages"], role="prompt")
        if a.get("gen_ai.completion") is not None:
            b.output(a["gen_ai.completion"], role="completion")
        if a.get("gen_ai.output.messages") is not None:
            b.output(a["gen_ai.output.messages"], role="completion")
        tool_args = a.get("gen_ai.tool.call.arguments")
        if tool_args is None:
            tool_args = a.get("gen_ai.tool.arguments")
        if tool_args is not None:
            b.input(tool_args, role="arguments")
        tool_res = a.get("gen_ai.tool.call.result")
        if tool_res is None:
            tool_res = a.get("gen_ai.tool.result")
        if tool_res is not None:
            b.output(tool_res, role="result")
        for ev in sp.get("events") or []:
            ea = ev.get("attributes") or {}
            en = str(ev.get("name") or "")
            body = (
                ea.get("gen_ai.prompt")
                if ea.get("gen_ai.prompt") is not None
                else ea.get("gen_ai.completion")
                if ea.get("gen_ai.completion") is not None
                else ea.get("content")
                if ea.get("content") is not None
                else ea.get("body")
                if ea.get("body") is not None
                else ea.get("message")
            )
            if body is not None:
                if en in (
                    "gen_ai.content.prompt",
                    "gen_ai.user.message",
                    "gen_ai.system.message",
                    "gen_ai.tool.message",
                ):
                    b.input(body, role="prompt")
                elif en in (
                    "gen_ai.content.completion",
                    "gen_ai.choice",
                    "gen_ai.assistant.message",
                ):
                    b.output(body, role="completion")
            if en == "gen_ai.choice" and ea.get("finish_reason"):
                reasons = b.attributes.get("gen_ai.response.finish_reasons")
                if reasons is None:
                    b.attributes["gen_ai.response.finish_reasons"] = [ea["finish_reason"]]
                elif isinstance(reasons, list):
                    if ea["finish_reason"] not in reasons:
                        reasons.append(ea["finish_reason"])

        tokens_in = a.get("gen_ai.usage.input_tokens")
        if tokens_in is None:
            tokens_in = a.get("gen_ai.usage.prompt_tokens")
        tokens_out = a.get("gen_ai.usage.output_tokens")
        if tokens_out is None:
            tokens_out = a.get("gen_ai.usage.completion_tokens")
        tokens_total = a.get("gen_ai.usage.total_tokens")
        if tokens_total is None and tokens_in is not None and tokens_out is not None:
            tokens_total = tokens_in + tokens_out

        cache_read = a.get("gen_ai.usage.cache_read_input_tokens")
        if cache_read is None:
            cache_read = a.get("gen_ai.usage.cache_read_tokens")

        cache_creation = a.get("gen_ai.usage.cache_creation_input_tokens")
        if cache_creation is None:
            cache_creation = a.get("gen_ai.usage.cache_creation_tokens")

        usage = {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_total,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
        }
        start, end = _ns(sp.get("start_time_unix_nano")), _ns(sp.get("end_time_unix_nano"))
        b.resources = {k: v for k, v in usage.items() if v is not None}
        if start and end:
            b.resources["latency_ms"] = (end - start).total_seconds() * 1000.0
        status = sp.get("status") or {}
        code = str(status.get("code", "")).upper()
        if code in ("2", "STATUS_CODE_ERROR", "ERROR"):
            b.status = EventStatus.ERROR
            b.error = {"message": status.get("message")}
        else:
            b.status = EventStatus.OK
            if code in ("", "0", "STATUS_CODE_UNSET", "UNSET"):
                b.attributes["otel.status"] = "UNSET"
        for ev in sp.get("events") or []:
            if ev.get("name") == "exception":
                ea = ev.get("attributes") or {}
                b.error = {"type": ea.get("exception.type"), "message": ea.get("exception.message")}
        b.attributes.update(
            {
                "otel.name": name,
                "otel.kind": sp.get("kind"),
                "otel.attributes": raw_attrs,
                "otel.scope": sp.get("scope"),
            }
        )
        for genai_attr in (
            "gen_ai.request.temperature",
            "gen_ai.request.top_p",
            "gen_ai.request.max_tokens",
            "gen_ai.request.stop_sequences",
            "gen_ai.request.presence_penalty",
            "gen_ai.request.frequency_penalty",
            "gen_ai.request.seed",
            "gen_ai.response.id",
            "gen_ai.response.finish_reasons",
            "gen_ai.response.model",
            "gen_ai.tool.call.id",
            "gen_ai.tool.name",
        ):
            if a.get(genai_attr) is not None:
                b.attributes[genai_attr] = a[genai_attr]
        if service:
            b.attributes["service.name"] = service
        if res.get("service.version"):
            b.attributes["code_version"] = res.get("service.version")
        # AgentWatch extension: declared information identity of outputs and sources of inputs
        instance_ids = _as_list(a.get(AW_INSTANCE_ID))
        for k, iid in enumerate(instance_ids[: len(b.outputs)]):
            b.outputs[k] = replace(b.outputs[k], instance=str(iid))
        sources = [str(x) for x in _as_list(a.get(AW_SOURCE))]
        if sources:
            if b.inputs:
                b.inputs[0] = replace(b.inputs[0], sources=tuple(sources))
            else:
                b.reference(sources)
        b.source_ids = [("otel.span", f"{trace}/{sp.get('span_id')}")]
        if sp.get("parent_span_id"):
            b.parents.append(DeclaredLink("parent", "otel.span", f"{trace}/{sp['parent_span_id']}"))
        for lk in sp.get("links") or []:
            if lk.get("span_id"):
                b.parents.append(
                    DeclaredLink(
                        "depends_on", "otel.span", f"{lk.get('trace_id') or trace}/{lk['span_id']}"
                    )
                )
        if trace:
            b.run_key = ("otel.trace", trace)
        b.time = TemporalInfo(
            start=start,
            end=end,
            basis="source" if start else "missing",
            ordering_key=f"{trace}:{sp.get('span_id')}",
        )
        ev_out = b.build()
        if not sp.get("parent_span_id"):
            ev_out = replace(
                ev_out,
                missing=tuple(m for m in ev_out.missing if m != "parent"),
                facets=tuple(sorted({*ev_out.facets, "trace_root"})),
            )
        return ev_out
