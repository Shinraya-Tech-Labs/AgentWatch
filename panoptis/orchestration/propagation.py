"""
MAG-004 — Failure Propagation Tracer.

When one agent fails, trace its impact on others. Show blast radius across
the agent network using the inter-agent DAG.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from panoptis.orchestration.dag import InterAgentDAG


@dataclass
class PropagationReport:
    origin: str
    impacted_nodes: list[str] = field(default_factory=list)
    impacted_agents: set[str] = field(default_factory=set)
    depth: int = 0


def trace_propagation(dag: InterAgentDAG, origin_node: str) -> PropagationReport:
    """BFS downstream from a failed node — what else got hit?"""
    if origin_node not in dag.nodes:
        return PropagationReport(origin=origin_node)

    # We need outgoing edges — DAG keeps `_out` private, walk via edges list.
    out_index: dict[str, list[str]] = {}
    for e in dag.edges:
        out_index.setdefault(e.src, []).append(e.dst)

    impacted: list[str] = []
    impacted_agents: set[str] = set()
    depth = 0
    seen = {origin_node}
    frontier: deque[tuple[str, int]] = deque([(origin_node, 0)])
    while frontier:
        n, d = frontier.popleft()
        depth = max(depth, d)
        if n != origin_node:
            impacted.append(n)
            impacted_agents.add(dag.nodes[n].agent_id)
        for nxt in out_index.get(n, []):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append((nxt, d + 1))
    return PropagationReport(
        origin=origin_node,
        impacted_nodes=impacted,
        impacted_agents=impacted_agents,
        depth=depth,
    )


__all__ = ["PropagationReport", "trace_propagation"]
