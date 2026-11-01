"""Normalizer for the native instrumentation SDK (``panoptis.instrument``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from panoptis.events.model import (
    Confidence,
    DeclaredLink,
    Diagnostic,
    Effect,
    EffectKind,
    EntityRef,
    EventKind,
    EventStatus,
)
from panoptis.events.normalize import (
    EventBuilder,
    NormalizeContext,
    Normalizer,
    NormalizeResult,
    source_order,
    status_from,
    temporal,
)
from panoptis.evidence.model import RawObservation


def entity(value: Any, default_kind: str = "component") -> EntityRef | None:
    if not value:
        return None
    text = str(value)
    if ":" in text:
        return EntityRef.parse(text)
    return EntityRef(default_kind, text)


def event_kind(value: Any) -> EventKind:
    try:
        return EventKind(str(value).upper())
    except ValueError:
        return EventKind.UNKNOWN


class NativeNormalizer(Normalizer):
    name = "native"
    version = "2"  # 2: input sources (declared / runtime-identity references)
    source_kinds = frozenset(
        {
            "native.span.start",
            "native.span.end",
            "native.run.start",
            "native.run.end",
            "native.point",
        }
    )
    maturity = "VALIDATED"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        return self._declared_key(obs, "run_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        starts: dict[str, RawObservation] = {}
        ends: dict[str, RawObservation] = {}
        run_starts: dict[str, RawObservation] = {}
        run_ends: dict[str, RawObservation] = {}
        points: list[RawObservation] = []
        for obs in source_order(observations):
            sk = obs.source_kind
            if sk == "native.point":
                points.append(obs)
                continue
            if sk.startswith("native.run"):
                rid = obs.declared("run_id")
                if not rid:
                    result.diagnostics.append(
                        Diagnostic(
                            obs.obs_id, "error", "missing_run_id", "run observation without run_id"
                        )
                    )
                    continue
                (run_starts if sk.endswith("start") else run_ends)[rid] = obs
                continue
            sid = obs.declared("span_id")
            if not sid:
                result.diagnostics.append(
                    Diagnostic(
                        obs.obs_id, "error", "missing_span_id", "span observation without span_id"
                    )
                )
                continue
            (starts if sk.endswith("start") else ends)[sid] = obs

        for rid in list(dict.fromkeys([*run_starts, *run_ends])):
            result.events.append(
                self._run_event(rid, run_starts.get(rid), run_ends.get(rid), ctx, result)
            )
        for sid in list(dict.fromkeys([*starts, *ends])):
            result.events.append(self._span_event(sid, starts.get(sid), ends.get(sid), ctx, result))
        for obs in points:
            result.events.append(self._point_event(obs, ctx))
        return result

    def _run_event(
        self,
        rid: str,
        start: RawObservation | None,
        end: RawObservation | None,
        ctx: NormalizeContext,
        result: NormalizeResult,
    ) -> Any:
        obs = [o for o in (start, end) if o is not None]
        b = EventBuilder(self, ctx, obs)
        sp: dict[str, Any] = start.payload() if start else {}
        ep: dict[str, Any] = end.payload() if end else {}
        b.kind = EventKind.LIFECYCLE
        b.operation = f"run:{sp.get('name') or ep.get('name') or 'unnamed'}"
        b.facets = ["run"]
        b.source_ids = [("native.run", rid)]
        b.run_key = ("native.run", rid)
        b.time = temporal(start, end) if start else temporal(end, None)
        if end is None:
            result.diagnostics.append(
                Diagnostic(
                    start.obs_id if start else None,
                    "warning",
                    "unclosed_run",
                    f"run {rid} has no end observation",
                )
            )
            b.status = EventStatus.UNKNOWN
        else:
            b.status = status_from(ep.get("outcome"))
            if ep.get("error"):
                b.error = ep["error"]
        attrs = dict(sp.get("attributes") or {})
        if sp.get("system"):
            attrs["system"] = sp["system"]
        attrs["outcome"] = ep.get("outcome")
        b.attributes = attrs
        if sp.get("system"):
            b.actor = EntityRef("system", str(sp["system"]))
        b.extra_missing = [] if end else ["end_time"]
        ev = b.build()
        # a run has no parent by definition; do not report it as missing
        return _drop_missing(ev, "parent")

    def _span_event(
        self,
        sid: str,
        start: RawObservation | None,
        end: RawObservation | None,
        ctx: NormalizeContext,
        result: NormalizeResult,
    ) -> Any:
        obs = [o for o in (start, end) if o is not None]
        b = EventBuilder(self, ctx, obs)
        sp: dict[str, Any] = start.payload() if start else {}
        ep: dict[str, Any] = end.payload() if end else {}
        anchor = start or end
        assert anchor is not None  # nosec B101 — invariant: caller passes start or end
        b.kind = event_kind(sp.get("kind"))
        b.operation = str(sp.get("operation") or "unknown")
        if start is None:
            b.kind = EventKind.UNKNOWN
            result.diagnostics.append(
                Diagnostic(
                    end.obs_id if end else None,
                    "warning",
                    "unpaired_end",
                    f"span {sid} end without start",
                )
            )
        b.actor = entity(sp.get("actor"))
        b.object = entity(sp.get("object"))
        attrs = {**(sp.get("attributes") or {}), **(ep.get("attributes") or {})}
        if attrs.pop("actor_basis", None) == "inherited_from_parent_span":
            b.attr_confidence = Confidence(0.8, "actor_inherited_from_parent_span")
        b.attributes = attrs
        if sp.get("call_key"):
            b.attributes["call_key"] = sp["call_key"]
        b.facets = list(sp.get("facets") or []) + list(ep.get("facets") or [])
        inputs = sp.get("inputs") or ep.get("late_inputs") or []
        seen_inputs = {(i.get("role"), str(i.get("value"))) for i in inputs}
        for extra in ep.get("late_inputs") or []:
            if (extra.get("role"), str(extra.get("value"))) not in seen_inputs:
                inputs.append(extra)
        for item in inputs:
            b.input(
                item.get("value"),
                role=item.get("role") or "input",
                label=item.get("label"),
                sources=tuple(item.get("sources") or ()),
            )
        for item in ep.get("outputs") or []:
            b.output(item.get("value"), role=item.get("role") or "output", label=item.get("label"))
        unfaithful = [
            i.get("role")
            for i in [*inputs, *(ep.get("outputs") or [])]
            if i.get("faithful") is False
        ]
        if unfaithful:
            b.attributes["non_json_values"] = unfaithful
        b.source_ids = [("native.span", sid)]
        parent = anchor.declared("parent_span_id")
        run_id = anchor.declared("run_id")
        if parent:
            b.parents.append(DeclaredLink("parent", "native.span", parent))
        elif run_id:
            b.parents.append(DeclaredLink("parent", "native.run", run_id))
        links = {
            (lnk["span_id"], lnk.get("relation", "depends_on"))
            for lnk in [*(sp.get("links") or []), *(ep.get("links") or [])]
        }
        for other, relation in sorted(links):
            b.parents.append(DeclaredLink(relation, "native.span", other))
        if run_id:
            b.run_key = ("native.run", run_id)
        b.time = temporal(start, end) if start else temporal(end, None)
        if end is None:
            b.status = EventStatus.UNKNOWN
            result.diagnostics.append(
                Diagnostic(
                    start.obs_id if start else None,
                    "warning",
                    "unpaired_start",
                    f"span {sid} never ended",
                )
            )
        else:
            b.status = status_from(ep.get("status"))
            b.error = ep.get("error")
        res = dict(ep.get("resources") or {})
        if ep.get("duration_ms") is not None:
            res.setdefault("latency_ms", ep["duration_ms"])
        b.resources = res
        b.effects = _effects(b.kind, b.object, b.facets, attrs)
        return b.build()

    def _point_event(self, obs: RawObservation, ctx: NormalizeContext) -> Any:
        b = EventBuilder(self, ctx, [obs])
        p: dict[str, Any] = obs.payload()
        b.kind = event_kind(p.get("kind"))
        b.operation = str(p.get("operation") or "unknown")
        b.actor = entity(p.get("actor"))
        b.object = entity(p.get("object"))
        b.attributes = dict(p.get("data") or {})
        b.status = EventStatus.ERROR if b.kind == EventKind.FAILURE else EventStatus.OK
        eid = obs.declared("event_id")
        if eid:
            b.source_ids = [("native.point", eid)]
        parent = obs.declared("parent_span_id")
        run_id = obs.declared("run_id")
        if parent:
            b.parents.append(DeclaredLink("parent", "native.span", parent))
        elif run_id:
            b.parents.append(DeclaredLink("parent", "native.run", run_id))
        if run_id:
            b.run_key = ("native.run", run_id)
        b.time = temporal(obs)
        b.extra_missing = []
        ev = b.build()
        return _drop_missing(ev, "end_time")


def _effects(
    kind: EventKind, obj: EntityRef | None, facets: list[str], attrs: dict[str, Any]
) -> list[Effect]:
    if obj is None:
        return []
    target = obj.canonical
    if kind == EventKind.MEMORY_ACCESS:
        access = attrs.get("access") or (
            "write" if "write" in facets else "read" if "read" in facets else None
        )
        if access == "write":
            return [Effect(EffectKind.WRITE, target)]
        if access == "read":
            return [Effect(EffectKind.READ, target)]
        return []
    if kind == EventKind.RETRIEVAL:
        return [Effect(EffectKind.READ, target)]
    if kind == EventKind.STATE_MUTATION:
        return [Effect(EffectKind.WRITE, target)]
    if kind in (EventKind.MESSAGE, EventKind.DELEGATION):
        return [Effect(EffectKind.SEND, target)]
    return []


def _drop_missing(ev: Any, name: str) -> Any:
    from dataclasses import replace

    return replace(ev, missing=tuple(m for m in ev.missing if m != name))
