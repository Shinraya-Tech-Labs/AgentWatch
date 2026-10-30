"""INFORMATION view: information instances and the provenance resolution hierarchy (ADR-0017).

Content identity is not information identity. Two values with equal bytes share a content id
(storage is deduplicated by it), but every value an event produces is its own INFORMATION
INSTANCE:

    inst:<event_id>/o<k>          the k-th output of an event
    inst:<event_id>/o<k>/i<j>     the j-th item of a list output (e.g. one retrieved document)
    inst:<event_id>/in<k>         a value an event consumed that no observed event produced as
                                  such (constructed by uninstrumented code, or of unknown origin)

Where a consumed value came from is resolved in a fixed order; the first level that yields
evidence decides and weaker levels are not consulted:

    1. DECLARED_REFERENCE      the sensor declared the source (explicit reference, declared
                               instance id, or runtime object identity seen by the SDK)
    2. CORRELATION_LINEAGE     the consumer's declared parent or linked span consumed or
                               produced this identical value
    3. MESSAGE_REFERENCE /     the identical value was produced by a message on the same
       ARTIFACT_REFERENCE      channel, or by a write to the same named object
    4. TEMPORAL_CONTENT_MATCH  the identical value was produced earlier in the run
    5. CONTENT_CONTAINMENT     the value was constructed and contains text of earlier instances
    6. UNRESOLVED              no evidence: an origin of unknown provenance

Memory reads are linked to the write whose value they returned (TRANSFERS,
MEMORY_REFERENCE), across runs when the store outlives the run.

Levels 3-5 can yield several candidates. They are never chosen between arbitrarily
(``_resolve``): a candidate is RESOLVED only when no other candidate explains the content
equally well. Otherwise it is kept as an AMBIGUOUS ``CANDIDATE_SOURCE`` (not an information
flow), and only the candidates that are ancestors under EVERY alternative explanation become
flow edges. Content similarity never becomes a flow edge into a value that entered from outside
the system (``MATCHES_CONTENT``, descriptive only).
"""

from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from panoptis.events.model import ArtifactRef, ComputationalEvent, EventKind
from panoptis.events.normalize import ArtifactContent
from panoptis.graph.model import (
    Basis,
    InfoEvidence,
    RelType,
    View,
    make_relation,
    node_event,
    strength_of,
)

EXTERNAL_KINDS = (EventKind.RETRIEVAL, EventKind.EXTERNAL_IO, EventKind.EXTERNAL_INPUT)
# events whose outputs carry data from elsewhere (content inference into their outputs is
# allowed when they did not declare where it came from)
CARRIER_KINDS = (
    EventKind.STATE_MUTATION,
    EventKind.MESSAGE,
    EventKind.DELEGATION,
    EventKind.MEMORY_ACCESS,
    EventKind.OPERATION,
    EventKind.TRANSFORMATION,
)
EPS = 1e-6


def node_instance(event_id: str, slot: str) -> str:
    return f"inst:{event_id}/{slot}"


def instance_event(node: str) -> str | None:
    """Event id of an instance node (its producer; for ``/in`` nodes, its consumer)."""
    if not node.startswith("inst:"):
        return None
    return node[5:].split("/", 1)[0]


@dataclass
class _Inst:
    node: str
    content_id: str
    event_id: str
    available: float  # time from which later events can have used it
    external: bool
    tokens: frozenset[str] = field(default_factory=frozenset)


@dataclass
class _Params:
    tokens_of: Callable[[str], frozenset[str]]
    seam: int  # shingles a single concatenation seam can create (shingle size - 1)
    theta: float
    max_list_items: int
    derived_by: str


class _Run:
    """Builds the INFORMATION relations of one run, in time order."""

    def __init__(
        self,
        run_id: str | None,
        evs: list[ComputationalEvent],
        artifacts: dict[str, ArtifactContent],
        register: Callable[[Any, str], str],
        source_index: dict[tuple[str, str], str],
        transfers: dict[str, tuple[ComputationalEvent, bool]],
        run_of: dict[str, str | None],
        p: _Params,
    ) -> None:  # run_of may include context runs (memory writes of other runs)
        self.run_id = run_id
        self.evs = sorted(
            evs, key=lambda e: (e.time.start is None, e.time.start or 0, e.time.ordering_key)
        )
        self.artifacts = artifacts
        self.register = register
        self.source_index = source_index
        self.transfers = transfers
        self.run_of = run_of
        self.p = p
        self.by_id = {e.event_id: e for e in self.evs}
        self.obs = {e.event_id: list(e.derived_from) for e in self.evs}
        self.times = self._times()
        self.rels: list[dict[str, Any]] = []
        self.bits: dict[str, int] = {}
        self.anc: dict[str, int] = {}
        self.outputs: dict[str, list[_Inst]] = {}
        self.declared_instance: dict[str, str] = {}
        self.items: dict[str, list[str]] = {}
        self.consumed: dict[tuple[str, str], str] = {}  # (event id, content id) -> node
        self.exact: dict[str, list[_Inst]] = defaultdict(list)
        self.tok_index: dict[str, list[_Inst]] = defaultdict(list)
        self.df: dict[str, int] = {}
        self.pending: list[tuple[float, int, _Inst]] = []
        self.seq = 0
        self.scope: dict[str, tuple[str, ...]] = {}  # event -> declared CONTAINS chain from root
        self._declared_parts: dict[str, list[_Inst]] = {}
        self.content_of_node: dict[str, str] = {}

    # ── bookkeeping ────────────────────────────────────────────────────────────────────────
    def _times(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        last = 0.0
        for ev in self.evs:
            if ev.time.start is not None:
                s = ev.time.start.timestamp()
                e = ev.time.end.timestamp() if ev.time.end is not None else s
                last = max(last, s)
            else:  # no timestamp: keep the observed order
                s = e = last + EPS
                last = s
            out[ev.event_id] = (s, max(s, e))
        return out

    def bit(self, node: str) -> int:
        if node not in self.bits:
            self.bits[node] = 1 << len(self.bits)
        return self.bits[node]

    def anc_star(self, node: str) -> int:
        return self.anc.get(node, 0) | self.bit(node)

    def add_anc(self, node: str, bits: int) -> None:
        self.anc[node] = self.anc.get(node, 0) | bits

    def is_anc(self, a: str, b: str) -> bool:
        return bool(self.anc.get(b, 0) & self.bit(a))

    def tokens(self, content_id: str) -> frozenset[str]:
        toks = self.p.tokens_of(content_id)
        return toks if toks else frozenset({f"#{content_id}"})  # short value: exact identity

    def rel(
        self,
        rtype: RelType,
        tail: str,
        head: str,
        evidence: InfoEvidence,
        *,
        basis: Basis,
        confidence: float = 1.0,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        src = instance_event(tail) or tail.partition(":")[2]
        dst = instance_event(head) or head.partition(":")[2]
        strength = strength_of(evidence)
        self.rels.append(
            make_relation(
                View.INFORMATION,
                rtype,
                [tail],
                [head],
                basis=basis,
                confidence=confidence,
                run_id=self.run_id,
                evidence=list(dict.fromkeys([*self.obs.get(src, []), *self.obs.get(dst, [])])),
                derived_by=self.p.derived_by,
                attributes={
                    "evidence_type": evidence.value,
                    "strength": strength,
                    "mode": "HIGH_FIDELITY" if strength == "STRONG" else "BEST_EFFORT",
                    "source_event": src,
                    "target_event": dst,
                    **(attributes or {}),
                },
            )
        )

    # ── availability ───────────────────────────────────────────────────────────────────────
    def schedule(self, inst: _Inst) -> None:
        self.seq += 1
        heapq.heappush(self.pending, (inst.available, self.seq, inst))

    def advance(self, t: float) -> None:
        while self.pending and self.pending[0][0] <= t + EPS:
            self.make_available(heapq.heappop(self.pending)[2])

    def make_available(self, inst: _Inst) -> None:
        self.content_of_node[inst.node] = inst.content_id
        self.exact[inst.content_id].append(inst)
        if self.items.get(inst.content_id):
            return  # a decomposed list takes part in containment through its items
        inst.tokens = self.tokens(inst.content_id)
        if next(iter(inst.tokens)).startswith("#"):
            return
        # exact prefix filter: containment(y in x) >= theta requires x to contain one of y's
        # first len - ceil(theta*len) + 1 tokens under any fixed order (pigeonhole), so indexing
        # only those rarest tokens loses no match; every candidate is verified exactly
        keep = len(inst.tokens) - math.ceil(self.p.theta * len(inst.tokens) - EPS) + 1
        for t in sorted(inst.tokens, key=lambda k: (self.df.get(k, 0), k))[:keep]:
            self.tok_index[t].append(inst)

    def containment(self, toks: frozenset[str], exclude_event: str) -> list[tuple[_Inst, float]]:
        if next(iter(toks)).startswith("#"):
            return []
        seen: dict[str, _Inst] = {}
        for t in toks:
            for inst in self.tok_index.get(t, ()):
                seen.setdefault(inst.node, inst)
        out = []
        for inst in seen.values():
            if inst.event_id == exclude_event:
                continue
            c = len(inst.tokens & toks) / len(inst.tokens)
            if c >= self.p.theta - EPS:
                out.append((inst, c))
        return out

    # ── build ──────────────────────────────────────────────────────────────────────────────
    def build(self) -> list[dict[str, Any]]:
        for ev in self.evs:
            _, end = self.times[ev.event_id]
            ext = ev.kind in EXTERNAL_KINDS
            insts = []
            for k, ref in enumerate(ev.outputs):
                inst = _Inst(
                    node_instance(ev.event_id, f"o{k}"), ref.artifact_id, ev.event_id, end, ext
                )
                insts.append(inst)
                if ref.instance:
                    self.declared_instance[ref.instance] = inst.node
                self._decompose(ref.artifact_id)
            self.outputs[ev.event_id] = insts
        # token document frequency over every value of the run (for the prefix filter)
        contents = {r.artifact_id for ev in self.evs for r in (*ev.outputs, *ev.inputs)}
        contents |= {i for items in self.items.values() for i in items}
        for cid in contents:
            if cid:
                for t in self.p.tokens_of(cid):
                    self.df[t] = self.df.get(t, 0) + 1
        parents = {
            ev.event_id: [
                eid
                for lnk in ev.parents
                if (eid := self.source_index.get((lnk.key_space, lnk.value))) in self.by_id
            ]
            for ev in self.evs
        }
        container = {
            ev.event_id: next(
                (
                    eid
                    for lnk in ev.parents
                    if lnk.relation == "parent"
                    and (eid := self.source_index.get((lnk.key_space, lnk.value))) in self.by_id
                ),
                None,
            )
            for ev in self.evs
        }
        for ev in self.evs:
            chain: list[str] = []
            cur: str | None = ev.event_id
            while cur is not None and cur not in chain and len(chain) < 256:
                chain.append(cur)
                cur = container.get(cur)
            self.scope[ev.event_id] = tuple(reversed(chain))
        for ev in self.evs:
            start, _ = self.times[ev.event_id]
            self.advance(start)
            enode = node_event(ev.event_id)
            self.bit(enode)
            if ev.event_id in self.transfers:
                self._transfer(ev)
            for k, ref in enumerate(ev.inputs):
                self._input(ev, k, ref, parents[ev.event_id], start)
            self._outputs(ev)
        return self.rels

    def _decompose(self, content_id: str) -> None:
        if content_id in self.items:
            return
        self.items[content_id] = []
        content = self.artifacts.get(content_id)
        if content is None:
            return
        value = json.loads(content.content_json)
        if isinstance(value, list) and 1 < len(value) <= self.p.max_list_items:
            self.items[content_id] = [self.register(item, "item") for item in value]

    def _transfer(self, ev: ComputationalEvent) -> None:
        w, verified = self.transfers[ev.event_id]
        wnode, rnode = node_event(w.event_id), node_event(ev.event_id)
        other_run = self.run_of.get(w.event_id)
        self.rel(
            RelType.TRANSFERS,
            wnode,
            rnode,
            InfoEvidence.MEMORY_REFERENCE,
            basis=Basis.CONTENT_MATCH if verified else Basis.KEY_MATCH,
            confidence=1.0 if verified else 0.5,
            attributes={
                "memory": ev.object.canonical if ev.object else None,
                "key": ev.attributes.get("key"),
                "via": "memory",
                "value_match": "identical" if verified else "unverified",
                **({"cross_run": other_run} if other_run != self.run_id else {}),
            },
        )
        self.add_anc(rnode, self.anc_star(wnode))

    def _outputs(self, ev: ComputationalEvent) -> None:
        enode = node_event(ev.event_id)
        ext = ev.kind in EXTERNAL_KINDS
        for k, inst in enumerate(self.outputs[ev.event_id]):
            ref = ev.outputs[k]
            self.rel(
                RelType.PRODUCES,
                enode,
                inst.node,
                InfoEvidence.DECLARED_OUTPUT,
                basis=Basis.DECLARED,
                attributes={"content_id": inst.content_id, "role": ref.role, "label": ref.label},
            )
            self.add_anc(inst.node, self.anc_star(enode))
            toks = self.tokens(inst.content_id)
            decomposed = bool(self.items.get(inst.content_id))
            # declared structure outranks content: a memory read's value is explained by the
            # write it returned, a pass-through output (equal to one of the event's own inputs)
            # by that input — content inference into them would bypass the hierarchy
            explained = (
                ev.event_id in self.transfers and self.transfers[ev.event_id][1]
            ) or inst.content_id in {i.artifact_id for i in ev.inputs}
            # a decomposed list is matched through its items (one retrieved document at a time)
            wanted = ext or (ev.kind in CARRIER_KINDS and not explained)
            cands = [] if decomposed or not wanted else self.containment(toks, ev.event_id)
            if ext and not decomposed:
                self._similar(inst.node, cands)
            elif cands and ev.kind in CARRIER_KINDS and not explained:
                # a carrier moves or stores data it did not declare; a generator (model,
                # tool) explains its own output, so similar earlier text is no evidence of copying
                res = self._compute(inst.node, toks, cands)
                self._emit(res, inst.node, InfoEvidence.CONTENT_CONTAINMENT, RelType.DERIVES_FROM)
            self.schedule(inst)
            for j, iid in enumerate(self.items.get(inst.content_id, [])):
                if iid == inst.content_id:
                    continue
                item = _Inst(f"{inst.node}/i{j}", iid, ev.event_id, inst.available, inst.external)
                self.rel(
                    RelType.CONTAINS_ITEM,
                    inst.node,
                    item.node,
                    InfoEvidence.DECLARED_OUTPUT,
                    basis=Basis.CONTENT_MATCH,
                    attributes={"content_id": iid, "index": j},
                )
                self.add_anc(item.node, self.anc_star(inst.node))
                if ext:
                    self._similar(item.node, self.containment(self.tokens(iid), ev.event_id))
                self.schedule(item)

    def _similar(self, node: str, cands: list[tuple[_Inst, float]]) -> None:
        """Content that entered from outside the system (retrieved, external): similar earlier
        text is recorded as a similarity, never as a derivation."""
        for c, score in sorted(cands, key=lambda cs: cs[0].node):
            self.rel(
                RelType.MATCHES_CONTENT,
                c.node,
                node,
                InfoEvidence.CONTENT_MATCH_ONLY,
                basis=Basis.CONTENT_MATCH,
                confidence=score,
                attributes={"containment": round(score, 3)},
            )

    def _consume(
        self, ev: ComputationalEvent, node: str, evidence: InfoEvidence, base: dict[str, Any]
    ) -> None:
        self.rel(
            RelType.CONSUMES,
            node,
            node_event(ev.event_id),
            evidence,
            basis=Basis.DECLARED if strength_of(evidence) != "WEAK" else Basis.CONTENT_MATCH,
            attributes={**base, "resolution": "RESOLVED"},
        )
        self.consumed[(ev.event_id, base["content_id"])] = node
        self.add_anc(node_event(ev.event_id), self.anc_star(node))

    def _input(
        self, ev: ComputationalEvent, k: int, ref: ArtifactRef, parents: list[str], start: float
    ) -> None:
        cid = ref.artifact_id
        base = {"content_id": cid, "role": ref.role, "label": ref.label}
        # 1. declared references
        declared = [self._ref_node(s) for s in ref.sources]
        found = list(dict.fromkeys(n for n in declared if n))
        if not cid:
            # a reference-only input: the sensor named its sources but captured no content
            for src in found:
                self._consume(
                    ev, src, InfoEvidence.DECLARED_REFERENCE, {**base, "reference_only": True}
                )
            if len(found) < len(declared):
                self.rel(
                    RelType.CONSUMES,
                    node_instance(ev.event_id, f"in{k}"),
                    node_event(ev.event_id),
                    InfoEvidence.DECLARED_INPUT,
                    basis=Basis.DECLARED,
                    attributes={
                        **base,
                        "resolution": "UNRESOLVED",
                        "reference_only": True,
                        "unresolved_references": len(declared) - len(found),
                    },
                )
            return
        if found:
            if len(found) == 1 and self._content(found[0]) == cid:
                self._consume(ev, found[0], InfoEvidence.DECLARED_REFERENCE, base)
                return
            node = node_instance(ev.event_id, f"in{k}")
            self._declared_parts[node] = [
                _Inst(src, c, "", 0.0, False) for src in found if (c := self._content_any(src))
            ]
            for src in found:
                self.rel(
                    RelType.DERIVES_FROM,
                    src,
                    node,
                    InfoEvidence.DECLARED_REFERENCE,
                    basis=Basis.DECLARED,
                    attributes={"content_id": cid, "part_of_input": True},
                )
                self.add_anc(node, self.anc_star(src))
            self._constructed(ev, node, base, start, unresolved_refs=len(declared) - len(found))
            return
        # 2. correlation: a declared parent / linked span consumed or produced this value
        corr: list[str] = []
        for pid in parents:
            corr += [
                i.node
                for i in self.outputs.get(pid, [])
                if i.content_id == cid and i.available <= start + EPS
            ]
            if (pid, cid) in self.consumed:
                corr.append(self.consumed[(pid, cid)])
        corr = list(dict.fromkeys(corr))
        if len(corr) == 1:
            self._consume(ev, corr[0], InfoEvidence.CORRELATION_LINEAGE, base)
            return
        # 3./4. the identical value was produced earlier (same channel / object first)
        same = [i for i in self.exact.get(cid, []) if i.event_id != ev.event_id]
        if same:
            level = InfoEvidence.TEMPORAL_CONTENT_MATCH
            if ev.object is not None:
                channel = [
                    i
                    for i in same
                    if (pe := self.by_id.get(i.event_id)) is not None
                    and pe.object is not None
                    and pe.object.canonical == ev.object.canonical
                    and pe.kind in (EventKind.MESSAGE, EventKind.STATE_MUTATION)
                ]
                if channel:
                    same = channel
                    level = (
                        InfoEvidence.MESSAGE_REFERENCE
                        if self.by_id[channel[0].event_id].kind == EventKind.MESSAGE
                        else InfoEvidence.ARTIFACT_REFERENCE
                    )
            trivial = next(iter(self.tokens(cid))).startswith("#")
            if trivial and level == InfoEvidence.TEMPORAL_CONTENT_MATCH:
                # equal short values ("{}", "ok", 42) coincide far too often to link on their own
                self._constructed(ev, node_instance(ev.event_id, f"in{k}"), base, start)
                return
            node = node_instance(ev.event_id, f"in{k}")
            res = self._compute(node, self.tokens(cid), [(i, 1.0) for i in same], identical=True)
            if len(res.certain) == 1:
                # exactly one certain producer of this very value: the input IS that instance
                self._consume(ev, res.certain[0], level, base)
                self._emit_candidates(res, node_event(ev.event_id), level)
                return
            if not res.certain and not res.ambiguous:
                # every identical copy was set aside (e.g. a parallel replica's own input):
                # fall through to the next level, containment
                self._constructed(ev, node, base, start)
                return
            self._emit(res, node, level, RelType.DERIVES_FROM)
            self._constructed(ev, node, base, start, ambiguous=bool(res.ambiguous), identical=True)
            return
        # 5./6. a constructed value, or one of unknown origin
        self._constructed(ev, node_instance(ev.event_id, f"in{k}"), base, start)

    def _constructed(
        self,
        ev: ComputationalEvent,
        node: str,
        base: dict[str, Any],
        start: float,
        *,
        unresolved_refs: int = 0,
        identical: bool = False,
        ambiguous: bool = False,
    ) -> None:
        """An input value no observed event produced as such (or several did, ambiguously)."""
        if not identical:
            toks = self.tokens(base["content_id"])
            if self.anc.get(node):
                # declared sources outrank content inference: look only for text they lack
                declared_text: frozenset[str] = frozenset().union(
                    *(self.tokens(i.content_id) for i in self._declared_parts.get(node, []))
                )
                toks = frozenset(toks - declared_text)
            cands = self.containment(toks, ev.event_id) if len(toks) > self.p.seam else []
            if cands:
                res = self._compute(node, toks, cands)
                self._emit(res, node, InfoEvidence.CONTENT_CONTAINMENT, RelType.DERIVES_FROM)
                ambiguous = ambiguous or bool(res.ambiguous)
        has_parents = bool(self.anc.get(node))
        status = "RESOLVED" if has_parents else ("AMBIGUOUS" if ambiguous else "UNRESOLVED")
        self.rel(
            RelType.CONSUMES,
            node,
            node_event(ev.event_id),
            InfoEvidence.DECLARED_INPUT,
            basis=Basis.DECLARED,
            attributes={
                **base,
                "resolution": status,
                "constructed": not identical,
                **({"unresolved_references": unresolved_refs} if unresolved_refs else {}),
            },
        )
        self.consumed[(ev.event_id, base["content_id"])] = node
        self.add_anc(node_event(ev.event_id), self.anc_star(node))
        # the consumed value existed from the moment it was consumed
        self.make_available(_Inst(node, base["content_id"], ev.event_id, start, False))

    def _content_any(self, node: str) -> str | None:
        return self.content_of_node.get(node) or self._content(node)

    def _content(self, node: str) -> str | None:
        eid = instance_event(node)
        rest = node.split("/")[1:]
        if eid is None or len(rest) != 1 or not rest[0].startswith("o"):
            return None
        insts = self.outputs.get(eid, [])
        k = int(rest[0][1:]) if rest[0][1:].isdigit() else -1
        return insts[k].content_id if 0 <= k < len(insts) else None

    def _ref_node(self, ref: str) -> str | None:
        """``<key_space>:<source id>/o<k>`` or a sensor-declared instance id."""
        if ref in self.declared_instance:
            return self.declared_instance[ref]
        head, _, slot = ref.rpartition("/")
        ks, _, sid = head.partition(":")
        eid = self.source_index.get((ks, sid))
        if eid is None or not slot.startswith("o") or not slot[1:].isdigit():
            return None
        insts = self.outputs.get(eid, [])
        k = int(slot[1:])
        return insts[k].node if k < len(insts) else None

    def _signature(self, event_id: str) -> tuple[str, str, str | None] | None:
        ev = self.by_id.get(event_id)
        if ev is None:
            return None
        return (ev.kind.value, ev.operation, ev.object.canonical if ev.object else None)

    def _replicas(self, a: str, b: str) -> bool:
        """The same operation run in two parallel scopes (e.g. two workers' retrievals)."""
        if a == b or self._signature(a) is None or self._signature(a) != self._signature(b):
            return False
        sa, sb = self.scope.get(a, ()), self.scope.get(b, ())
        return len(sa) > 1 and len(sb) > 1 and sa[-2] != sb[-2]

    def _compute(
        self,
        target: str,
        target_tokens: frozenset[str],
        candidates: list[tuple[_Inst, float]],
        *,
        identical: bool = False,
    ) -> _Resolution:
        """Resolve content candidates for ``target`` without choosing arbitrarily.

        1. Candidates already ancestors of the target add nothing.
        2. Parallel replicas (the same operation in sibling scopes): of identical copies, the
           one produced in the scope closest to the target is kept; an identical input that a
           parallel replica constructed is an independent construction, not a source.
        3. A candidate whose shared text is fully carried by a later candidate it flowed into
           is a shortcut of that candidate (transitive reduction; reachability is kept).
        4. A remaining candidate m is RESOLVED unless another explanation fits the content as
           well: a copy carrying all of m's shared text, or m's own upstream candidates
           covering it (the target could have been built from them without m — a discarded
           intermediate looks exactly like this). Such an m is kept as a CANDIDATE_SOURCE.
        5. For an ambiguous m, a candidate that is an ancestor under m and under every
           alternative is certain when the target cannot be explained without it: removing
           it (and what derives from it) leaves more unexplained text than one seam makes.
        """
        theta = self.p.theta
        seam = self.p.seam
        target_event = instance_event(target) or target.partition(":")[2]
        target_scope = self.scope.get(target_event, ())

        def explains(part: frozenset[str], by: frozenset[str], parts: int) -> bool:
            """Could a value assembled from ``parts`` pieces with text ``by`` carry ``part``?
            It must cover at least ``theta`` of it, and leave uncovered no more than the
            shingles that span the seams of such an assembly (``seam`` per seam, parts+1 seams).
            """
            covered = len(part & by)
            return covered >= theta * len(part) and len(part) - covered <= seam * (parts + 1)

        def locality(n: str) -> int:
            """Depth of the innermost declared span containing both candidate and target."""
            sc = self.scope.get(instance_event(n) or "", ())
            depth = 0
            for a, b in zip(sc, target_scope, strict=False):
                if a != b:
                    break
                depth += 1
            return depth

        target_anc = self.anc.get(target, 0)
        cands = {c.node: (c, s) for c, s in candidates if not (target_anc & self.bit(c.node))}
        # 2. parallel replicas
        target_is_input = "/in" in target
        for n in list(cands):
            inst_n = cands[n][0]
            if (
                target_is_input
                and "/in" in n
                and self._replicas(inst_n.event_id, target_event)
                and inst_n.content_id == self._target_content(target)
            ):
                del cands[n]  # the same input, constructed independently by a parallel replica
        replica_local: set[str] = set()
        for n in sorted(cands):
            inst_n = cands[n][0]
            for n2 in sorted(cands):
                inst_n2 = cands[n2][0]
                if (
                    n2 != n
                    and inst_n2.content_id == inst_n.content_id
                    and self._replicas(inst_n.event_id, inst_n2.event_id)
                    and locality(n2) > locality(n)
                ):
                    replica_local.add(n2)
                    cands.pop(n, None)
                    break
        res = _Resolution({n: sc for n, (_, sc) in cands.items()})
        if not cands:
            return res

        def toks(n: str) -> frozenset[str]:
            c = cands[n][0]
            return c.tokens or frozenset({f"#{c.content_id}"})

        shared = {n: (toks(n) if identical else toks(n) & target_tokens) or toks(n) for n in cands}
        nodes = sorted(cands)
        explained_text: frozenset[str] = frozenset().union(*(shared[n] for n in nodes))
        dominated = {
            c
            for c in nodes
            if any(d != c and self.is_anc(c, d) and not (shared[c] - toks(d)) for d in nodes)
        }
        maximal = [c for c in nodes if c not in dominated]

        def necessary(c: str) -> bool:
            rest = [d for d in nodes if d != c and not self.is_anc(c, d)]
            union: frozenset[str] = (
                frozenset().union(*(toks(d) for d in rest)) if rest else frozenset()
            )
            return len(explained_text - union) > seam

        certain: set[str] = set()
        for m in maximal:
            alternatives: list[list[str]] = [
                [m2] for m2 in maximal if m2 != m and not (shared[m] - toks(m2))
            ]
            below = [c for c in nodes if c != m and self.is_anc(c, m)]
            if below:
                # the most upstream way to build the target without m: drop every member whose
                # text its own ancestors in the set already explain
                layer = set(below)
                for c in sorted(below, key=lambda n: -bin(self.anc.get(n, 0)).count("1")):
                    ups = [d for d in layer if d != c and self.is_anc(d, c)]
                    if ups and explains(
                        shared[c], frozenset().union(*(toks(d) for d in ups)), len(ups)
                    ):
                        layer.discard(c)
                up = sorted(layer)
                union = frozenset().union(*(toks(c) for c in up))
                if explains(shared[m], union, len(up)):
                    alternatives.append(up)
            if not alternatives:
                certain.add(m)
                continue
            res.ambiguous[m] = alternatives
            common = self.anc_star(m)
            for alt in alternatives:
                bits = 0
                for a in alt:
                    bits |= self.anc_star(a)
                common &= bits
            certain.update(c for c in nodes if common & self.bit(c) and necessary(c))
        # link from the latest certain candidates (earlier ones are reachable through them)
        res.certain = sorted(
            c for c in certain if not any(d != c and self.is_anc(c, d) for d in certain)
        )
        for c in certain:
            res.ambiguous.pop(c, None)
        res.local = sorted(replica_local & set(res.certain))
        return res

    def _target_content(self, node: str) -> str | None:
        eid = instance_event(node)
        ev = self.by_id.get(eid or "")
        slot = node.split("/")[-1]
        if ev is not None and slot.startswith("in") and slot[2:].isdigit():
            k = int(slot[2:])
            return ev.inputs[k].artifact_id if k < len(ev.inputs) else None
        return self._content_any(node)

    def _emit(self, res: _Resolution, target: str, evidence: InfoEvidence, flow: RelType) -> None:
        for c in res.certain:
            self.rel(
                flow,
                c,
                target,
                evidence,
                basis=Basis.CONTENT_MATCH,
                confidence=res.score[c],
                attributes={
                    "resolution": "RESOLVED",
                    "containment": round(res.score[c], 3),
                    **({"certain_under_alternatives": True} if res.ambiguous else {}),
                    **({"scope_locality": True} if c in res.local else {}),
                },
            )
            self.add_anc(target, self.anc_star(c))
        self._emit_candidates(res, target, evidence)

    def _emit_candidates(self, res: _Resolution, target: str, evidence: InfoEvidence) -> None:
        for m, alts in sorted(res.ambiguous.items()):
            self.rel(
                RelType.CANDIDATE_SOURCE,
                m,
                target,
                evidence,
                basis=Basis.CONTENT_MATCH,
                confidence=res.score[m],
                attributes={
                    "resolution": "AMBIGUOUS",
                    "containment": round(res.score[m], 3),
                    "alternatives": alts,
                    "certain": res.certain,
                },
            )


@dataclass
class _Resolution:
    score: dict[str, float]
    certain: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[list[str]]] = field(default_factory=dict)
    local: list[str] = field(default_factory=list)  # certain because of replica locality


def memory_transfers(
    events: Sequence[ComputationalEvent], run_of: dict[str, str | None]
) -> dict[str, tuple[ComputationalEvent, bool]]:
    """read event id -> (the write whose value it returned, value verified).

    The most recent earlier write of the same store and key in the same run whose value is not
    contradicted by the read; else the most recent earlier such write in any run (a store that
    outlives a run, such as a cache). A read that returned a different value than every write
    of its key is linked to none (stale or written by something unobserved)."""
    ordered = sorted(
        events, key=lambda e: (e.time.start is None, e.time.start or 0, e.time.ordering_key)
    )
    writes: dict[tuple[str, Any], list[tuple[ComputationalEvent, set[str]]]] = defaultdict(list)
    out: dict[str, tuple[ComputationalEvent, bool]] = {}
    for ev in ordered:
        if ev.kind != EventKind.MEMORY_ACCESS or ev.object is None:
            continue
        attrs = ev.attributes
        key = (ev.object.canonical, attrs.get("key"))
        if attrs.get("access") == "write" or "write" in ev.facets:
            written = {a.artifact_id for a in ev.inputs if a.role == "value"} or {
                a.artifact_id for a in ev.inputs
            }
            writes[key].append((ev, written))
            continue
        if not (attrs.get("access") == "read" or "read" in ev.facets) or attrs.get("key") is None:
            continue
        read = {a.artifact_id for a in ev.outputs if a.role == "value"} or {
            a.artifact_id for a in ev.outputs
        }
        run = run_of.get(ev.event_id)
        prior = writes.get(key, [])
        for pool in ([w for w in prior if run_of.get(w[0].event_id) == run], prior):
            match = next(
                (
                    (w, bool(read & written))
                    for w, written in reversed(pool)
                    if not (read and written and not (read & written))
                ),
                None,
            )
            if match is not None:
                out[ev.event_id] = match
                break
    return out


def build_information_instances(
    events: Sequence[ComputationalEvent],
    run_of: dict[str, str | None],
    artifacts: dict[str, ArtifactContent],
    register: Callable[[Any, str], str],
    source_index: dict[tuple[str, str], str],
    *,
    derived_by: str,
    tokens_of: Callable[[str], frozenset[str]],
    containment_threshold: float,
    max_list_items: int,
    shingle_size: int,
    memory_context: Sequence[ComputationalEvent] = (),
    memory_context_runs: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    params = _Params(tokens_of, shingle_size - 1, containment_threshold, max_list_items, derived_by)
    transfers = memory_transfers(
        [*events, *memory_context], {**(memory_context_runs or {}), **run_of}
    )
    by_run: dict[str | None, list[ComputationalEvent]] = defaultdict(list)
    for ev in events:
        by_run[run_of.get(ev.event_id)].append(ev)
    rels: list[dict[str, Any]] = []
    for run_id in sorted(by_run, key=lambda r: r or ""):
        rels.extend(
            _Run(
                run_id,
                by_run[run_id],
                artifacts,
                register,
                source_index,
                transfers,
                {**(memory_context_runs or {}), **run_of},
                params,
            ).build()
        )
    return rels
