"""Consensus Telemetry Broadcast and Observability Stream."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from panoptis.consensus.engine import ConsensusResult
from panoptis.governance.slasher import SlashingEvent


class ConsensusTelemetryEvent(BaseModel):
    """Structured telemetry event emitted per multi-agent consensus round."""
    event_id: str
    timestamp: float
    consensus_reached: bool
    winner_output: Optional[str] = None
    winning_agent_id: Optional[str] = None
    agreement_score: float
    quorum_percentage: float
    total_participating_agents: int
    byzantine_count: int
    byzantine_agents: List[str]
    divergence_scores: Dict[str, float]
    slashing_actions: List[Dict[str, Any]] = Field(default_factory=list)


class ConsensusTelemetryEmitter:
    """Telemetry collector and broadcaster for multi-agent consensus rounds."""

    def __init__(self) -> None:
        self.events: List[ConsensusTelemetryEvent] = []

    def emit_round(
        self,
        round_id: str,
        result: ConsensusResult,
        slashing_events: Optional[List[SlashingEvent]] = None,
    ) -> ConsensusTelemetryEvent:
        """Record and return a consensus round telemetry event."""
        actions: List[Dict[str, Any]] = []
        if slashing_events:
            for s in slashing_events:
                actions.append({
                    "agent_id": s.agent_id,
                    "slashed_tokens": s.slashed_tokens,
                    "remaining_quota": s.remaining_quota,
                    "new_weight": s.new_weight,
                    "status": s.status,
                })

        event = ConsensusTelemetryEvent(
            event_id=round_id,
            timestamp=time.time(),
            consensus_reached=result.consensus_reached,
            winner_output=result.winner_output,
            winning_agent_id=result.winning_agent_id,
            agreement_score=result.agreement_score,
            quorum_percentage=result.quorum_percentage,
            total_participating_agents=len(result.participating_agents),
            byzantine_count=len(result.byzantine_agents),
            byzantine_agents=result.byzantine_agents,
            divergence_scores=result.divergence_scores,
            slashing_actions=actions,
        )

        self.events.append(event)
        return event

    def get_recent_events(self, limit: int = 50) -> List[ConsensusTelemetryEvent]:
        """Fetch the most recent consensus telemetry events."""
        return self.events[-limit:]
