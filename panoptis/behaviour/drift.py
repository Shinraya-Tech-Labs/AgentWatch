"""Behavioural distance candidates and drift tests.

No distance here is claimed to be *the* behavioural distance: several candidates are
reported side by side so AWBench can measure which ones separate known configuration
changes (RESEARCH_HYPOTHESES H6).

Drift: per-feature two-sample permutation tests (difference in means, seeded) with
Benjamini–Hochberg control of the false discovery rate across features.
"""

from __future__ import annotations

import math
import random
from statistics import mean, pstdev
from typing import Any

from panoptis.behaviour.profile import FEATURES

MIN_RUNS = 5
PERMUTATIONS = 2000


def _js(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    sp, sq = sum(p.values()) or 1.0, sum(q.values()) or 1.0
    pp = {k: p.get(k, 0.0) / sp for k in keys}
    qq = {k: q.get(k, 0.0) / sq for k in keys}
    m = {k: (pp[k] + qq[k]) / 2 for k in keys}

    def kl(a: dict[str, float]) -> float:
        return sum(a[k] * math.log2(a[k] / m[k]) for k in keys if a[k] > 0)

    return round(0.5 * kl(pp) + 0.5 * kl(qq), 6)


def distances(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Candidate distances between two sets of run profiles (sets may be single runs)."""
    feats = list(FEATURES)
    va = {f: [float(p["features"][f]) for p in a] for f in feats}
    vb = {f: [float(p["features"][f]) for p in b] for f in feats}
    l1 = 0.0
    for f in feats:
        pooled = va[f] + vb[f]
        scale = (
            pstdev(pooled) if len(pooled) > 1 and pstdev(pooled) > 0 else (abs(mean(pooled)) or 1.0)
        )
        l1 += abs(mean(va[f]) - mean(vb[f])) / scale
    kinds_a: dict[str, float] = {}
    kinds_b: dict[str, float] = {}
    motif_a: dict[str, float] = {}
    motif_b: dict[str, float] = {}
    for p in a:
        for k, v in p["kind_distribution"].items():
            kinds_a[k] = kinds_a.get(k, 0.0) + v
        for k, v in p["motif_counts"].items():
            motif_a[k] = motif_a.get(k, 0.0) + v
    for p in b:
        for k, v in p["kind_distribution"].items():
            kinds_b[k] = kinds_b.get(k, 0.0) + v
        for k, v in p["motif_counts"].items():
            motif_b[k] = motif_b.get(k, 0.0) + v
    motif_js = _js(motif_a, motif_b) if (sum(motif_a.values()) and sum(motif_b.values())) else None
    return {
        "scaled_l1_features": round(l1 / len(feats), 6),
        "js_kind_distribution": _js(kinds_a, kinds_b),
        "js_motif_distribution": motif_js,
        "energy_distance": _energy(a, b) if len(a) > 1 and len(b) > 1 else None,
        "note": "Candidate distances; none is canonical. See RESEARCH_HYPOTHESES H6.",
    }


def _energy(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> float:
    feats = list(FEATURES)
    allp = a + b
    scale = {}
    for f in feats:
        vals = [float(p["features"][f]) for p in allp]
        s = pstdev(vals)
        scale[f] = s if s > 0 else 1.0

    def vec(p: dict[str, Any]) -> list[float]:
        return [float(p["features"][f]) / scale[f] for f in feats]

    xa, xb = [vec(p) for p in a], [vec(p) for p in b]

    def d(x: list[float], y: list[float]) -> float:
        return math.sqrt(sum((i - j) ** 2 for i, j in zip(x, y, strict=True)))

    ab = mean(d(x, y) for x in xa for y in xb)
    aa = mean(d(x, y) for x in xa for y in xa)
    bb = mean(d(x, y) for x in xb for y in xb)
    return round(2 * ab - aa - bb, 6)


def permutation_test(
    x: list[float], y: list[float], *, n: int = PERMUTATIONS, seed: int = 11
) -> float:
    obs = abs(mean(x) - mean(y))
    pooled = x + y
    rng = random.Random(seed)  # noqa: S311  # nosec B311 — seeded statistics, not security
    hits = 0
    for _ in range(n):
        rng.shuffle(pooled)
        if abs(mean(pooled[: len(x)]) - mean(pooled[len(x) :])) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n + 1)


def benjamini_hochberg(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    q: dict[str, float] = {}
    prev = 1.0
    for rank in range(m, 0, -1):
        key, p = items[rank - 1]
        prev = min(prev, p * m / rank)
        q[key] = round(prev, 6)
    return q


def drift(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], *, alpha: float = 0.05
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "n_baseline": len(baseline),
        "n_candidate": len(candidate),
        "alpha": alpha,
        "method": "per-feature permutation test on difference of means (2000 permutations, seeded) with Benjamini-Hochberg FDR",
        "distances": distances(baseline, candidate) if baseline and candidate else None,
        "maturity": "EXPERIMENTAL",
    }
    if len(baseline) < MIN_RUNS or len(candidate) < MIN_RUNS:
        result["status"] = "insufficient_data"
        result["message"] = (
            f"drift testing needs ≥{MIN_RUNS} runs per side; descriptive deltas only"
        )
        result["features"] = _deltas(baseline, candidate, None)
        return result
    pvals = {}
    for f in FEATURES:
        x = [float(p["features"][f]) for p in baseline]
        y = [float(p["features"][f]) for p in candidate]
        pvals[f] = 1.0 if (pstdev(x + y) == 0) else permutation_test(x, y)
    q = benjamini_hochberg(pvals)
    feats = _deltas(baseline, candidate, (pvals, q))
    # The smallest q-value this design can produce even for perfectly separated samples:
    # permutation p is bounded below by 1/(permutations+1) and by the number of distinct
    # splits; BH multiplies the smallest p by the number of features.
    splits = math.comb(len(baseline) + len(candidate), len(baseline))
    p_floor = max(1 / (PERMUTATIONS + 1), 2 / splits)
    result["min_achievable_q"] = round(min(1.0, p_floor * len(FEATURES)), 6)
    result["status"] = "tested" if result["min_achievable_q"] < alpha else "underpowered"
    if result["status"] == "underpowered":
        result["message"] = (
            f"with {len(baseline)}+{len(candidate)} runs no feature can reach q<{alpha}; add runs before concluding 'no drift'"
        )
    result["features"] = feats
    result["drifted_features"] = [
        f["feature"] for f in feats if f["q_value"] is not None and f["q_value"] < alpha
    ]
    result["behaviour_changed"] = bool(result["drifted_features"])
    return result


def _deltas(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    tests: tuple[dict[str, float], dict[str, float]] | None,
) -> list[dict[str, Any]]:
    out = []
    for f in FEATURES:
        x = [float(p["features"][f]) for p in a] or [0.0]
        y = [float(p["features"][f]) for p in b] or [0.0]
        ma, mb = mean(x), mean(y)
        out.append(
            {
                "feature": f,
                "baseline_mean": round(ma, 6),
                "candidate_mean": round(mb, 6),
                "relative_change": round((mb - ma) / abs(ma), 4) if ma else None,
                "p_value": round(tests[0][f], 6) if tests else None,
                "q_value": tests[1][f] if tests else None,
            }
        )
    return out
