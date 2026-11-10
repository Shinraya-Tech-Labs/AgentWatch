"""Targeted regression test suite for OTel GenAI Semantic Conventions v2 and MCP normalizers.

Verifies CNCF gen_ai.* attributes, tool call spans, token usage,
interval temporal info, MCP protocol tools/call, resources/read, prompts/get,
JSON-RPC errors, tool execution errors, and DeclaredLink provenance.
"""

from __future__ import annotations

from panoptis.contracts.order import HappensBeforeGraph
from panoptis.events.model import EventKind, EventStatus
from panoptis.sensors.base import ListSink
from panoptis.sensors.mcp import MCPTap
from panoptis.sensors.otel import drafts_from_spans, spans_from_otlp_json


def events_of(engine, drafts):
    engine.ingest(drafts)
    engine.process()
    iid = engine.interp_id_for("default")
    return engine.store.events(iid), engine.store.diagnostics(iid), iid


def _val(v):
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, list):
        return {"arrayValue": {"values": [_val(x) for x in v]}}
    if isinstance(v, dict):
        return {"kvlistValue": {"values": [{"key": k, "value": _val(x)} for k, x in v.items()]}}
    return {"stringValue": str(v)}


def _make_otlp(spans: list[dict], service_name: str = "genai-service") -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]
                },
                "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}],
            }
        ]
    }


def test_otel_genai_v2_attributes_full(engine):
    trace_id = "1" * 32
    span_id = "2" * 16
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "chat gpt-4o",
        "kind": 3,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000001500000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": _val("chat")},
            {"key": "gen_ai.system", "value": _val("openai")},
            {"key": "gen_ai.request.model", "value": _val("gpt-4o")},
            {"key": "gen_ai.request.temperature", "value": _val(0.7)},
            {"key": "gen_ai.request.top_p", "value": _val(0.95)},
            {"key": "gen_ai.request.max_tokens", "value": _val(4096)},
            {"key": "gen_ai.request.stop_sequences", "value": _val(["\n", "User:"])},
            {"key": "gen_ai.request.presence_penalty", "value": _val(0.5)},
            {"key": "gen_ai.request.frequency_penalty", "value": _val(0.2)},
            {"key": "gen_ai.request.seed", "value": _val(42)},
            {"key": "gen_ai.response.id", "value": _val("chatcmpl-test-123")},
            {"key": "gen_ai.response.model", "value": _val("gpt-4o-2024-08-06")},
            {"key": "gen_ai.response.finish_reasons", "value": _val(["stop"])},
            {"key": "gen_ai.usage.input_tokens", "value": _val(150)},
            {"key": "gen_ai.usage.output_tokens", "value": _val(80)},
            {"key": "gen_ai.usage.total_tokens", "value": _val(230)},
            {"key": "gen_ai.usage.cache_read_input_tokens", "value": _val(50)},
            {"key": "gen_ai.prompt", "value": _val("Explain quantum computing")},
            {"key": "gen_ai.completion", "value": _val("Quantum computing harnesses...")},
        ],
        "status": {"code": 1},
    }
    otlp = _make_otlp([span])
    evs, diags, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    assert len(evs) == 1
    ev = evs[0]

    assert ev["kind"] == EventKind.MODEL_INVOCATION.value
    assert ev["operation"] == "chat:gpt-4o"
    assert ev["object"] == "model:openai/gpt-4o"
    assert ev["status"] == EventStatus.OK.value

    # Attributes recovery
    attrs = ev["attributes"]
    assert attrs.get("gen_ai.request.temperature") == 0.7
    assert attrs.get("gen_ai.request.top_p") == 0.95
    assert attrs.get("gen_ai.request.max_tokens") == 4096
    assert attrs.get("gen_ai.request.stop_sequences") == ["\n", "User:"]
    assert attrs.get("gen_ai.request.presence_penalty") == 0.5
    assert attrs.get("gen_ai.request.frequency_penalty") == 0.2
    assert attrs.get("gen_ai.request.seed") == 42
    assert attrs.get("gen_ai.response.id") == "chatcmpl-test-123"
    assert attrs.get("gen_ai.response.model") == "gpt-4o-2024-08-06"
    assert attrs.get("gen_ai.response.finish_reasons") == ["stop"]

    # Usage recovery
    res = ev["resources"]
    assert res.get("tokens_in") == 150
    assert res.get("tokens_out") == 80
    assert res.get("tokens_total") == 230
    assert res.get("cache_read_tokens") == 50
    assert res.get("latency_ms") == 1500.0

    # Temporal info
    assert ev["time"]["basis"] == "source"
    assert ev["time"]["start"] is not None
    assert ev["time"]["end"] is not None

    # Inputs and outputs
    assert len(ev["inputs"]) == 1 and ev["inputs"][0]["role"] == "prompt"
    assert len(ev["outputs"]) == 1 and ev["outputs"][0]["role"] == "completion"


def test_otel_genai_v2_tool_call_span(engine):
    trace_id = "2" * 32
    span_id = "3" * 16
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "execute_tool get_weather",
        "kind": 3,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000250000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": _val("execute_tool")},
            {"key": "gen_ai.tool.name", "value": _val("get_weather")},
            {"key": "gen_ai.tool.call.id", "value": _val("call_12345")},
            {
                "key": "gen_ai.tool.call.arguments",
                "value": _val({"location": "San Francisco, CA", "unit": "fahrenheit"}),
            },
            {
                "key": "gen_ai.tool.call.result",
                "value": _val({"temperature": 68, "conditions": "sunny"}),
            },
        ],
        "status": {"code": 1},
    }
    otlp = _make_otlp([span])
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    assert len(evs) == 1
    ev = evs[0]

    assert ev["kind"] == EventKind.TOOL_INVOCATION.value
    assert ev["operation"] == "get_weather"
    assert ev["object"] == "tool:get_weather"
    assert ev["attributes"]["gen_ai.tool.call.id"] == "call_12345"

    assert len(ev["inputs"]) == 1 and ev["inputs"][0]["role"] == "arguments"
    assert len(ev["outputs"]) == 1 and ev["outputs"][0]["role"] == "result"
    assert ev["inputs"][0]["artifact_id"] != ""
    assert ev["outputs"][0]["artifact_id"] != ""


def test_otel_genai_v2_span_events_choice_and_finish_reason(engine):
    trace_id = "3" * 32
    span_id = "4" * 16
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "generate_content",
        "kind": 3,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000001000000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": _val("generate_content")},
            {"key": "gen_ai.request.model", "value": _val("gemini-4-argon")},
        ],
        "events": [
            {
                "name": "gen_ai.content.prompt",
                "timeUnixNano": "1700000000100000000",
                "attributes": [{"key": "content", "value": _val("Plan a 3-day itinerary")}],
            },
            {
                "name": "gen_ai.choice",
                "timeUnixNano": "1700000000900000000",
                "attributes": [
                    {"key": "content", "value": _val("Day 1: Arrival...")},
                    {"key": "finish_reason", "value": _val("stop")},
                ],
            },
        ],
        "status": {"code": 1},
    }
    otlp = _make_otlp([span])
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    assert len(evs) == 1
    ev = evs[0]

    assert ev["kind"] == EventKind.MODEL_INVOCATION.value
    assert ev["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    assert len(ev["inputs"]) == 1 and ev["inputs"][0]["role"] == "prompt"
    assert len(ev["outputs"]) == 1 and ev["outputs"][0]["role"] == "completion"


def test_otel_genai_v2_error_and_exception_event(engine):
    trace_id = "4" * 32
    span_id = "5" * 16
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "chat gpt-4o",
        "kind": 3,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000500000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": _val("chat")},
            {"key": "gen_ai.request.model", "value": _val("gpt-4o")},
        ],
        "events": [
            {
                "name": "exception",
                "timeUnixNano": "1700000000450000000",
                "attributes": [
                    {"key": "exception.type", "value": _val("RateLimitError")},
                    {"key": "exception.message", "value": _val("429 Too Many Requests")},
                ],
            }
        ],
        "status": {"code": 2, "message": "rate limit error"},
    }
    otlp = _make_otlp([span])
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    assert len(evs) == 1
    ev = evs[0]

    assert ev["status"] == EventStatus.ERROR.value
    assert ev["error"]["type"] == "RateLimitError"
    assert ev["error"]["message"] == "429 Too Many Requests"


def test_mcp_tools_call_success_artifacts_and_provenance(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="filesystem", session_id="sess-fs-1")
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "/etc/hosts"}},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {
                "content": [{"type": "text", "text": "127.0.0.1 localhost"}],
                "isError": False,
            },
        },
    )
    evs, diags, _ = events_of(engine, sink.drafts)
    assert not [d for d in diags if d["level"] == "error"]
    assert len(evs) == 1
    ev = evs[0]

    assert ev["kind"] == EventKind.TOOL_INVOCATION.value
    assert ev["operation"] == "read_file"
    assert ev["object"] == "tool:mcp/filesystem/read_file"
    assert ev["status"] == EventStatus.OK.value

    # Artifacts
    assert len(ev["inputs"]) == 1
    assert ev["inputs"][0]["role"] == "arguments"
    assert ev["inputs"][0]["label"] == "read_file"
    assert ev["inputs"][0]["artifact_id"] != ""

    assert len(ev["outputs"]) == 1
    assert ev["outputs"][0]["role"] == "result"
    assert ev["outputs"][0]["artifact_id"] != ""

    # Provenance & graph acyclicity
    assert [tuple(s) for s in ev["source_ids"]] == [("mcp.call", "sess-fs-1/42")]
    obs = engine.store.observations("default")
    iid = engine.interp_id_for("default")
    built = engine.build("default", iid, obs)
    g = HappensBeforeGraph(built["events"])
    assert not g.has_cycle()
    assert g.cycle_nodes == ()


def test_mcp_tool_execution_error(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="db_server", session_id="sess-db")
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {"name": "execute_query", "arguments": {"sql": "SELECT * FROM missing_tbl"}},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": 101,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Table missing_tbl does not exist"}],
            },
        },
    )
    evs, _, _ = events_of(engine, sink.drafts)
    assert len(evs) == 1
    ev = evs[0]

    assert ev["status"] == EventStatus.ERROR.value
    assert ev["error"]["type"] == "ToolExecutionError"
    assert "missing_tbl does not exist" in ev["error"]["message"]


def test_mcp_jsonrpc_protocol_error(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="api_server", session_id="sess-api")
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": 999,
            "method": "unknown_endpoint",
            "params": {},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": 999,
            "error": {
                "code": -32601,
                "message": "Method not found",
                "data": {"available": ["tools/call", "resources/read"]},
            },
        },
    )
    evs, _, _ = events_of(engine, sink.drafts)
    assert len(evs) == 1
    ev = evs[0]

    assert ev["status"] == EventStatus.ERROR.value
    assert ev["error"]["type"] == "JSONRPCError"
    assert ev["error"]["code"] == -32601
    assert ev["error"]["message"] == "Method not found"
    assert ev["error"]["data"] == {"available": ["tools/call", "resources/read"]}


def test_mcp_resources_read_and_prompts_get(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="content_server", session_id="sess-content")

    # resources/read
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": "r1",
            "method": "resources/read",
            "params": {"uri": "file:///project/README.md"},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": "r1",
            "result": {"contents": [{"uri": "file:///project/README.md", "text": "# Hello"}]},
        },
    )

    # prompts/get
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": "p1",
            "method": "prompts/get",
            "params": {"name": "summarizer", "arguments": {"length": "short"}},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": "p1",
            "result": {
                "description": "Summarize text",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": "Please summarize"}}
                ],
            },
        },
    )

    evs, _, _ = events_of(engine, sink.drafts)
    assert len(evs) == 2

    # Verify resources/read
    res_ev = next(e for e in evs if e["operation"] == "resources/read")
    assert res_ev["kind"] == EventKind.RETRIEVAL.value
    assert res_ev["object"] == "mcp_resource:mcp/content_server/file:///project/README.md"
    assert len(res_ev["outputs"]) == 1 and res_ev["outputs"][0]["role"] == "resource"
    assert res_ev["status"] == EventStatus.OK.value

    # Verify prompts/get
    prompt_ev = next(e for e in evs if e["operation"] == "prompts/get")
    assert prompt_ev["kind"] == EventKind.RETRIEVAL.value
    assert prompt_ev["object"] == "mcp_prompt:mcp/content_server/summarizer"
    assert len(prompt_ev["outputs"]) == 1 and prompt_ev["outputs"][0]["role"] == "prompt"
    assert prompt_ev["status"] == EventStatus.OK.value


def test_mcp_unpaired_request_emits_diagnostic(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="slow_server", session_id="sess-slow")
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": "unpaired-100",
            "method": "tools/call",
            "params": {"name": "long_task", "arguments": {}},
        },
    )
    evs, diags, _ = events_of(engine, sink.drafts)
    assert len(evs) == 1
    assert evs[0]["status"] == EventStatus.UNKNOWN.value
    assert any(d["code"] == "no_response" for d in diags)


def test_mcp_notification_message(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="notify_server", session_id="sess-notify")
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "method": "notifications/resources/updated",
            "params": {"uri": "file:///data/cache.json"},
        },
    )
    evs, _, _ = events_of(engine, sink.drafts)
    assert len(evs) == 1
    ev = evs[0]

    assert ev["kind"] == EventKind.MESSAGE.value
    assert ev["operation"] == "notification:notifications/resources/updated"
    assert ev["object"] == "mcp_server:notify_server"
    assert ev["status"] == EventStatus.OK.value


def test_otel_genai_v2_falsy_values_and_zero_metrics(engine):
    trace_id = "5" * 32
    span_id = "6" * 16
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "execute_tool zero_args",
        "kind": 3,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000100000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": _val("execute_tool")},
            {"key": "gen_ai.tool.name", "value": _val("get_time")},
            {"key": "gen_ai.tool.call.arguments", "value": _val({})},
            {"key": "gen_ai.tool.call.result", "value": _val(0)},
            {"key": "gen_ai.usage.input_tokens", "value": _val(0)},
            {"key": "gen_ai.usage.output_tokens", "value": _val(10)},
            {"key": "gen_ai.usage.cache_read_input_tokens", "value": _val(0)},
        ],
        "status": {"code": 1},
    }
    otlp = _make_otlp([span])
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    assert len(evs) == 1
    ev = evs[0]

    # Verify input argument artifact is preserved even when empty dict
    assert len(ev["inputs"]) == 1
    assert ev["inputs"][0]["role"] == "arguments"

    # Verify output result artifact is preserved even when 0
    assert len(ev["outputs"]) == 1
    assert ev["outputs"][0]["role"] == "result"

    # Verify zero-valued metrics are not dropped
    res = ev["resources"]
    assert res.get("tokens_in") == 0
    assert res.get("tokens_out") == 10
    assert res.get("tokens_total") == 10
    assert res.get("cache_read_tokens") == 0


def test_otel_genai_v2_multiple_choices_finish_reasons(engine):
    trace_id = "6" * 32
    span_id = "7" * 16
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "generate_content multi_choice",
        "kind": 3,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000001000000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": _val("generate_content")},
            {"key": "gen_ai.request.model", "value": _val("gpt-4o")},
        ],
        "events": [
            {
                "name": "gen_ai.choice",
                "timeUnixNano": "1700000000500000000",
                "attributes": [
                    {"key": "content", "value": _val("Option A")},
                    {"key": "finish_reason", "value": _val("stop")},
                ],
            },
            {
                "name": "gen_ai.choice",
                "timeUnixNano": "1700000000900000000",
                "attributes": [
                    {"key": "content", "value": _val("Option B")},
                    {"key": "finish_reason", "value": _val("length")},
                ],
            },
        ],
        "status": {"code": 1},
    }
    otlp = _make_otlp([span])
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    assert len(evs) == 1
    ev = evs[0]

    assert ev["attributes"]["gen_ai.response.finish_reasons"] == ["stop", "length"]
    assert len(ev["outputs"]) == 2


def test_mcp_sequential_jsonrpc_id_reuse(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="filesystem", session_id="sess-reuse")

    # Call 1 with ID 1
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "/a.txt"}},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "file a"}], "isError": False},
        },
    )

    # Call 2 sequentially reusing ID 1
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "/b.txt"}},
        },
    )
    tap.record(
        "server->client",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
        },
    )

    evs, diags, iid = events_of(engine, sink.drafts)
    assert not [d for d in diags if d["level"] == "error"]
    assert len(evs) == 2

    # Verify both calls were preserved
    assert evs[0]["operation"] == "read_file"
    assert evs[1]["operation"] == "write_file"
    assert evs[0]["event_id"] != evs[1]["event_id"]

    # Verify DAG acyclicity across sequential ID reuse
    obs = engine.store.observations("default")
    built = engine.build("default", iid, obs)
    g = HappensBeforeGraph(built["events"])
    assert not g.has_cycle()


def _oi_span(span_id: str, name: str, attrs: dict) -> dict:
    return {
        "traceId": "9" * 32,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000001000000000",
        "attributes": [{"key": k, "value": _val(v)} for k, v in attrs.items()],
        "status": {"code": 1},
    }


def test_openinference_llm_span_becomes_model_invocation(engine):
    span = _oi_span(
        "a" * 16,
        "ChatCompletion",
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "gpt-4o",
            "llm.provider": "openai",
            "llm.token_count.prompt": 11,
            "llm.token_count.completion": 4,
            "input.value": "ping",
            "output.value": "pong",
        },
    )
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(_make_otlp([span]))))
    ev = evs[0]
    assert ev["kind"] == EventKind.MODEL_INVOCATION.value
    assert ev["operation"] == "chat:gpt-4o"
    assert ev["resources"]["tokens_in"] == 11
    assert ev["resources"]["tokens_total"] == 15
    assert ev["inputs"][0]["role"] == "prompt"
    assert ev["outputs"][0]["role"] == "completion"
    # the original attributes stay visible, untranslated
    assert ev["attributes"]["otel.attributes"]["llm.model_name"] == "gpt-4o"


def test_openinference_tool_span_becomes_tool_invocation(engine):
    span = _oi_span(
        "b" * 16,
        "lookup",
        {"openinference.span.kind": "TOOL", "tool.name": "lookup", "input.value": "{}"},
    )
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(_make_otlp([span]))))
    assert evs[0]["kind"] == EventKind.TOOL_INVOCATION.value
    assert evs[0]["operation"] == "lookup"


def test_openinference_retriever_span_becomes_retrieval(engine):
    span = _oi_span("c" * 16, "vector search", {"openinference.span.kind": "RETRIEVER"})
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(_make_otlp([span]))))
    assert evs[0]["kind"] == EventKind.RETRIEVAL.value


def test_openinference_chain_span_stays_operation(engine):
    span = _oi_span("d" * 16, "my chain", {"openinference.span.kind": "CHAIN"})
    evs, _, _ = events_of(engine, drafts_from_spans(spans_from_otlp_json(_make_otlp([span]))))
    assert evs[0]["kind"] == EventKind.OPERATION.value
