"""
OBS-004 — Trajectory Mapping.

Builds a graph of the agent's actual execution path from a session's events,
compares it to the intended path (if available), and surfaces:
  - recursive loops
  - repeated tool calls
  - dead-end branches
  - deviation from the original goal
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.schema import AgentEvent, EventType


@dataclass
class TrajectoryNode:
    node_id: str
    label: str
    event_type: str
    step: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryEdge:
    src: str
    dst: str
    weight: int = 1


@dataclass
class TrajectoryReport:
    nodes: list[TrajectoryNode]
    edges: list[TrajectoryEdge]
    loops: list[list[str]]
    repeated_tools: dict[str, int]
    dead_ends: list[str]
    deviation_score: float  # 0..1 — 0 = on track, 1 = completely off-goal

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.__dict__ for n in self.nodes],
            "edges": [e.__dict__ for e in self.edges],
            "loops": self.loops,
            "repeated_tools": self.repeated_tools,
            "dead_ends": self.dead_ends,
            "deviation_score": self.deviation_score,
        }


def _node_label(event: AgentEvent) -> str:
    if event.tool_call:
        return f"{event.event_type.value}:{event.tool_call.tool_name}"
    return event.event_type.value


def build_trajectory(
    events: list[AgentEvent],
    *,
    intended_tools: list[str] | None = None,
) -> TrajectoryReport:
    """Build a trajectory graph from an ordered list of events."""
    nodes: list[TrajectoryNode] = []
    edges: list[TrajectoryEdge] = []
    edge_index: dict[tuple[str, str], TrajectoryEdge] = {}

    prev: TrajectoryNode | None = None
    for ev in events:
        node = TrajectoryNode(
            node_id=ev.event_id,
            label=_node_label(ev),
            event_type=ev.event_type.value,
            step=ev.step_number,
        )
        nodes.append(node)
        if prev is not None:
            key = (prev.label, node.label)
            if key in edge_index:
                edge_index[key].weight += 1
            else:
                edge = TrajectoryEdge(src=prev.node_id, dst=node.node_id, weight=1)
                edge_index[key] = edge
                edges.append(edge)
        prev = node

    # Loop detection — labels repeating in a tight window
    loops = _detect_loops([n.label for n in nodes])

    # Repeated tools — counts > 1
    tool_counts: Counter[str] = Counter()
    for ev in events:
        if ev.tool_call:
            tool_counts[ev.tool_call.tool_name] += 1
    repeated = {t: c for t, c in tool_counts.items() if c > 1}

    # Dead-ends: tool_call without a matching tool_result/error after it
    dead_ends: list[str] = []
    for idx, ev in enumerate(events):
        if ev.event_type == EventType.TOOL_CALL and ev.tool_call:
            tail = events[idx + 1 : idx + 5]
            if not any(t.event_type in (EventType.TOOL_RESULT, EventType.TOOL_ERROR) for t in tail):
                dead_ends.append(ev.event_id)

    # Deviation: fraction of tool calls outside the intended set
    deviation = 0.0
    if intended_tools and tool_counts:
        intended_set = {t.lower() for t in intended_tools}
        total = sum(tool_counts.values())
        off = sum(c for t, c in tool_counts.items() if t.lower() not in intended_set)
        deviation = off / total if total else 0.0

    return TrajectoryReport(
        nodes=nodes,
        edges=edges,
        loops=loops,
        repeated_tools=repeated,
        dead_ends=dead_ends,
        deviation_score=deviation,
    )


def _detect_loops(labels: list[str], window: int = 8) -> list[list[str]]:
    """Detect short repeated subsequences (a→b→a→b style loops)."""
    loops: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for size in range(2, window // 2 + 1):
        for i in range(len(labels) - size * 2 + 1):
            a = tuple(labels[i : i + size])
            b = tuple(labels[i + size : i + size * 2])
            if a == b and a not in seen:
                seen.add(a)
                loops.append(list(a))
    return loops


__all__ = [
    "TrajectoryNode",
    "TrajectoryEdge",
    "TrajectoryReport",
    "build_trajectory",
]
