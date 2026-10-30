"""Run reconstruction, summaries and comparison.

Comparison aligns two runs' events by *signature* (kind, operation, object) in
occurrence order (difflib's longest-matching-block alignment), then reports:

* the earliest divergence: first structural mismatch, or the first aligned pair whose
  status or output content differs;
* structural diff: signature multiset and parent→child edge differences;
* resource diff: tokens, cost, latency, errors, retries;
* information-flow diff: content-derivation links, memory transfers, artifacts;
* the share of the second run's events and latency inside the divergence point's
  descendant cone (a *dependency* measure, not a causal attribution).
"""

from __future__ import annotations

import difflib
from collections import Counter
from collections.abc import Sequence
from typing import Any

from panoptis.graph.traverse import Graph
from panoptis.query.workspace import Workspace


def signature(e: dict[str, Any]) -> str:
    return f"{e['kind']}|{e['operation']}|{e.get('object') or ''}"


def _ordered(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda e: (
            e["time"]["start"] or "~",
            e["time"].get("ordering_key") or "",
            e["event_id"],
        ),
    )


def _latency(e: dict[str, Any]) -> float:
    return float(e["time"].get("duration_ms") or e["resources"].get("latency_ms") or 0.0)


def summarize(ws: Workspace, run_ref: str) -> dict[str, Any]:
    run = ws.resolve_run(run_ref)
    events = _ordered(ws.events(run["run_id"]))
    g = ws.graph(run["run_id"])
    stats = g.stats({f"event:{e['event_id']}" for e in events})
    actors = Counter(e["actor"] for e in events if e.get("actor"))
    tools = Counter(
        e["object"] for e in events if e["kind"] == "TOOL_INVOCATION" and e.get("object")
    )
    models = Counter(
        e["object"] for e in events if e["kind"] == "MODEL_INVOCATION" and e.get("object")
    )
    failures = [e for e in events if e["status"] in ("ERROR", "TIMEOUT")]
    retries = sum(1 for r in g.relations.values() if r["type"] == "RETRIES")
    missing = Counter(m for e in events for m in e["missing"])
    motifs = ws.derived("motif_instance", run["run_id"])
    return {
        "run": run,
        "events": events,
        "graph": stats,
        "actors": dict(actors),
        "tools": dict(tools),
        "models": dict(models),
        "failures": [
            {
                "event_id": e["event_id"],
                "label": f"{e['kind']} {e['operation']}",
                "error": e.get("error"),
            }
            for e in failures
        ],
        "retries": retries,
        "missing_facts": dict(missing),
        "information": {
            "artifacts": len({a["artifact_id"] for e in events for a in e["outputs"]}),
            "content_derivations": sum(
                1 for r in g.relations.values() if r["type"] == "DERIVES_FROM"
            ),
            "memory_transfers": sum(1 for r in g.relations.values() if r["type"] == "TRANSFERS"),
        },
        "motifs": Counter(m["motif_id"] for m in motifs),
    }


def tree(
    ws: Workspace, run_id: str, events: list[dict[str, Any]] | None = None
) -> list[tuple[int, dict[str, Any]]]:
    """Depth-first CONTAINS tree (multi-parent events appear under each declared parent)."""
    events = events if events is not None else _ordered(ws.events(run_id))
    g = ws.graph(run_id, views=["EXECUTION"])
    by_node = {f"event:{e['event_id']}": e for e in events}
    order = {n: i for i, n in enumerate(by_node)}
    roots = [n for n in by_node if not [p for p in g.parents(n) if p in by_node]]
    out: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()

    def walk(n: str, d: int) -> None:
        out.append((d, by_node[n]))
        if n in seen:
            return
        seen.add(n)
        for c in sorted((c for c in g.children(n) if c in by_node), key=lambda c: order[c]):
            walk(c, d + 1)

    for r in sorted(roots, key=lambda n: order[n]):
        walk(r, 0)
    return out


def _right_shift_gaps(
    ops: Sequence[tuple[str, int, int, int, int]], ka: list[str], kb: list[str]
) -> list[tuple[str, int, int, int, int]]:
    """Normalize insertions/deletions to the right past identical elements.

    With repeated identical steps (retries) every placement of the extra steps is an equally
    minimal alignment; the earliest *divergence* is where the shared prefix ends, so a gap is
    shifted right while the element after it equals the gap's first element."""
    out = list(ops)
    for n, (tag, i1, i2, j1, j2) in enumerate(out):
        if tag == "insert":
            while j2 < len(kb) and i1 < len(ka) and kb[j1] == kb[j2] == ka[i1]:
                i1, i2, j1, j2 = i1 + 1, i2 + 1, j1 + 1, j2 + 1
            out[n] = (tag, i1, i2, j1, j2)
        elif tag == "delete":
            while i2 < len(ka) and j1 < len(kb) and ka[i1] == ka[i2] == kb[j1]:
                i1, i2, j1, j2 = i1 + 1, i2 + 1, j1 + 1, j2 + 1
            out[n] = (tag, i1, i2, j1, j2)
    return out


def _same_step(ea: list[dict[str, Any]], eb: list[dict[str, Any]], i: int, j: int) -> bool:
    return i < len(ea) and j < len(eb) and signature(ea[i]) == signature(eb[j])


def _content_divergence(x: dict[str, Any], y: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if x["status"] != y["status"]:
        reasons.append(f"status {x['status']} → {y['status']}")
    if sorted(o["artifact_id"] for o in x["outputs"]) != sorted(
        o["artifact_id"] for o in y["outputs"]
    ):
        reasons.append("different output content")
    if sorted(o["artifact_id"] for o in x["inputs"]) != sorted(
        o["artifact_id"] for o in y["inputs"]
    ):
        reasons.append("different input content")
    return {
        "type": "content",
        "operation": "equal-signature",
        "a_event": _brief(x),
        "b_event": _brief(y),
        "reasons": reasons,
    }


def compare(ws: Workspace, ref_a: str, ref_b: str) -> dict[str, Any]:
    a, b = summarize(ws, ref_a), summarize(ws, ref_b)
    ea, eb = a["events"], b["events"]
    sa, sb = [signature(e) for e in ea], [signature(e) for e in eb]
    sm = difflib.SequenceMatcher(a=sa, b=sb, autojunk=False)
    # the earliest divergence is located on (signature, status): with repeated identical calls
    # (retries), aligning on the signature alone lets extra attempts be 'inserted' before the
    # first one, blaming an attempt that behaved identically in both runs
    ka = [f"{s}|{e['status']}" for s, e in zip(sa, ea, strict=True)]
    kb = [f"{s}|{e['status']}" for s, e in zip(sb, eb, strict=True)]
    ops = _right_shift_gaps(
        difflib.SequenceMatcher(a=ka, b=kb, autojunk=False).get_opcodes(), ka, kb
    )
    divergence: dict[str, Any] | None = None
    aligned: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            aligned.extend(zip(range(i1, i2), range(j1, j2), strict=True))
    first_struct = next(((i1, j1, tag) for tag, i1, i2, j1, j2 in ops if tag != "equal"), None)
    first_content = None
    for i, j in aligned:
        x, y = ea[i], eb[j]
        reasons = []
        if x["status"] != y["status"]:
            reasons.append(f"status {x['status']} → {y['status']}")
        ox = sorted(o["artifact_id"] for o in x["outputs"])
        oy = sorted(o["artifact_id"] for o in y["outputs"])
        if ox != oy:
            reasons.append("different output content")
        ix = sorted(o["artifact_id"] for o in x["inputs"])
        iy = sorted(o["artifact_id"] for o in y["inputs"])
        if ix != iy:
            reasons.append("different input content")
        if reasons:
            first_content = (i, j, reasons)
            break
    candidates = []
    if first_struct and _same_step(ea, eb, first_struct[0], first_struct[1]):
        # the diverging step exists in both runs and only its status/content differs
        i, j, _tag = first_struct
        candidates.append((j, _content_divergence(ea[i], eb[j])))
        first_struct = None
    if first_struct:
        i, j, tag = first_struct
        candidates.append(
            (
                j,
                {
                    "type": "structural",
                    "operation": tag,
                    "a_event": _brief(ea[i]) if i < len(ea) else None,
                    "b_event": _brief(eb[j]) if j < len(eb) else None,
                    "reasons": [
                        f"{tag}: run A has {_brief(ea[i])['label'] if i < len(ea) else 'nothing'} where run B has {_brief(eb[j])['label'] if j < len(eb) else 'nothing'}"
                    ],
                },
            )
        )
    if first_content:
        i, j, reasons = first_content
        candidates.append(
            (
                j,
                {
                    "type": "content",
                    "operation": "equal-signature",
                    "a_event": _brief(ea[i]),
                    "b_event": _brief(eb[j]),
                    "reasons": reasons,
                },
            )
        )
    if candidates:
        divergence = min(candidates, key=lambda c: c[0])[1]
        b_event = divergence.get("b_event")
        divergence["cone"] = _cone_share(
            ws, b["run"]["run_id"], b_event if isinstance(b_event, dict) else None, eb, ea
        )

    ca, cb = Counter(sa), Counter(sb)
    structural = {
        "only_in_a": _counter_diff(ca, cb),
        "only_in_b": _counter_diff(cb, ca),
        "edges_only_in_a": _edge_diff(ws, a, b),
        "edges_only_in_b": _edge_diff(ws, b, a),
        "alignment_similarity": round(sm.ratio(), 4),
        "graph_a": a["graph"],
        "graph_b": b["graph"],
    }
    resources = _resources(a, b)
    information = {
        k: {
            "a": a["information"][k],
            "b": b["information"][k],
            "delta": b["information"][k] - a["information"][k],
        }
        for k in a["information"]
    }
    motifs = {
        m: {"a": a["motifs"].get(m, 0), "b": b["motifs"].get(m, 0)}
        for m in sorted(set(a["motifs"]) | set(b["motifs"]))
    }
    return {
        "run_a": _run_brief(a),
        "run_b": _run_brief(b),
        "identical_structure": not first_struct,
        "earliest_divergence": divergence,
        "structural": structural,
        "resources": resources,
        "information": information,
        "motifs": motifs,
        "fingerprint": {
            "a": a["run"]["fingerprint"],
            "b": b["run"]["fingerprint"],
            "same_system_version": a["run"]["system_version"] == b["run"]["system_version"],
        },
        "method": "signature alignment (difflib) over time-ordered events; cone share uses EXECUTION+INFORMATION descendants",
    }


def _brief(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": e["event_id"],
        "label": f"{e['kind']} {e['operation']}",
        "actor": e.get("actor"),
        "status": e["status"],
        "start": e["time"]["start"],
    }


def _run_brief(s: dict[str, Any]) -> dict[str, Any]:
    r = s["run"]
    return {
        k: r.get(k)
        for k in (
            "run_id",
            "name",
            "status",
            "started_at",
            "duration_ms",
            "event_count",
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "system_version",
        )
    }


def _counter_diff(x: Counter[str], y: Counter[str]) -> list[dict[str, Any]]:
    return [{"signature": k, "count": v} for k, v in sorted((x - y).items())]


def _edge_diff(ws: Workspace, s1: dict[str, Any], s2: dict[str, Any]) -> list[str]:
    def edges(s: dict[str, Any]) -> Counter[str]:
        g = ws.graph(s["run"]["run_id"], views=["EXECUTION"])
        by = {f"event:{e['event_id']}": signature(e) for e in s["events"]}
        c: Counter[str] = Counter()
        for r in g.relations.values():
            if r["type"] in ("CONTAINS", "DEPENDS_ON"):
                for t in r["tail"]:
                    for h in r["head"]:
                        if t in by and h in by:
                            c[f"{by[t]} -{r['type']}-> {by[h]}"] += 1
        return c

    return sorted((edges(s1) - edges(s2)).elements())[:100]


def _resources(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def agg(s: dict[str, Any]) -> dict[str, float]:
        evs = s["events"]
        leaf = [e for e in evs if e["kind"] not in ("OPERATION", "LIFECYCLE")]
        return {
            "duration_ms": float(s["run"].get("duration_ms") or 0.0),
            "tokens_in": float(s["run"].get("tokens_in") or 0),
            "tokens_out": float(s["run"].get("tokens_out") or 0),
            "cost_usd": float(s["run"].get("cost_usd") or 0.0),
            "events": float(len(evs)),
            "errors": float(len(s["failures"])),
            "retries": float(s["retries"]),
            "model_calls": float(sum(1 for e in evs if e["kind"] == "MODEL_INVOCATION")),
            "tool_calls": float(sum(1 for e in evs if e["kind"] == "TOOL_INVOCATION")),
            "leaf_latency_ms": sum(_latency(e) for e in leaf),
        }

    ra, rb = agg(a), agg(b)
    return {
        k: {
            "a": round(ra[k], 3),
            "b": round(rb[k], 3),
            "delta": round(rb[k] - ra[k], 3),
            "ratio": round(rb[k] / ra[k], 4) if ra[k] else None,
        }
        for k in ra
    }


def _cone_share(
    ws: Workspace,
    run_b: str,
    b_event: dict[str, Any] | None,
    eb: list[dict[str, Any]],
    ea: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not b_event:
        return None
    g: Graph = ws.graph(run_b, views=["EXECUTION", "INFORMATION"])
    start = f"event:{b_event['event_id']}"
    desc = {
        s.node
        for s in g.descendants(
            start,
            types=[
                "CONTAINS",
                "DEPENDS_ON",
                "RESPONDS_TO",
                "RETRIES",
                "PRODUCES",
                "CONSUMES",
                "DERIVES_FROM",
                "CONTAINS_ITEM",
                "TRANSFERS",
            ],
        )
    }
    desc.add(start)
    in_cone = [e for e in eb if f"event:{e['event_id']}" in desc]
    leaf = lambda e: e["kind"] not in ("OPERATION", "LIFECYCLE")  # noqa: E731
    lat_b = sum(_latency(e) for e in eb if leaf(e))
    lat_cone = sum(_latency(e) for e in in_cone if leaf(e))
    tok_b = sum(
        float(e["resources"].get("tokens_in") or 0) + float(e["resources"].get("tokens_out") or 0)
        for e in eb
    )
    tok_cone = sum(
        float(e["resources"].get("tokens_in") or 0) + float(e["resources"].get("tokens_out") or 0)
        for e in in_cone
    )
    return {
        "events_in_cone": len(in_cone),
        "events_total": len(eb),
        "latency_share": round(lat_cone / lat_b, 4) if lat_b else None,
        "token_share": round(tok_cone / tok_b, 4) if tok_b else None,
        "interpretation": "share of run B inside the divergence point's dependency cone; dependency, not causation",
    }
