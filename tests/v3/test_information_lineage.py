"""Adversarial information-lineage tests (ADR-0017).

Same content is not the same information. Each test builds a small program whose true data
flow is known and checks that the INFORMATION graph neither invents flow from coincident
content nor drops flow that the evidence establishes. "Resolved" below means reachable over
information-flow relations by default traversal (ambiguous candidates excluded).
"""

from __future__ import annotations

from panoptis import instrument as aw
from panoptis.provenance.lineage import lineage
from panoptis.query.workspace import Workspace

FLOW = ["PRODUCES", "CONSUMES", "DERIVES_FROM", "CONTAINS_ITEM", "TRANSFERS"]
TEXT = "the reactor cooling loop must be inspected before any restart of unit two"


def copy_of(s: str) -> str:
    """An equal but distinct object (as if deserialized): no runtime identity."""
    return "".join(list(s))


def run(engine, sink, body, name: str = "probe") -> tuple[Workspace, str]:
    with aw.run(name):
        body()
    engine.ingest(sink.drafts)
    sink.drafts.clear()
    ws = Workspace(engine)
    return ws, ws.resolve_run("latest")["run_id"]


def event(ws: Workspace, run_id: str, operation: str, n: int = 0) -> dict:
    return [e for e in ws.events(run_id) if e["operation"] == operation][n]


def resolved_ancestor_ops(ws: Workspace, run_id: str, node: str) -> set[str]:
    g = ws.graph(run_id)
    ops = set()
    for s in g.ancestors(node, views=["INFORMATION"], types=FLOW, skip_kinds=["entity"]):
        if s.node.startswith("event:"):
            ops.add(ws.event(s.node)["operation"])
    return ops


def candidate_ops(ws: Workspace, run_id: str, event_id: str) -> set[str]:
    """Producers offered as AMBIGUOUS candidates for any input of the event."""
    out = set()
    targets = {f"event:{event_id}"} | {
        r["tail"][0]
        for r in ws.relations(run_id, view="INFORMATION")
        if r["type"] == "CONSUMES" and r["head"] == [f"event:{event_id}"]
    }
    for r in ws.relations(run_id, view="INFORMATION"):
        if r["type"] == "CANDIDATE_SOURCE" and r["head"][0] in targets:
            out.add(ws.event(r["attributes"]["source_event"])["operation"])
    return out


# ── identical output, different producers ────────────────────────────────────────────────
def _two_producers(consume_value):
    def body():
        with aw.span("TOOL_INVOCATION", "A") as sa:
            sa.output(copy_of(TEXT))
        with aw.span("TOOL_INVOCATION", "B") as sb:
            vb = copy_of(TEXT)
            sb.output(vb)
        consume_value(vb, sb)

    return body


def test_identical_outputs_without_evidence_are_ambiguous_never_first_seen(engine, sink):
    def consume(vb, sb):
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(copy_of(vb))

    ws, rid = run(engine, sink, _two_producers(consume))
    c = event(ws, rid, "C")
    anc = resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}")
    assert "A" not in anc and "B" not in anc  # nothing invented
    assert candidate_ops(ws, rid, c["event_id"]) == {"A", "B"}  # ambiguity is exposed


def test_runtime_identity_selects_the_actual_producer(engine, sink):
    def consume(vb, sb):
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(vb)  # the very object B produced

    ws, rid = run(engine, sink, _two_producers(consume))
    c = event(ws, rid, "C")
    assert resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}") == {"B"}
    rel = next(
        r
        for r in ws.relations(rid, view="INFORMATION")
        if r["type"] == "CONSUMES" and r["head"] == [f"event:{c['event_id']}"]
    )
    assert rel["attributes"]["evidence_type"] == "DECLARED_REFERENCE"
    assert rel["attributes"]["mode"] == "HIGH_FIDELITY"


def test_explicit_source_outranks_earlier_identical_content(engine, sink):
    def consume(vb, sb):
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(copy_of(vb), source=sb)

    ws, rid = run(engine, sink, _two_producers(consume))
    c = event(ws, rid, "C")
    assert resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}") == {"B"}
    assert candidate_ops(ws, rid, c["event_id"]) == set()


def test_decorated_calls_carry_identity_through_arguments(engine, sink):
    @aw.tool("A")
    def a() -> str:
        return copy_of(TEXT)

    @aw.tool("B")
    def b() -> str:
        return copy_of(TEXT)

    @aw.tool("C")
    def c(x: str) -> int:
        return len(x)

    def body():
        a()
        c(b())

    ws, rid = run(engine, sink, body)
    ce = event(ws, rid, "C")
    assert resolved_ancestor_ops(ws, rid, f"event:{ce['event_id']}") == {"B"}


# ── unused duplicate ─────────────────────────────────────────────────────────────────────
def _unused_duplicate(consume_value):
    def body():
        with aw.span("TOOL_INVOCATION", "A") as sa:
            x = copy_of(TEXT)
            sa.output(x)
        with aw.span("TOOL_INVOCATION", "B") as sb:  # copies A's value; nobody uses the copy
            sb.input(x)
            sb.output(copy_of(x))
        consume_value(x)

    return body


def test_unused_duplicate_is_not_an_ancestor_with_identity(engine, sink):
    def consume(x):
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(x)

    ws, rid = run(engine, sink, _unused_duplicate(consume))
    c = event(ws, rid, "C")
    assert resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}") == {"A"}


def test_unused_duplicate_is_not_an_ancestor_without_identity(engine, sink):
    def consume(x):
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(copy_of(x))

    ws, rid = run(engine, sink, _unused_duplicate(consume))
    c = event(ws, rid, "C")
    anc = resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}")
    assert "A" in anc  # an ancestor under every explanation
    assert "B" not in anc  # the copy could have been used, or not: a candidate only
    assert "B" in candidate_ops(ws, rid, c["event_id"])


# ── cache reuse across runs ──────────────────────────────────────────────────────────────
def test_cache_reuse_across_runs_reaches_the_original_production(engine, sink):
    @aw.tool("A")
    def a() -> str:
        return copy_of(TEXT)

    def first():
        aw.memory_write("cache", "k", a())

    run(engine, sink, first, name="writer")

    @aw.tool("C")
    def c(x: str) -> int:
        return len(x)

    def second():
        value = aw.memory_read("cache", "k", copy_of(TEXT))  # a fresh object, as if loaded
        c(value)

    ws, rid = run(engine, sink, second, name="reader")
    ce = event(ws, rid, "C")
    anc = resolved_ancestor_ops(ws, rid, f"event:{ce['event_id']}")
    assert {"A", "write:cache", "read:cache"} <= anc, anc


# ── fan-out / fan-in ─────────────────────────────────────────────────────────────────────
DOCS = [
    {"id": "d1", "text": "solar panels convert sunlight into electricity using silicon cells"},
    {"id": "d2", "text": "battery storage keeps surplus solar energy available after sunset"},
    {"id": "d3", "text": "grid operators balance demand with pumped hydro and gas peakers"},
]


def _fan(join):
    @aw.retriever("kb")
    def search(q: str) -> list[dict[str, str]]:
        return DOCS

    def analyst(i: int):
        @aw.tool(f"analyst{i}")
        def f(text: str) -> str:
            return f"analysis {i}: " + text + f" — assessed by analyst number {i} for the report"

        return f

    @aw.tool("reducer")
    def reducer(parts) -> str:
        return "merged"

    def body():
        docs = search("energy")
        outs = [analyst(i)(docs[i]["text"]) for i in range(3)]
        reducer(join(outs))

    return body


def test_fan_in_keeps_every_contributor_with_identity(engine, sink):
    ws, rid = run(engine, sink, _fan(lambda outs: outs))
    r = event(ws, rid, "reducer")
    anc = resolved_ancestor_ops(ws, rid, f"event:{r['event_id']}")
    assert {"analyst0", "analyst1", "analyst2", "kb"} <= anc, anc


def test_fan_in_keeps_every_contributor_by_content(engine, sink):
    ws, rid = run(engine, sink, _fan(lambda outs: "\n\n".join(copy_of(o) for o in outs)))
    r = event(ws, rid, "reducer")
    anc = resolved_ancestor_ops(ws, rid, f"event:{r['event_id']}")
    assert {"analyst0", "analyst1", "analyst2", "kb"} <= anc, anc


# ── partial content ──────────────────────────────────────────────────────────────────────
def test_partial_contents_make_both_producers_ancestors(engine, sink):
    p1 = "the first paragraph describes the inspection schedule for the cooling loop"
    p2 = "the second paragraph lists the spare parts that must be ordered this week"

    def body():
        with aw.span("TOOL_INVOCATION", "A") as sa:
            sa.output(copy_of(p1))
        with aw.span("TOOL_INVOCATION", "B") as sb:
            sb.output(copy_of(p2))
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(p1 + "\n\n" + p2)

    ws, rid = run(engine, sink, body)
    c = event(ws, rid, "C")
    assert resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}") == {"A", "B"}


# ── coincidental overlap ─────────────────────────────────────────────────────────────────
def test_coincidental_phrase_overlap_creates_no_edge(engine, sink):
    def body():
        with aw.span("MODEL_INVOCATION", "A", actor="agent:one") as sa:
            sa.output("quarterly revenue grew in europe and asia while costs stayed flat overall")
        with aw.span("MODEL_INVOCATION", "B", actor="agent:two") as sb:
            sb.input("the board asked why revenue grew in europe and asia but margins fell sharply")

    ws, rid = run(engine, sink, body)
    b = event(ws, rid, "B")
    assert resolved_ancestor_ops(ws, rid, f"event:{b['event_id']}") == set()
    assert candidate_ops(ws, rid, b["event_id"]) == set()


def test_identical_text_in_another_run_is_not_information_flow(engine, sink):
    def first():
        with aw.span("MODEL_INVOCATION", "A") as sa:
            sa.output(copy_of(TEXT))

    run(engine, sink, first, name="one")

    def second():
        with aw.span("TOOL_INVOCATION", "C") as sc:
            sc.input(copy_of(TEXT))

    ws, rid = run(engine, sink, second, name="two")
    c = event(ws, rid, "C")
    assert resolved_ancestor_ops(ws, rid, f"event:{c['event_id']}") == set()
    rel = next(
        r
        for r in ws.relations(rid, view="INFORMATION")
        if r["type"] == "CONSUMES" and r["head"] == [f"event:{c['event_id']}"]
    )
    assert rel["attributes"]["resolution"] == "UNRESOLVED"  # unknown stays unknown


def test_retrieved_copy_of_own_output_is_similarity_not_derivation(engine, sink):
    @aw.model("m")
    def gen(p: str) -> str:
        return copy_of(TEXT)

    @aw.retriever("kb")
    def search(q: str) -> list[dict[str, str]]:
        return [{"id": "x", "text": copy_of(TEXT)}, {"id": "y", "text": "unrelated second doc"}]

    def body():
        gen("write a note")
        search("note")

    ws, rid = run(engine, sink, body)
    r = event(ws, rid, "kb")
    assert "m" not in resolved_ancestor_ops(ws, rid, f"event:{r['event_id']}")
    types = {x["type"] for x in ws.relations(rid, view="INFORMATION")}
    assert "MATCHES_CONTENT" in types


# ── discarded intermediate ───────────────────────────────────────────────────────────────
def test_discarded_intermediate_is_not_an_information_ancestor(engine, sink):
    summaries = [
        f"summary {i}: facet {i} of the storage question has its own findings and caveats"
        for i in range(3)
    ]

    def worker(i: int):
        @aw.tool(f"worker{i}")
        def w() -> str:
            return copy_of(summaries[i])

        return w

    @aw.tool("critic")
    def critic(review: str) -> str:
        return "Draft: " + review  # reviewed, then thrown away

    def body():
        outs = [worker(i)() for i in range(3)]
        critic("\n".join(copy_of(o) for o in outs))
        aw.artifact("report.md", " ".join(copy_of(o) for o in outs) + " Estimate: 42.")

    ws, rid = run(engine, sink, body)
    report = next(e for e in ws.events(rid) if "artifact_creation" in e["facets"])
    node = f"inst:{report['event_id']}/o0"
    anc = resolved_ancestor_ops(ws, rid, node)
    assert {"worker0", "worker1", "worker2"} <= anc, anc
    assert "critic" not in anc
    tree = lineage(ws, node, run_id=rid)
    assert tree["metrics"]["ambiguous_candidates"]  # the draft is shown as a candidate


def test_declared_report_input_makes_the_intermediate_certain(engine, sink):
    """High-fidelity mode: when the writer declares what it consumed, no ambiguity remains."""

    @aw.tool("answer")
    def answer(ctx: str) -> str:
        return "Answer: " + ctx

    def body():
        a = answer(copy_of(TEXT))
        with aw.span("STATE_MUTATION", "write_report", facets=["artifact_creation"]) as s:
            s.input(a)
            s.output(a + " Estimate: 42.", role="artifact", label="report.md")

    ws, rid = run(engine, sink, body)
    report = next(e for e in ws.events(rid) if "artifact_creation" in e["facets"])
    assert "answer" in resolved_ancestor_ops(ws, rid, f"inst:{report['event_id']}/o0")
