"""
MAG-003 — Inter-Agent Trust Scoring.

Score trust between agents based on historical interactions. Flag when a
low-trust agent influences a high-trust agent.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class TrustEdge:
    src: str
    dst: str
    interactions: int = 0
    successes: int = 0

    @property
    def score(self) -> float:
        if self.interactions == 0:
            return 0.5
        return self.successes / self.interactions


class InterAgentTrust:
    def __init__(self) -> None:
        self._edges: dict[tuple[str, str], TrustEdge] = {}
        self._agent_score: dict[str, float] = defaultdict(lambda: 0.5)

    def record(self, src: str, dst: str, *, success: bool) -> None:
        key = (src, dst)
        edge = self._edges.get(key) or TrustEdge(src=src, dst=dst)
        edge.interactions += 1
        if success:
            edge.successes += 1
        self._edges[key] = edge
        # Update per-agent rolling score based on its outgoing edges
        outgoing = [e for k, e in self._edges.items() if k[0] == src]
        if outgoing:
            self._agent_score[src] = sum(e.score for e in outgoing) / len(outgoing)

    def score(self, agent_id: str) -> float:
        return self._agent_score.get(agent_id, 0.5)

    def edges(self) -> list[TrustEdge]:
        return list(self._edges.values())

    def low_trust_influencing_high_trust(
        self, *, low_threshold: float = 0.4, high_threshold: float = 0.7
    ) -> list[TrustEdge]:
        return [
            e
            for e in self._edges.values()
            if self.score(e.src) <= low_threshold and self.score(e.dst) >= high_threshold
        ]


__all__ = ["InterAgentTrust", "TrustEdge"]
