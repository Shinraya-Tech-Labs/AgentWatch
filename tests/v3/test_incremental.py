"""Incremental processing must produce exactly what a full rebuild produces."""

from __future__ import annotations

import json
import uuid

from otlp_fixture import OTLP

from panoptis import instrument as aw
from panoptis.core.schema import AgentEvent, EventType
from panoptis.evidence.canonical import canonical_json
from panoptis.runtime.engine import Engine
from panoptis.sensors.base import ListSink
from panoptis.sensors.claude_code import drafts_from_lines
from panoptis.sensors.langchain import AgentWatchLangChainSensor
from panoptis.sensors.otel import drafts_from_spans, spans_from_otlp_json


def native_run(name: str, fail: int = 0) -> None:
    state = {"fail": fail}

    @aw.retriever("kb")
    def search(q):
        return [
            {"id": "a", "text": f"document about {q} with enough words to be matched by content"},
            {"id": "b", "text": "second"},
        ]

    @aw.tool("t")
    def tool(x):
        if state["fail"]:
            state["fail"] -= 1
            raise TimeoutError("t")
        return x

    with aw.run(name):
        with aw.span("OPERATION", "step", actor="agent:a"):
            docs = search(name)
            for _ in range(4):
                try:
                    tool(docs[0]["text"])
                    break
                except TimeoutError:
                    continue


def snapshot(engine: Engine) -> dict[str, list[str]]:
    iid = engine.interp_id_for("default")
    st = engine.store
    diags = sorted(
        canonical_json({k: d[k] for k in ("obs_id", "level", "code", "message")})
        for d in st.diagnostics(iid)
    )
    return {
        "events": sorted(canonical_json(e) for e in st.events(iid)),
        "relations": sorted(canonical_json(r) for r in st.relations(iid, all_runs=True)),
        "runs": sorted(canonical_json(r) for r in st.runs(iid)),
        "entities": sorted(canonical_json(e) for e in st.entities(iid)),
        "motifs": sorted(canonical_json(m) for m in st.derived(iid, "motif_instance")),
        "profiles": sorted(canonical_json(m) for m in st.derived(iid, "behaviour_profile")),
        "diagnostics": diags,
    }


def test_incremental_equals_full_rebuild(engine, sink):
    batches: list[list] = []
    native_run("r1")
    batches.append(list(sink.drafts))
    sink.drafts.clear()
    native_run("r2", fail=2)
    batches.append(list(sink.drafts))
    sink.drafts.clear()
    batches.append(drafts_from_spans(spans_from_otlp_json(OTLP)))
    batches.append(
        drafts_from_lines(
            [
                {
                    "type": "assistant",
                    "session_id": "S1",
                    "message": {
                        "id": "m1",
                        "model": "claude-sonnet-5",
                        "content": [{"type": "tool_use", "id": "tu1", "name": "Read", "input": {}}],
                    },
                },
                {
                    "type": "user",
                    "session_id": "S1",
                    "message": {
                        "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "x"}]
                    },
                },
            ],
            live=True,
        )
    )
    native_run("r3")
    batches.append(list(sink.drafts))
    sink.drafts.clear()

    modes = []
    for b in batches:
        engine.ingest(b)
        engine.process()
        modes.append(engine.last_mode)
    assert modes[0] == "full" and set(modes[1:]) == {"incremental"}, modes
    incremental_state = snapshot(engine)
    engine.process(force=True)
    assert engine.last_mode == "full"
    full_state = snapshot(engine)
    for key in full_state:
        assert incremental_state[key] == full_state[key], key


def test_late_observation_for_existing_run_rebuilds_only_that_run(engine, sink):
    native_run("a")
    native_run("b")
    engine.ingest(sink.drafts)
    engine.process()
    ends = [d for d in sink.drafts if d.source_kind == "native.run.end"]
    # re-sending is idempotent; a genuinely new observation for run "a" arrives late
    late = sink.drafts[0]
    late_payload = {
        **late.to_dict(),
        "source_seq": 999,
        "source_kind": "native.point",
        "payload": {"kind": "FAILURE", "operation": "late_alarm", "data": {}},
        "declared_ids": {"event_id": uuid.uuid4().hex[:8], "run_id": late.declared_ids["run_id"]},
    }
    from panoptis.evidence.model import ObservationDraft

    engine.ingest([ObservationDraft.from_dict(json.loads(json.dumps(late_payload)))])
    report = engine.process()
    assert report["mode"] == "incremental" and report["runs_rebuilt"] == 1
    assert len(ends) == 2
    state = snapshot(engine)
    engine.process(force=True)
    assert snapshot(engine) == state


def _langchain_run(parent_known: bool = True) -> list:
    ls = ListSink()
    h = AgentWatchLangChainSensor(ls)
    root, child = uuid.uuid4(), uuid.uuid4()
    if parent_known:
        h.on_chain_start({"name": "c"}, {"q": 1}, run_id=root)
    h.on_tool_start({"name": "t"}, "x", run_id=child, parent_run_id=root)
    h.on_tool_end("y", run_id=child, parent_run_id=root)
    if parent_known:
        h.on_chain_end({"a": 1}, run_id=root)
    return ls.drafts


def test_langchain_root_run_enables_incremental(engine, sink):
    native_run("x")
    engine.ingest(sink.drafts)
    engine.process()
    drafts = _langchain_run()
    assert all(d.declared_ids.get("root_run_id") for d in drafts)
    engine.ingest(drafts)
    engine.process()
    assert engine.last_mode == "incremental"
    state = snapshot(engine)
    engine.process(force=True)
    assert snapshot(engine) == state


def test_unknown_correlation_falls_back_to_full(engine, sink):
    native_run("x")
    engine.ingest(sink.drafts)
    engine.process()
    drafts = _langchain_run(parent_known=False)  # parent never seen: root unknown, no key
    assert not any(d.declared_ids.get("root_run_id") for d in drafts)
    engine.ingest(drafts)
    engine.process()
    assert engine.last_mode == "full"


def test_legacy_sessions_are_incremental(engine):
    engine.ingest_legacy(
        [AgentEvent(session_id="s1", agent_id="a", event_type=EventType.SESSION_START)]
    )
    engine.process()
    engine.ingest_legacy(
        [AgentEvent(session_id="s2", agent_id="a", event_type=EventType.SESSION_START)]
    )
    engine.process()
    assert engine.last_mode == "incremental"
    state = snapshot(engine)
    engine.process(force=True)
    assert snapshot(engine) == state
