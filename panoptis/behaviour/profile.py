"""Behaviour profiles (per run) and behavioural genomes (per scope of runs).

A profile is a versioned feature vector computed from one run's interpretation. A genome
aggregates profiles over a scope (a system version, a run name, an explicit run set) with
bootstrap confidence intervals. Feature definitions live in FEATURES; changing any
definition bumps FEATURES_VERSION.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import Counter
from statistics import mean, median
from typing import Any

from panoptis.analysis.base import AnalysisInput, Analyzer
from panoptis.analysis.maturity import Maturity
from panoptis.behaviour.motifs import REGISTRY, MotifAnalyzer
from panoptis.events.model import EventKind, EventStatus
from panoptis.graph.traverse import Graph

FEATURES_VERSION = "2"
PROFILE_NS = uuid.UUID("7a2b3c4d-5e6f-5071-8293-a4b5c6d7e8f9")
LEAF = {
    EventKind.TOOL_INVOCATION,
    EventKind.MODEL_INVOCATION,
    EventKind.RETRIEVAL,
    EventKind.EXTERNAL_IO,
    EventKind.MEMORY_ACCESS,
}

FEATURES: dict[str, str] = {
    "events": "number of events in the run",
    "leaf_ops": "tool + model + retrieval + external + memory events",
    "graph_depth": "longest CONTAINS/DEPENDS_ON chain from a root",
    "mean_branching": "mean number of structural children over events that have children",
    "multi_parent_events": "events with >1 structural parent",
    "error_rate": "fraction of leaf operations with ERROR/TIMEOUT status",
    "retry_rate": "RETRIES relations per leaf operation",
    "tool_calls": "TOOL_INVOCATION events",
    "tool_diversity": "distinct tools / tool calls (0 when no tool calls)",
    "tool_entropy": "Shannon entropy (bits) of tool usage",
    "model_calls": "MODEL_INVOCATION events",
    "distinct_models": "distinct model entities",
    "retrievals": "RETRIEVAL events",
    "delegations": "DELEGATION + MESSAGE events",
    "delegation_entropy": "Shannon entropy (bits) of (sender→receiver) hand-off pairs",
    "actors": "distinct actors",
    "content_derivations": "DERIVES_FROM relations (content containment links)",
    "memory_transfers": "TRANSFERS relations (memory write → read of same key)",
    "memory_dependency": "fraction of model invocations with a memory read among their information ancestors",
    "duration_ms": "run wall-clock duration",
    "log_leaf_latency_p50": "log10(1 + median leaf latency ms)",
    "tokens_total": "declared input + output tokens",
    "model_input_bytes_mean": "mean size of model-invocation input artifacts (bytes)",
    "model_output_bytes_mean": "mean size of model-invocation output artifacts (bytes)",
    "cost_usd": "declared cost",
}


def _entropy(counter: Counter[str]) -> float:
    n = sum(counter.values())
    if not n:
        return 0.0
    return -sum((c / n) * math.log2(c / n) for c in counter.values() if c)


def profile_features(
    run: dict[str, Any],
    events: list[Any],
    relations: list[dict[str, Any]],
    motif_ids: list[str],
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    g = Graph(relations)
    sizes = {aid: a.size_bytes for aid, a in (artifacts or {}).items()}
    stats = g.stats({f"event:{e.event_id}" for e in events})
    leaf = [e for e in events if e.kind in LEAF]
    tools = Counter(
        e.object.canonical for e in events if e.kind == EventKind.TOOL_INVOCATION and e.object
    )
    hand = Counter(
        f"{e.actor.canonical}->{e.object.canonical}"
        for e in events
        if e.kind in (EventKind.DELEGATION, EventKind.MESSAGE) and e.actor and e.object
    )
    lat = [e.time.duration_ms for e in leaf if e.time.duration_ms is not None]
    retries = sum(1 for r in relations if r["type"] == "RETRIES")
    model_events = [e for e in events if e.kind == EventKind.MODEL_INVOCATION]
    mem_reads = {
        f"event:{e.event_id}"
        for e in events
        if e.kind == EventKind.MEMORY_ACCESS
        and ("read" in e.facets or e.attributes.get("access") == "read")
    }
    mem_dep = 0
    if model_events and mem_reads:
        for e in model_events:
            anc = {
                s.node
                for s in g.ancestors(
                    f"event:{e.event_id}",
                    views=["INFORMATION"],
                    skip_kinds=["entity"],
                    max_depth=20,
                )
            }
            if anc & mem_reads:
                mem_dep += 1
    tok = sum(
        float(e.resources.get("tokens_in") or 0) + float(e.resources.get("tokens_out") or 0)
        for e in events
    )
    feats: dict[str, Any] = {
        "events": len(events),
        "leaf_ops": len(leaf),
        "graph_depth": stats["max_depth"],
        "mean_branching": stats["mean_branching"],
        "multi_parent_events": stats["multi_parent_events"],
        "error_rate": round(
            sum(1 for e in leaf if e.status in (EventStatus.ERROR, EventStatus.TIMEOUT))
            / len(leaf),
            4,
        )
        if leaf
        else 0.0,
        "retry_rate": round(retries / len(leaf), 4) if leaf else 0.0,
        "tool_calls": sum(tools.values()),
        "tool_diversity": round(len(tools) / sum(tools.values()), 4) if tools else 0.0,
        "tool_entropy": round(_entropy(tools), 4),
        "model_calls": len(model_events),
        "distinct_models": len({e.object.canonical for e in model_events if e.object}),
        "retrievals": sum(1 for e in events if e.kind == EventKind.RETRIEVAL),
        "delegations": sum(hand.values()),
        "delegation_entropy": round(_entropy(hand), 4),
        "actors": len({e.actor.canonical for e in events if e.actor}),
        "content_derivations": sum(1 for r in relations if r["type"] == "DERIVES_FROM"),
        "memory_transfers": sum(1 for r in relations if r["type"] == "TRANSFERS"),
        "memory_dependency": round(mem_dep / len(model_events), 4) if model_events else 0.0,
        "duration_ms": float(run.get("duration_ms") or 0.0),
        "log_leaf_latency_p50": round(math.log10(1 + median(lat)), 4) if lat else 0.0,
        "tokens_total": tok,
        "model_input_bytes_mean": _mean_size(
            [a.artifact_id for e in model_events for a in e.inputs], sizes, len(model_events)
        ),
        "model_output_bytes_mean": _mean_size(
            [a.artifact_id for e in model_events for a in e.outputs], sizes, len(model_events)
        ),
        "cost_usd": float(run.get("cost_usd") or 0.0),
    }
    kinds = Counter(e.kind.value for e in events)
    motifs = Counter(motif_ids)
    return {
        "features": feats,
        "kind_distribution": {k: round(v / len(events), 4) for k, v in sorted(kinds.items())}
        if events
        else {},
        "motif_counts": {mid: motifs.get(mid, 0) for mid in sorted(REGISTRY.definitions)},
    }


def _mean_size(artifact_ids: list[str], sizes: dict[str, int], n: int) -> float:
    return round(sum(sizes.get(a, 0) for a in artifact_ids) / n, 2) if n else 0.0


class ProfileAnalyzer(Analyzer):
    name = "behaviour_profile"
    version = FEATURES_VERSION
    maturity = Maturity.EXPERIMENTAL
    record_type = "behaviour_profile"

    def analyze(self, data: AnalysisInput) -> list[dict[str, Any]]:
        motif_records = MotifAnalyzer().analyze(data)
        by_run: dict[str, list[str]] = {}
        for m in motif_records:
            by_run.setdefault(m["run_id"], []).append(m["motif_id"])
        out = []
        for run_id, run in data.runs.items():
            prof = profile_features(
                run,
                data.run_events(run_id),
                data.run_relations(run_id),
                by_run.get(run_id, []),
                data.artifacts,
            )
            out.append(
                self.record(
                    str(uuid.uuid5(PROFILE_NS, f"{run_id}|{FEATURES_VERSION}")),
                    run_id,
                    {
                        "run_id": run_id,
                        "run_name": run.get("name"),
                        "run_status": run.get("status"),
                        "system_version": run.get("system_version"),
                        "started_at": run.get("started_at"),
                        "features_version": FEATURES_VERSION,
                        **prof,
                    },
                )
            )
        return out


def bootstrap_ci(
    values: list[float], *, n: int = 1000, alpha: float = 0.05, seed: int = 7
) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)  # noqa: S311  # nosec B311 — seeded statistics, not security
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(n))
    return (round(means[int(n * alpha / 2)], 6), round(means[int(n * (1 - alpha / 2)) - 1], 6))


def genome(profiles: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    """Aggregate run profiles into a behavioural genome for ``scope``."""
    n = len(profiles)
    feats: dict[str, Any] = {}
    for f in FEATURES:
        vals = [float(p["features"][f]) for p in profiles if f in p["features"]]
        if not vals:
            continue
        feats[f] = {
            "mean": round(mean(vals), 6),
            "min": min(vals),
            "max": max(vals),
            "ci95": bootstrap_ci(vals),
        }
    motif_freq = {
        mid: round(sum(1 for p in profiles if p["motif_counts"].get(mid)) / n, 4) if n else 0.0
        for mid in sorted(REGISTRY.definitions)
    }
    kinds: Counter[str] = Counter()
    for p in profiles:
        kinds.update({k: v / n for k, v in p["kind_distribution"].items()})
    return {
        "scope": scope,
        "n_runs": n,
        "features_version": FEATURES_VERSION,
        "features": feats,
        "motif_frequency": motif_freq,
        "kind_distribution": {k: round(v, 4) for k, v in sorted(kinds.items())},
        "runs": [p["run_id"] for p in profiles],
        "maturity": "EXPERIMENTAL",
        "note": "Descriptive fingerprint of measured structure; not a claim about model internals.",
    }
