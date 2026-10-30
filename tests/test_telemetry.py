"""Tests for the telemetry enhancements (buffering, idempotency, legacy config)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from panoptis.telemetry.otel import _OTEL_AVAILABLE, TelemetryConfig, TelemetryProvider


def test_telemetry_config_backward_compatibility():
    # Test that both new and legacy parameters work
    config = TelemetryConfig(
        endpoint="http://legacy:4317", insecure=True, headers={"x-test": "val"}
    )
    assert config.otlp_endpoint == "http://legacy:4317"
    assert config.endpoint == "http://legacy:4317"
    assert config.insecure is True
    assert config.headers == {"x-test": "val"}


def test_telemetry_provider_buffering():
    provider = TelemetryProvider()
    mock_span = MagicMock()
    mock_span.name = "test-span"

    # Export before initialization should buffer
    provider.export(mock_span)
    assert len(provider._buffer) == 1
    assert provider._buffer[0] == mock_span


def test_telemetry_provider_buffer_overflow():
    provider = TelemetryProvider()
    provider._max_buffer_size = 5

    spans = []
    for i in range(10):
        s = MagicMock()
        s.name = f"span-{i}"
        spans.append(s)
        provider.export(s)

    assert len(provider._buffer) == 5
    # Should contain the LAST 5 spans (dropped oldest)
    assert provider._buffer[0].name == "span-5"
    assert provider._buffer[-1].name == "span-9"


def test_telemetry_initialization_idempotency():
    provider = TelemetryProvider()
    provider.initialize()
    assert provider._initialized is True

    # Second call should not raise or re-initialize
    provider.initialize()
    assert provider._initialized is True


def test_telemetry_flush_on_initialize():
    provider = TelemetryProvider()
    mock_span = MagicMock()
    mock_span.name = "flush-me"
    provider.export(mock_span)

    # Mock exporter to verify flush
    mock_exporter = MagicMock()
    provider._exporter = mock_exporter

    provider.initialize()
    assert len(provider._buffer) == 0
    # exporter.export should have been called (via provider.export in flush)
    assert mock_exporter.export.called


def test_telemetry_record_methods():
    provider = TelemetryProvider()
    provider.initialize()

    # These should not raise even if OTel is not fully active
    provider.record_event("framework", "event")
    provider.record_blocked("framework", "high")
    provider.record_tokens(100, "framework")
    provider.record_session_duration(1.5, "framework", "success")


def test_export_retries_until_success():
    provider = TelemetryProvider()

    exporter = MagicMock()
    exporter.export.side_effect = [
        Exception("network"),
        Exception("network"),
        None,
    ]

    provider._initialized = True
    provider._exporter = exporter

    span = MagicMock()
    provider.export(span)

    assert exporter.export.call_count == 3


def test_export_gives_up_after_max_retries():
    provider = TelemetryProvider()

    exporter = MagicMock()
    exporter.export.side_effect = Exception("network")

    provider._initialized = True
    provider._exporter = exporter

    span = MagicMock()
    provider.export(span)

    assert exporter.export.call_count == 3


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_success():
    provider = TelemetryProvider()
    provider.initialize()

    # Mock tracer to intercept start_span
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_span.return_value = mock_span
    provider._tracer = mock_tracer

    trace_data = {
        "trace_id": "123e4567-e89b-12d3-a456-426614174000",
        "agent": {"framework": "langchain"},
        "spans": [
            {
                "span_id": "123e4567-e89b-12d3-a456-426614174001",
                "parent_span_id": None,
                "name": "root_span",
                "start_time": "2023-01-01T12:00:00Z",
                "end_time": "2023-01-01T12:00:01Z",
                "kind": "agent",
                "token_count": 100,
                "attributes": {"custom": "val"},
            }
        ],
    }

    provider.export_reasoning_trace(trace_data)

    # Assert span was created and ended
    assert mock_tracer.start_span.call_count == 1
    assert mock_span.end.call_count == 1

    # Assert attributes were set
    mock_span.set_attribute.assert_any_call(
        "panoptis.span_id", "123e4567-e89b-12d3-a456-426614174001"
    )
    mock_span.set_attribute.assert_any_call("panoptis.token_count", 100)
    mock_span.set_attribute.assert_any_call("custom", "val")


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_hierarchy():
    provider = TelemetryProvider()
    provider.initialize()

    mock_tracer = MagicMock()
    provider._tracer = mock_tracer

    trace_data = {
        "trace_id": "trace-uuid",
        "spans": [
            {
                "span_id": "parent-uuid",
                "start_time": "2023-01-01T12:00:00Z",
            },
            {
                "span_id": "child-uuid",
                "parent_span_id": "parent-uuid",
                "start_time": "2023-01-01T12:00:01Z",
            },
        ],
    }

    provider.export_reasoning_trace(trace_data)

    assert mock_tracer.start_span.call_count == 2
    # Verify both spans end
    # Because spans are created dynamically and we mock start_span
    # we just need to ensure the call count is 2 and no exceptions occurred


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_malformed_ids():
    provider = TelemetryProvider()
    provider.initialize()

    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_span.return_value = mock_span
    provider._tracer = mock_tracer

    trace_data = {
        "trace_id": "not-a-uuid",
        "spans": [
            {
                "span_id": "also-not-a-uuid",
                "start_time": "2023-01-01T12:00:00Z",
            }
        ],
    }

    provider.export_reasoning_trace(trace_data)
    assert mock_tracer.start_span.call_count == 1
    mock_span.set_attribute.assert_any_call("panoptis.span_id", "also-not-a-uuid")


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_missing_end_time():
    provider = TelemetryProvider()
    provider.initialize()

    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_span.return_value = mock_span
    provider._tracer = mock_tracer

    trace_data = {
        "trace_id": "uuid",
        "spans": [
            {
                "span_id": "uuid",
                "start_time": "2023-01-01T12:00:00Z",
                # no end_time
            }
        ],
    }

    provider.export_reasoning_trace(trace_data)
    # the end method must have been called with start_time equivalent
    assert mock_span.end.call_count == 1


def test_export_reasoning_trace_otel_unavailable():
    provider = TelemetryProvider()
    provider._initialized = False  # simulates unavailable

    trace_data = {"trace_id": "uuid", "spans": [{"span_id": "uuid"}]}

    # Should not raise exception and return False
    assert provider.export_reasoning_trace(trace_data) is False


def test_export_reasoning_trace_collector_isolation():
    import asyncio

    from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus
    from panoptis.telemetry.collector import TraceCollector

    collector = TraceCollector()

    # Mock global telemetry provider to throw
    import panoptis.telemetry.otel

    original = panoptis.telemetry.otel._provider
    mock_provider = MagicMock()
    mock_provider.export_reasoning_trace.side_effect = Exception("OTel is down")
    panoptis.telemetry.otel._provider = mock_provider

    event = AgentEvent(
        session_id="session-1",
        agent_id="agent-1",
        event_type=EventType.SESSION_END,
        status=ExecutionStatus.SUCCESS,
        step_number=1,
    )

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(collector.ingest(event))

        # Verify trace is NOT marked exported
        trace = collector.get_trace("session-1")
        assert trace.is_exported is False
    finally:
        panoptis.telemetry.otel._provider = original


def test_export_reasoning_trace_collector_success():
    import asyncio

    from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus
    from panoptis.telemetry.collector import TraceCollector

    collector = TraceCollector()

    import panoptis.telemetry.otel

    original = panoptis.telemetry.otel._provider
    mock_provider = MagicMock()
    mock_provider.export_reasoning_trace.return_value = True
    panoptis.telemetry.otel._provider = mock_provider

    event = AgentEvent(
        session_id="session-success",
        agent_id="agent-1",
        event_type=EventType.SESSION_END,
        status=ExecutionStatus.SUCCESS,
        step_number=1,
    )

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(collector.ingest(event))

        # Verify trace IS marked exported
        trace = collector.get_trace("session-success")
        assert trace.is_exported is True
    finally:
        panoptis.telemetry.otel._provider = original


def _single_span_trace_data():
    return {
        "trace_id": "123e4567-e89b-12d3-a456-426614174000",
        "agent": {"framework": "langchain"},
        "spans": [
            {
                "span_id": "123e4567-e89b-12d3-a456-426614174001",
                "parent_span_id": None,
                "name": "root_span",
                "start_time": "2023-01-01T12:00:00Z",
                "end_time": "2023-01-01T12:00:01Z",
            }
        ],
    }


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_returns_true_only_after_flush():
    # A successful export must force-flush the span pipeline and report success.
    provider = TelemetryProvider()
    provider._initialized = True
    provider._tracer = MagicMock()
    provider._exporter = MagicMock()
    provider._tracer_provider = MagicMock()
    provider._tracer_provider.force_flush.return_value = True

    assert provider.export_reasoning_trace(_single_span_trace_data()) is True
    provider._tracer_provider.force_flush.assert_called_once()


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_exporter_unavailable_returns_false():
    # Without an active span pipeline the export cannot be confirmed, so the
    # trace must not be reported as exported (keeps it eligible for retry).
    provider = TelemetryProvider()
    provider._initialized = True
    provider._tracer = MagicMock()
    provider._exporter = MagicMock()
    provider._tracer_provider = None

    assert provider.export_reasoning_trace(_single_span_trace_data()) is False


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_failed_flush_returns_false():
    # force_flush returning False means the backend never acknowledged the spans.
    provider = TelemetryProvider()
    provider._initialized = True
    provider._tracer = MagicMock()
    provider._exporter = MagicMock()
    provider._tracer_provider = MagicMock()
    provider._tracer_provider.force_flush.return_value = False

    assert provider.export_reasoning_trace(_single_span_trace_data()) is False


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_flush_exception_returns_false():
    # An exporter that raises during flush must not be treated as a success.
    provider = TelemetryProvider()
    provider._initialized = True
    provider._tracer = MagicMock()
    provider._exporter = MagicMock()
    provider._tracer_provider = MagicMock()
    provider._tracer_provider.force_flush.side_effect = Exception("exporter down")

    assert provider.export_reasoning_trace(_single_span_trace_data()) is False


def test_collector_retries_export_after_failure():
    # A failed export leaves is_exported False; the next terminal event retries
    # and marks the trace exported once the export succeeds.
    import asyncio

    from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus
    from panoptis.telemetry.collector import TraceCollector

    collector = TraceCollector()

    import panoptis.telemetry.otel

    original = panoptis.telemetry.otel._provider
    mock_provider = MagicMock()
    mock_provider.export_reasoning_trace.side_effect = [False, True]
    panoptis.telemetry.otel._provider = mock_provider

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        first = AgentEvent(
            session_id="retry-session",
            agent_id="agent-1",
            event_type=EventType.SESSION_END,
            status=ExecutionStatus.SUCCESS,
            step_number=1,
        )
        loop.run_until_complete(collector.ingest(first))
        trace = collector.get_trace("retry-session")
        assert trace.is_exported is False

        second = AgentEvent(
            session_id="retry-session",
            agent_id="agent-1",
            event_type=EventType.SESSION_END,
            status=ExecutionStatus.SUCCESS,
            step_number=2,
        )
        loop.run_until_complete(collector.ingest(second))
        assert trace.is_exported is True
        assert mock_provider.export_reasoning_trace.call_count == 2
    finally:
        panoptis.telemetry.otel._provider = original


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_no_exporter_returns_false():
    # force_flush can succeed even with no exporter configured; that is not a
    # real export, so success must not be reported.
    provider = TelemetryProvider()
    provider._initialized = True
    provider._tracer = MagicMock()
    provider._exporter = None
    provider._tracer_provider = MagicMock()
    provider._tracer_provider.force_flush.return_value = True

    assert provider.export_reasoning_trace(_single_span_trace_data()) is False


@pytest.mark.skipif(not _OTEL_AVAILABLE, reason="requires opentelemetry-sdk")
def test_export_reasoning_trace_is_idempotent_across_retries():
    # A retry after a failed flush must not re-emit spans or re-record token
    # metrics; it only re-attempts delivery of the already-queued spans.
    provider = TelemetryProvider()
    provider._initialized = True
    provider._tracer = MagicMock()
    provider._token_counter = MagicMock()
    provider._exporter = MagicMock()
    provider._tracer_provider = MagicMock()
    provider._tracer_provider.force_flush.side_effect = [False, True]

    trace_data = {
        "trace_id": "123e4567-e89b-12d3-a456-426614174000",
        "agent": {"framework": "langchain"},
        "spans": [
            {
                "span_id": "123e4567-e89b-12d3-a456-426614174001",
                "name": "root_span",
                "start_time": "2023-01-01T12:00:00Z",
                "end_time": "2023-01-01T12:00:01Z",
                "token_count": 100,
            }
        ],
    }

    assert provider.export_reasoning_trace(trace_data) is False
    assert provider.export_reasoning_trace(trace_data) is True

    assert provider._tracer.start_span.call_count == 1
    assert provider._token_counter.add.call_count == 1
    assert provider._tracer_provider.force_flush.call_count == 2
