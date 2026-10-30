"""
MAG-001 — Inter-Agent Causal DAG.

Which agent caused which action. Visual directed acyclic graph.
Trace failures across agent boundaries.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class DagNode:
    node_id: str
    agent_id: str
    action: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DagEdge:
    src: str
    dst: str
    kind: str  # delegate | reply | broadcast | causes
    weight: float = 1.0


class InterAgentDAG:
    """Append-only DAG with cycle detection."""

    def __init__(self) -> None:
        self._nodes: dict[str, DagNode] = {}
        self._edges: list[DagEdge] = []
        self._out: dict[str, list[str]] = {}
        self._in: dict[str, list[str]] = {}

    def add_node(
        self,
        node_id: str,
        agent_id: str,
        action: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> DagNode:
        node = DagNode(
            node_id=node_id,
            agent_id=agent_id,
            action=action,
            timestamp=datetime.now(UTC),
            metadata=metadata or {},
        )
        self._nodes[node_id] = node
        self._out.setdefault(node_id, [])
        self._in.setdefault(node_id, [])
        return node

    def add_edge(self, src: str, dst: str, kind: str = "causes") -> DagEdge:
        if src not in self._nodes or dst not in self._nodes:
            raise ValueError("both endpoints must be added first")
        if self._creates_cycle(src, dst):
            raise ValueError(f"adding {src}->{dst} would create a cycle")
        edge = DagEdge(src=src, dst=dst, kind=kind)
        self._edges.append(edge)
        self._out.setdefault(src, []).append(dst)
        self._in.setdefault(dst, []).append(src)
        return edge

    def _creates_cycle(self, src: str, dst: str) -> bool:
        # DFS from dst, see if we can reach src
        seen: set[str] = set()
        stack: list[str] = [dst]
        while stack:
            n = stack.pop()
            if n == src:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self._out.get(n, []))
        return False

    def trace_failure(self, failed_node_id: str) -> list[DagNode]:
        if failed_node_id not in self._nodes:
            return []
        chain: list[DagNode] = []
        seen = {failed_node_id}
        frontier: deque[str] = deque([failed_node_id])
        while frontier:
            n = frontier.popleft()
            chain.append(self._nodes[n])
            for parent in self._in.get(n, []):
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return chain

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {**n.__dict__, "timestamp": n.timestamp.isoformat()} for n in self._nodes.values()
            ],
            "edges": [e.__dict__ for e in self._edges],
        }

    @property
    def nodes(self) -> dict[str, DagNode]:
        return dict(self._nodes)

    @property
    def edges(self) -> list[DagEdge]:
        return list(self._edges)


__all__ = ["InterAgentDAG", "DagNode", "DagEdge"]
