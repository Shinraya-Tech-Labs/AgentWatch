"""Trajectory forecasting framework (EXPERIMENTAL).

Dataset: for every finished run with a declared outcome, prefixes at fixed fractions of
its leaf operations, each described by features observable *so far*. Forecast: k-nearest
neighbours among prefixes (same fraction) of *other* runs; the outcome distribution of
the neighbours with Laplace smoothing. Evaluation: leave-one-run-out Brier score, ECE and
the base-rate Brier for comparison. Nothing is forecast when data is insufficient.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import mean, pstdev
from typing import Any

from panoptis.query.workspace import Workspace

LEAF = {"TOOL_INVOCATION", "MODEL_INVOCATION", "RETRIEVAL", "EXTERNAL_IO", "MEMORY_ACCESS"}
FRACTIONS = (0.25, 0.5, 0.75)
FEATURES = [
    "ops",
    "errors",
    "error_share",
    "repeat_share",
    "retrievals",
    "model_calls",
    "tool_calls",
    "elapsed_s",
]
MIN_RUNS = 10


def prefix_features(events: list[dict[str, Any]], fraction: float) -> dict[str, float]:
    leaf = [e for e in events if e["kind"] in LEAF]
    n = max(1, math.ceil(len(leaf) * fraction)) if leaf else 0
    pre = leaf[:n]
    ops = [f"{e['kind']}:{e['operation']}" for e in pre]
    starts = [e["time"]["start"] for e in pre if e["time"]["start"]]
    from panoptis.evidence.model import parse_ts

    elapsed = (
        (parse_ts(max(starts)) - parse_ts(min(starts))).total_seconds() if len(starts) > 1 else 0.0
    )
    errors = sum(1 for e in pre if e["status"] in ("ERROR", "TIMEOUT"))
    return {
        "ops": float(len(pre)),
        "errors": float(errors),
        "error_share": errors / len(pre) if pre else 0.0,
        "repeat_share": 1 - len(set(ops)) / len(ops) if ops else 0.0,
        "retrievals": float(sum(1 for e in pre if e["kind"] == "RETRIEVAL")),
        "model_calls": float(sum(1 for e in pre if e["kind"] == "MODEL_INVOCATION")),
        "tool_calls": float(sum(1 for e in pre if e["kind"] == "TOOL_INVOCATION")),
        "elapsed_s": elapsed,
    }


def dataset(ws: Workspace, name: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for r in ws.runs():
        if r.get("status") not in ("OK", "ERROR") or (name and r.get("name") != name):
            continue
        evs = ws.events(r["run_id"])
        for f in FRACTIONS:
            rows.append(
                {
                    "run_id": r["run_id"],
                    "name": r.get("name"),
                    "fraction": f,
                    "outcome": r["status"],
                    "x": prefix_features(evs, f),
                }
            )
    return rows


def _knn(
    query: dict[str, float], pool: list[dict[str, Any]], k: int
) -> list[tuple[float, dict[str, Any]]]:
    scale = {f: (pstdev([p["x"][f] for p in pool]) or 1.0) for f in FEATURES}
    dist = [
        (math.sqrt(sum(((query[f] - p["x"][f]) / scale[f]) ** 2 for f in FEATURES)), p)
        for p in pool
    ]
    dist.sort(key=lambda t: (t[0], t[1]["run_id"]))
    return dist[:k]


def _predict(
    query: dict[str, float], pool: list[dict[str, Any]], outcomes: list[str], k: int
) -> dict[str, float]:
    nb = _knn(query, pool, k)
    c = Counter(p["outcome"] for _, p in nb)
    total = len(nb) + len(outcomes)
    return {o: round((c.get(o, 0) + 1) / total, 4) for o in outcomes}


def evaluate(ws: Workspace, name: str | None = None, k: int = 7) -> dict[str, Any]:
    rows = dataset(ws, name)
    runs = sorted({r["run_id"] for r in rows})
    outcomes = ["OK", "ERROR"]
    if len(runs) < MIN_RUNS:
        return {
            "status": "insufficient_data",
            "runs": len(runs),
            "needed": MIN_RUNS,
            "maturity": "EXPERIMENTAL",
        }
    per_fraction = {}
    for f in FRACTIONS:
        preds, labels = [], []
        for rid in runs:
            test = [r for r in rows if r["run_id"] == rid and r["fraction"] == f]
            pool = [r for r in rows if r["run_id"] != rid and r["fraction"] == f]
            for t in test:
                preds.append(_predict(t["x"], pool, outcomes, min(k, len(pool)))["ERROR"])
                labels.append(1.0 if t["outcome"] == "ERROR" else 0.0)
        base = mean(labels)
        brier = mean((p - y) ** 2 for p, y in zip(preds, labels, strict=True))
        base_brier = mean((base - y) ** 2 for y in labels)
        per_fraction[str(f)] = {
            "n": len(labels),
            "brier": round(brier, 4),
            "base_rate_brier": round(base_brier, 4),
            "ece": round(_ece(preds, labels), 4),
            "error_base_rate": round(base, 4),
            "skill_vs_base_rate": round(1 - brier / base_brier, 4) if base_brier else None,
        }
    return {
        "status": "evaluated",
        "method": "leave-one-run-out kNN",
        "k": k,
        "runs": len(runs),
        "by_prefix_fraction": per_fraction,
        "maturity": "EXPERIMENTAL",
    }


def _ece(preds: list[float], labels: list[float], bins: int = 5) -> float:
    total = len(preds)
    err = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(preds) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if idx:
            err += (
                len(idx) / total * abs(mean(preds[i] for i in idx) - mean(labels[i] for i in idx))
            )
    return err


def forecast(ws: Workspace, run_ref: str, *, k: int = 7) -> dict[str, Any]:
    run = ws.resolve_run(run_ref)
    evs = ws.events(run["run_id"])
    leaf = [e for e in evs if e["kind"] in LEAF]
    rows = [r for r in dataset(ws, run.get("name")) if r["run_id"] != run["run_id"]]
    runs = {r["run_id"] for r in rows}
    if len(runs) < MIN_RUNS:
        return {
            "status": "insufficient_data",
            "run_id": run["run_id"],
            "comparable_runs": len(runs),
            "needed": MIN_RUNS,
            "message": "not enough finished runs of this program to forecast",
            "maturity": "EXPERIMENTAL",
        }
    # the current run is treated as a prefix: pick the fraction bucket closest to its progress
    typical = (
        mean(sum(1 for e in ws.events(rid) if e["kind"] in LEAF) for rid in list(runs)[:50]) or 1
    )
    progress = min(0.75, max(0.25, len(leaf) / typical))
    frac = min(FRACTIONS, key=lambda f: abs(f - progress))
    q = prefix_features(evs, 1.0)
    pool = [r for r in rows if r["fraction"] == frac]
    nb = _knn(q, pool, min(k, len(pool)))
    probs = _predict(q, pool, ["OK", "ERROR"], min(k, len(pool)))
    ev = evaluate(ws, run.get("name"), k)
    return {
        "status": "forecast",
        "run_id": run["run_id"],
        "observed_so_far": q,
        "compared_at_prefix_fraction": frac,
        "outcomes": probs,
        "uncertainty": {
            "neighbours": len(nb),
            "smoothing": "Laplace",
            "note": "probabilities are neighbour frequencies, calibrated only as far as the evaluation below shows",
        },
        "supporting_trajectories": [
            {"run_id": p["run_id"], "outcome": p["outcome"], "distance": round(d, 4)} for d, p in nb
        ],
        "calibration": ev.get("by_prefix_fraction", {}).get(str(frac))
        if ev.get("status") == "evaluated"
        else ev,
        "maturity": "EXPERIMENTAL",
    }
