"""Interpretable latent-state baseline (EXPERIMENTAL).

1. Each run's leaf operations are cut into fixed windows of ``window`` events.
2. Each window gets a small, named feature vector (error share, repeat share, novelty,
   input growth, model/tool/retrieval mix).
3. Change points: a window whose standardized distance to the previous window exceeds
   mean + 2·sd of all consecutive distances.
4. States: k-means over standardized windows of *all* runs (k chosen by silhouette over
   2..max_k, fixed seed). A state's name is derived from its centroid's most extreme
   features, so the vocabulary comes from the data rather than being hard-coded.
"""

from __future__ import annotations

import math
import random
from statistics import mean, pstdev
from typing import Any

from panoptis.query.workspace import Workspace

LEAF = {"TOOL_INVOCATION", "MODEL_INVOCATION", "RETRIEVAL", "EXTERNAL_IO", "MEMORY_ACCESS"}
FEATURES = [
    "error_share",
    "repeat_share",
    "novelty",
    "input_growth",
    "model_share",
    "tool_share",
    "retrieval_share",
    "memory_share",
]
FEATURE_WORDS = {
    "error_share": ("failing", "clean"),
    "repeat_share": ("repeating", "varied"),
    "novelty": ("exploring", "settled"),
    "input_growth": ("context-growing", "context-shrinking"),
    "model_share": ("model-heavy", "model-light"),
    "tool_share": ("tool-heavy", "tool-light"),
    "retrieval_share": ("retrieving", "not-retrieving"),
    "memory_share": ("memory-bound", "memory-free"),
}


def _windows(events: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    leaf = [e for e in events if e["kind"] in LEAF]
    seen: set[str] = set()
    out = []
    prev_size = None
    for i in range(0, len(leaf), window):
        chunk = leaf[i : i + window]
        if len(chunk) < max(2, window // 2):
            break
        ops = [f"{e['kind']}:{e['operation']}" for e in chunk]
        novel = sum(1 for o in ops if o not in seen)
        seen.update(ops)
        size = mean(float(e["resources"].get("tokens_in") or len(e["inputs"])) for e in chunk)
        growth = 0.0 if not prev_size else (size - prev_size) / prev_size
        prev_size = size or prev_size
        n = len(chunk)
        out.append(
            {
                "events": [e["event_id"] for e in chunk],
                "t_start": chunk[0]["time"]["start"],
                "features": {
                    "error_share": sum(1 for e in chunk if e["status"] in ("ERROR", "TIMEOUT")) / n,
                    "repeat_share": 1 - len(set(ops)) / n,
                    "novelty": novel / n,
                    "input_growth": max(-1.0, min(1.0, growth)),
                    "model_share": sum(1 for e in chunk if e["kind"] == "MODEL_INVOCATION") / n,
                    "tool_share": sum(1 for e in chunk if e["kind"] == "TOOL_INVOCATION") / n,
                    "retrieval_share": sum(1 for e in chunk if e["kind"] == "RETRIEVAL") / n,
                    "memory_share": sum(1 for e in chunk if e["kind"] == "MEMORY_ACCESS") / n,
                },
            }
        )
    return out


def _kmeans(
    points: list[list[float]], k: int, seed: int = 3, iters: int = 50
) -> tuple[list[int], list[list[float]]]:
    rng = random.Random(seed)  # noqa: S311  # nosec B311 — seeded statistics, not security
    cents = [list(p) for p in rng.sample(points, k)]
    assign = [0] * len(points)
    for _ in range(iters):
        changed = False
        for i, p in enumerate(points):
            best = min(
                range(k), key=lambda c: sum((a - b) ** 2 for a, b in zip(p, cents[c], strict=True))
            )
            if best != assign[i]:
                assign[i] = best
                changed = True
        for c in range(k):
            members = [points[i] for i in range(len(points)) if assign[i] == c]
            if members:
                cents[c] = [mean(col) for col in zip(*members, strict=True)]
        if not changed:
            break
    return assign, cents


def _silhouette(points: list[list[float]], assign: list[int]) -> float:
    def d(a: list[float], b: list[float]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))

    scores = []
    labels = set(assign)
    if len(labels) < 2:
        return -1.0
    for i, p in enumerate(points):
        own = [d(p, q) for j, q in enumerate(points) if assign[j] == assign[i] and j != i]
        a = mean(own) if own else 0.0
        b = min(
            mean(d(p, q) for j, q in enumerate(points) if assign[j] == c)
            for c in labels
            if c != assign[i]
        )
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return mean(scores)


def infer_states(
    ws: Workspace, run_refs: list[str] | None = None, *, window: int = 4, max_k: int = 5
) -> dict[str, Any]:
    runs = [ws.resolve_run(r) for r in run_refs] if run_refs else ws.runs()
    per_run: dict[str, list[dict[str, Any]]] = {
        r["run_id"]: _windows(ws.events(r["run_id"]), window) for r in runs
    }
    allw = [(rid, w) for rid, ws_ in per_run.items() for w in ws_]
    result: dict[str, Any] = {
        "method": __doc__.strip().splitlines()[0] if __doc__ else "",
        "window": window,
        "features": FEATURES,
        "maturity": "EXPERIMENTAL",
    }
    if len(allw) < 6:
        result.update({"status": "insufficient_data", "windows": len(allw), "needed": 6})
        return result
    mu = {f: mean(w["features"][f] for _, w in allw) for f in FEATURES}
    sd = {f: (pstdev([w["features"][f] for _, w in allw]) or 1.0) for f in FEATURES}
    pts = [[(w["features"][f] - mu[f]) / sd[f] for f in FEATURES] for _, w in allw]
    best: tuple[float, int, list[int], list[list[float]]] | None = None
    for k in range(2, min(max_k, len(pts) - 1) + 1):
        assign, cents = _kmeans(pts, k)
        s = _silhouette(pts, assign)
        if best is None or s > best[0]:
            best = (s, k, assign, cents)
    assert best is not None  # nosec B101 — invariant: range(2, max_k) always executes
    sil, k, assign, cents = best
    states = []
    for c, cent in enumerate(cents):
        ranked = sorted(range(len(FEATURES)), key=lambda i: -abs(cent[i]))[:2]
        name = (
            "/".join(
                FEATURE_WORDS[FEATURES[i]][0 if cent[i] > 0 else 1]
                for i in ranked
                if abs(cent[i]) > 0.3
            )
            or "baseline"
        )
        states.append(
            {
                "state": f"S{c}",
                "name": name,
                "centroid_z": {FEATURES[i]: round(cent[i], 3) for i in range(len(FEATURES))},
                "windows": sum(1 for a in assign if a == c),
            }
        )
    trajectories: dict[str, Any] = {}
    idx = 0
    dists: list[float] = []
    for rid, wins in per_run.items():
        seq = []
        prev = None
        for w in wins:
            p = pts[idx]
            if prev is not None:
                dists.append(math.sqrt(sum((a - b) ** 2 for a, b in zip(p, prev, strict=True))))
            seq.append(
                {
                    "t_start": w["t_start"],
                    "state": f"S{assign[idx]}",
                    "events": w["events"],
                    "features": {f: round(v, 3) for f, v in w["features"].items()},
                }
            )
            prev = p
            idx += 1
        trajectories[rid] = seq
    thr = (mean(dists) + 2 * pstdev(dists)) if len(dists) > 1 else float("inf")
    idx = 0
    for rid, seq in trajectories.items():
        prev = None
        for i, _item in enumerate(seq):
            p = pts[idx]
            if (
                prev is not None
                and math.sqrt(sum((a - b) ** 2 for a, b in zip(p, prev, strict=True))) > thr
            ):
                seq[i]["change_point"] = True
            prev = p
            idx += 1
    result.update(
        {
            "status": "estimated",
            "k": k,
            "silhouette": round(sil, 4),
            "states": states,
            "change_point_threshold": round(thr, 4) if math.isfinite(thr) else None,
            "trajectories": trajectories,
        }
    )
    return result
