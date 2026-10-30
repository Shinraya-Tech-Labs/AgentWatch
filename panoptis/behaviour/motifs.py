"""Behavioural motifs: named, precisely defined recurring patterns in a run's structure.

Every motif has a written definition that can be checked against the evidence it cites.
All detectors are deterministic rules or graph queries over the interpretation. None is
promoted beyond EXPERIMENTAL until AWBench measures its precision and recall.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from panoptis.analysis.base import AnalysisInput, Analyzer
from panoptis.analysis.maturity import Maturity
from panoptis.events.model import ComputationalEvent, EventKind, EventStatus
from panoptis.graph.build import event_order
from panoptis.graph.information import instance_event

MOTIF_NS = uuid.UUID("5e1d7c9a-8b2f-5c3d-9e4f-6a7b8c9d0e1f")


@dataclass(frozen=True)
class MotifDefinition:
    motif_id: str
    name: str
    version: str
    kind: str  # RULE | GRAPH_QUERY | STATISTICAL
    definition: str
    maturity: Maturity = Maturity.EXPERIMENTAL
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "motif_id": self.motif_id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "definition": self.definition,
            "maturity": self.maturity.value,
            "parameters": self.parameters,
        }


@dataclass
class MotifInstance:
    motif_id: str
    run_id: str
    events: list[str]
    artifacts: list[str] = field(default_factory=list)
    confidence: float = 1.0
    explanation: str = ""
    evidence: list[str] = field(
        default_factory=list
    )  # relation ids / event ids justifying the match
    measures: dict[str, Any] = field(default_factory=dict)
    t_start: str | None = None
    t_end: str | None = None


Detector = Callable[
    [str, list[ComputationalEvent], list[dict[str, Any]], AnalysisInput], list[MotifInstance]
]


class MotifRegistry:
    def __init__(self) -> None:
        self.definitions: dict[str, MotifDefinition] = {}
        self.detectors: dict[str, Detector] = {}

    def register(self, definition: MotifDefinition) -> Callable[[Detector], Detector]:
        def deco(fn: Detector) -> Detector:
            self.definitions[definition.motif_id] = definition
            self.detectors[definition.motif_id] = fn
            return fn

        return deco

    def detect(
        self,
        run_id: str,
        events: list[ComputationalEvent],
        relations: list[dict[str, Any]],
        data: AnalysisInput,
    ) -> list[MotifInstance]:
        out: list[MotifInstance] = []
        for mid, det in self.detectors.items():
            out.extend(det(run_id, events, relations, data))
        return out


REGISTRY = MotifRegistry()
LEAF = (
    EventKind.TOOL_INVOCATION,
    EventKind.MODEL_INVOCATION,
    EventKind.RETRIEVAL,
    EventKind.EXTERNAL_IO,
    EventKind.MEMORY_ACCESS,
)


def _span(events: Sequence[ComputationalEvent]) -> tuple[str | None, str | None]:
    starts = [e.time.start for e in events if e.time.start is not None]
    ends = [t for e in events if (t := e.time.end or e.time.start) is not None]
    return (min(starts).isoformat() if starts else None, max(ends).isoformat() if ends else None)


@REGISTRY.register(
    MotifDefinition(
        "M001",
        "retry_loop",
        "1",
        "GRAPH_QUERY",
        "A chain of ≥1 RETRIES relations: a call fails (ERROR/TIMEOUT) and a call with the same kind, "
        "operation and object is issued again under the same parent. Instance = the whole chain.",
        parameters={"min_attempts": 2},
    )
)
def detect_retry_loop(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    nxt: dict[str, tuple[str, dict[str, Any]]] = {}
    has_prev: set[str] = set()
    for r in relations:
        if r["type"] == "RETRIES":
            a, b = r["tail"][0][6:], r["head"][0][6:]
            nxt[a] = (b, r)
            has_prev.add(b)
    by_id = {e.event_id: e for e in events}
    out = []
    for start in nxt:
        if start in has_prev:
            continue
        chain, rels = [start], []
        cur = start
        while cur in nxt:
            cur, rel = nxt[cur]
            chain.append(cur)
            rels.append(rel)
        evs = [by_id[c] for c in chain if c in by_id]
        final = evs[-1].status if evs else EventStatus.UNKNOWN
        t0, t1 = _span(evs)
        out.append(
            MotifInstance(
                "M001",
                run_id,
                chain,
                confidence=min(r["confidence"] for r in rels),
                evidence=[r["rel_id"] for r in rels],
                explanation=f"{evs[0].operation}: {len(chain)} attempts, final status {final.value}",
                measures={
                    "attempts": len(chain),
                    "recovered": final == EventStatus.OK,
                    "operation": evs[0].operation,
                },
                t_start=t0,
                t_end=t1,
            )
        )
    return out


@REGISTRY.register(
    MotifDefinition(
        "M002",
        "repeated_tool_invocation",
        "1",
        "RULE",
        "The same tool (object) is invoked ≥3 times in one run with byte-identical arguments (same input artifact).",
        parameters={"min_repeats": 3},
    )
)
def detect_repeated_tool(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    groups: dict[tuple[str, str], list[ComputationalEvent]] = defaultdict(list)
    for e in events:
        if e.kind == EventKind.TOOL_INVOCATION and e.object and e.inputs:
            groups[(e.object.canonical, ",".join(sorted(a.artifact_id for a in e.inputs)))].append(
                e
            )
    out = []
    for (tool, args), evs in groups.items():
        if len(evs) >= 3:
            evs = event_order(evs)
            t0, t1 = _span(evs)
            statuses = Counter(e.status.value for e in evs)
            out.append(
                MotifInstance(
                    "M002",
                    run_id,
                    [e.event_id for e in evs],
                    artifacts=args.split(","),
                    explanation=f"{tool} called {len(evs)}× with identical arguments",
                    measures={"repeats": len(evs), "tool": tool, "statuses": dict(statuses)},
                    evidence=[e.event_id for e in evs],
                    t_start=t0,
                    t_end=t1,
                )
            )
    return out


@REGISTRY.register(
    MotifDefinition(
        "M003",
        "delegation_ping_pong",
        "1",
        "RULE",
        "In time order, DELEGATION/MESSAGE events form alternating hand-offs between the same two actors "
        "(A→B, B→A, A→B …) at least 3 times in succession.",
        parameters={"min_alternations": 3},
    )
)
def detect_ping_pong(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    hand = [
        e
        for e in event_order(events)
        if e.kind in (EventKind.DELEGATION, EventKind.MESSAGE) and e.actor and e.object
    ]
    out = []
    i = 0
    while i < len(hand) - 2:
        a, b = hand[i].actor.canonical, hand[i].object.canonical  # type: ignore[union-attr]
        j = i + 1
        expect = (b, a)
        chain = [hand[i]]
        while j < len(hand) and (hand[j].actor.canonical, hand[j].object.canonical) == expect:  # type: ignore[union-attr]
            chain.append(hand[j])
            expect = (expect[1], expect[0])
            j += 1
        if len(chain) >= 3:
            t0, t1 = _span(chain)
            out.append(
                MotifInstance(
                    "M003",
                    run_id,
                    [e.event_id for e in chain],
                    explanation=f"{a} ⇄ {b}: {len(chain)} alternating hand-offs",
                    measures={"alternations": len(chain), "actors": [a, b]},
                    evidence=[e.event_id for e in chain],
                    t_start=t0,
                    t_end=t1,
                )
            )
            i = j
        else:
            i += 1
    return out


@REGISTRY.register(
    MotifDefinition(
        "M004",
        "retrieval_echo",
        "2",
        "GRAPH_QUERY",
        "A RETRIEVAL returns content (an item, or the whole result) that was produced earlier in the same run by a "
        "MODEL_INVOCATION or written to memory — identical artifact or DERIVES_FROM containment ≥ threshold. The "
        "system is retrieving its own earlier output as if it were evidence.",
    )
)
def detect_retrieval_echo(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    by_id = {e.event_id: e for e in events}

    def producer(node: str) -> ComputationalEvent | None:
        eid = instance_event(node)
        return by_id.get(eid) if eid else None

    out = []
    seen: set[tuple[str, str]] = set()
    for r in sorted(relations, key=lambda r: r["rel_id"]):
        if r["type"] != "MATCHES_CONTENT":
            continue
        dst, src_node = producer(r["head"][0]), r["tail"][0]
        src = producer(src_node)
        if dst is None or src is None or dst.kind != EventKind.RETRIEVAL:
            continue
        own = src.kind == EventKind.MODEL_INVOCATION and "/o" in src_node
        memory = src.kind == EventKind.MEMORY_ACCESS and "write" in src.facets
        if not (own or memory) or (src.event_id, dst.event_id) in seen:
            continue
        seen.add((src.event_id, dst.event_id))
        conf = r["confidence"]
        basis = "identical" if conf >= 1.0 else "content_match"
        out.append(
            MotifInstance(
                "M004",
                run_id,
                [src.event_id, dst.event_id],
                artifacts=[r["attributes"].get("content_id") or r["head"][0]],
                confidence=conf,
                evidence=[r["rel_id"]],
                explanation=f"retrieval {dst.operation} returned content produced earlier by {src.kind.value} {src.operation} ({basis})",
                measures={"basis": basis},
                t_start=_span([src])[0],
                t_end=_span([dst])[1],
            )
        )
    return out


@REGISTRY.register(
    MotifDefinition(
        "M005",
        "context_expansion",
        "2",
        "GRAPH_QUERY",
        "≥3 successive MODEL_INVOCATIONs of the same model by the same actor in a run, where each call's input "
        "carries the previous call's input forward (identical artifact or a DERIVES_FROM content link) and the input "
        "size (tokens_in when declared, else input artifact bytes) strictly increases, with last/first ≥ 1.5. "
        "v2 (after held-out AWBench): v1 fired on independent actors calling one model.",
        parameters={"min_calls": 3, "min_growth": 1.5},
    )
)
def detect_context_expansion(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    by_model: dict[str, list[ComputationalEvent]] = defaultdict(list)
    for e in event_order(events):
        if e.kind == EventKind.MODEL_INVOCATION and e.object:
            actor = e.actor.canonical if e.actor else "(no actor)"
            by_model[f"{e.object.canonical} by {actor}"].append(e)
    # the next input contains the previous input's text: a resolved derivation or an ambiguous
    # candidate both establish that (M005 is about carried content, not which copy carried it)
    derives = {
        (r["tail"][0], r["head"][0])
        for r in relations
        if r["type"] in ("DERIVES_FROM", "CANDIDATE_SOURCE")
    }
    consumed: dict[str, set[str]] = defaultdict(set)
    for r in relations:
        if r["type"] == "CONSUMES" and r["head"][0].startswith("event:"):
            consumed[r["head"][0][6:]].add(r["tail"][0])

    def carried_forward(prev: ComputationalEvent, nxt: ComputationalEvent) -> bool:
        if {x.artifact_id for x in prev.inputs} & {x.artifact_id for x in nxt.inputs}:
            return True  # the identical value is passed again
        a, b = consumed.get(prev.event_id, set()), consumed.get(nxt.event_id, set())
        return bool(a & b) or any((x, y) in derives for x in a for y in b)

    def size(e: ComputationalEvent) -> tuple[float, str]:
        t = e.resources.get("tokens_in")
        if t:
            return float(t), "tokens_in"
        total = sum(
            data.artifacts[a.artifact_id].size_bytes
            for a in e.inputs
            if a.artifact_id in data.artifacts
        )
        return float(total), "input_bytes"

    out = []

    def flush(seq: list[tuple[ComputationalEvent, float]], model: str) -> None:
        if len(seq) >= 3 and seq[0][1] > 0 and seq[-1][1] / seq[0][1] >= 1.5:
            evs_ = [x[0] for x in seq]
            t0, t1 = _span(evs_)
            out.append(
                MotifInstance(
                    "M005",
                    run_id,
                    [x.event_id for x in evs_],
                    explanation=f"{model}: input grew {seq[0][1]:.0f}→{seq[-1][1]:.0f} over {len(evs_)} calls",
                    measures={
                        "sizes": [x[1] for x in seq],
                        "unit": size(evs_[0])[1],
                        "growth": round(seq[-1][1] / seq[0][1], 3),
                    },
                    evidence=[x.event_id for x in evs_],
                    t_start=t0,
                    t_end=t1,
                )
            )

    for model, evs in by_model.items():
        run_: list[tuple[ComputationalEvent, float]] = []
        for e in evs:
            s_, _ = size(e)
            if run_ and (s_ <= run_[-1][1] or not carried_forward(run_[-1][0], e)):
                flush(run_, model)
                run_ = []
            run_.append((e, s_))
        flush(run_, model)
    return out


@REGISTRY.register(
    MotifDefinition(
        "M006",
        "information_bottleneck",
        "2",
        "GRAPH_QUERY",
        "In the INFORMATION view, an intermediate artifact X lies on every lineage path from ≥2 distinct origin "
        "artifacts (retrieved items / external inputs) to a final output artifact: removing X disconnects all origins "
        "from the output. X is neither an origin nor the output.",
        parameters={"min_origins": 2},
    )
)
def detect_bottleneck(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    back = {"PRODUCES", "CONSUMES", "DERIVES_FROM", "CONTAINS_ITEM", "TRANSFERS"}
    inc: dict[str, list[str]] = defaultdict(list)
    for r in relations:
        if (
            r["view"] == "INFORMATION"
            and r["type"] in back
            and (r.get("attributes") or {}).get("resolution") != "AMBIGUOUS"
        ):
            for t in r["tail"]:
                for h in r["head"]:
                    if not t.startswith("entity:") and not h.startswith("entity:"):
                        inc[h].append(t)
    kinds = {e.event_id: e.kind for e in events}
    origins_kinds = (EventKind.RETRIEVAL, EventKind.EXTERNAL_INPUT)
    produced_by = {r["head"][0]: r["tail"][0][6:] for r in relations if r["type"] == "PRODUCES"}
    origin_nodes = {n for n, eid in produced_by.items() if kinds.get(eid) in origins_kinds}
    for r in relations:
        if r["type"] == "CONTAINS_ITEM" and r["tail"][0] in origin_nodes:
            origin_nodes.update(r["head"])
    finals = [
        f"inst:{e.event_id}/o{k}"
        for e in events
        if "artifact_creation" in e.facets
        for k in range(len(e.outputs))
    ]

    def ancestors(node: str, banned: str | None = None) -> set[str]:
        seen: set[str] = set()
        stack = [node]
        while stack:
            n = stack.pop()
            for p in inc.get(n, []):
                if p != banned and p not in seen:
                    seen.add(p)
                    stack.append(p)
        return seen

    out = []
    for final in dict.fromkeys(finals):
        anc = ancestors(final)
        origins = anc & origin_nodes
        if len(origins) < 2:
            continue
        for x in sorted(
            a for a in anc if a.startswith("inst:") and a not in origin_nodes and a != final
        ):
            if not (ancestors(final, banned=x) & origins):
                out.append(
                    MotifInstance(
                        "M006",
                        run_id,
                        [],
                        artifacts=[x, final],
                        evidence=[x, final],
                        explanation=f"all {len(origins)} origins reach the output only through value {x[5:17]}",
                        measures={"origins": len(origins)},
                    )
                )
    return out


@REGISTRY.register(
    MotifDefinition(
        "M007",
        "silent_strategy_change",
        "1",
        "STATISTICAL",
        "Split the run's leaf operations (tool/model/retrieval/external) at the time midpoint. If each half has ≥3 "
        "operations, the Jaccard similarity of the operation sets is < 0.34, and no FAILURE/ERROR event occurs in the "
        "run, the run switched strategy without an observed trigger. Heuristic; EXPERIMENTAL.",
        parameters={"min_ops_per_half": 3, "max_jaccard": 0.34},
    )
)
def detect_strategy_change(
    run_id: str,
    events: list[ComputationalEvent],
    relations: list[dict[str, Any]],
    data: AnalysisInput,
) -> list[MotifInstance]:
    leaf = [e for e in event_order(events) if e.kind in LEAF and e.time.start]
    if len(leaf) < 6 or any(
        e.status in (EventStatus.ERROR, EventStatus.TIMEOUT) or e.kind == EventKind.FAILURE
        for e in events
    ):
        return []
    mid = leaf[0].time.start + (leaf[-1].time.start - leaf[0].time.start) / 2  # type: ignore[operator]
    first = [e for e in leaf if e.time.start <= mid]  # type: ignore[operator]
    second = [e for e in leaf if e.time.start > mid]  # type: ignore[operator]
    if len(first) < 3 or len(second) < 3:
        return []
    s1 = {f"{e.kind.value}:{e.operation}" for e in first}
    s2 = {f"{e.kind.value}:{e.operation}" for e in second}
    jac = len(s1 & s2) / len(s1 | s2)
    if jac >= 0.34:
        return []
    t0, t1 = _span(leaf)
    return [
        MotifInstance(
            "M007",
            run_id,
            [e.event_id for e in leaf],
            confidence=round(1 - jac, 3),
            explanation=f"operation set changed (Jaccard {jac:.2f}) with no observed failure",
            measures={"jaccard": round(jac, 3), "before": sorted(s1), "after": sorted(s2)},
            t_start=t0,
            t_end=t1,
        )
    ]


class MotifAnalyzer(Analyzer):
    name = "motifs"
    version = "3"  # 2: M005 v2; 3: information instances (ADR-0017)
    maturity = Maturity.EXPERIMENTAL
    record_type = "motif_instance"

    def analyze(self, data: AnalysisInput) -> list[dict[str, Any]]:
        out = []
        for run_id in data.runs:
            evs = data.run_events(run_id)
            rels = data.run_relations(run_id)
            for inst in REGISTRY.detect(run_id, evs, rels, data):
                d = REGISTRY.definitions[inst.motif_id]
                key = f"{inst.motif_id}|{run_id}|{','.join(inst.events)}|{','.join(inst.artifacts)}"
                out.append(
                    self.record(
                        str(uuid.uuid5(MOTIF_NS, key)),
                        run_id,
                        {
                            "motif_id": inst.motif_id,
                            "motif_name": d.name,
                            "motif_version": d.version,
                            "run_id": run_id,
                            "events": inst.events,
                            "artifacts": inst.artifacts,
                            "confidence": inst.confidence,
                            "explanation": inst.explanation,
                            "evidence": inst.evidence,
                            "measures": inst.measures,
                            "t_start": inst.t_start,
                            "t_end": inst.t_end,
                        },
                    )
                )
        return out


def motif_stats(
    instances: list[dict[str, Any]], runs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Support and outcome association per motif (descriptive counts, no significance claims)."""
    run_status = {r["run_id"]: r.get("status") for r in runs}
    total = len(runs)
    by_motif: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    for m in instances:
        by_motif[m["motif_id"]].add(m["run_id"])
        counts[m["motif_id"]] += 1
    out = []
    for mid, d in sorted(REGISTRY.definitions.items()):
        with_runs = by_motif.get(mid, set())
        fail_with = sum(1 for r in with_runs if run_status.get(r) == "ERROR")
        without = [r for r in run_status if r not in with_runs]
        fail_without = sum(1 for r in without if run_status.get(r) == "ERROR")
        out.append(
            {
                **d.to_dict(),
                "instances": counts.get(mid, 0),
                "runs_with": len(with_runs),
                "support": round(len(with_runs) / total, 4) if total else 0.0,
                "error_rate_with": round(fail_with / len(with_runs), 4) if with_runs else None,
                "error_rate_without": round(fail_without / len(without), 4) if without else None,
                "n_with": len(with_runs),
                "n_without": len(without),
            }
        )
    return out
