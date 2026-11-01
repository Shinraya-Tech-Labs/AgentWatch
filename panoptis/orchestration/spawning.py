"""
MAG-008 — Agent Spawning Tracker.

Track dynamically spawned agents. Prevent runaway agent spawning by
enforcing a maximum tree depth and total agent count.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class SpawnNode:
    agent_id: str
    parent_id: str | None
    spawned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    depth: int = 0


class SpawnLimitExceeded(Exception):  # noqa: N818 — semantic name preserved for API stability
    pass


class SpawningTracker:
    """Enforce depth / count limits and surface the spawn tree."""

    def __init__(self, *, max_depth: int = 5, max_total: int = 50):
        self.max_depth = max_depth
        self.max_total = max_total
        self._nodes: dict[str, SpawnNode] = {}
        self._children: dict[str, list[str]] = {}

    def register(self, agent_id: str, parent_id: str | None = None) -> SpawnNode:
        if agent_id in self._nodes:
            return self._nodes[agent_id]
        if len(self._nodes) >= self.max_total:
            raise SpawnLimitExceeded(f"max_total {self.max_total} agents reached")

        depth = 0
        if parent_id:
            parent = self._nodes.get(parent_id)
            if parent is None:
                raise ValueError(f"unknown parent: {parent_id}")
            depth = parent.depth + 1
            if depth > self.max_depth:
                raise SpawnLimitExceeded(f"max_depth {self.max_depth} exceeded at {agent_id}")

        node = SpawnNode(agent_id=agent_id, parent_id=parent_id, depth=depth)
        self._nodes[agent_id] = node
        self._children.setdefault(agent_id, [])
        if parent_id:
            self._children.setdefault(parent_id, []).append(agent_id)
        return node

    def descendants(self, agent_id: str) -> list[SpawnNode]:
        out: list[SpawnNode] = []
        frontier: deque[str] = deque([agent_id])
        while frontier:
            n = frontier.popleft()
            for c in self._children.get(n, []):
                out.append(self._nodes[c])
                frontier.append(c)
        return out

    def __len__(self) -> int:
        return len(self._nodes)

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {**n.__dict__, "spawned_at": n.spawned_at.isoformat()} for n in self._nodes.values()
            ],
            "children": dict(self._children),
        }


__all__ = ["SpawningTracker", "SpawnNode", "SpawnLimitExceeded"]
