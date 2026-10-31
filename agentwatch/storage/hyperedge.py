"""Distributed Hyperedge Relation Store with O(1) Bi-Directional Lineage Traversal (Issue #736 / ADR-0005).

Provides high-throughput partitioned hyperedge storage partitioned across:
- Views: EXECUTION, INFORMATION, CAUSAL
- Shards: (tenant_id, run_id)
- Dual-Adjacency Indexing: O(1) forward and reverse traversal lookups
- Thread-safe append-only concurrency with immutable run sealing
- Sub-millisecond recursive backward origin and forward impact calculations.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class HyperedgeView(StrEnum):
    """The three canonical graph views defined in ADR-0005."""

    EXECUTION = "EXECUTION"
    INFORMATION = "INFORMATION"
    CAUSAL = "CAUSAL"


class SealedRunError(RuntimeError):
    """Raised when attempting to append relations to an immutably sealed run."""

    pass


@dataclass(frozen=True, slots=True)
class Hyperedge:
    """An n-ary computational relation connecting arbitrary vertex sets."""

    rel_id: str
    tenant_id: str
    run_id: str
    interp_id: str
    view: str  # HyperedgeView or str
    rel_type: str
    tail: tuple[str, ...]  # Input / cause / source vertices
    head: tuple[str, ...]  # Output / effect / target vertices
    basis: str = "declared"
    evidence_class: str | None = None
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "rel_id": self.rel_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "interp_id": self.interp_id,
            "view": str(self.view),
            "type": self.rel_type,
            "tail": list(self.tail),
            "head": list(self.head),
            "basis": self.basis,
            "evidence_class": self.evidence_class,
            "confidence": self.confidence,
            "attributes": dict(self.attributes),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Hyperedge:
        return cls(
            rel_id=d["rel_id"],
            tenant_id=d["tenant_id"],
            run_id=d["run_id"],
            interp_id=d.get("interp_id", "default"),
            view=d["view"],
            rel_type=d.get("type", d.get("rel_type", "REL")),
            tail=tuple(d.get("tail") or ()),
            head=tuple(d.get("head") or ()),
            basis=d.get("basis", "declared"),
            evidence_class=d.get("evidence_class"),
            confidence=float(d.get("confidence", 1.0)),
            attributes=dict(d.get("attributes") or {}),
            created_at=float(d.get("created_at", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class LineageResult:
    """Provenance derivation result from backward origin traversal."""

    target_node: str
    root_nodes: tuple[str, ...]
    max_depth: int
    traversed_node_count: int
    traversed_edge_count: int
    execution_time_ms: float
    nodes_by_depth: dict[int, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImpactResult:
    """Blast radius result from forward impact traversal."""

    source_node: str
    affected_nodes: tuple[str, ...]
    max_depth: int
    traversed_node_count: int
    traversed_edge_count: int
    execution_time_ms: float
    nodes_by_depth: dict[int, list[str]] = field(default_factory=dict)


class HyperedgeShard:
    """In-memory partition shard for a single (tenant_id, run_id).

    Maintains dual indexed adjacency lists providing O(1) amortized
    lookup per hyperedge during graph walks.
    """

    def __init__(self, tenant_id: str, run_id: str) -> None:
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.is_sealed = False
        self.sealed_at: float | None = None

        # Lock for concurrent appends within this shard
        self._lock = threading.Lock()

        # Storage structures
        self.edges: dict[str, Hyperedge] = {}
        # Forward adjacency: node -> list of Hyperedge (where node in edge.tail)
        self.forward_edges: dict[str, list[Hyperedge]] = defaultdict(list)
        # Reverse adjacency: node -> list of Hyperedge (where node in edge.head)
        self.reverse_edges: dict[str, list[Hyperedge]] = defaultdict(list)

        # Dual index for O(1) vertex adjacency
        self.forward_neighbors: dict[str, set[str]] = defaultdict(set)
        self.reverse_neighbors: dict[str, set[str]] = defaultdict(set)

        # View index: view -> set of rel_ids
        self.view_edges: dict[str, set[str]] = defaultdict(set)

    def append(self, edge: Hyperedge) -> None:
        with self._lock:
            if self.is_sealed:
                raise SealedRunError(
                    f"Cannot append relation {edge.rel_id}: run '{self.run_id}' for tenant '{self.tenant_id}' is immutably sealed."
                )

            # Store edge
            self.edges[edge.rel_id] = edge
            self.view_edges[edge.view].add(edge.rel_id)

            # Update dual adjacency structures
            for t in edge.tail:
                self.forward_edges[t].append(edge)
                for h in edge.head:
                    self.forward_neighbors[t].add(h)

            for h in edge.head:
                self.reverse_edges[h].append(edge)
                for t in edge.tail:
                    self.reverse_neighbors[h].add(t)

    def append_batch(self, edges: Sequence[Hyperedge]) -> int:
        with self._lock:
            if self.is_sealed:
                raise SealedRunError(
                    f"Cannot append batch: run '{self.run_id}' for tenant '{self.tenant_id}' is immutably sealed."
                )

            count = 0
            for edge in edges:
                self.edges[edge.rel_id] = edge
                self.view_edges[edge.view].add(edge.rel_id)
                for t in edge.tail:
                    self.forward_edges[t].append(edge)
                    for h in edge.head:
                        self.forward_neighbors[t].add(h)
                for h in edge.head:
                    self.reverse_edges[h].append(edge)
                    for t in edge.tail:
                        self.reverse_neighbors[h].add(t)
                count += 1
            return count

    def seal(self) -> None:
        with self._lock:
            self.is_sealed = True
            self.sealed_at = datetime.now(UTC).timestamp()

    # ── Traversal Engine ──────────────────────────────────────────────────────

    def backward_origin(
        self,
        target_node: str,
        max_depth: int = 50,
        views: set[str] | None = None,
    ) -> LineageResult:
        """Trace origins backward from target_node in O(1) amortized lookup per step."""
        t0 = time.perf_counter()

        visited_nodes: set[str] = {target_node}
        visited_edges: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(target_node, 0)])

        nodes_by_depth: dict[int, list[str]] = defaultdict(list)
        nodes_by_depth[0].append(target_node)
        root_nodes: set[str] = set()
        max_seen_depth = 0

        while queue:
            curr_node, depth = queue.popleft()
            max_seen_depth = max(max_seen_depth, depth)

            incoming_edges = self.reverse_edges.get(curr_node, [])
            valid_incoming = 0

            if depth < max_depth:
                for edge in incoming_edges:
                    if views and edge.view not in views:
                        continue
                    valid_incoming += 1
                    visited_edges.add(edge.rel_id)

                    for parent in edge.tail:
                        if parent not in visited_nodes:
                            visited_nodes.add(parent)
                            nodes_by_depth[depth + 1].append(parent)
                            queue.append((parent, depth + 1))

            if (valid_incoming == 0 or depth >= max_depth) and curr_node != target_node:
                root_nodes.add(curr_node)

        # If target had no parents, it is its own root
        if not root_nodes and not self.reverse_edges.get(target_node):
            root_nodes.add(target_node)

        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000.0

        return LineageResult(
            target_node=target_node,
            root_nodes=tuple(sorted(root_nodes)),
            max_depth=max_seen_depth,
            traversed_node_count=len(visited_nodes),
            traversed_edge_count=len(visited_edges),
            execution_time_ms=elapsed_ms,
            nodes_by_depth=dict(nodes_by_depth),
        )

    def forward_impact_radius(
        self,
        source_node: str,
        max_depth: int = 50,
        views: set[str] | None = None,
    ) -> ImpactResult:
        """Trace forward blast radius from source_node in O(1) amortized lookup per step."""
        t0 = time.perf_counter()

        visited_nodes: set[str] = {source_node}
        visited_edges: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(source_node, 0)])

        nodes_by_depth: dict[int, list[str]] = defaultdict(list)
        nodes_by_depth[0].append(source_node)
        max_seen_depth = 0

        while queue:
            curr_node, depth = queue.popleft()
            max_seen_depth = max(max_seen_depth, depth)

            if depth >= max_depth:
                continue

            outgoing_edges = self.forward_edges.get(curr_node, [])
            for edge in outgoing_edges:
                if views and edge.view not in views:
                    continue
                visited_edges.add(edge.rel_id)

                for child in edge.head:
                    if child not in visited_nodes:
                        visited_nodes.add(child)
                        nodes_by_depth[depth + 1].append(child)
                        queue.append((child, depth + 1))

        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000.0

        return ImpactResult(
            source_node=source_node,
            affected_nodes=tuple(sorted(visited_nodes - {source_node})),
            max_depth=max_seen_depth,
            traversed_node_count=len(visited_nodes),
            traversed_edge_count=len(visited_edges),
            execution_time_ms=elapsed_ms,
            nodes_by_depth=dict(nodes_by_depth),
        )

    def derivation_depth(
        self,
        node: str,
        views: set[str] | None = None,
    ) -> int:
        """Calculate the max derivation depth from root predecessors."""
        lineage = self.backward_origin(node, max_depth=1000, views=views)
        return lineage.max_depth


class HyperedgeRelationStore:
    """Partitioned distributed hyperedge relation store (ADR-0005).

    Manages per-(tenant_id, run_id) shards with append-only guarantees,
    immutable relation sealing, and sub-millisecond bidirectional graph queries.
    """

    def __init__(self) -> None:
        self._shards: dict[tuple[str, str], HyperedgeShard] = {}
        self._store_lock = threading.RLock()

    def get_or_create_shard(self, tenant_id: str, run_id: str) -> HyperedgeShard:
        key = (tenant_id, run_id)
        with self._store_lock:
            if key not in self._shards:
                self._shards[key] = HyperedgeShard(tenant_id, run_id)
            return self._shards[key]

    def get_shard(self, tenant_id: str, run_id: str) -> HyperedgeShard | None:
        key = (tenant_id, run_id)
        with self._store_lock:
            return self._shards.get(key)

    def append(self, edge: Hyperedge) -> None:
        """Append an n-ary hyperedge relation into its partitioned shard."""
        shard = self.get_or_create_shard(edge.tenant_id, edge.run_id)
        shard.append(edge)

    def append_batch(self, tenant_id: str, run_id: str, edges: Sequence[Hyperedge]) -> int:
        """Batch append hyperedges into a partition shard."""
        shard = self.get_or_create_shard(tenant_id, run_id)
        return shard.append_batch(edges)

    def seal_run(self, tenant_id: str, run_id: str) -> None:
        """Immutably seal relation store for completed run."""
        shard = self.get_or_create_shard(tenant_id, run_id)
        shard.seal()

    def is_run_sealed(self, tenant_id: str, run_id: str) -> bool:
        shard = self.get_shard(tenant_id, run_id)
        return shard.is_sealed if shard else False

    def get_hyperedge(self, tenant_id: str, run_id: str, rel_id: str) -> Hyperedge | None:
        shard = self.get_shard(tenant_id, run_id)
        if not shard:
            return None
        return shard.edges.get(rel_id)

    def backward_origin(
        self,
        tenant_id: str,
        run_id: str,
        target_node: str,
        max_depth: int = 50,
        views: set[str] | None = None,
    ) -> LineageResult:
        shard = self.get_shard(tenant_id, run_id)
        if not shard:
            return LineageResult(
                target_node=target_node,
                root_nodes=(target_node,),
                max_depth=0,
                traversed_node_count=1,
                traversed_edge_count=0,
                execution_time_ms=0.0,
            )
        return shard.backward_origin(target_node, max_depth, views)

    def forward_impact_radius(
        self,
        tenant_id: str,
        run_id: str,
        source_node: str,
        max_depth: int = 50,
        views: set[str] | None = None,
    ) -> ImpactResult:
        shard = self.get_shard(tenant_id, run_id)
        if not shard:
            return ImpactResult(
                source_node=source_node,
                affected_nodes=(),
                max_depth=0,
                traversed_node_count=1,
                traversed_edge_count=0,
                execution_time_ms=0.0,
            )
        return shard.forward_impact_radius(source_node, max_depth, views)

    def derivation_depth(
        self,
        tenant_id: str,
        run_id: str,
        node: str,
        views: set[str] | None = None,
    ) -> int:
        shard = self.get_shard(tenant_id, run_id)
        if not shard:
            return 0
        return shard.derivation_depth(node, views)

    def total_edges_count(self) -> int:
        with self._store_lock:
            return sum(len(s.edges) for s in self._shards.values())
