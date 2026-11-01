"""``causes`` / ``effects`` for an event: dependency, correlation, hypotheses and
interventions reported in separate, labelled sections."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from panoptis.causality.hypotheses import list_hypotheses
from panoptis.compare.runs import signature
from panoptis.query.workspace import Workspace

DEP_TYPES = [
    "CONTAINS",
    "DEPENDS_ON",
    "RESPONDS_TO",
    "RETRIES",
    "PRODUCES",
    "CONSUMES",
    "DERIVES_FROM",
    "CONTAINS_ITEM",
    "TRANSFERS",
]
MIN_CORR_RUNS = 5


def _cone(ws: Workspace, event_id: str, direction: str, max_depth: int) -> list[dict[str, Any]]:
    ev = ws.event(event_id)
    g = ws.graph(ev.get("run_id"), views=["EXECUTION", "INFORMATION"])
    node = f"event:{ev['event_id']}"
    walk = g.ancestors if direction == "up" else g.descendants
    steps = list(walk(node, types=DEP_TYPES, max_depth=max_depth, skip_kinds=["entity"]))
    if direction == "up":
        # the content an event produced may derive from earlier content even when the
        # event declared no inputs: walk back from its outputs too
        seen = {s.node for s in steps} | {node}
        for k in range(len(ev["outputs"])):
            for s in g.ancestors(
                f"inst:{ev['event_id']}/o{k}",
                types=DEP_TYPES,
                max_depth=max_depth,
                skip_kinds=["entity"],
            ):
                if s.node not in seen:
                    seen.add(s.node)
                    steps.append(s)
    out = []
    for s in steps:
        if not s.node.startswith("event:") or s.node == node:
            continue
        rel = g.relations[s.via] if s.via else {}
        out.append(
            {
                **ws.describe_node(s.node),
                "depth": s.depth,
                "via": s.rel_type,
                "basis": rel.get("basis"),
                "confidence": rel.get("confidence"),
                "evidence_class": "OBSERVATIONAL",
                "claim": "dependency: in the control/data path; not shown to be necessary",
            }
        )
    return out


def _correlations(
    ws: Workspace, ev: dict[str, Any], upstream: list[dict[str, Any]]
) -> dict[str, Any]:
    run = ws.resolve_run(ev["run_id"]) if ev.get("run_id") else None
    if run is None:
        return {"status": "not_applicable", "reason": "event has no run"}
    peers = [r for r in ws.runs() if r.get("name") == run.get("name")]
    target_sig = signature(ev)
    presence: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"with": set(), "without": set()}
    )
    outcome: dict[str, bool] = {}
    up_sigs = {signature(ws.event(u["node"][6:])) for u in upstream}
    for r in peers:
        evs = ws.events(r["run_id"])
        targets = [e for e in evs if signature(e) == target_sig]
        if not targets:
            continue
        outcome[r["run_id"]] = any(e["status"] in ("ERROR", "TIMEOUT") for e in targets)
        sigs = {signature(e) for e in evs}
        for s in up_sigs:
            presence[s]["with" if s in sigs else "without"].add(r["run_id"])
    if len(outcome) < MIN_CORR_RUNS:
        return {
            "status": "insufficient_data",
            "runs_with_this_operation": len(outcome),
            "needed": MIN_CORR_RUNS,
        }
    rows = []
    for s, p in presence.items():
        w, wo = p["with"], p["without"]
        if not w or not wo:
            continue
        fw = sum(outcome[r] for r in w) / len(w)
        fwo = sum(outcome[r] for r in wo) / len(wo)
        rows.append(
            {
                "upstream_signature": s,
                "failure_rate_with": round(fw, 3),
                "failure_rate_without": round(fwo, 3),
                "n_with": len(w),
                "n_without": len(wo),
                "difference": round(fw - fwo, 3),
                "evidence_class": "CORRELATIONAL",
            }
        )
    rows.sort(key=lambda r: -abs(float(r["difference"])))  # type: ignore[arg-type]
    return {
        "status": "computed",
        "outcome": f"{target_sig} ends in ERROR/TIMEOUT",
        "peer_runs": len(outcome),
        "associations": rows[:20],
        "claim": "co-occurrence across runs of the same name; confounding is not controlled",
    }


def _interventions(
    ws: Workspace, ev: dict[str, Any], upstream_ids: set[str], role: str
) -> list[dict[str, Any]]:
    out = []
    for b in ws.store.experiments(ws.tenant_id, "branch_result", subject=ev.get("run_id")):
        fork = b.get("branch_event")
        if role == "cause" and fork not in upstream_ids and fork != ev["event_id"]:
            continue
        if role == "effect" and fork != ev["event_id"]:
            continue
        out.append(
            {
                "branch_id": b.get("branch_id"),
                "experiment_id": b.get("record_id"),
                "fork_event": fork,
                "substitution": b.get("substitution_preview"),
                "outcome_changed": b.get("outcome_changed"),
                "divergence": b.get("divergence"),
                "reproduction_confidence": b.get("reproduction_confidence"),
                "evidence_class": "INTERVENTIONAL",
                "label": b.get("label", "SIMULATED"),
            }
        )
    return out


def causes(ws: Workspace, event_ref: str, *, max_depth: int = 25) -> dict[str, Any]:
    ev = ws.event(event_ref)
    upstream = _cone(ws, ev["event_id"], "up", max_depth)
    ids = {u["node"][6:] for u in upstream}
    node = f"event:{ev['event_id']}"
    return {
        "event": ws.describe_node(node),
        "dependencies": upstream,
        "correlations": _correlations(ws, ev, upstream),
        "hypotheses": [h for h in list_hypotheses(ws) if h["effect"] in (node, signature(ev))],
        "interventions": _interventions(ws, ev, ids, "cause"),
        "legend": {
            "dependencies": "OBSERVATIONAL — structurally upstream; influence possible, not established",
            "correlations": "CORRELATIONAL — cross-run association; not causal",
            "hypotheses": "claims with computed evidence class; PROPOSED until interventions support them",
            "interventions": "INTERVENTIONAL — a recorded branch changed an upstream event and the outcome was observed",
        },
    }


def effects(ws: Workspace, event_ref: str, *, max_depth: int = 25) -> dict[str, Any]:
    ev = ws.event(event_ref)
    downstream = _cone(ws, ev["event_id"], "down", max_depth)
    node = f"event:{ev['event_id']}"
    return {
        "event": ws.describe_node(node),
        "dependents": downstream,
        "hypotheses": [h for h in list_hypotheses(ws) if h["cause"] in (node, signature(ev))],
        "interventions": _interventions(ws, ev, set(), "effect"),
        "legend": {
            "dependents": "OBSERVATIONAL — structurally downstream (descendant cone)",
            "interventions": "INTERVENTIONAL — branches forked at this event and their observed outcomes",
        },
    }
