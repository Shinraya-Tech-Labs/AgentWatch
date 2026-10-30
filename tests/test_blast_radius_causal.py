from __future__ import annotations

import pytest

from panoptis.core.blast_radius import BlastRadiusEstimator
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ToolCallData,
)


def _tool_event(tool: str, raw: str, resources: list[str] | None = None) -> AgentEvent:
    return AgentEvent(
        session_id="S",
        agent_id="A",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name=tool, raw_command=raw, affected_resources=resources or []),
    )


def test_blast_radius_detects_missing_where_clause():
    estimator = BlastRadiusEstimator()
    # DELETE without WHERE
    event = _tool_event("sql", "DELETE FROM orders")
    radius = estimator.estimate(event)
    assert radius.score >= 90
    assert radius.is_critical_resource is True
    assert "Extreme blast radius" in radius.explanation


def test_blast_radius_detects_critical_table():
    estimator = BlastRadiusEstimator()
    # DELETE with WHERE but on a critical table
    event = _tool_event("sql", "DELETE FROM users WHERE id = 1")
    radius = estimator.estimate(event)
    assert radius.score >= 75
    assert radius.is_critical_resource is True


def test_blast_radius_detects_critical_fs_path():
    estimator = BlastRadiusEstimator()
    event = _tool_event("bash", "rm -rf /etc")
    radius = estimator.estimate(event)
    assert radius.score == 100
    assert radius.is_critical_resource is True


def test_blast_radius_detects_production_tag():
    estimator = BlastRadiusEstimator()
    event = _tool_event("bash", "cat config.yaml", resources=["server.prod.yaml"])
    radius = estimator.estimate(event)
    assert radius.score >= 90
    assert radius.is_critical_resource is True


@pytest.mark.asyncio
async def test_safety_engine_escalates_on_high_blast_radius():
    # Setup a lenient policy
    from panoptis.core.safety import SafetyPolicy

    lenient_policy = SafetyPolicy(
        policy_id="lenient",
        name="Lenient",
        block_on_high=False,
        require_approval_on_high=False,
        require_approval_on_medium=False,
    )

    # Mock approval callback
    approval_called = False

    async def mock_approval(event, safety):
        nonlocal approval_called
        approval_called = True
        return True

    engine = SafetyEngine(policy=lenient_policy, approval_callback=mock_approval)

    # This command looks safe to a pattern-only scanner (DELETE with a simple WHERE)
    # But it touches a critical table 'users'
    event = _tool_event("sql", "DELETE FROM users WHERE id = 5")

    result = await engine.check_event(event)

    # Should have escalated to REQUIRE_APPROVAL because of blast radius
    assert result.safety.requires_approval is True
    assert approval_called is True
    assert any("ESCALATED" in r for r in result.safety.reasons)
