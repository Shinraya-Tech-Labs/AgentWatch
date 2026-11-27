"""Automated Token Budget Slashing and Byzantine Agent Governance Engine.

Provides automated disciplinary throttling, token budget slashing, and priority weight
demotion for rogue, hallucinating, or repeatedly divergent AI agents in multi-agent swarms.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from panoptis.consensus.engine import ConsensusResult


class SlashingEvent(BaseModel):
    """Immutable audit record of an automated token slashing event."""
    agent_id: str
    timestamp: float
    reason: str
    divergence_score: float
    slashed_tokens: int
    remaining_quota: int
    previous_weight: float
    new_weight: float
    status: str


class AgentBudgetProfile(BaseModel):
    """Dynamic token budget and priority weight profile for an active agent."""
    agent_id: str
    initial_quota: int = 100000
    current_quota: int = 100000
    initial_weight: float = 1.0
    current_weight: float = 1.0
    consecutive_violations: int = 0
    total_violations: int = 0
    total_slashed_tokens: int = 0
    status: str = "active"  # "active", "probation", "suspended"
    last_slashed_at: Optional[float] = None
    slashing_history: List[SlashingEvent] = Field(default_factory=list)


class TokenBudgetSlasher:
    """Automated token budget slashing engine for multi-agent governance."""

    def __init__(
        self,
        default_quota: int = 100000,
        default_weight: float = 1.0,
        slash_fraction: float = 0.5,
        weight_decay_fraction: float = 0.5,
        max_strikes_before_suspension: int = 3,
        probation_recovery_rounds: int = 2,
    ) -> None:
        """Initialize the slasher engine.

        Args:
            default_quota: Initial default token quota per agent.
            default_weight: Initial default voting weight per agent.
            slash_fraction: Fraction of remaining token budget slashed per Byzantine event (0.0 to 1.0).
            weight_decay_fraction: Factor by which voting weight is halved/reduced per violation.
            max_strikes_before_suspension: Number of consecutive violations triggering total suspension.
            probation_recovery_rounds: Number of clean consensus rounds needed to step down probation.
        """
        self.default_quota = default_quota
        self.default_weight = default_weight
        self.slash_fraction = slash_fraction
        self.weight_decay_fraction = weight_decay_fraction
        self.max_strikes_before_suspension = max_strikes_before_suspension
        self.probation_recovery_rounds = probation_recovery_rounds

        self.profiles: Dict[str, AgentBudgetProfile] = {}
        self.audit_events: List[SlashingEvent] = []

    def get_or_create_profile(self, agent_id: str) -> AgentBudgetProfile:
        """Retrieve or register an agent budget profile."""
        if agent_id not in self.profiles:
            self.profiles[agent_id] = AgentBudgetProfile(
                agent_id=agent_id,
                initial_quota=self.default_quota,
                current_quota=self.default_quota,
                initial_weight=self.default_weight,
                current_weight=self.default_weight,
            )
        return self.profiles[agent_id]

    def slash_agent(
        self,
        agent_id: str,
        divergence_score: float,
        reason: str = "Byzantine divergence during multi-agent consensus",
    ) -> SlashingEvent:
        """Apply an automated token budget slashing and priority downgrade to an agent."""
        profile = self.get_or_create_profile(agent_id)
        now = time.time()

        profile.consecutive_violations += 1
        profile.total_violations += 1

        prev_weight = profile.current_weight
        prev_quota = profile.current_quota

        if profile.consecutive_violations >= self.max_strikes_before_suspension:
            # Immediate complete suspension
            slashed = profile.current_quota
            profile.current_quota = 0
            profile.current_weight = 0.0
            profile.status = "suspended"
        else:
            slashed = int(profile.current_quota * self.slash_fraction)
            profile.current_quota = max(0, profile.current_quota - slashed)
            profile.current_weight = max(0.05, round(profile.current_weight * (1.0 - self.weight_decay_fraction), 4))
            profile.status = "probation"

        profile.total_slashed_tokens += slashed
        profile.last_slashed_at = now

        event = SlashingEvent(
            agent_id=agent_id,
            timestamp=now,
            reason=reason,
            divergence_score=divergence_score,
            slashed_tokens=slashed,
            remaining_quota=profile.current_quota,
            previous_weight=prev_weight,
            new_weight=profile.current_weight,
            status=profile.status,
        )

        profile.slashing_history.append(event)
        self.audit_events.append(event)
        return event

    def process_consensus_outcome(
        self, outcome: ConsensusResult
    ) -> List[SlashingEvent]:
        """Process a completed consensus round, slashing byzantine agents and acknowledging honest peers."""
        slashed_events: List[SlashingEvent] = []

        # 1. Slash all detected Byzantine divergent agents
        for agent_id in outcome.byzantine_agents:
            div_score = outcome.divergence_scores.get(agent_id, 1.0)
            reason = f"Divergence score {div_score} exceeded maximum tolerance during consensus round"
            event = self.slash_agent(agent_id, div_score, reason)
            slashed_events.append(event)

        # 2. Reward / recover participating agents who agreed
        for agent_id in outcome.participating_agents:
            if agent_id not in outcome.byzantine_agents:
                profile = self.get_or_create_profile(agent_id)
                if profile.status == "probation":
                    if profile.consecutive_violations > 0:
                        profile.consecutive_violations -= 1
                    if profile.consecutive_violations == 0:
                        profile.status = "active"
                        profile.current_weight = min(profile.initial_weight, profile.current_weight * 1.5)

        return slashed_events

    def restore_agent(
        self,
        agent_id: str,
        restored_quota: Optional[int] = None,
        restored_weight: Optional[float] = None,
    ) -> AgentBudgetProfile:
        """Administratively restore a suspended or penalized agent after probation review."""
        profile = self.get_or_create_profile(agent_id)
        profile.current_quota = restored_quota if restored_quota is not None else profile.initial_quota
        profile.current_weight = restored_weight if restored_weight is not None else profile.initial_weight
        profile.consecutive_violations = 0
        profile.status = "active"
        return profile
