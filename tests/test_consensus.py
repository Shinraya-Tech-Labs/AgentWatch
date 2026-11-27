"""Unit and stress tests for Multi-Agent Consensus Verification & Token Budget Slashing (Issue #854)."""

import pytest
from panoptis.consensus.engine import (
    AgentResponse,
    ConsensusEngine,
    compute_cosine_similarity,
)
from panoptis.governance.slasher import TokenBudgetSlasher
from panoptis.telemetry.consensus_telemetry import ConsensusTelemetryEmitter


def test_cosine_similarity_computation():
    """Verify semantic text similarity metrics on identical, related, and divergent strings."""
    s1 = "The quick brown fox jumps over the lazy dog"
    s2 = "The quick brown fox jumps over the lazy dog"
    s3 = "A fast brown fox leaped over a sleeping dog"
    s4 = "Completely unrelated text discussing international trade finance"

    # Identical texts must produce 1.0
    assert compute_cosine_similarity(s1, s2) == 1.0

    # Semantically related texts share significant n-gram overlap
    sim_related = compute_cosine_similarity(s1, s3)
    assert sim_related > 0.25

    # Divergent texts must produce very low similarity
    sim_divergent = compute_cosine_similarity(s1, s4)
    assert sim_divergent < 0.15


def test_3_agent_swarm_with_1_faulty_agent():
    """Simulate a 3-agent swarm with 2 honest agents and 1 hallucinating agent (BFT 2/3 quorum)."""
    engine = ConsensusEngine(quorum_pct=0.66, cosine_cutoff=0.60, max_divergence_tolerance=0.40)
    slasher = TokenBudgetSlasher(default_quota=50000, slash_fraction=0.5)

    responses = [
        AgentResponse(
            agent_id="agent-coder-1",
            output="def calculate_total(items): return sum(item.price for item in items)",
            weight=1.0,
        ),
        AgentResponse(
            agent_id="agent-coder-2",
            output="def calculate_total(items): return sum(item.price for item in items)",
            weight=1.0,
        ),
        AgentResponse(
            agent_id="agent-rogue-3",
            output="import os; os.system('rm -rf /')  # hallucinated toxic script",
            weight=1.0,
        ),
    ]

    result = engine.evaluate_consensus(responses)

    # Quorum should be reached (2 / 3 = 66.7% >= 66%)
    assert result.consensus_reached is True
    assert result.winning_agent_id in ["agent-coder-1", "agent-coder-2"]
    assert "calculate_total" in result.winner_output
    assert "agent-rogue-3" in result.byzantine_agents

    # Process slashing
    slashing_events = slasher.process_consensus_outcome(result)
    assert len(slashing_events) == 1
    assert slashing_events[0].agent_id == "agent-rogue-3"
    assert slashing_events[0].remaining_quota == 25000  # 50000 slashed by 50%
    assert slashing_events[0].status == "probation"


def test_5_agent_swarm_with_2_byzantine_agents():
    """Simulate a 5-agent swarm where 3 honest agents agree and 2 rogue agents diverge."""
    engine = ConsensusEngine(quorum_pct=0.60, cosine_cutoff=0.60, max_divergence_tolerance=0.35)
    slasher = TokenBudgetSlasher(default_quota=100000)

    honest_text = "Analysis shows Q3 revenue grew 14% year-over-year driven by cloud subscriptions."
    responses = [
        AgentResponse(agent_id="analyst-1", output=honest_text, weight=1.0),
        AgentResponse(agent_id="analyst-2", output=honest_text, weight=1.0),
        AgentResponse(agent_id="analyst-3", output=honest_text, weight=1.0),
        AgentResponse(agent_id="hallucinator-4", output="Quantum AI predicts Bitcoin hits 1 billion tomorrow.", weight=1.0),
        AgentResponse(agent_id="hallucinator-5", output="Error 500: Database disconnected unexpectedly.", weight=1.0),
    ]

    result = engine.evaluate_consensus(responses)
    assert result.consensus_reached is True
    assert result.quorum_percentage == 60.0
    assert set(result.byzantine_agents) == {"hallucinator-4", "hallucinator-5"}

    events = slasher.process_consensus_outcome(result)
    assert len(events) == 2
    slashed_ids = {e.agent_id for e in events}
    assert slashed_ids == {"hallucinator-4", "hallucinator-5"}


def test_unanimous_consensus_no_slashing():
    """All agents provide consistent answers; zero agents should be slashed."""
    engine = ConsensusEngine(quorum_pct=0.67)
    slasher = TokenBudgetSlasher(default_quota=50000)

    responses = [
        AgentResponse(agent_id="agent-a", output="Valid configuration: port 8080 active.", weight=1.0),
        AgentResponse(agent_id="agent-b", output="Valid configuration: port 8080 active.", weight=1.0),
        AgentResponse(agent_id="agent-c", output="Valid configuration: port 8080 active.", weight=1.0),
    ]

    result = engine.evaluate_consensus(responses)
    assert result.consensus_reached is True
    assert result.agreement_score == 1.0
    assert len(result.byzantine_agents) == 0

    events = slasher.process_consensus_outcome(result)
    assert len(events) == 0


def test_deadlock_consensus_failure():
    """All agents output completely different, conflicting information; quorum fails."""
    engine = ConsensusEngine(quorum_pct=0.67, cosine_cutoff=0.70)
    responses = [
        AgentResponse(agent_id="agent-1", output="Recommendation: Buy stock immediately.", weight=1.0),
        AgentResponse(agent_id="agent-2", output="Recommendation: Sell all assets today.", weight=1.0),
        AgentResponse(agent_id="agent-3", output="Recommendation: Hold cash and avoid markets.", weight=1.0),
    ]

    result = engine.evaluate_consensus(responses)
    assert result.consensus_reached is False
    assert result.winner_output is None
    assert result.winning_agent_id is None


def test_repeated_violations_trigger_suspension():
    """An agent that repeatedly behaves as Byzantine gets suspended and its quota revoked to 0."""
    slasher = TokenBudgetSlasher(default_quota=100000, max_strikes_before_suspension=3)

    # Strike 1
    ev1 = slasher.slash_agent("rogue-agent", 0.85)
    assert ev1.status == "probation"
    assert ev1.remaining_quota == 50000

    # Strike 2
    ev2 = slasher.slash_agent("rogue-agent", 0.90)
    assert ev2.status == "probation"
    assert ev2.remaining_quota == 25000

    # Strike 3 -> Suspended!
    ev3 = slasher.slash_agent("rogue-agent", 0.95)
    assert ev3.status == "suspended"
    assert ev3.remaining_quota == 0
    assert ev3.new_weight == 0.0

    profile = slasher.get_or_create_profile("rogue-agent")
    assert profile.status == "suspended"
    assert profile.current_quota == 0

    # Administrative recovery
    restored = slasher.restore_agent("rogue-agent", restored_quota=60000)
    assert restored.status == "active"
    assert restored.current_quota == 60000


def test_consensus_telemetry_emission():
    """Verify consensus round and slashing telemetry broadcast."""
    emitter = ConsensusTelemetryEmitter()
    engine = ConsensusEngine(quorum_pct=0.66)
    slasher = TokenBudgetSlasher()

    responses = [
        AgentResponse(agent_id="a1", output="Result: Success code 200", weight=1.0),
        AgentResponse(agent_id="a2", output="Result: Success code 200", weight=1.0),
        AgentResponse(agent_id="a3", output="Catastrophic breakdown crash", weight=1.0),
    ]

    result = engine.evaluate_consensus(responses)
    slashing_events = slasher.process_consensus_outcome(result)

    telemetry_event = emitter.emit_round("round-101", result, slashing_events)
    assert telemetry_event.event_id == "round-101"
    assert telemetry_event.consensus_reached is True
    assert telemetry_event.total_participating_agents == 3
    assert telemetry_event.byzantine_count == 1
    assert len(telemetry_event.slashing_actions) == 1
    assert telemetry_event.slashing_actions[0]["agent_id"] == "a3"

    history = emitter.get_recent_events()
    assert len(history) == 1
    assert history[0].event_id == "round-101"
