"""Branching timelines and the (EXPERIMENTAL) counterfactual framework.

A branch forks a recorded run at an instrumented call, substitutes that call's result,
and re-executes the program with every other instrumented call mocked from captures
(L2) or run live (L3). The branch run is a real, observed run; comparing it with the
source gives *simulated* (not predicted) downstream consequences.

Counterfactual results always label their provenance:

* OBSERVED        — the value was recorded in the source run
* SIMULATED       — produced by re-execution of the branch
* MODEL_ESTIMATED — estimated from other recorded runs where the alternative occurred
* UNKNOWN         — neither re-execution nor comparable history is available
"""

from __future__ import annotations

import math
from typing import Any

from panoptis.compare.runs import signature
from panoptis.evidence.canonical import canonical_json
from panoptis.lab.replay import ReplayUnavailableError, replay
from panoptis.query.workspace import Workspace


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def create_branch(
    ws: Workspace,
    run_ref: str,
    at_event: str,
    substitution: Any,
    *,
    level: str = "L2",
    note: str | None = None,
) -> dict[str, Any]:
    run = ws.resolve_run(run_ref)
    ev = ws.event(at_event)
    if ev.get("run_id") != run["run_id"]:
        raise ValueError("branch event does not belong to the source run")
    key = ev["attributes"].get("call_key")
    if not key:
        raise ValueError(
            "branching requires an instrumented call (event has no call_key); use aw.tool/aw.model/aw.retriever"
        )
    if level not in ("L2", "L3"):
        raise ValueError("branches execute at L2 (mock) or L3 (partial re-execution)")
    parent_branch = (run.get("attributes") or {}).get("branch_id")
    doc = {
        "source_run": run["run_id"],
        "branch_event": ev["event_id"],
        "call_key": key,
        "substitution": substitution,
        "substitution_preview": canonical_json(substitution)[:200],
        "replay_level": level,
        "note": note,
        "parent_branch": parent_branch,
        "event_label": f"{ev['kind']} {ev['operation']}",
    }
    bid = ws.store.put_experiment(ws.tenant_id, "branch", doc, subject=run["run_id"])
    return {**doc, "branch_id": bid}


def execute_branch(
    ws: Workspace, branch_id: str, *, live: list[str] | None = None
) -> dict[str, Any]:
    br = ws.store.experiment(branch_id)
    if br is None or br.get("record_type") != "branch":
        raise LookupError(f"branch {branch_id} not found")
    result = replay(
        ws,
        br["source_run"],
        br["replay_level"],
        substitutions={br["call_key"]: br["substitution"]},
        live=live,
        branch_id=branch_id,
    )
    fresh = Workspace(ws.engine, ws.tenant_id)
    comparison = result.get("comparison")
    source = fresh.resolve_run(br["source_run"])
    branch_run = fresh.resolve_run(result["replay_run"]) if result.get("replay_run") else None
    outcome_changed = None
    final_changed = None
    if branch_run:
        outcome_changed = branch_run.get("status") != source.get("status")
        final_changed = _final_outputs(fresh, source["run_id"]) != _final_outputs(
            fresh, branch_run["run_id"]
        )
    doc = {
        "branch_id": branch_id,
        "source_run": br["source_run"],
        "branch_run": branch_run["run_id"] if branch_run else None,
        "branch_event": br["branch_event"],
        "call_key": br["call_key"],
        "substitution_preview": br["substitution_preview"],
        "replay_level": br["replay_level"],
        "label": "SIMULATED",
        "outcome": {
            "source": source.get("status"),
            "branch": branch_run.get("status") if branch_run else None,
        },
        "outcome_changed": outcome_changed,
        "final_outputs_changed": final_changed,
        "divergence": (comparison or {}).get("earliest_divergence"),
        "reproduction_confidence": result["reproduction_confidence"]["value"],
        "mocked_components": result["mocked_components"],
        "live_components": result["live_components"],
        "missing_dependencies": result["missing_dependencies"],
        "stale_captures": result.get("stale_captures", []),
        "exit_code": result.get("exit_code"),
        "replay_record": result.get("record_id"),
    }
    rid = ws.store.put_experiment(ws.tenant_id, "branch_result", doc, subject=br["source_run"])
    return {**doc, "record_id": rid}


def _final_outputs(ws: Workspace, run_id: str) -> list[str]:
    return sorted(
        a["artifact_id"]
        for e in ws.events(run_id)
        if "artifact_creation" in e["facets"]
        for a in e["outputs"]
    )


def branch_history(ws: Workspace, run_ref: str) -> dict[str, Any]:
    run = ws.resolve_run(run_ref)
    branches = ws.store.experiments(ws.tenant_id, "branch", subject=run["run_id"])
    results = {
        r["branch_id"]: r
        for r in ws.store.experiments(ws.tenant_id, "branch_result", subject=run["run_id"])
    }
    return {
        "run_id": run["run_id"],
        "branches": [
            {**b, "branch_id": b["record_id"], "result": results.get(b["record_id"])}
            for b in branches
        ],
    }


def counterfactual(
    ws: Workspace,
    run_ref: str,
    at_event: str,
    alternative: Any,
    *,
    execute: bool = True,
    level: str = "L3",
) -> dict[str, Any]:
    """What might have happened if ``at_event`` had produced ``alternative``?"""
    run = ws.resolve_run(run_ref)
    ev = ws.event(at_event)
    base = {
        "question": f"What if {ev['kind']} {ev['operation']} had returned the alternative?",
        "source_run": run["run_id"],
        "event": ev["event_id"],
        "observed": {
            "label": "OBSERVED",
            "run_status": run.get("status"),
            "event_status": ev["status"],
        },
        "maturity": "EXPERIMENTAL",
    }
    if (
        execute
        and ev["attributes"].get("call_key")
        and (run.get("attributes") or {}).get("command")
    ):
        try:
            br = create_branch(
                ws, run["run_id"], ev["event_id"], alternative, level=level, note="counterfactual"
            )
            res = execute_branch(ws, br["branch_id"])
            return {
                **base,
                "method": f"re-execution ({level})",
                "estimate": {
                    "label": "SIMULATED",
                    "run_status": res["outcome"]["branch"],
                    "outcome_changed": res["outcome_changed"],
                    "final_outputs_changed": res["final_outputs_changed"],
                    "divergence": res["divergence"],
                    "uncertainty": {
                        "reproduction_confidence": res["reproduction_confidence"],
                        "live_components": res["live_components"],
                        "missing_dependencies": res["missing_dependencies"],
                        "note": "single re-execution; live components (program logic, clocks, networks) may vary between executions",
                    },
                },
                "branch_id": br["branch_id"],
                "experiment_id": res["record_id"],
            }
        except ReplayUnavailableError as exc:
            base["reexecution_unavailable"] = str(exc)
    return {**base, **_estimate_from_history(ws, run, ev, alternative)}


def _estimate_from_history(
    ws: Workspace, run: dict[str, Any], ev: dict[str, Any], alternative: Any
) -> dict[str, Any]:
    alt_json = canonical_json(alternative)
    sig = signature(ev)
    matches = []
    for r in ws.runs():
        if r["run_id"] == run["run_id"] or r.get("name") != run.get("name"):
            continue
        for e in ws.events(r["run_id"]):
            if signature(e) != sig:
                continue
            for o in e["outputs"]:
                art = ws.store.artifact(ws.tenant_id, o["artifact_id"])
                if art and canonical_json(art["content"]) == alt_json:
                    matches.append(r)
                    break
            else:
                continue
            break
    n = len(matches)
    if n < 3:
        return {
            "method": "historical comparison",
            "estimate": {
                "label": "UNKNOWN",
                "reason": f"only {n} recorded runs where this operation produced the alternative (need ≥3)",
                "supporting_runs": [m["run_id"] for m in matches],
            },
        }
    errors = sum(1 for m in matches if m.get("status") == "ERROR")
    return {
        "method": "historical comparison (runs of the same program where the alternative actually occurred)",
        "estimate": {
            "label": "MODEL_ESTIMATED",
            "outcome_distribution": {
                "ERROR": round(errors / n, 4),
                "not ERROR": round(1 - errors / n, 4),
            },
            "error_rate_ci95": _wilson(errors, n),
            "supporting_runs": [m["run_id"] for m in matches],
            "n": n,
            "caveat": "runs differ in other respects too; this is an association across runs, not an intervention",
        },
    }
