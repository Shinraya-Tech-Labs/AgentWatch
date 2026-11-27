"""Multi-Agent Consensus Verification Engine with Byzantine Fault Tolerance (BFT).

Validates outputs produced across a swarm of autonomous AI agents by calculating
pairwise semantic agreement matrices, checking Byzantine quorum thresholds, and
identifying divergent or hallucinating agents for governance slashing.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alpha-numeric tokens and bi-grams for semantic matching."""
    words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    if not words:
        return []
    bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)]
    return words + bigrams


def compute_cosine_similarity(text1: str, text2: str) -> float:
    """Compute cosine similarity between two texts based on term frequencies."""
    if text1.strip() == text2.strip():
        return 1.0

    tokens1 = _tokenize(text1)
    tokens2 = _tokenize(text2)

    if not tokens1 or not tokens2:
        return 0.0

    counts1 = Counter(tokens1)
    counts2 = Counter(tokens2)

    all_tokens = set(counts1.keys()).union(set(counts2.keys()))
    dot_product = sum(counts1.get(t, 0) * counts2.get(t, 0) for t in all_tokens)
    magnitude1 = math.sqrt(sum(v ** 2 for v in counts1.values()))
    magnitude2 = math.sqrt(sum(v ** 2 for v in counts2.values()))

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return min(1.0, max(0.0, dot_product / (magnitude1 * magnitude2)))


class AgentResponse(BaseModel):
    """Output submitted by an individual agent during a consensus round."""
    agent_id: str
    output: str
    weight: float = Field(default=1.0, ge=0.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConsensusResult(BaseModel):
    """The evaluated outcome of a multi-agent consensus round."""
    consensus_reached: bool
    winner_output: Optional[str] = None
    winning_agent_id: Optional[str] = None
    agreement_score: float = Field(ge=0.0, le=1.0)
    quorum_percentage: float
    total_weight: float
    consensus_weight: float
    byzantine_agents: List[str] = Field(default_factory=list)
    divergence_scores: Dict[str, float] = Field(default_factory=dict)
    similarity_matrix: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    participating_agents: List[str] = Field(default_factory=list)


class ConsensusEngine:
    """Deterministic Multi-Agent Consensus Verification Engine."""

    def __init__(
        self,
        quorum_pct: float = 0.66,
        cosine_cutoff: float = 0.60,
        max_divergence_tolerance: float = 0.35,
    ) -> None:
        """Initialize consensus engine parameters.

        Args:
            quorum_pct: Minimum fraction of total agent weight required for quorum (default: 0.67 for 2/3 BFT).
            cosine_cutoff: Pairwise semantic similarity cutoff to be considered in agreement.
            max_divergence_tolerance: Maximum allowable divergence from consensus before an agent is flagged as Byzantine.
        """
        if not 0.0 < quorum_pct <= 1.0:
            raise ValueError(f"quorum_pct must be between 0 and 1, got {quorum_pct}")
        if not 0.0 <= cosine_cutoff <= 1.0:
            raise ValueError(f"cosine_cutoff must be between 0 and 1, got {cosine_cutoff}")
        if not 0.0 <= max_divergence_tolerance <= 1.0:
            raise ValueError(f"max_divergence_tolerance must be between 0 and 1, got {max_divergence_tolerance}")

        self.quorum_pct = quorum_pct
        self.cosine_cutoff = cosine_cutoff
        self.max_divergence_tolerance = max_divergence_tolerance

    def build_similarity_matrix(
        self, responses: List[AgentResponse]
    ) -> Dict[str, Dict[str, float]]:
        """Construct an NxN pairwise similarity matrix across all agent responses."""
        matrix: Dict[str, Dict[str, float]] = {}
        for r1 in responses:
            matrix[r1.agent_id] = {}
            for r2 in responses:
                if r1.agent_id == r2.agent_id:
                    matrix[r1.agent_id][r2.agent_id] = 1.0
                elif r2.agent_id in matrix and r1.agent_id in matrix[r2.agent_id]:
                    matrix[r1.agent_id][r2.agent_id] = matrix[r2.agent_id][r1.agent_id]
                else:
                    sim = compute_cosine_similarity(r1.output, r2.output)
                    matrix[r1.agent_id][r2.agent_id] = round(sim, 4)
        return matrix

    def evaluate_consensus(self, responses: List[AgentResponse]) -> ConsensusResult:
        """Evaluate consensus across submitted agent responses using Byzantine fault-tolerant clustering."""
        if not responses:
            return ConsensusResult(
                consensus_reached=False,
                agreement_score=0.0,
                quorum_percentage=0.0,
                total_weight=0.0,
                consensus_weight=0.0,
            )

        agent_ids = [r.agent_id for r in responses]
        total_weight = sum(r.weight for r in responses)
        if total_weight <= 0.0:
            raise ValueError("Total agent weight must be strictly positive")

        similarity_matrix = self.build_similarity_matrix(responses)

        # For each candidate agent response, compute the weighted agreement cluster
        candidate_scores: List[Tuple[AgentResponse, float, float, List[str]]] = []

        for candidate in responses:
            agreeing_weight = 0.0
            agreeing_agents: List[str] = []
            similarities_to_candidate: List[float] = []

            for peer in responses:
                sim = similarity_matrix[candidate.agent_id][peer.agent_id]
                similarities_to_candidate.append(sim)
                if sim >= self.cosine_cutoff:
                    agreeing_weight += peer.weight
                    agreeing_agents.append(peer.agent_id)

            avg_similarity = sum(similarities_to_candidate) / len(similarities_to_candidate)
            candidate_scores.append((candidate, agreeing_weight, avg_similarity, agreeing_agents))

        # Select the candidate with the highest agreeing weight (and highest avg similarity as tie-breaker)
        candidate_scores.sort(key=lambda x: (x[1], x[2]), reverse=True)
        winner, winning_weight, avg_cluster_sim, cluster_agents = candidate_scores[0]

        quorum_ratio = winning_weight / total_weight
        consensus_reached = quorum_ratio >= self.quorum_pct

        # Calculate divergence scores against the selected winning candidate
        divergence_scores: Dict[str, float] = {}
        byzantine_agents: List[str] = []

        for r in responses:
            sim = similarity_matrix[winner.agent_id][r.agent_id]
            divergence = round(1.0 - sim, 4)
            divergence_scores[r.agent_id] = divergence

            if divergence > self.max_divergence_tolerance:
                byzantine_agents.append(r.agent_id)

        agreement_score = round(winning_weight / total_weight, 4)

        return ConsensusResult(
            consensus_reached=consensus_reached,
            winner_output=winner.output if consensus_reached else None,
            winning_agent_id=winner.agent_id if consensus_reached else None,
            agreement_score=agreement_score,
            quorum_percentage=round(quorum_ratio * 100.0, 2),
            total_weight=round(total_weight, 4),
            consensus_weight=round(winning_weight, 4),
            byzantine_agents=byzantine_agents,
            divergence_scores=divergence_scores,
            similarity_matrix=similarity_matrix,
            participating_agents=agent_ids,
        )
