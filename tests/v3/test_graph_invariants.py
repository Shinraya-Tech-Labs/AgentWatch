"""Graph invariants (ADR-0017), checked over seeded random programs.

Each program mixes producers of repeated phrases (so identical content from different
producers is common), consumers that pass the produced object itself (runtime identity) or an
equal copy, concatenations, retrievals, memory round-trips, and values of unknown origin.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from sqlalchemy import text

from panoptis import instrument as aw
from panoptis.evidence.canonical import canonical_json
from panoptis.graph.information import instance_event
from panoptis.query.workspace import Workspace
from panoptis.runtime.engine import Engine
from panoptis.storage.store import Store

PHRASES = [
    "the cooling loop must be inspected before any restart of unit two",
    "spare pumps are stored in the east warehouse behind the loading bay",
    "the night shift reported a pressure drop in the secondary circuit",
    "all inspection records must be signed by the duty engineer on site",
    "the vendor confirmed delivery of replacement valves next tuesday",
]
FLOW = {"PRODUCES", "CONSUMES", "DERIVES_FROM", "CONTAINS_ITEM", "TRANSFERS"}
SEEDS = range(12)


def rng_for(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 - reproducible test programs, not security


def copy_of(s: str) -> str:
    return "".join(list(s))


def random_program(rng: random.Random, name: str) -> dict[str, Any]:
    """Run one program; return what it did, for checking against the graph."""
    facts: dict[str, Any] = {"identity": [], "unknown": []}
    produced: list[tuple[Any, str, str]] = []  # (span, value, operation)

    @aw.retriever("kb")
    def search(q: str) -> list[dict[str, str]]:
        return [{"id": str(i), "text": copy_of(p)} for i, p in enumerate(rng.sample(PHRASES, 2))]

    with aw.run(name):
        for i in range(rng.randint(4, 10)):
            step = rng.choice(
                [
                    "produce",
                    "produce",
                    "identity",
                    "copy",
                    "concat",
                    "retrieve",
                    "memory",
                    "unknown",
                ]
            )
            op = f"{step}{i}"
            if (
                step == "produce"
                or not produced
                and step in ("identity", "copy", "concat", "memory")
            ):
                with aw.span("TOOL_INVOCATION", f"produce{i}") as s:
                    v = copy_of(rng.choice(PHRASES))
                    s.output(v)
                produced.append((s, v, f"produce{i}"))
            elif step == "identity":
                _, v, src = rng.choice(produced)
                with aw.span("TOOL_INVOCATION", op) as s:
                    s.input(v)
                facts["identity"].append((op, src))
            elif step == "copy":
                _, v, _src = rng.choice(produced)
                with aw.span("TOOL_INVOCATION", op) as s:
                    s.input(copy_of(v))
            elif step == "concat":
                parts = [copy_of(rng.choice(produced)[1]) for _ in range(2)]
                with aw.span("TOOL_INVOCATION", op) as s:
                    s.input(" ".join(parts))
            elif step == "retrieve":
                search(f"query {i}")
            elif step == "memory":
                _, v, _src = rng.choice(produced)
                aw.memory_write("notes", f"k{i}", v)
                aw.memory_read("notes", f"k{i}", copy_of(v))
            else:
                token = f"zq{rng.randrange(10**9)} unobserved origin value number {i} kx{rng.randrange(10**9)}"
                with aw.span("TOOL_INVOCATION", op) as s:
                    s.input(token)
                facts["unknown"].append(op)
    return facts


@pytest.fixture
def programs(engine, sink):
    facts = {}
    for seed in SEEDS:
        facts[f"prog{seed}"] = random_program(rng_for(seed), f"prog{seed}")
    engine.ingest(sink.drafts)
    sink.drafts.clear()
    return Workspace(engine), facts


def _runs(ws: Workspace) -> list[dict[str, Any]]:
    return [r for r in ws.runs() if r.get("name", "").startswith("prog")]


def test_similarity_and_ambiguity_are_never_traversed_as_flow(programs):
    ws, _ = programs
    for run in _runs(ws):
        g = ws.graph(run["run_id"])
        non_flow = {
            rid
            for rid, r in g.relations.items()
            if r["type"] in ("MATCHES_CONTENT", "CANDIDATE_SOURCE")
        }
        for node in list(g.nodes):
            for step in g.descendants(node, views=["INFORMATION"]):
                assert step.via not in non_flow, (run["name"], step)


def test_equal_content_does_not_imply_instance_identity(programs):
    ws, _ = programs
    for run in _runs(ws):
        by_content: dict[str, set[str]] = {}
        for r in ws.relations(run["run_id"], view="INFORMATION"):
            if r["type"] == "PRODUCES":
                by_content.setdefault(r["attributes"]["content_id"], set()).add(r["head"][0])
        for nodes in by_content.values():
            producers = {instance_event(n) for n in nodes}
            assert len(nodes) == len(producers) or len(producers) == 1


def test_explicit_identity_outranks_content_heuristics(programs):
    ws, facts = programs
    for run in _runs(ws):
        ops = {e["operation"]: e["event_id"] for e in ws.events(run["run_id"])}
        rels = ws.relations(run["run_id"], view="INFORMATION")
        for consumer, source in facts[run["name"]]["identity"]:
            cid = ops[consumer]
            into = [r for r in rels if r["head"] == [f"event:{cid}"]]
            consumes = [r for r in into if r["type"] == "CONSUMES"]
            assert consumes and consumes[0]["attributes"]["evidence_type"] == "DECLARED_REFERENCE"
            assert instance_event(consumes[0]["tail"][0]) == ops[source]
            assert not [r for r in into if r["type"] == "CANDIDATE_SOURCE"]


def test_unknown_provenance_stays_unknown(programs):
    ws, facts = programs
    for run in _runs(ws):
        ops = {e["operation"]: e["event_id"] for e in ws.events(run["run_id"])}
        rels = ws.relations(run["run_id"], view="INFORMATION")
        for op in facts[run["name"]]["unknown"]:
            node = f"inst:{ops[op]}/in0"
            consumes = [r for r in rels if r["type"] == "CONSUMES" and r["tail"] == [node]]
            assert consumes and consumes[0]["attributes"]["resolution"] == "UNRESOLVED"
            assert not [r for r in rels if r["head"] == [node]]  # nothing invented into it


def test_flow_never_goes_backwards_in_time(programs):
    ws, _ = programs
    for run in _runs(ws):
        evs = {e["event_id"]: e for e in ws.events(run["run_id"])}
        for r in ws.relations(run["run_id"], view="INFORMATION"):
            if r["type"] not in FLOW or r["type"] == "PRODUCES":
                continue
            a, b = r["attributes"]["source_event"], r["attributes"]["target_event"]
            if a in evs and b in evs and a != b:
                ta, tb = evs[a]["time"], evs[b]["time"]
                assert (
                    (ta["end"] or ta["start"]) <= (tb["start"] or tb["end"])
                    or r["type"] == "CONSUMES"
                    and "/in" in r["tail"][0]
                ), r


def test_every_information_relation_states_its_evidence(programs):
    ws, _ = programs
    for run in _runs(ws):
        for r in ws.relations(run["run_id"], view="INFORMATION"):
            a = r["attributes"]
            assert a["evidence_type"] and a["strength"] in ("STRONG", "MEDIUM", "WEAK", "NONE")
            assert a["mode"] in ("HIGH_FIDELITY", "BEST_EFFORT")
            assert "source_event" in a and "target_event" in a


def test_graph_rebuild_is_deterministic(programs):
    ws, _ = programs

    def snapshot() -> list[str]:
        iid = ws.engine.interp_id_for("default")
        return sorted(canonical_json(r) for r in ws.store.relations(iid, all_runs=True))

    first = snapshot()
    ws.engine.process(force=True)
    assert snapshot() == first


def test_incremental_equals_full_rebuild_on_random_programs(engine, sink):
    for seed in SEEDS:
        random_program(rng_for(seed), f"prog{seed}")
        engine.ingest(sink.drafts)
        sink.drafts.clear()
        engine.process()
    iid = engine.interp_id_for("default")
    incremental = sorted(canonical_json(r) for r in engine.store.relations(iid, all_runs=True))
    engine.process(force=True)
    assert engine.last_mode == "full"
    assert (
        sorted(canonical_json(r) for r in engine.store.relations(iid, all_runs=True)) == incremental
    )


def test_raw_evidence_stays_immutable(programs):
    ws, _ = programs
    store = ws.store
    ws.engine.process(force=True)
    obs = store.observations()
    assert obs and all(o.verify_hash() in (True, None) for o in obs)
    with pytest.raises(Exception):
        with store.engine.begin() as conn:
            conn.execute(text("UPDATE aw3_observations SET payload_json = '{}'"))


def test_erased_data_cannot_be_reconstructed_from_the_graph(tmp_path, sink):
    secret = "Alice Example's diagnosis mentions a rare condition in her records"  # noqa: S105
    store = Store(f"sqlite:///{(tmp_path / 'enc.db').as_posix()}", encrypt_payloads=True)
    engine = Engine(store)

    @aw.model("m")
    def summarize(p: str) -> str:
        return "summary: " + p

    with aw.run("intake", subject_id="alice"):
        summarize(secret)
    engine.ingest(sink.drafts)
    engine.process()
    engine.erase_subject("default", "alice", reason="test: right to erasure")
    engine.process(force=True)
    ws = Workspace(engine)
    iid = engine.interp_id_for("default")
    dumped = json.dumps(
        [
            store.relations(iid, all_runs=True),
            store.events(iid),
            store.derived(iid, "motif_instance"),
        ]
    )
    assert "Alice" not in dumped and "diagnosis" not in dumped
    with store.engine.connect() as conn:
        rows = [r[0] for r in conn.execute(text("SELECT payload_json FROM aw3_observations"))]
    assert not any("Alice" in r for r in rows)
    assert ws.runs() == [] or all("Alice" not in json.dumps(r) for r in ws.runs())
    store.close()
