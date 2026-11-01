"""Round-trip ComputationalEvent to/from plain dicts."""

from __future__ import annotations

from typing import Any

from panoptis.events.model import (
    ArtifactRef,
    ComputationalEvent,
    Confidence,
    DeclaredLink,
    Effect,
    EffectKind,
    EntityRef,
    EventKind,
    EventStatus,
    TemporalInfo,
    dumps,
)
from panoptis.evidence.model import parse_ts


def event_from_dict(d: dict[str, Any]) -> ComputationalEvent:
    t = d["time"]
    conf = d.get("confidence") or {}
    co = conf.get("observation") or {"value": 1.0, "basis": "declared"}
    ca = conf.get("attribution") or {"value": 1.0, "basis": "declared"}
    return ComputationalEvent(
        event_id=d["event_id"],
        tenant_id=d["tenant_id"],
        interp_id=d["interp_id"],
        normalizer=d["normalizer"],
        derived_from=tuple(d["derived_from"]),
        kind=EventKind(d["kind"]),
        operation=d["operation"],
        time=TemporalInfo(
            start=parse_ts(t["start"]) if t.get("start") else None,
            end=parse_ts(t["end"]) if t.get("end") else None,
            basis=t.get("basis", "sensor"),
            uncertainty_ms=t.get("uncertainty_ms"),
            ordering_key=t.get("ordering_key", ""),
        ),
        status=EventStatus(d["status"]),
        actor=EntityRef.parse(d["actor"]) if d.get("actor") else None,
        object=EntityRef.parse(d["object"]) if d.get("object") else None,
        facets=tuple(d.get("facets") or ()),
        inputs=tuple(ArtifactRef.from_dict(a) for a in d.get("inputs") or ()),
        outputs=tuple(ArtifactRef.from_dict(a) for a in d.get("outputs") or ()),
        effects=tuple(
            Effect(EffectKind(e["kind"]), e["target"], e.get("target_type", "entity"))
            for e in d.get("effects") or ()
        ),
        source_ids=tuple((s[0], s[1]) for s in d.get("source_ids") or ()),
        parents=tuple(
            DeclaredLink(p["relation"], p["key_space"], p["value"]) for p in d.get("parents") or ()
        ),
        run_key=(d["run_key"][0], d["run_key"][1]) if d.get("run_key") else None,
        error_json=dumps(d["error"]) if d.get("error") else None,
        resources_json=dumps(d.get("resources") or {}),
        attributes_json=dumps(d.get("attributes") or {}),
        confidence_observation=Confidence(co["value"], co["basis"], co.get("calibrated", False)),
        confidence_attribution=Confidence(ca["value"], ca["basis"], ca.get("calibrated", False)),
        missing=tuple(d.get("missing") or ()),
    )
