"""Regression tests for bugs found during the v3 work."""

from __future__ import annotations

import asyncio

from panoptis.core.event_bus import EventBus
from panoptis.core.schema import AgentEvent, EventType
from panoptis.tracing.collector import TraceCollector


def test_hipaa_redaction_does_not_leak_into_other_handlers():
    """v0.2 bug: the collector redacted the shared event in place, so whether another handler
    (the Postgres writer) saw raw or redacted PHI depended on dispatch order."""
    for _ in range(5):  # dispatch order comes from a set; repeat to cover orderings
        bus = EventBus()
        collector = TraceCollector(hipaa_compliance_mode=True)
        seen: list[str] = []

        async def persist(ev: AgentEvent) -> None:
            seen.append(ev.goal or "")

        bus.subscribe_fn(collector.ingest, handler_id="collector")
        bus.subscribe_fn(persist, handler_id="persist")
        original = "MRN: 1234567 diagnosed with diabetes"
        event = AgentEvent(
            session_id="s", agent_id="a", event_type=EventType.SESSION_START, goal=original
        )
        asyncio.run(bus.publish(event))
        assert event.goal == original, "publisher's object must not be mutated"
        assert seen == [original], "other handlers see the event as published"
        trace = collector.get_trace("s")
        assert trace is not None
        assert "1234567" not in (trace.session.goal or ""), "collector keeps only its redacted copy"


def test_motif_explanation_does_not_claim_none_when_motifs_exist():
    """Regression: explain() used `lines.extend(...) or lines.append(...)`; extend returns
    None, so 'No motifs detected.' was appended even when motifs were listed."""
    from panoptis.query.engine import explain

    lines = explain(
        {
            "type": "motifs",
            "result": [{"motif_id": "M001", "motif_name": "retry_loop", "explanation": "x"}],
        }
    )
    assert lines == ["- M001 retry_loop: x"]
    assert explain({"type": "motifs", "result": []}) == ["No motifs detected."]


def test_compare_blames_the_first_differing_retry_not_an_identical_one(engine, sink):
    """Regression (held-out AWBench async_event_pipeline, tool_timeout): with retries the
    earliest divergence is the first attempt whose outcome differs, not an attempt that
    failed identically in both runs."""
    from panoptis import instrument as aw
    from panoptis.compare.runs import compare
    from panoptis.query.workspace import Workspace, run_order_key

    def run(fails: int) -> None:
        state = {"n": fails}

        @aw.tool("lookup")
        def lookup(x: str) -> str:
            if state["n"]:
                state["n"] -= 1
                raise TimeoutError("slow")
            return "value"

        with aw.run("lookup-job"):  # same program, same run name
            for _ in range(6):
                try:
                    lookup("k")
                    break
                except TimeoutError:
                    continue

    run(1)
    run(3)
    engine.ingest(sink.drafts)
    ws = Workspace(engine)
    base, perturbed = (r["run_id"] for r in sorted(ws.runs(), key=run_order_key))
    runs = {"perturbed": perturbed}
    d = compare(ws, base, perturbed)["earliest_divergence"]
    attempts = [
        e["event_id"]
        for e in sorted(
            ws.events(runs["perturbed"], kind="TOOL_INVOCATION"),
            key=lambda e: e["time"]["start"],
        )
    ]
    assert d["b_event"]["event_id"] == attempts[1]  # the 2nd attempt: OK in base, ERROR here
    assert "status OK → ERROR" in d["reasons"]


def test_memory_read_value_is_explained_by_the_write_not_by_content(engine, sink):
    """Regression (held-out AWBench): declared structure outranks content — a memory read that
    returned the written value gets its lineage from the write, not from similar text."""
    from panoptis import instrument as aw
    from panoptis.query.workspace import Workspace

    text = "the enriched record lists the solar and storage findings for the report"

    @aw.tool("other")
    def other() -> str:
        return "".join(list(text))  # identical text from an unrelated producer

    with aw.run("mem"):
        other()
        aw.memory_write("db", "record", "".join(list(text)))
        aw.memory_read("db", "record", "".join(list(text)))
    engine.ingest(sink.drafts)
    ws = Workspace(engine)
    rid = ws.resolve_run("mem")["run_id"]
    read = next(e for e in ws.events(rid) if e["operation"] == "read:db")
    into = [r for r in ws.relations(rid) if r["head"] == [f"inst:{read['event_id']}/o0"]]
    assert [r["type"] for r in into] == ["PRODUCES"]  # no content-inferred parents
    assert any(r["type"] == "TRANSFERS" for r in ws.relations(rid))
