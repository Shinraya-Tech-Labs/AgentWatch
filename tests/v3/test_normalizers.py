"""Normalization invariants across all sources."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from panoptis import instrument as aw
from panoptis.core.schema import AgentEvent, EventType, ToolCallData, ToolResultData
from panoptis.evidence.model import ObservationDraft, SensorRef
from panoptis.sensors.base import ListSink
from panoptis.sensors.claude_code import drafts_from_lines
from panoptis.sensors.langchain import AgentWatchLangChainSensor
from panoptis.sensors.legacy.translator import LegacyTranslator, TranslationStatus
from panoptis.sensors.llm_clients import instrument_anthropic, instrument_openai
from panoptis.sensors.mcp import MCPTap
from panoptis.sensors.otel import drafts_from_spans, spans_from_otlp_json


def events_of(engine, drafts):
    engine.ingest(drafts)
    engine.process()
    iid = engine.interp_id_for("default")
    return engine.store.events(iid), engine.store.diagnostics(iid), iid


# ── native ────────────────────────────────────────────────────────────────
def test_native_parents_multiparent_and_runs(engine, sink):
    with aw.run("r"):
        with aw.span("OPERATION", "a", actor="agent:x") as a:
            pass
        with aw.span("OPERATION", "b", actor="agent:y") as b:
            pass
        with aw.span("OPERATION", "join", actor="agent:z", links=[a, b]):
            pass
    evs, diags, iid = events_of(engine, sink.drafts)
    assert not [d for d in diags if d["level"] == "error"]
    join = next(e for e in evs if e["operation"] == "join")
    rels = [p["relation"] for p in join["parents"]]
    assert rels.count("depends_on") == 2 and "parent" in rels
    graph_rels = engine.store.relations(iid, all_runs=True, view="EXECUTION")
    deps = [
        r
        for r in graph_rels
        if r["type"] == "DEPENDS_ON" and r["head"] == [f"event:{join['event_id']}"]
    ]
    assert len(deps) == 2
    assert all(e["run_id"] for e in evs)


def test_unpaired_span_is_reported_not_dropped(engine):
    ref = SensorRef("native", "1", "n1")
    d = ObservationDraft(
        sensor=ref,
        source_kind="native.span.start",
        payload={"kind": "TOOL_INVOCATION", "operation": "t"},
        source_seq=1,
        observed_at=datetime.now(UTC),
        declared_ids={"span_id": "s1"},
    )
    evs, diags, _ = events_of(engine, [d])
    assert len(evs) == 1 and evs[0]["status"] == "UNKNOWN"
    assert (
        "end_time" in evs[0]["missing"]
        and "run" in evs[0]["missing"]
        and "parent" in evs[0]["missing"]
    )
    assert any(x["code"] == "unpaired_start" for x in diags)
    assert evs[0]["run_id"] is None  # no run was declared, none is invented


def test_unknown_source_kind_gets_diagnostic(engine):
    d = ObservationDraft(
        sensor=SensorRef("x", "1", "x1"),
        source_kind="mystery.thing",
        payload={},
        observed_at=datetime.now(UTC),
    )
    evs, diags, _ = events_of(engine, [d])
    assert evs == [] and diags[0]["code"] == "no_normalizer"


def test_rebuild_is_deterministic(engine, sink):
    with aw.run("r"):
        with aw.span("TOOL_INVOCATION", "t", object="tool:t") as s:
            s.input({"q": 1}).output("ok")
    evs1, _, iid = events_of(engine, sink.drafts)
    engine.process(force=True)
    evs2 = engine.store.events(iid)
    assert [e["event_id"] for e in evs1] == [e["event_id"] for e in evs2]
    assert evs1 == evs2


# ── legacy ────────────────────────────────────────────────────────────────
def test_legacy_translation_reports_loss():
    t = LegacyTranslator()
    ev = AgentEvent(
        session_id="s1",
        agent_id="a1",
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name="bash", raw_command="ls", arguments={"command": "ls"}),
    )
    r = t.translate(ev)
    assert r.status == TranslationStatus.ACCEPTED_WITH_LOSS
    assert (
        "parent" in r.lost_fields
        and "call_result_pairing" in r.lost_fields
        and "timestamp_provenance" in r.lost_fields
    )
    assert r.inferred_fields == []
    bad = t.translate({"event_id": "x", "event_type": "nope"})
    assert bad.status == TranslationStatus.REJECTED and bad.draft is None


def test_legacy_interpretations_are_flagged(engine):
    ev = AgentEvent(session_id="s1", agent_id="a1", event_type=EventType.CONFIDENCE_SCORE)
    results, _ = engine.ingest_legacy([ev])
    assert results[0].warnings
    engine.process()
    evs = engine.store.events(engine.interp_id_for("default"))
    assert evs[0]["kind"] == "UNKNOWN" and "legacy_interpretation" in evs[0]["facets"]


def test_legacy_pairs_only_by_declared_tool_id(engine):
    call = AgentEvent(
        session_id="s",
        agent_id="a",
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name="read", tool_id="tu1"),
    )
    res = AgentEvent(
        session_id="s",
        agent_id="a",
        event_type=EventType.TOOL_RESULT,
        tool_result=ToolResultData(tool_name="read", tool_id="tu1", output="data"),
    )
    orphan = AgentEvent(
        session_id="s",
        agent_id="a",
        event_type=EventType.TOOL_RESULT,
        tool_result=ToolResultData(tool_name="read", output="other"),
    )
    engine.ingest_legacy([call, res, orphan])
    engine.process()
    evs = engine.store.events(engine.interp_id_for("default"))
    tools = [e for e in evs if e["kind"] == "TOOL_INVOCATION"]
    assert len(tools) == 2
    paired = next(e for e in tools if e["inputs"] and e["outputs"])
    assert len(paired["derived_from"]) == 2
    unpaired = next(e for e in tools if e is not paired)
    assert "unpaired_result" in unpaired["facets"]
    assert all(e["run_id"] for e in evs)  # session_id declares the run


def test_legacy_mutation_of_original_does_not_change_evidence(engine):
    ev = AgentEvent(
        session_id="s", agent_id="a", event_type=EventType.GOAL_SET, goal="original goal"
    )
    engine.ingest_legacy([ev])
    ev.goal = "tampered after ingest"
    obs = engine.store.observations()[0]
    assert obs.payload()["goal"] == "original goal"


# ── otel ──────────────────────────────────────────────────────────────────
from otlp_fixture import OTLP  # noqa: E402


def test_otel_genai_mapping(engine):
    evs, _, iid = events_of(engine, drafts_from_spans(spans_from_otlp_json(OTLP)))
    by_op = {e["operation"]: e for e in evs}
    chat = by_op["chat:gpt-4o"]
    assert chat["kind"] == "MODEL_INVOCATION" and chat["object"] == "model:openai/gpt-4o"
    assert chat["status"] == "ERROR" and chat["resources"]["tokens_in"] == 120
    assert chat["actor"] == "service:rag-api"
    root = next(e for e in evs if "trace_root" in e["facets"])
    assert root["kind"] == "OPERATION" and "parent" not in root["missing"]
    db = by_op["postgresql:select"]
    assert db["kind"] == "RETRIEVAL"
    assert {
        "relation": "depends_on",
        "key_space": "otel.span",
        "value": f"{'a' * 32}/{'2' * 16}",
    } in db["parents"]
    assert len({e["run_id"] for e in evs}) == 1
    run = engine.store.runs(iid)[0]
    assert run["completeness"] == 1.0


# ── langchain ─────────────────────────────────────────────────────────────
def test_langchain_sensor_preserves_parent_run_id(engine):
    sink = ListSink()
    h = AgentWatchLangChainSensor(sink)
    root, llm, tool = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h.on_chain_start({"name": "AgentExecutor"}, {"input": "q"}, run_id=root)
    h.on_chat_model_start(
        {"kwargs": {"model": "claude-sonnet-5"}},
        [[SimpleNamespace(type="human", content="hi")]],
        run_id=llm,
        parent_run_id=root,
    )
    h.on_llm_end(
        SimpleNamespace(
            generations=[[SimpleNamespace(text="call tool")]],
            llm_output={"token_usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        ),
        run_id=llm,
        parent_run_id=root,
    )
    h.on_tool_start({"name": "search"}, "q", run_id=tool, parent_run_id=root)
    h.on_tool_error(RuntimeError("boom"), run_id=tool, parent_run_id=root)
    h.on_chain_end({"output": "done"}, run_id=root)
    assert all(d.declared_ids.get("run_id") for d in sink.drafts)
    evs, diags, iid = events_of(engine, sink.drafts)
    assert not diags
    m = next(e for e in evs if e["kind"] == "MODEL_INVOCATION")
    assert m["object"] == "model:claude-sonnet-5" and m["resources"]["tokens_in"] == 5
    t = next(e for e in evs if e["kind"] == "TOOL_INVOCATION")
    assert t["status"] == "ERROR"
    assert {p["value"] for p in t["parents"]} == {str(root)}
    rels = engine.store.relations(iid, all_runs=True, view="EXECUTION")
    assert sum(1 for r in rels if r["type"] == "CONTAINS") == 2
    assert all(e["run_id"] for e in evs)


# ── claude code ───────────────────────────────────────────────────────────
def test_claude_code_stream(engine):
    lines = [
        {"type": "system", "subtype": "init", "session_id": "S", "model": "claude-sonnet-5"},
        {
            "type": "assistant",
            "session_id": "S",
            "message": {
                "id": "m1",
                "model": "claude-sonnet-5",
                "content": [
                    {"type": "text", "text": "Reading"},
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file": "a.py"}},
                    {"type": "tool_use", "id": "tu2", "name": "Grep", "input": {"pattern": "x"}},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        },
        {
            "type": "user",
            "session_id": "S",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "print(1)"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu2",
                        "content": "no match",
                        "is_error": True,
                    },
                ]
            },
        },
        {
            "type": "assistant",
            "session_id": "S",
            "message": {
                "id": "m2",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "Done"}],
            },
        },
        {
            "type": "result",
            "session_id": "S",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": 0.01,
            "duration_ms": 900,
            "result": "Done",
        },
    ]
    evs, diags, iid = events_of(engine, drafts_from_lines(lines, live=True))
    tools = {e["operation"]: e for e in evs if e["kind"] == "TOOL_INVOCATION"}
    assert tools["Read"]["status"] == "OK" and tools["Grep"]["status"] == "ERROR"
    m2 = next(e for e in evs if e["kind"] == "MODEL_INVOCATION" and e["source_ids"][0][1] == "m2")
    assert {p["value"] for p in m2["parents"] if p["relation"] == "depends_on"} == {"tu1", "tu2"}
    run = engine.store.runs(iid)[0]
    assert run["status"] == "OK" and run["completeness"] == 1.0


# ── provider SDK clients ──────────────────────────────────────────────────
class _Completions:
    def create(self, **kw):
        return {
            "id": "resp1",
            "model": kw["model"],
            "choices": [{"message": {"content": "hello", "tool_calls": [{"id": "call1"}]}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }


def test_openai_client_sensor_passthrough_and_linkage(engine, sink):
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    instrument_openai(client, sink)
    with aw.run("r"):
        out = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
        client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "tool", "tool_call_id": "call1", "content": "42"}]
        )
    assert out["choices"][0]["message"]["content"] == "hello"  # return value unchanged
    evs, diags, iid = events_of(engine, sink.drafts)
    calls = [e for e in evs if e["kind"] == "MODEL_INVOCATION"]
    assert len(calls) == 2 and all(c["run_id"] for c in calls)
    rels = engine.store.relations(iid, all_runs=True, view="EXECUTION")
    assert any(r["type"] == "DEPENDS_ON" for r in rels)


class _Messages:
    def create(self, **kw):
        raise TimeoutError("upstream timeout")


def test_anthropic_client_error_is_recorded_and_reraised(engine, sink):
    client = SimpleNamespace(messages=_Messages())
    instrument_anthropic(client, sink)
    try:
        client.messages.create(model="claude-sonnet-5", messages=[])
    except TimeoutError:
        pass
    else:  # pragma: no cover
        raise AssertionError("error must propagate unchanged")
    evs, _, _ = events_of(engine, sink.drafts)
    assert evs[0]["status"] == "ERROR" and evs[0]["error"]["type"] == "TimeoutError"


def test_mcp_tap_pairs_by_jsonrpc_id(engine):
    sink = ListSink()
    tap = MCPTap(sink, server="github")
    tap.record(
        "client->server",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_issues", "arguments": {"q": "bug"}},
        },
    )
    tap.record(
        "server->client",
        {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "3 issues"}]}},
    )
    evs, _, _ = events_of(engine, sink.drafts)
    assert (
        len(evs) == 1
        and evs[0]["kind"] == "TOOL_INVOCATION"
        and evs[0]["object"] == "tool:mcp/github/search_issues"
    )
    assert evs[0]["status"] == "OK"


def _otlp_span(span_id: str, name: str, attrs: dict, parent: str | None = None, t: int = 0) -> dict:
    def val(v):
        return {"stringValue": v} if isinstance(v, str) else {"intValue": str(v)}

    return {
        "traceId": "b" * 32,
        "spanId": span_id * 16,
        **({"parentSpanId": parent * 16} if parent else {}),
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(1700000000000000000 + t * 1_000_000_000),
        "endTimeUnixNano": str(1700000000500000000 + t * 1_000_000_000),
        "attributes": [{"key": k, "value": val(v)} for k, v in attrs.items()],
        "status": {},
    }


def test_otel_panoptis_extension_declares_artifacts_and_lineage(engine):
    """ADR-0018: the panoptis.* attributes (AgentWatch's own convention, not an OTel standard)
    turn an otherwise opaque span into an artifact write whose input is a declared instance."""
    spans = [
        _otlp_span("1", "agent", {"gen_ai.operation.name": "invoke_agent"}),
        _otlp_span(
            "2",
            "chat m",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "m",
                "gen_ai.completion": "the summary text produced by the model for the report",
                "panoptis.information.instance_id": "summary-1",
            },
            parent="1",
            t=1,
        ),
        _otlp_span("3", "write report", {}, parent="1", t=2),
        _otlp_span(
            "4",
            "write report",
            {
                "panoptis.artifact.operation": "create",
                "panoptis.artifact.id": "report.md",
                "panoptis.artifact.content": "# Report\nthe summary text produced by the model",
                "panoptis.information.source": "summary-1",
            },
            parent="1",
            t=3,
        ),
    ]
    otlp = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [{"scope": {"name": "t"}, "spans": spans}],
            }
        ]
    }
    evs, _, iid = events_of(engine, drafts_from_spans(spans_from_otlp_json(otlp)))
    plain = next(e for e in evs if e["operation"] == "write report")
    assert plain["kind"] == "OPERATION"  # OTel alone has no artifact-write convention
    enriched = next(e for e in evs if e["operation"] == "create:report.md")
    assert enriched["kind"] == "STATE_MUTATION" and "artifact_creation" in enriched["facets"]
    assert enriched["object"] == "file:report.md"
    chat = next(e for e in evs if e["kind"] == "MODEL_INVOCATION")
    rels = engine.store.relations(iid, all_runs=True)
    consumes = [
        r
        for r in rels
        if r["type"] == "CONSUMES" and r["head"] == [f"event:{enriched['event_id']}"]
    ]
    assert [r["tail"][0] for r in consumes] == [f"inst:{chat['event_id']}/o0"]
    assert consumes[0]["attributes"]["evidence_type"] == "DECLARED_REFERENCE"
    assert consumes[0]["attributes"]["reference_only"] is True
