"""
MAG-007 — Consensus Failure Detector.

When agents disagree on approach, surface conflict to the human.
"""

from __future__ import annotations

from dataclasses import dataclass

from panoptis.scoring.drift import cosine, embed


@dataclass
class AgentVote:
    agent_id: str
    proposal: str


@dataclass
class ConsensusReport:
    agreed: bool
    majority_proposal: str | None
    minority_proposals: list[str]
    agreement_ratio: float
    semantic_clusters: list[list[str]]  # list of clusters by agent_id


def detect_consensus(
    votes: list[AgentVote],
    *,
    similarity_threshold: float = 0.65,
    majority_ratio: float = 0.6,
) -> ConsensusReport:
    if not votes:
        return ConsensusReport(True, None, [], 1.0, [])

    # Greedy cluster proposals by semantic similarity. Empty/whitespace-only
    # proposals (an agent that failed, timed out, or returned nothing) embed to
    # a zero vector, and cosine(zero, zero) == 0, so they would never cluster
    # with each other — each would form its own singleton and the round would be
    # reported as maximal disagreement. Group them explicitly into one cluster
    # instead (a None seed vector marks the empty group).
    clusters: list[tuple[list[float] | None, list[AgentVote]]] = []
    empty_votes: list[AgentVote] = []
    for v in votes:
        if not v.proposal.strip():
            empty_votes.append(v)
            continue
        vec = embed(v.proposal)
        placed = False
        for c_vec, c_votes in clusters:
            if c_vec is not None and cosine(vec, c_vec) >= similarity_threshold:
                c_votes.append(v)
                placed = True
                break
        if not placed:
            clusters.append((vec, [v]))
    if empty_votes:
        clusters.append((None, empty_votes))

    cluster_sizes = [len(c) for _, c in clusters]
    total = sum(cluster_sizes)
    biggest_idx = cluster_sizes.index(max(cluster_sizes))
    biggest_vec, biggest = clusters[biggest_idx]
    agreement_ratio = len(biggest) / total
    # A majority made of empty proposals is not an actionable consensus — every
    # agent was silent — so it is reported as "not agreed" (no usable proposal)
    # rather than a confident agreement on empty text.
    biggest_is_empty = biggest_vec is None
    agreed = agreement_ratio >= majority_ratio and not biggest_is_empty

    return ConsensusReport(
        agreed=agreed,
        majority_proposal=biggest[0].proposal if agreed else None,
        minority_proposals=[
            v.proposal for i, (_, c) in enumerate(clusters) if i != biggest_idx for v in c
        ],
        agreement_ratio=agreement_ratio,
        semantic_clusters=[[v.agent_id for v in c] for _, c in clusters],
    )


__all__ = ["AgentVote", "ConsensusReport", "detect_consensus"]
