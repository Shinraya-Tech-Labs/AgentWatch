"""
MEM-002 — Causal Memory Graph.

Temporal knowledge graph: every decision has causal edges to
the context that caused it, the constraints that shaped it, and
the outcome it produced. Supports "why did we choose X?" queries.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class EdgeKind(str, Enum):
    CAUSED_BY = "caused_by"
    CONSTRAINED_BY = "constrained_by"
    PRODUCED = "produced"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


@dataclass
class CausalNode:
    node_id: str
    kind: str  # decision | context | constraint | outcome
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalEdge:
    src: str
    dst: str
    kind: EdgeKind
    weight: float = 1.0


class CausalGraph:
    """Bounded causal graph with reverse lookups to prevent memory exhaustion."""

    def __init__(self, max_nodes: int = 1000) -> None:
        self.max_nodes = max_nodes
        self._nodes: dict[str, CausalNode] = {}
        self._out: dict[str, list[CausalEdge]] = {}
        self._in: dict[str, list[CausalEdge]] = {}

    def add_node(self, node: CausalNode) -> CausalNode:
        if len(self._nodes) >= self.max_nodes and node.node_id not in self._nodes:
            # Evict oldest node based on timestamp
            oldest_id = min(self._nodes.keys(), key=lambda k: self._nodes[k].timestamp)
            self._nodes.pop(oldest_id, None)
            self._out.pop(oldest_id, None)
            self._in.pop(oldest_id, None)
            # Remove references in other nodes' incoming/outgoing edges
            for adj in self._out.values():
                adj[:] = [e for e in adj if e.dst != oldest_id]
            for adj in self._in.values():
                adj[:] = [e for e in adj if e.src != oldest_id]

        self._nodes[node.node_id] = node
        self._out.setdefault(node.node_id, [])
        self._in.setdefault(node.node_id, [])
        return node

    def add_edge(self, src: str, dst: str, kind: EdgeKind, weight: float = 1.0) -> CausalEdge:
        if src not in self._nodes or dst not in self._nodes:
            raise ValueError("both endpoints must exist")
        edge = CausalEdge(src=src, dst=dst, kind=kind, weight=weight)
        self._out.setdefault(src, []).append(edge)
        self._in.setdefault(dst, []).append(edge)
        return edge

    def explain(self, decision_id: str, *, max_depth: int = 4) -> list[CausalNode]:
        """BFS upstream from a decision to surface the causal chain."""
        if decision_id not in self._nodes:
            return []
        seen = {decision_id}
        chain: list[CausalNode] = [self._nodes[decision_id]]
        frontier: deque[tuple[str, int]] = deque([(decision_id, 0)])
        while frontier:
            node_id, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            for edge in self._in.get(node_id, []):
                if edge.src in seen:
                    continue
                seen.add(edge.src)
                chain.append(self._nodes[edge.src])
                frontier.append((edge.src, depth + 1))
        return chain

    def downstream(self, node_id: str, *, max_depth: int = 4) -> list[CausalNode]:
        if node_id not in self._nodes:
            return []
        seen = {node_id}
        out: list[CausalNode] = []
        frontier: deque[tuple[str, int]] = deque([(node_id, 0)])
        while frontier:
            n, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            for edge in self._out.get(n, []):
                if edge.dst in seen:
                    continue
                seen.add(edge.dst)
                out.append(self._nodes[edge.dst])
                frontier.append((edge.dst, depth + 1))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                n.__dict__ | {"timestamp": n.timestamp.isoformat()} for n in self._nodes.values()
            ],
            "edges": [
                e.__dict__ | {"kind": e.kind.value} for adj in self._out.values() for e in adj
            ],
        }

    @property
    def nodes(self) -> dict[str, CausalNode]:
        return dict(self._nodes)


__all__ = ["CausalGraph", "CausalNode", "CausalEdge", "EdgeKind"]
