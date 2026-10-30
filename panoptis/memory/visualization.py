"""
MEM-009 — Memory Visualization.

Builds a renderable graph payload for the frontend memory page:
    - episodic / semantic / procedural nodes
    - retrieval paths as edges
    - corrupted memories tagged in red
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from panoptis.memory.health import HealthReport

_KIND_COLORS = {
    "episodic": "#2563eb",  # blue
    "semantic": "#16a34a",  # green
    "procedural": "#f59e0b",  # amber
    "corrupted": "#dc2626",  # red
    "stale": "#9ca3af",  # gray
    "conflict": "#a855f7",  # purple
}


@dataclass
class VizNode:
    id: str
    label: str
    kind: str
    color: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VizEdge:
    src: str
    dst: str
    kind: str = "retrieval"
    weight: float = 1.0


@dataclass
class VizPayload:
    nodes: list[VizNode] = field(default_factory=list)
    edges: list[VizEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.__dict__ for n in self.nodes],
            "edges": [e.__dict__ for e in self.edges],
        }


def build_payload(
    memories: list[dict[str, Any]],
    retrievals: list[tuple[str, str]] | None = None,
    health: HealthReport | None = None,
) -> VizPayload:
    payload = VizPayload()
    bad_keys: set[str] = set()
    if health:
        for issue in health.issues:
            bad_keys.add(issue.key)

    for m in memories:
        key = str(m.get("key", "?"))
        kind = str(m.get("type", "semantic"))
        if key in bad_keys:
            color = _KIND_COLORS["corrupted"]
            display_kind = "corrupted"
        else:
            color = _KIND_COLORS.get(kind, "#6b7280")
            display_kind = kind
        payload.nodes.append(
            VizNode(
                id=key,
                label=str(m.get("title") or m.get("key") or key)[:60],
                kind=display_kind,
                color=color,
                metadata={"value_preview": str(m.get("value", ""))[:80]},
            )
        )

    if retrievals:
        for src, dst in retrievals:
            payload.edges.append(VizEdge(src=src, dst=dst))

    return payload


__all__ = ["VizNode", "VizEdge", "VizPayload", "build_payload"]
