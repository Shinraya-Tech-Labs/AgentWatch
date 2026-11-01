"""OpenTelemetry sensor: OTLP decoding (JSON, and protobuf when available) and an
in-process SpanProcessor. One observation per span, stored as received (flattened)."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Any

from panoptis.evidence.model import ClockInfo, ObservationDraft, SensorRef

SENSOR_VERSION = "1"


def _any_value(v: dict[str, Any]) -> Any:
    if not isinstance(v, dict):
        return v
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in v:
            return v[key]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {
            kv["key"]: _any_value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])
        }
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def _attrs(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {kv["key"]: _any_value(kv.get("value", {})) for kv in items or []}


def _hex_id(value: Any, length: int) -> str | None:
    """OTLP/JSON ids are hex; tolerate base64 (protobuf-JSON mapping) too."""
    if not value:
        return None
    s = str(value)
    if len(s) == length and all(c in "0123456789abcdefABCDEF" for c in s):
        return s.lower()
    try:
        return base64.b64decode(s).hex()
    except (ValueError, TypeError):
        return s


def _ts(nanos: Any) -> datetime | None:
    if nanos in (None, "", 0, "0"):
        return None
    return datetime.fromtimestamp(int(nanos) / 1e9, tz=UTC)


def spans_from_otlp_json(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten an ExportTraceServiceRequest (OTLP/JSON) into span dicts."""
    out: list[dict[str, Any]] = []
    for rs in body.get("resourceSpans", []) or []:
        resource = _attrs((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])) or []:
            scope = ss.get("scope") or ss.get("instrumentationLibrary") or {}
            for sp in ss.get("spans", []) or []:
                out.append(
                    {
                        "trace_id": _hex_id(sp.get("traceId"), 32),
                        "span_id": _hex_id(sp.get("spanId"), 16),
                        "parent_span_id": _hex_id(sp.get("parentSpanId"), 16),
                        "name": sp.get("name"),
                        "kind": sp.get("kind"),
                        "start_time_unix_nano": str(sp.get("startTimeUnixNano") or ""),
                        "end_time_unix_nano": str(sp.get("endTimeUnixNano") or ""),
                        "attributes": _attrs(sp.get("attributes")),
                        "status": sp.get("status") or {},
                        "events": [
                            {
                                "name": e.get("name"),
                                "time_unix_nano": str(e.get("timeUnixNano") or ""),
                                "attributes": _attrs(e.get("attributes")),
                            }
                            for e in sp.get("events", []) or []
                        ],
                        "links": [
                            {
                                "trace_id": _hex_id(lk.get("traceId"), 32),
                                "span_id": _hex_id(lk.get("spanId"), 16),
                                "attributes": _attrs(lk.get("attributes")),
                            }
                            for lk in sp.get("links", []) or []
                        ],
                        "resource": resource,
                        "scope": {"name": scope.get("name"), "version": scope.get("version")},
                    }
                )
    return out


def spans_from_otlp_protobuf(data: bytes) -> list[dict[str, Any]]:
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "OTLP protobuf decoding requires the 'opentelemetry-proto' package (pip install panoptis[otel])"
        ) from exc
    req = ExportTraceServiceRequest()
    req.ParseFromString(data)
    return spans_from_otlp_json(MessageToDict(req))


def drafts_from_spans(
    spans: list[dict[str, Any]], *, instance_id: str = "otlp-receiver", tenant_id: str = "default"
) -> list[ObservationDraft]:
    ref = SensorRef("otel", SENSOR_VERSION, instance_id)
    drafts = []
    for sp in spans:
        start = _ts(sp.get("start_time_unix_nano"))
        drafts.append(
            ObservationDraft(
                sensor=ref,
                source_kind="otel.span",
                payload=sp,
                observed_at=start,
                clock=ClockInfo(
                    source="source",
                    clock_id=str(
                        (sp.get("resource") or {}).get("host.name")
                        or (sp.get("resource") or {}).get("service.name")
                        or "otel"
                    ),
                ),
                declared_ids={
                    k: v
                    for k, v in {
                        "trace_id": sp.get("trace_id"),
                        "span_id": sp.get("span_id"),
                        "parent_span_id": sp.get("parent_span_id"),
                    }.items()
                    if v
                },
                tenant_id=tenant_id,
            )
        )
    return drafts


try:  # inherit the SDK base class when available so newer SDK hooks (e.g. _on_ending) exist
    from opentelemetry.sdk.trace import SpanProcessor as _SpanProcessorBase
except ImportError:  # pragma: no cover - optional dependency
    _SpanProcessorBase = object  # type: ignore[assignment,misc]


class AgentWatchSpanProcessor(_SpanProcessorBase):  # type: ignore[misc,valid-type]
    """OpenTelemetry SDK SpanProcessor that emits each finished span as an observation."""

    def __init__(self, sink: Any, tenant_id: str = "default") -> None:
        self.sink = sink
        self.tenant_id = tenant_id
        self.instance_id = f"otel-sdk-{uuid.uuid4().hex[:8]}"

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def _on_ending(self, span: Any) -> None:
        return None

    def on_end(self, span: Any) -> None:
        try:
            ctx = span.get_span_context()
            parent = span.parent
            status = span.status
            sp = {
                "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
                "parent_span_id": format(parent.span_id, "016x") if parent else None,
                "name": span.name,
                "kind": getattr(span.kind, "name", str(span.kind)),
                "start_time_unix_nano": str(span.start_time or ""),
                "end_time_unix_nano": str(span.end_time or ""),
                "attributes": dict(span.attributes or {}),
                "status": {
                    "code": getattr(status.status_code, "name", str(status.status_code)),
                    "message": status.description,
                },
                "events": [
                    {
                        "name": e.name,
                        "time_unix_nano": str(e.timestamp),
                        "attributes": dict(e.attributes or {}),
                    }
                    for e in span.events
                ],
                "links": [
                    {
                        "trace_id": format(lk.context.trace_id, "032x"),
                        "span_id": format(lk.context.span_id, "016x"),
                        "attributes": dict(lk.attributes or {}),
                    }
                    for lk in span.links
                ],
                "resource": dict(span.resource.attributes) if span.resource else {},
                "scope": {
                    "name": getattr(span.instrumentation_scope, "name", None),
                    "version": getattr(span.instrumentation_scope, "version", None),
                },
            }
            for d in drafts_from_spans(
                [sp], instance_id=self.instance_id, tenant_id=self.tenant_id
            ):
                self.sink.emit(d)
        except Exception:  # pragma: no cover - never break the host application
            return

    def shutdown(self) -> None:
        self.sink.flush()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.sink.flush()
        return True
