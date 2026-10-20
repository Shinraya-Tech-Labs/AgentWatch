"""Workspace: the single read model used by the API, CLI and frontend.

Everything the interfaces show comes from here, so the three surfaces cannot drift into
separate interpretations of the same evidence.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cached_property
from typing import Any

from agentwatch.evidence.model import parse_ts
from agentwatch.graph.traverse import Graph
from agentwatch.runtime.engine import Engine
from agentwatch.storage.store import Store


def run_order_key(run: dict[str, Any]) -> tuple[str, str]:
    """Chronological order of runs: start time, then the first observation (ULIDs are
    assigned in recording order), so runs sharing a coarse timestamp still order correctly."""
    return (run.get("started_at") or "", run.get("first_observation") or "")


class NotFoundError(LookupError):
    pass


class AmbiguousError(LookupError):
    pass


class Workspace:
    def __init__(
        self,
        engine: Engine | Store | str | None = None,
        tenant_id: str = "default",
        *,
        process: bool = True,
    ) -> None:
        self.engine = engine if isinstance(engine, Engine) else Engine(engine)
        self.store = self.engine.store
        self.tenant_id = tenant_id
        if process:
            self.engine.process(tenant_id)
        self.interp_id = self.engine.interp_id_for(tenant_id)

    # ── runs ──────────────────────────────────────────────────────────────
    def runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Most recent first."""
        return sorted(self.store.runs(self.interp_id, limit=limit), key=run_order_key, reverse=True)

    def resolve_run(self, ref: str) -> dict[str, Any]:
        runs = self.runs()
        if not runs:
            raise NotFoundError("no runs recorded yet")
        if ref.startswith("latest"):
            offset = int(ref.split("~", 1)[1]) if "~" in ref else 0
            ordered = sorted(runs, key=run_order_key, reverse=True)
            if offset >= len(ordered):
                raise NotFoundError(f"only {len(ordered)} runs exist")
            return ordered[offset]
        exact = [r for r in runs if r["run_id"] == ref]
        if exact:
            return exact[0]
        pref = [r for r in runs if r["run_id"].startswith(ref)]
        if len(pref) == 1:
            return pref[0]
        if len(pref) > 1:
            raise AmbiguousError(f"run prefix {ref!r} matches {len(pref)} runs")
        named = [r for r in runs if r.get("name") == ref]
        if named:
            return sorted(named, key=run_order_key, reverse=True)[0]
        raise NotFoundError(f"no run matches {ref!r}")

    # ── events ────────────────────────────────────────────────────────────
    def events(self, run_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        return self.store.events(self.interp_id, run_id=run_id, kind=kind)

    @cached_property
    def all_events(self) -> dict[str, dict[str, Any]]:
        return {e["event_id"]: e for e in self.store.events(self.interp_id)}

    def event(self, ref: str) -> dict[str, Any]:
        ref = ref.removeprefix("event:")
        ev = self.store.event(self.interp_id, ref)
        if ev:
            return ev
        matches = [e for eid, e in self.all_events.items() if eid.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousError(f"event prefix {ref!r} matches {len(matches)} events")
        raise NotFoundError(f"no event matches {ref!r}")

    def event_evidence(self, event_id: str) -> list[dict[str, Any]]:
        ev = self.event(event_id)
        out = []
        for oid in ev["derived_from"]:
            obs = self.store.get_observation(oid)
            if obs:
                out.append(obs.to_dict())
        return out

    # ── graph ─────────────────────────────────────────────────────────────
    def relations(self, run_id: str | None = None, view: str | None = None) -> list[dict[str, Any]]:
        if run_id is None:
            return self.store.relations(self.interp_id, view=view, all_runs=True)
        return self.store.relations(self.interp_id, run_id=run_id, view=view)

    def graph(self, run_id: str | None = None, views: Iterable[str] | None = None) -> Graph:
        key = (run_id, tuple(sorted(views)) if views else None)
        cache = self.__dict__.setdefault("_graph_cache", {})
        if key not in cache:
            cache[key] = self._build_graph(run_id, views)
        return cache[key]

    def _build_graph(self, run_id: str | None, views: Iterable[str] | None) -> Graph:
        rels = self.relations(run_id)
        vs = set(views) if views else None
        events = self.events(run_id) if run_id is not None else list(self.all_events.values())
        if run_id is not None:
            # a memory read may return a value written in an earlier run (a cache): include the
            # runs such transfers reach, so provenance can follow them to the original producer
            seen = {run_id}
            while len(seen) < 8:
                linked = {
                    r["attributes"]["cross_run"]
                    for r in rels
                    if r["type"] == "TRANSFERS" and r["attributes"].get("cross_run")
                } - seen
                if not linked:
                    break
                for other in sorted(x for x in linked if x):
                    seen.add(other)
                    rels = rels + self.relations(other)
                    events = events + self.events(other)
        times = {
            f"event:{e['event_id']}": parse_ts(e["time"]["start"]).timestamp()
            for e in events
            if e["time"]["start"]
        }
        return Graph([r for r in rels if vs is None or r["view"] in vs], times=times)

    # ── artifacts / nodes ─────────────────────────────────────────────────
    def artifact(self, ref: str, with_content: bool = True) -> dict[str, Any]:
        aid = self.resolve_artifact_id(ref)
        art = self.store.artifact(self.tenant_id, aid, with_content=with_content)
        if art is None:
            raise NotFoundError(f"artifact {ref!r} not found")
        art["labels"] = sorted(
            {
                a.get("label")
                for e in self.all_events.values()
                for a in e["outputs"] + e["inputs"]
                if a["artifact_id"] == aid and a.get("label")
            }
        )
        return art

    def resolve_artifact_id(self, ref: str, run_id: str | None = None) -> str:
        """Resolve an artifact id, id prefix or output label (within ``run_id`` when given)."""
        ref = ref.removeprefix("artifact:")
        if self.store.artifact(self.tenant_id, ref, with_content=False):
            return ref
        scoped = [
            e for e in self.all_events.values() if run_id is None or e.get("run_id") == run_id
        ]
        by_label = {a["artifact_id"] for e in scoped for a in e["outputs"] if a.get("label") == ref}
        if len(by_label) == 1:
            return by_label.pop()
        if len(by_label) > 1:
            # the most recently produced version of a labelled artifact
            order = sorted(scoped, key=lambda e: e["time"]["start"] or "")
            for e in reversed(order):
                for a in e["outputs"]:
                    if a.get("label") == ref:
                        return str(a["artifact_id"])
        if len(ref) >= 6:
            matches = self.store.artifacts_by_prefix(self.tenant_id, ref)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise AmbiguousError(f"artifact prefix {ref!r} matches {len(matches)} artifacts")
        raise NotFoundError(f"no artifact matches {ref!r}")

    def resolve_node(self, ref: str, run_id: str | None = None) -> str:
        if ref.startswith(("entity:", "inst:")):
            return ref
        if ref.startswith("artifact:"):
            return self.instance_for(self.resolve_artifact_id(ref, run_id), run_id)
        if ref.startswith("event:"):
            return f"event:{self.event(ref)['event_id']}"
        try:
            return f"event:{self.event(ref)['event_id']}"
        except (NotFoundError, AmbiguousError):
            return self.instance_for(self.resolve_artifact_id(ref, run_id), run_id)

    def instances_of(self, content_id: str, run_id: str | None = None) -> list[str]:
        """Information instances carrying this content, oldest first (ADR-0017): equal bytes
        produced by different events are different instances."""
        index = self._instance_index(run_id)
        return list(index.get(content_id, []))

    def _instance_index(self, run_id: str | None) -> dict[str, list[str]]:
        """content id -> instance nodes (produced values first, oldest first), built once."""
        cache = self.__dict__.setdefault("_instance_index_cache", {})
        if run_id in cache:
            return cache[run_id]
        scoped = sorted(
            (e for e in self.all_events.values() if run_id is None or e.get("run_id") == run_id),
            key=lambda e: e["time"]["start"] or "",
        )
        index: dict[str, list[str]] = {}
        for e in scoped:
            for k, a in enumerate(e["outputs"]):
                index.setdefault(a["artifact_id"], []).append(f"inst:{e['event_id']}/o{k}")
        runs = {run_id} if run_id else {e.get("run_id") for e in scoped}
        for r in sorted(x for x in runs if x):
            for rel in self.relations(r, view="INFORMATION"):
                cid = rel["attributes"].get("content_id")
                if not cid:
                    continue
                if rel["type"] == "CONTAINS_ITEM":
                    node = rel["head"][0]
                elif rel["type"] == "CONSUMES" and rel["tail"][0].startswith("inst:"):
                    node = rel["tail"][0]
                else:
                    continue
                nodes = index.setdefault(cid, [])
                if node not in nodes:
                    nodes.append(node)
        cache[run_id] = index
        return index

    def instance_for(self, content_id: str, run_id: str | None = None) -> str:
        """The most recent instance of a content (e.g. the latest ``report.md``)."""
        found = self.instances_of(content_id, run_id)
        if not found:
            raise NotFoundError(f"no information instance carries artifact {content_id[:12]}")
        return found[-1]

    def instance_content(self, node: str) -> str | None:
        """Content id of an instance node."""
        eid, _, slot = node.removeprefix("inst:").partition("/")
        e = self.all_events.get(eid)
        if e is None:
            return None
        parts = slot.split("/")
        if len(parts) == 1 and parts[0].startswith("o") and parts[0][1:].isdigit():
            k = int(parts[0][1:])
            return e["outputs"][k]["artifact_id"] if k < len(e["outputs"]) else None
        if len(parts) == 1 and parts[0].startswith("in") and parts[0][2:].isdigit():
            k = int(parts[0][2:])
            return e["inputs"][k]["artifact_id"] if k < len(e["inputs"]) else None
        rev_cache = self.__dict__.setdefault("_instance_rev_cache", {})
        rid = e.get("run_id")
        if rid not in rev_cache:
            rev_cache[rid] = {
                n: cid for cid, nodes in self._instance_index(rid).items() for n in nodes
            }
        return rev_cache[rid].get(node)

    def describe_node(self, node: str) -> dict[str, Any]:
        kind, _, ident = node.partition(":")
        if kind == "event":
            e = self.all_events.get(ident) or self.event(ident)
            return {
                "node": node,
                "type": "event",
                "label": f"{e['kind']} {e['operation']}",
                "actor": e.get("actor"),
                "status": e["status"],
                "start": e["time"]["start"],
                "run_id": e.get("run_id"),
            }
        if kind == "inst":
            cid = self.instance_content(node)
            eid, _, slot = ident.partition("/")
            art = self.store.artifact(self.tenant_id, cid, with_content=False) if cid else None
            ev = self.all_events.get(eid) or {}
            refs = ev.get("inputs", []) if slot.startswith("in") else ev.get("outputs", [])
            k = slot.split("/")[0].lstrip("oin")
            ref = refs[int(k)] if k.isdigit() and int(k) < len(refs) else {}
            item = "/i" in slot
            return {
                "node": node,
                "type": "instance",
                "content_id": cid,
                "label": (ref.get("label") if not item else None)
                or ((art or {}).get("preview") or "")[:60],
                "role": "item" if item else ref.get("role"),
                "preview": (art or {}).get("preview"),
                "size_bytes": (art or {}).get("size_bytes"),
                "producer_event": None if slot.startswith("in") else eid,
                "consumer_event": eid if slot.startswith("in") else None,
                "erased": bool(art and art.get("erased")),
                "same_content_instances": len(self.instances_of(cid, ev.get("run_id")))
                if cid
                else 0,
            }
        if kind == "artifact":
            art = self.store.artifact(self.tenant_id, ident, with_content=False) or {}
            labels = sorted(
                {
                    a.get("label")
                    for e in self.all_events.values()
                    for a in e["outputs"] + e["inputs"]
                    if a["artifact_id"] == ident and a.get("label")
                }
            )
            roles = sorted(
                {
                    a.get("role")
                    for e in self.all_events.values()
                    for a in e["outputs"] + e["inputs"]
                    if a["artifact_id"] == ident
                }
            )
            return {
                "node": node,
                "type": "artifact",
                "label": labels[0] if labels else (art.get("preview") or "")[:60],
                "roles": roles,
                "preview": art.get("preview"),
                "size_bytes": art.get("size_bytes"),
            }
        ent = self._entity_index().get(ident)
        if ent is None:
            return {"node": node, "type": "entity", "label": ident}
        return {
            "node": node,
            "type": "entity",
            "label": ident,
            "kind": ent.get("kind"),
            "event_count": ent.get("event_count"),
            "runs": len(ent.get("runs") or []),
            "resolution": (ent.get("resolution") or {}).get("basis"),
            "aliases": ent.get("aliases", []),
            # the observed name was merged into a declared canonical entity
            **({"alias_of": ent["canonical_key"]} if ent["canonical_key"] != ident else {}),
        }

    def _entity_index(self) -> dict[str, dict[str, Any]]:
        cache = self.__dict__.setdefault("_entity_cache", {})
        if not cache:
            for e in self.entities():
                cache[e["canonical_key"]] = e
                for alias in e.get("aliases", []):
                    cache.setdefault(alias, e)
        return cache

    def entities(self) -> list[dict[str, Any]]:
        return self.store.entities(self.interp_id)

    def diagnostics(self) -> list[dict[str, Any]]:
        return self.store.diagnostics(self.interp_id)

    def derived(self, record_type: str, scope: str | None = None) -> list[dict[str, Any]]:
        return self.store.derived(self.interp_id, record_type, scope)

    # ── contracts & assessments (v3.1) ─────────────────────────────────────
    def contracts(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.store.list_contracts(self.tenant_id)]

    def contract(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        c = self.store.get_contract(self.tenant_id, name, version)
        return c.to_dict() if c else None

    def assessment(self, assessment_id: str) -> dict[str, Any] | None:
        return self.store.get_assessment(self.tenant_id, assessment_id)
