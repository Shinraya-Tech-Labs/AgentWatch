from __future__ import annotations

import pytest

from panoptis.core.blast_radius import BlastRadiusEstimator
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    RiskLevel,
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


def test_safe_sql_operations_no_escalation():
    estimator = BlastRadiusEstimator()

    # SELECT is safe (no pattern match, no causal analysis)
    radius = estimator.estimate(
        _tool_event("sql", "SELECT id, name FROM products WHERE price > 100")
    )
    assert radius.score < 60
    assert not estimator.requires_approval(radius)

    # INSERT is safe (no pattern match)
    radius = estimator.estimate(_tool_event("sql", "INSERT INTO orders_log (msg) VALUES ('test')"))
    assert radius.score < 60
    assert not estimator.requires_approval(radius)

    # DELETE with WHERE on non-critical table should NOT be escalated by blast radius
    # (It might be HIGH risk in main engine, but blast radius shouldn't FORCE it if it's below threshold)
    radius = estimator.estimate(_tool_event("sql", "DELETE FROM temp_records WHERE id = 1"))
    # Currently this fails (returns 60). We want it to be < 60.
    assert radius.score < 60
    assert not estimator.requires_approval(radius)


def test_normal_filesystem_operations_no_escalation():
    estimator = BlastRadiusEstimator()

    # ls is safe
    radius = estimator.estimate(_tool_event("bash", "ls -R /tmp/my_app"))
    assert radius.score < 60
    assert not estimator.requires_approval(radius)

    # mkdir is safe
    radius = estimator.estimate(_tool_event("bash", "mkdir -p build/artifacts"))
    assert radius.score < 60
    assert not estimator.requires_approval(radius)

    # cat/read is safe
    radius = estimator.estimate(_tool_event("bash", "cat logs/app.log"))
    assert radius.score < 60
    assert not estimator.requires_approval(radius)

    # rm single file is low risk (score 25)
    radius = estimator.estimate(_tool_event("bash", "rm debug.log"))
    assert radius.score == 25
    assert not estimator.requires_approval(radius)


@pytest.mark.asyncio
async def test_existing_safety_behavior_unchanged_for_low_risk():
    # Setup a standard engine
    engine = SafetyEngine()

    # echo hello is SAFE
    event = _tool_event("bash", "echo 'hello world'")
    result = await engine.check_event(event)
    assert result.status == ExecutionStatus.RUNNING  # Not blocked
    assert result.safety.risk_level == RiskLevel.SAFE
    assert result.safety.requires_approval is False


@pytest.mark.asyncio
async def test_backward_compatibility_of_critical_blocks():
    engine = SafetyEngine()

    # rm -rf / was and still is CRITICAL/BLOCKED
    event = _tool_event("bash", "rm -rf /")
    result = await engine.check_event(event)
    assert result.status == ExecutionStatus.BLOCKED
    assert result.safety.risk_level == RiskLevel.CRITICAL
    assert any("Recursive deletion" in r for r in result.safety.reasons)


def test_blast_radius_escalates_only_when_warranted():
    estimator = BlastRadiusEstimator()

    # Missing WHERE clause -> Escalated to 90
    radius = estimator.estimate(_tool_event("sql", "DELETE FROM products"))
    assert radius.score >= 90
    assert estimator.requires_approval(radius)

    # Critical table -> Escalated to 75
    radius = estimator.estimate(_tool_event("sql", "UPDATE users SET active = 0 WHERE id = 1"))
    assert radius.score >= 75
    assert estimator.requires_approval(radius)

    # Wildcard deletion -> Escalated to 65
    radius = estimator.estimate(_tool_event("bash", "rm -rf docs/*"))
    assert radius.score >= 65
    assert estimator.requires_approval(radius)
