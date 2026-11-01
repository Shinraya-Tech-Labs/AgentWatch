"""Deterministic entity resolution.

Entities are identified by canonical keys that the source declared (``model:<name>``,
``tool:<name>``, ``memory:<store>`` ...). Resolution is exact-key, confidence 1.0, basis
``EXACT_KEY``. No fuzzy merging is ever performed.

The only way two keys become one entity is a DECLARED alias the user supplies
(``{"model:gpt-4o": "model:openai/gpt-4o"}``): the entity then has basis ``DECLARED_ALIAS``
and lists the observed keys it absorbed. Events keep the names as observed.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from panoptis.events.model import ComputationalEvent, EntityRef

ENTITY_NAMESPACE = uuid.UUID("3c9e1f0a-2b4d-5e6f-8a7b-9c0d1e2f3a4b")
RESOLVER = "exact_key"
RESOLVER_VERSION = "1"


def entity_id_for(tenant_id: str, canonical: str) -> str:
    return str(uuid.uuid5(ENTITY_NAMESPACE, f"{tenant_id}|{canonical}"))


def load_aliases(source: Mapping[str, str] | str | Path | None = None) -> dict[str, str]:
    """Declared entity aliases: ``{observed key: canonical key}``.

    ``source`` is a mapping, a path to a JSON object, or None to read the path in
    ``AGENTWATCH_ENTITY_ALIASES`` (unset: no aliases). Keys must look like ``kind:name``, an
    alias cannot point to itself, and a target cannot itself be an alias (no chains)."""
    if source is None:
        source = os.environ.get("AGENTWATCH_ENTITY_ALIASES") or None
        if source is None:
            return {}
    if isinstance(source, (str, Path)):
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("entity alias file must contain a JSON object")
        source = raw
    aliases = {str(k): str(v) for k, v in source.items()}
    for k, v in aliases.items():
        for key in (k, v):
            if ":" not in key or key.startswith(":") or key.endswith(":"):
                raise ValueError(f"entity alias keys look like kind:name, got {key!r}")
        if k == v:
            raise ValueError(f"entity alias {k!r} points to itself")
        if v in aliases:
            raise ValueError(f"entity alias target {v!r} is itself an alias (no chains)")
    return aliases


def resolve_entities(
    events: Sequence[ComputationalEvent],
    tenant_id: str,
    run_of: dict[str, str | None],
    aliases: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    aliases = aliases or {}

    def touch(ref: EntityRef, role: str, ev: ComputationalEvent) -> None:
        observed = ref.canonical
        key = aliases.get(observed, observed)
        if key != observed:
            ref = EntityRef.parse(key)
        rec = found.get(key)
        if rec is None:
            rec = found[key] = {
                "entity_id": entity_id_for(tenant_id, key),
                "kind": ref.kind,
                "canonical_key": key,
                "name": ref.key,
                "roles": {},
                "event_count": 0,
                "runs": set(),
                "first_seen": None,
                "last_seen": None,
                "resolution": {
                    "basis": "EXACT_KEY",
                    "confidence": 1.0,
                    "resolver": f"{RESOLVER}@{RESOLVER_VERSION}",
                },
            }
        if key != observed:
            rec.setdefault("aliases", set()).add(observed)
            rec["resolution"] = {**rec["resolution"], "basis": "DECLARED_ALIAS"}
        rec["roles"][role] = rec["roles"].get(role, 0) + 1
        rec["event_count"] += 1
        rid = run_of.get(ev.event_id)
        if rid:
            rec["runs"].add(rid)
        ts = ev.time.start.isoformat() if ev.time.start else None
        if ts:
            rec["first_seen"] = min(filter(None, [rec["first_seen"], ts]))
            rec["last_seen"] = max(filter(None, [rec["last_seen"], ts]))

    for ev in events:
        if ev.actor:
            touch(ev.actor, "actor", ev)
        if ev.object:
            touch(ev.object, "object", ev)
        for eff in ev.effects:
            if eff.target_type == "entity" and (not ev.object or eff.target != ev.object.canonical):
                touch(EntityRef.parse(eff.target), f"effect_{eff.kind.value.lower()}", ev)
    out = []
    for rec in found.values():
        rec["runs"] = sorted(rec["runs"])
        if "aliases" in rec:
            rec["aliases"] = sorted(rec["aliases"])
        out.append(rec)
    return sorted(out, key=lambda r: r["canonical_key"])


def merge_entities(
    stored: Sequence[dict[str, Any]], added: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Aggregate entities of runs that are new to the store into the stored aggregates.

    Only valid when ``added`` comes from events no stored aggregate already counts (new runs):
    counts and roles add, runs union, first/last seen take min/max — the same result as
    resolving all events again. Replacing a run's events needs a full recompute (a minimum
    cannot be subtracted)."""
    merged: dict[str, dict[str, Any]] = {
        e["canonical_key"]: {**e, "roles": dict(e["roles"]), "runs": list(e["runs"])}
        for e in stored
    }
    for e in added:
        cur = merged.get(e["canonical_key"])
        if cur is None:
            merged[e["canonical_key"]] = {**e, "roles": dict(e["roles"]), "runs": list(e["runs"])}
            continue
        for role, n in e["roles"].items():
            cur["roles"][role] = cur["roles"].get(role, 0) + n
        cur["event_count"] += e["event_count"]
        if e.get("aliases") or cur.get("aliases"):
            cur["aliases"] = sorted(set(cur.get("aliases", [])) | set(e.get("aliases", [])))
            cur["resolution"] = {**cur["resolution"], "basis": "DECLARED_ALIAS"}
        cur["runs"] = sorted(set(cur["runs"]) | set(e["runs"]))
        firsts = [x for x in (cur["first_seen"], e["first_seen"]) if x]
        lasts = [x for x in (cur["last_seen"], e["last_seen"]) if x]
        cur["first_seen"] = min(firsts) if firsts else None
        cur["last_seen"] = max(lasts) if lasts else None
    return [merged[k] for k in sorted(merged)]
