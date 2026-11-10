"""
PANOPTIS Telemetry
OpenTelemetry integration for distributed tracing, metrics, and logging.
Exports spans to OTLP, Jaeger, or stdout.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from panoptis._version import __version__

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# OTel imports — graceful degradation if absent
# ─────────────────────────────────────────────
try:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.semconv.resource import ResourceAttributes

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    logger.debug("opentelemetry-sdk not installed — telemetry disabled")


class TelemetryConfig:
    def __init__(
        self,
        service_name: str = "panoptis",
        service_version: str = __version__,
        otlp_endpoint: str | None = None,
        export_to_console: bool = False,
        enable_metrics: bool = True,
        endpoint: str | None = None,  # Compatibility alias
        insecure: bool | None = None,  # Legacy
        headers: dict[str, str] | None = None,  # Legacy
    ):
        self.service_name = service_name
        self.service_version = service_version
        self.otlp_endpoint = endpoint or otlp_endpoint
        self.endpoint = self.otlp_endpoint  # Original OTELConfig attribute
        self.insecure = insecure
        self.headers = headers
        self.export_to_console = export_to_console
        self.enable_metrics = enable_metrics


# Compatibility aliases for OBS tests
OTELConfig = TelemetryConfig


class TelemetryProvider:
    """
    Wraps OpenTelemetry setup for PANOPTIS.
    Falls back gracefully when OTel SDK is not installed.
    """

    def __init__(self, config: TelemetryConfig | None = None):
        self._config = config or TelemetryConfig()
        self._tracer = None
        self._tracer_provider: Any = None
        self._emitted_trace_ids: set[str] = set()
        self._meter = None
        self._initialized = False
        self._buffer: list[Any] = []
        self._max_buffer_size = 1000
        self._exporter: Any = None

        # Metric instruments (created after init)
        self._event_counter = None
        self._blocked_counter = None
        self._session_duration = None
        self._token_counter = None

    def _export_with_retry(
        self,
        spans: list[Any],
        max_retries: int = 3,
        initial_delay: float = 1.0,
    ) -> None:
        """Export spans with exponential backoff retry."""
        if not self._exporter:
            return

        delay = initial_delay

        for attempt in range(max_retries):
            try:
                self._exporter.export(spans)
                return

            except Exception as exc:
                logger.warning(
                    "Span export failed (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

            if attempt == max_retries - 1:
                logger.error(
                    "Span export failed after %s retries",
                    max_retries,
                )
                return

            time.sleep(delay)
            delay *= 2

    def export(self, span: Any) -> None:
        """Export a span to the configured backend (or buffer if failing)."""
        if not self._initialized:
            if len(self._buffer) >= self._max_buffer_size:
                self._buffer.pop(0)
                logger.warning("Telemetry buffer overflow — dropping oldest span")
            self._buffer.append(span)
            return

        if self._exporter and hasattr(self._exporter, "export"):
            try:
                # OTel exporters usually expect a sequence of spans
                self._export_with_retry([span])
            except Exception as exc:
                logger.debug("Manual span export failed: %s", exc)
        else:
            # Real OTel export would happen here if SDK is available
            logger.debug("Exported span: %s", span.name)

    def _flush_buffer(self) -> None:
        """Flush the internal span buffer to the active exporter."""
        if not self._buffer:
            return

        logger.debug("Flushing %d buffered spans", len(self._buffer))
        for span in self._buffer:
            self.export(span)
        self._buffer.clear()

    def _flush_exported_spans(self, timeout_millis: int = 30000) -> bool:
        """Force-flush queued spans and report whether the export completed."""
        provider = self._tracer_provider
        if self._exporter is None or provider is None or not hasattr(provider, "force_flush"):
            return False

        try:
            return bool(provider.force_flush(timeout_millis))
        except Exception as exc:
            logger.warning("Telemetry span flush failed: %s", exc)
            return False

    def grafana_dashboard_template(self) -> dict[str, Any]:
        """Return a basic Grafana dashboard template for PANOPTIS."""
        return {
            "uid": "panoptis-main",
            "title": "PANOPTIS Observability",
            "panels": [
                {"title": "Confidence (p50 / p95)", "type": "timeseries"},
                {"title": "Risk Levels", "type": "piechart"},
                {"title": "Token Consumption", "type": "stat"},
            ],
        }

    def initialize(self) -> None:
        if self._initialized:
            logger.debug("Telemetry already initialized; skipping.")
            return

        if not _OTEL_AVAILABLE:
            self._initialized = True
            logger.info("OpenTelemetry not available — using no-op telemetry fallback")
            self._flush_buffer()
            return

        resource = Resource.create(
            {
                ResourceAttributes.SERVICE_NAME: self._config.service_name,
                ResourceAttributes.SERVICE_VERSION: self._config.service_version,
            }
        )

        # ── Tracing setup ────────────────────────────────────────────────
        tracer_provider = TracerProvider(resource=resource)

        if self._config.otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

                self._exporter = OTLPSpanExporter(
                    endpoint=self._config.otlp_endpoint,
                    insecure=self._config.insecure if self._config.insecure is not None else True,
                    headers=self._config.headers,
                )
                tracer_provider.add_span_processor(BatchSpanProcessor(self._exporter))
                logger.info("OTLP span exporter configured: %s", self._config.otlp_endpoint)
            except ImportError:
                logger.warning("opentelemetry-exporter-otlp not installed")

        if self._config.export_to_console:
            console_exporter = ConsoleSpanExporter()
            if not self._exporter:
                self._exporter = console_exporter
            tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))

        self._tracer_provider = tracer_provider

        try:
            trace.set_tracer_provider(tracer_provider)
        except RuntimeError:
            logger.debug("Tracer provider already set; using existing.")

        self._tracer = trace.get_tracer(
            self._config.service_name,
            self._config.service_version,
        )

        # ── Metrics setup ────────────────────────────────────────────────
        if self._config.enable_metrics:
            readers = []
            if self._config.otlp_endpoint:
                try:
                    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                        OTLPMetricExporter,
                    )

                    metric_exporter = OTLPMetricExporter(
                        endpoint=self._config.otlp_endpoint,
                        insecure=self._config.insecure
                        if self._config.insecure is not None
                        else True,
                        headers=self._config.headers,
                    )
                    readers.append(PeriodicExportingMetricReader(metric_exporter))
                except ImportError:
                    pass

            if self._config.export_to_console:
                readers.append(
                    PeriodicExportingMetricReader(
                        ConsoleMetricExporter(), export_interval_millis=60000
                    )
                )

            meter_provider = MeterProvider(resource=resource, metric_readers=readers)
            try:
                metrics.set_meter_provider(meter_provider)
            except RuntimeError:
                logger.debug("Meter provider already set; using existing.")

            self._meter = metrics.get_meter(self._config.service_name)
            self._create_instruments()

        self._initialized = True
        self._flush_buffer()
        logger.info("Telemetry initialized (service=%s)", self._config.service_name)

    def _create_instruments(self) -> None:
        if not self._meter:
            return
        self._event_counter = self._meter.create_counter(
            "panoptis.events.total",
            description="Total agent events processed",
        )
        self._blocked_counter = self._meter.create_counter(
            "panoptis.safety.blocked_total",
            description="Total actions blocked by safety engine",
        )
        self._token_counter = self._meter.create_counter(
            "panoptis.tokens.total",
            description="Total LLM tokens consumed",
        )
        self._session_duration = self._meter.create_histogram(
            "panoptis.session.duration_seconds",
            description="Agent session duration in seconds",
        )

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator:
        """Context manager to create a trace span."""
        if not self._tracer:
            yield None
            return

        with self._tracer.start_as_current_span(name) as span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, str(v))
            yield span

    def record_event(self, framework: str, event_type: str) -> None:
        if self._event_counter:
            self._event_counter.add(1, {"framework": framework, "event_type": event_type})

    def record_blocked(self, framework: str, risk_level: str) -> None:
        if self._blocked_counter:
            self._blocked_counter.add(1, {"framework": framework, "risk_level": risk_level})

    def record_tokens(self, count: int, framework: str) -> None:
        if self._token_counter:
            self._token_counter.add(count, {"framework": framework})

    def record_session_duration(self, duration_seconds: float, framework: str, status: str) -> None:
        if self._session_duration:
            self._session_duration.record(
                duration_seconds, {"framework": framework, "status": status}
            )

    def export_reasoning_trace(self, trace_data: dict[str, Any]) -> bool:
        """Export a finalized ReasoningTrace dict to OpenTelemetry."""
        if not self._initialized or not _OTEL_AVAILABLE or not self._tracer:
            return False

        import hashlib
        import uuid
        from datetime import datetime

        from opentelemetry.context import Context
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
            set_span_in_context,
        )

        def _safe_int_from_id(id_str: str | None, bits: int) -> int | None:
            if not id_str:
                return None
            try:
                return uuid.UUID(id_str).int & ((1 << bits) - 1)
            except ValueError:
                # Deterministic fallback ID generation for malformed identifiers.
                # Not used for security.
                h = hashlib.blake2b(
                    id_str.encode("utf-8"),
                    digest_size=16 if bits == 128 else 8,
                    usedforsecurity=False,
                ).digest()
                return int.from_bytes(h, byteorder="big") & ((1 << bits) - 1)

        def _parse_time(time_str: str | None) -> int | None:
            if not time_str:
                return None
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                return int(dt.timestamp() * 1e9)
            except Exception:
                try:
                    return int(float(time_str) * 1e9)
                except Exception:
                    return None

        agent = trace_data.get("agent", {})
        base_attrs = {
            "agent.id": agent.get("id", ""),
            "agent.name": agent.get("name", ""),
            "agent.framework": agent.get("framework", ""),
            "agent.model": agent.get("model", ""),
        }
        base_attrs = {k: str(v) for k, v in base_attrs.items() if v}

        trace_id_int = _safe_int_from_id(trace_data.get("trace_id"), 128)
        if not trace_id_int:
            return False

        trace_key = str(trace_data.get("trace_id"))
        if trace_key in self._emitted_trace_ids:
            return self._flush_exported_spans()

        # Create a fake root context to enforce trace_id
        dummy_span_id = _safe_int_from_id(trace_data.get("trace_id", "root"), 64) or 1
        root_span_context = SpanContext(
            trace_id=trace_id_int,
            span_id=dummy_span_id,
            is_remote=True,
            trace_flags=TraceFlags.SAMPLED,
        )
        base_context = set_span_in_context(NonRecordingSpan(root_span_context), Context())

        otel_contexts = {}  # span_id -> Context

        # Sort spans by start_time to ensure parents are processed first
        spans = sorted(trace_data.get("spans", []), key=lambda s: s.get("start_time") or "")

        for span_data in spans:
            aw_span_id = str(span_data.get("span_id", ""))
            parent_aw_id = str(span_data.get("parent_span_id", ""))

            parent_context = otel_contexts.get(parent_aw_id, base_context)
            start_time_ns = _parse_time(span_data.get("start_time")) or int(time.time() * 1e9)
            end_time_ns = _parse_time(span_data.get("end_time")) or start_time_ns

            span = self._tracer.start_span(
                name=str(span_data.get("name", "span")),
                context=parent_context,
                start_time=start_time_ns,
            )

            # Attributes
            for k, v in base_attrs.items():
                span.set_attribute(k, v)

            span.set_attribute("panoptis.span_id", aw_span_id)
            if parent_aw_id:
                span.set_attribute("panoptis.parent_span_id", parent_aw_id)

            kind = span_data.get("kind")
            if kind:
                span.set_attribute("panoptis.kind", str(kind))

            token_count = span_data.get("token_count")
            if token_count is not None:
                span.set_attribute("panoptis.token_count", int(token_count))
                self.record_tokens(int(token_count), str(agent.get("framework", "custom")))

            # Extra attributes
            attrs = span_data.get("attributes", {})
            if isinstance(attrs, dict):
                for k, v in attrs.items():
                    if isinstance(v, (str, int, float, bool)):
                        span.set_attribute(str(k), v)

            span.end(end_time=end_time_ns)
            otel_contexts[aw_span_id] = set_span_in_context(span, Context())

        self._emitted_trace_ids.add(trace_key)
        return self._flush_exported_spans()


# Compatibility alias for OBS tests
OTELExporter = TelemetryProvider

# Singleton
_provider: TelemetryProvider | None = None


def get_telemetry() -> TelemetryProvider:
    global _provider
    if _provider is None:
        _provider = TelemetryProvider()
    return _provider


def init_telemetry(config: TelemetryConfig) -> TelemetryProvider:
    global _provider
    _provider = TelemetryProvider(config)
    _provider.initialize()
    return _provider


# ─────────────────────────────────────────────
# OpenInference -> OTel GenAI bridge
# ─────────────────────────────────────────────
# Pure Python: needs neither the opentelemetry SDK nor any OpenInference package.

SPAN_KIND_ATTR = "openinference.span.kind"
AW_SPAN_KIND_ATTR = "panoptis.span_kind"

# OpenInference span kind -> (PANOPTIS event kind, gen_ai.operation.name or None).
# Kinds with no GenAI operation (CHAIN, RETRIEVER) are carried by "panoptis.span_kind".
OPENINFERENCE_SPAN_KIND_MAP: dict[str, tuple[str, str | None]] = {
    "LLM": ("MODEL_INVOCATION", "chat"),
    "TOOL": ("TOOL_INVOCATION", "execute_tool"),
    "AGENT": ("OPERATION", "invoke_agent"),
    "CHAIN": ("OPERATION", None),
    "RETRIEVER": ("RETRIEVAL", None),
}

# OpenInference attribute -> official GenAI attribute (one-to-one aliases).
OPENINFERENCE_ATTRIBUTE_MAP: dict[str, str] = {
    "llm.model_name": "gen_ai.request.model",
    "llm.provider": "gen_ai.provider.name",
    "llm.system": "gen_ai.system",
    "llm.token_count.prompt": "gen_ai.usage.input_tokens",
    "llm.token_count.completion": "gen_ai.usage.output_tokens",
    "llm.token_count.total": "gen_ai.usage.total_tokens",
    "tool.name": "gen_ai.tool.name",
    "agent.name": "gen_ai.agent.name",
}

_TOKEN_TARGETS = frozenset(
    {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens"}
)
_MESSAGE_PREFIXES = {
    "llm.input_messages": "gen_ai.input.messages",
    "llm.output_messages": "gen_ai.output.messages",
}
# Keys in these namespaces belong to OpenInference; consumed or not, they never stay top-level.
_OI_PREFIXES = (
    "openinference.",
    "llm.",
    "tool.",
    "tool_call.",
    "agent.",
    "retrieval.",
    "embedding.",
    "reranker.",
    "input.",
    "output.",
    "session.",
    "user.",
    "metadata",
)


def _as_token_count(value: Any) -> int | None:
    """Return an int for a numeric token count (int, whole float, digit string), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _json_or_raw(value: Any) -> Any:
    """Parse a JSON-encoded list/object string; any other value is returned as is."""
    if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _flattened_messages(oi: dict[str, Any], prefix: str) -> tuple[list[dict[str, Any]], set[str]]:
    """Collect ``<prefix>.<i>.message.<field>`` keys into a list of message dicts."""
    by_index: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    head = prefix + "."
    for key, value in oi.items():
        if not key.startswith(head):
            continue
        index, _, rest = key[len(head) :].partition(".")
        if index.isdigit() and rest.startswith("message."):
            by_index.setdefault(int(index), {})[rest[len("message.") :]] = value
            used.add(key)
    return [by_index[i] for i in sorted(by_index)], used


def normalize_otel_span(attributes: dict[str, Any]) -> dict[str, Any]:
    """Normalize span attributes from OpenInference and/or OTel GenAI conventions.

    Returns a new flat dict; ``attributes`` is never modified. The result holds:

    * every attribute outside the OpenInference namespaces, unchanged (``gen_ai.*``,
      ``db.*``, ``http.*`` and vendor keys pass through);
    * OpenInference values translated to ``gen_ai.*`` names (``OPENINFERENCE_ATTRIBUTE_MAP``),
      ``gen_ai.input.messages`` / ``gen_ai.output.messages`` lists (from a list, a JSON string
      or flattened ``llm.input_messages.N.message.*`` keys), ``gen_ai.operation.name`` and
      ``panoptis.span_kind`` derived from ``openinference.span.kind``;
    * ``extra``: OpenInference attributes with no mapping, and OpenInference values that lost
      to an official value, under their original keys.

    Precedence: when both conventions give the same information, the official ``gen_ai.*``
    value wins and the OpenInference value is kept in ``extra``. ``None`` counts as absent;
    ``0`` is a real value. Token counts given as digit strings become ints; a value that is
    not a number goes to ``extra``. An unknown span kind goes to ``extra`` and sets no kind.
    Running the function on its own output returns an equal dict.
    """
    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    oi: dict[str, Any] = {}
    for key, value in attributes.items():
        if key == "extra" and isinstance(value, dict):
            extra.update(value)  # output of an earlier call: keep what it kept
        elif key.startswith(_OI_PREFIXES) or key in ("input", "output"):
            if value is not None:
                oi[key] = value
        else:
            out[key] = value

    consumed: set[str] = set()

    def put(target: str, value: Any, source: str) -> None:
        """Set ``target`` from an OpenInference source unless an official value exists."""
        if value is None:
            return
        if out.get(target) is None:
            out[target] = value
            consumed.add(source)
        elif out[target] != value:
            extra[source] = oi[source]  # conflict: official value wins, original is kept
        else:
            consumed.add(source)

    for source, target in OPENINFERENCE_ATTRIBUTE_MAP.items():
        if source not in oi:
            continue
        value = oi[source]
        if target in _TOKEN_TARGETS:
            value = _as_token_count(value)  # None -> not a number, stays for "extra"
        put(target, value, source)

    for prefix, target in _MESSAGE_PREFIXES.items():
        messages = _json_or_raw(oi[prefix]) if prefix in oi else None
        if messages is None:
            messages, used = _flattened_messages(oi, prefix)
            if messages:
                consumed |= used
                if out.get(target) is None:
                    out[target] = messages
                elif out[target] != messages:
                    extra[prefix] = messages
        elif out.get(target) is None:
            out[target] = messages
            consumed.add(prefix)
        elif out[target] != messages:
            extra[prefix] = oi[prefix]
        else:
            consumed.add(prefix)

    kind = str(oi.get(SPAN_KIND_ATTR, "")).upper()
    if kind in OPENINFERENCE_SPAN_KIND_MAP:
        aw_kind, operation = OPENINFERENCE_SPAN_KIND_MAP[kind]
        out[AW_SPAN_KIND_ATTR] = aw_kind
        consumed.add(SPAN_KIND_ATTR)
        if operation:
            put("gen_ai.operation.name", operation, SPAN_KIND_ATTR)
        # input.value / output.value are the span payload; what they mean depends on the kind
        if kind == "TOOL":
            put("gen_ai.tool.call.arguments", _json_or_raw(oi.get("input.value")), "input.value")
            put("gen_ai.tool.call.result", _json_or_raw(oi.get("output.value")), "output.value")
        elif kind == "LLM":
            put("gen_ai.prompt", oi.get("input.value"), "input.value")
            put("gen_ai.completion", oi.get("output.value"), "output.value")

    for key, value in oi.items():
        if key not in consumed:
            extra.setdefault(key, value)
    if extra:
        out["extra"] = extra
    return out
