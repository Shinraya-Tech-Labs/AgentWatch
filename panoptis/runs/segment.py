"""Run segmentation.

A run is identified only from *declared* grouping (a native run id, an OTel trace id,
a LangChain root run, a Claude Code / legacy session). An event without a declared run
may inherit one through a *resolved declared parent*; that inference is recorded as
``run_basis = "parent_chain"``. Events with neither stay run-less — never guessed from
timing.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from panoptis.events.model import ComputationalEvent, EventKind, EventStatus

RUN_NAMESPACE = uuid.UUID("0b8d5e2c-7a41-5f3b-8c6d-1e9f0a2b3c4d")


def run_id_for(tenant_id: str, key: tuple[str, str]) -> str:
    return str(uuid.uuid5(RUN_NAMESPACE, f"{tenant_id}|{key[0]}|{key[1]}"))


@dataclass
class Segmentation:
    run_of: dict[str, str | None] = field(default_factory=dict)  # event_id -> run_id
    basis: dict[str, str] = field(
        default_factory=dict
    )  # event_id -> declared | parent_chain | none
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)


def build_source_index(events: Sequence[ComputationalEvent]) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for ev in events:
        for sid in ev.source_ids:
            index.setdefault(sid, ev.event_id)
    return index


def segment(events: Sequence[ComputationalEvent], tenant_id: str) -> Segmentation:
    seg = Segmentation()
    index = build_source_index(events)
    by_id = {e.event_id: e for e in events}
    for ev in events:
        if ev.run_key:
            seg.run_of[ev.event_id] = run_id_for(tenant_id, ev.run_key)
            seg.basis[ev.event_id] = "declared"
    # inherit through resolved declared parents (fixed point)
    changed = True
    while changed:
        changed = False
        for ev in events:
            if ev.event_id in seg.run_of:
                continue
            for link in ev.parents:
                pid = index.get((link.key_space, link.value))
                if pid and pid in seg.run_of and seg.run_of[pid]:
                    seg.run_of[ev.event_id] = seg.run_of[pid]
                    seg.basis[ev.event_id] = "parent_chain"
                    changed = True
                    break
    for ev in events:
        seg.run_of.setdefault(ev.event_id, None)
        seg.basis.setdefault(ev.event_id, "none")

    members: dict[str, list[ComputationalEvent]] = {}
    keys: dict[str, tuple[str, str]] = {}
    for ev in events:
        rid = seg.run_of[ev.event_id]
        if rid is None:
            continue
        members.setdefault(rid, []).append(ev)
        if ev.run_key and seg.basis[ev.event_id] == "declared":
            keys.setdefault(rid, ev.run_key)
    for rid, evs in members.items():
        seg.runs[rid] = summarize_run(rid, keys.get(rid), evs, index, by_id)
    return seg


def summarize_run(
    run_id: str,
    run_key: tuple[str, str] | None,
    evs: Sequence[ComputationalEvent],
    index: dict[tuple[str, str], str],
    by_id: dict[str, ComputationalEvent],
) -> dict[str, Any]:
    lifecycle = [e for e in evs if e.kind == EventKind.LIFECYCLE and "run" in e.facets]
    root_like = [e for e in evs if not any(index.get((p.key_space, p.value)) for p in e.parents)]
    name = None
    status = EventStatus.UNKNOWN.value
    attributes: dict[str, Any] = {}
    if lifecycle:
        lc = lifecycle[0]
        name = lc.operation.removeprefix("run:")
        status = lc.status.value
        attributes = lc.attributes
    elif root_like:
        name = root_like[0].operation
    starts: list[datetime] = [e.time.start for e in evs if e.time.start is not None]
    ends: list[datetime] = [t for e in evs if (t := e.time.end or e.time.start) is not None]
    declared = [(e, p) for e in evs for p in e.parents]
    resolved = sum(1 for _, p in declared if (p.key_space, p.value) in index)
    unresolved = [
        {"event_id": e.event_id, "relation": p.relation, "key_space": p.key_space, "value": p.value}
        for e, p in declared
        if (p.key_space, p.value) not in index
    ]
    kinds = Counter(e.kind.value for e in evs)
    models = sorted(
        {e.object.key for e in evs if e.kind == EventKind.MODEL_INVOCATION and e.object}
    )
    tools = sorted({e.object.key for e in evs if e.kind == EventKind.TOOL_INVOCATION and e.object})
    tokens_in = sum(int(e.resources.get("tokens_in") or 0) for e in evs)
    tokens_out = sum(int(e.resources.get("tokens_out") or 0) for e in evs)
    cost = sum(float(e.resources.get("cost_usd") or 0.0) for e in evs)
    errors = sum(1 for e in evs if e.status in (EventStatus.ERROR, EventStatus.TIMEOUT))
    fingerprint = {
        "system": attributes.get("system"),
        "models": models,
        "tools": tools,
        "code_version": attributes.get("code_version"),
    }
    from panoptis.evidence.canonical import canonical_json, sha256_hex

    return {
        "run_id": run_id,
        "run_key": list(run_key) if run_key else None,
        "name": name,
        "status": status,
        "status_basis": "run_lifecycle_event" if lifecycle else "not_declared",
        "started_at": min(starts).isoformat() if starts else None,
        # tie-breaker for runs that share a start time (coarse clocks): observation ids are
        # ULIDs assigned in recording order
        "first_observation": min((o for e in evs for o in e.derived_from), default=None),
        "ended_at": max(ends).isoformat() if ends else None,
        "duration_ms": (max(ends) - min(starts)).total_seconds() * 1000.0
        if starts and ends
        else None,
        "event_count": len(evs),
        "error_events": errors,
        "kinds": dict(kinds),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost, 6),
        "attributes": attributes,
        "fingerprint": fingerprint,
        "system_version": sha256_hex(canonical_json(fingerprint))[:16],
        "declared_links": len(declared),
        "resolved_links": resolved,
        "completeness": (resolved / len(declared)) if declared else 1.0,
        "unresolved_links": unresolved[:200],
    }
