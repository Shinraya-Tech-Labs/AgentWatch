"""In-memory graph over stored relations, with view-aware traversal.

Hyperedges: a relation ``tail[] → head[]`` connects every tail member to every head
member for traversal purposes, and traversal results keep the relation id so callers can
see the full hyperedge.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from panoptis.graph.model import EVIDENCE_RANK, STRENGTH_RANK, EvidenceClass


@dataclass(frozen=True)
class Step:
    node: str
    depth: int
    via: str | None  # rel_id
    rel_type: str | None
    frm: str | None  # neighbour we came from

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "depth": self.depth,
            "via": self.via,
            "rel_type": self.rel_type,
            "from": self.frm,
        }


class Graph:
    def __init__(
        self, relations: Sequence[dict[str, Any]], times: dict[str, float] | None = None
    ) -> None:
        """``times`` maps event nodes to start timestamps (epoch seconds). When given, traversal
        is time-respecting: a path may not reach an event that started before the event it
        came from. Information nodes are instances (ADR-0017), so content-identical values
        produced by different events are different nodes and need no further disambiguation.

        Ambiguous provenance (``CANDIDATE_SOURCE``, resolution AMBIGUOUS) is not an information
        flow and is skipped unless a traversal asks for it with ``include_ambiguous``."""
        self.relations = {r["rel_id"]: r for r in relations}
        self.times = times or {}
        self.out: dict[str, list[tuple[str, str]]] = defaultdict(
            list
        )  # node -> [(rel_id, neighbour)]
        self.inc: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.nodes: set[str] = set()
        for r in relations:
            for t in r["tail"]:
                for h in r["head"]:
                    self.out[t].append((r["rel_id"], h))
                    self.inc[h].append((r["rel_id"], t))
                    self.nodes.update((t, h))

    def _ok(
        self,
        rel: dict[str, Any],
        views: set[str] | None,
        types: set[str] | None,
        min_confidence: float,
        min_evidence: EvidenceClass | None,
        include_ambiguous: bool = False,
        min_strength: str | None = None,
    ) -> bool:
        attrs = rel.get("attributes") or {}
        if not include_ambiguous and (
            attrs.get("resolution") == "AMBIGUOUS" or attrs.get("strength") == "NONE"
        ):
            return False  # a candidate or a similarity, not an information flow
        if min_strength is not None and "strength" in attrs:
            if STRENGTH_RANK[attrs["strength"]] < STRENGTH_RANK[min_strength]:
                return False
        if views and rel["view"] not in views:
            return False
        if types and rel["type"] not in types:
            return False
        if rel["confidence"] < min_confidence:
            return False
        if min_evidence is not None:
            ec = EvidenceClass(rel["evidence_class"]) if rel.get("evidence_class") else None
            if EVIDENCE_RANK[ec] < EVIDENCE_RANK[min_evidence]:
                return False
        return True

    def _walk(
        self,
        start: str,
        adj: dict[str, list[tuple[str, str]]],
        *,
        views: Iterable[str] | None,
        types: Iterable[str] | None,
        max_depth: int,
        min_confidence: float,
        min_evidence: EvidenceClass | None,
        skip_kinds: Iterable[str] = (),
        direction: int = 1,
        include_ambiguous: bool = False,
        min_strength: str | None = None,
    ) -> list[Step]:
        vset = set(views) if views else None
        tset = set(types) if types else None
        skip = tuple(f"{k}:" for k in skip_kinds)
        seen = {start}
        out: list[Step] = []
        eps = 1e-6
        # state: node, depth, t (start time of the last event on the path)
        q: deque[tuple[str, int, float | None]] = deque([(start, 0, self.times.get(start))])
        while q:
            node, depth, t = q.popleft()
            if depth >= max_depth:
                continue
            for rel_id, nb in adj.get(node, []):
                if nb in seen or (skip and nb.startswith(skip)):
                    continue
                rel = self.relations[rel_id]
                if not self._ok(
                    rel, vset, tset, min_confidence, min_evidence, include_ambiguous, min_strength
                ):
                    continue
                tn = self.times.get(nb)
                if t is not None and tn is not None and (tn - t) * direction < -eps:
                    continue  # would reach an event on the wrong side of time
                seen.add(nb)
                out.append(Step(nb, depth + 1, rel_id, rel["type"], node))
                q.append((nb, depth + 1, tn if tn is not None else t))
        return out

    def ancestors(
        self,
        node: str,
        *,
        views: Iterable[str] | None = None,
        types: Iterable[str] | None = None,
        max_depth: int = 50,
        min_confidence: float = 0.0,
        min_evidence: EvidenceClass | None = None,
        skip_kinds: Iterable[str] = (),
        include_ambiguous: bool = False,
        min_strength: str | None = None,
    ) -> list[Step]:
        return self._walk(
            node,
            self.inc,
            direction=-1,
            views=views,
            types=types,
            max_depth=max_depth,
            min_confidence=min_confidence,
            min_evidence=min_evidence,
            skip_kinds=skip_kinds,
            include_ambiguous=include_ambiguous,
            min_strength=min_strength,
        )

    def descendants(
        self,
        node: str,
        *,
        views: Iterable[str] | None = None,
        types: Iterable[str] | None = None,
        max_depth: int = 50,
        min_confidence: float = 0.0,
        min_evidence: EvidenceClass | None = None,
        skip_kinds: Iterable[str] = (),
        include_ambiguous: bool = False,
        min_strength: str | None = None,
    ) -> list[Step]:
        return self._walk(
            node,
            self.out,
            views=views,
            types=types,
            max_depth=max_depth,
            min_confidence=min_confidence,
            min_evidence=min_evidence,
            skip_kinds=skip_kinds,
            include_ambiguous=include_ambiguous,
            min_strength=min_strength,
        )

    def parents(self, node: str, *, types: Iterable[str] = ("CONTAINS",)) -> list[str]:
        tset = set(types)
        return [nb for rid, nb in self.inc.get(node, []) if self.relations[rid]["type"] in tset]

    def children(self, node: str, *, types: Iterable[str] = ("CONTAINS",)) -> list[str]:
        tset = set(types)
        return [nb for rid, nb in self.out.get(node, []) if self.relations[rid]["type"] in tset]

    def shortest_path(self, a: str, b: str, *, views: Iterable[str] | None = None) -> list[Step]:
        vset = set(views) if views else None
        prev: dict[str, tuple[str, str]] = {}
        q: deque[str] = deque([a])
        seen = {a}
        while q:
            node = q.popleft()
            if node == b:
                break
            for rid, nb in self.out.get(node, []):
                if nb in seen or (vset and self.relations[rid]["view"] not in vset):
                    continue
                seen.add(nb)
                prev[nb] = (rid, node)
                q.append(nb)
        if b not in prev:
            return []
        path: list[Step] = []
        cur = b
        while cur != a:
            rid, p = prev[cur]
            path.append(Step(cur, 0, rid, self.relations[rid]["type"], p))
            cur = p
        path.reverse()
        return [Step(s.node, i + 1, s.via, s.rel_type, s.frm) for i, s in enumerate(path)]

    def stats(self, event_nodes: Iterable[str] | None = None) -> dict[str, Any]:
        """Structural statistics over the CONTAINS tree + DEPENDS_ON edges between events."""
        events = (
            set(event_nodes)
            if event_nodes is not None
            else {n for n in self.nodes if n.startswith("event:")}
        )
        struct = {"CONTAINS", "DEPENDS_ON", "RESPONDS_TO"}
        children: dict[str, list[str]] = defaultdict(list)
        has_parent: set[str] = set()
        for r in self.relations.values():
            if r["type"] in struct:
                for t in r["tail"]:
                    for h in r["head"]:
                        if t in events and h in events:
                            children[t].append(h)
                            has_parent.add(h)
        roots = [n for n in events if n not in has_parent]
        depth: dict[str, int] = {}
        q: deque[tuple[str, int]] = deque((r, 0) for r in roots)
        while q:
            n, d = q.popleft()
            if depth.get(n, -1) >= d:
                continue
            depth[n] = d
            if d < 10_000:
                q.extend((c, d + 1) for c in children.get(n, []))
        fanouts = [len(v) for v in children.values() if v]
        multi_parent = sum(
            1
            for n in events
            if sum(1 for r in self.inc.get(n, []) if self.relations[r[0]]["type"] in struct) > 1
        )
        by_type: dict[str, int] = defaultdict(int)
        for r in self.relations.values():
            by_type[f"{r['view']}.{r['type']}"] += 1
        return {
            "events": len(events),
            "roots": len(roots),
            "max_depth": max(depth.values()) if depth else 0,
            "mean_branching": round(sum(fanouts) / len(fanouts), 3) if fanouts else 0.0,
            "max_branching": max(fanouts) if fanouts else 0,
            "multi_parent_events": multi_parent,
            "unreached_events": len(events - set(depth)),
            "relations_by_type": dict(sorted(by_type.items())),
        }
