"""Tests for the cost-report aggregation (issue #339)."""

from __future__ import annotations

import pytest

from panoptis.core.schema import AgentFramework, AgentSession, ExecutionStatus
from panoptis.cost.reporting import build_cost_report, parse_sessions


def _session(
    *,
    framework: AgentFramework = AgentFramework.LANGCHAIN,
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    tokens: int = 0,
    usd: float = 0.0,
    agent_id: str = "a1",
    agent_name: str | None = None,
) -> AgentSession:
    return AgentSession(
        agent_id=agent_id,
        agent_name=agent_name,
        framework=framework,
        status=status,
        total_tokens=tokens,
        estimated_cost_usd=usd,
    )


def test_groups_by_framework_and_sums_tokens_and_usd() -> None:
    sessions = [
        _session(framework=AgentFramework.LANGCHAIN, tokens=100, usd=1.0),
        _session(framework=AgentFramework.LANGCHAIN, tokens=200, usd=2.0),
        _session(framework=AgentFramework.CREWAI, tokens=50, usd=0.5),
    ]
    report = build_cost_report(sessions, group_by="framework", days=30)
    by_group = {r.group: r for r in report.rows}
    assert by_group["langchain"].sessions == 2
    assert by_group["langchain"].total_tokens == 300
    assert by_group["langchain"].total_usd == pytest.approx(3.0)
    assert by_group["crewai"].total_tokens == 50
    assert report.total_tokens == 350
    assert report.total_usd == pytest.approx(3.5)


def test_cost_per_successful_goal() -> None:
    sessions = [
        _session(usd=3.0, status=ExecutionStatus.SUCCESS),
        _session(usd=3.0, status=ExecutionStatus.SUCCESS),
        _session(usd=4.0, status=ExecutionStatus.FAILURE),
    ]
    report = build_cost_report(sessions, group_by="framework")
    (row,) = report.rows
    assert row.successful == 2
    # 10 USD total across 2 successful sessions.
    assert row.cost_per_successful_goal == pytest.approx(5.0)


def test_zero_successful_yields_none_not_divide_error() -> None:
    sessions = [
        _session(usd=2.0, status=ExecutionStatus.FAILURE),
        _session(usd=1.0, status=ExecutionStatus.BLOCKED),
    ]
    report = build_cost_report(sessions)
    (row,) = report.rows
    assert row.successful == 0
    assert row.cost_per_successful_goal is None


def test_only_success_status_counts_as_successful() -> None:
    non_success = [
        ExecutionStatus.PENDING,
        ExecutionStatus.RUNNING,
        ExecutionStatus.FAILURE,
        ExecutionStatus.BLOCKED,
        ExecutionStatus.ROLLED_BACK,
        ExecutionStatus.TIMEOUT,
    ]
    sessions = [_session(status=s) for s in non_success]
    sessions.append(_session(status=ExecutionStatus.SUCCESS))
    report = build_cost_report(sessions)
    (row,) = report.rows
    assert row.successful == 1


def test_group_by_agent_uses_name_then_id() -> None:
    sessions = [
        _session(agent_id="id1", agent_name="Researcher", usd=1.0),
        _session(agent_id="id2", agent_name=None, usd=2.0),
    ]
    report = build_cost_report(sessions, group_by="agent")
    assert {r.group for r in report.rows} == {"Researcher", "id2"}


def test_group_by_status() -> None:
    sessions = [
        _session(status=ExecutionStatus.SUCCESS),
        _session(status=ExecutionStatus.FAILURE),
        _session(status=ExecutionStatus.FAILURE),
    ]
    report = build_cost_report(sessions, group_by="status")
    assert {r.group: r.sessions for r in report.rows} == {"success": 1, "failure": 2}


def test_rows_sorted_by_usd_descending() -> None:
    sessions = [
        _session(framework=AgentFramework.LANGCHAIN, usd=1.0),
        _session(framework=AgentFramework.CREWAI, usd=5.0),
        _session(framework=AgentFramework.CLAUDE_CODE, usd=3.0),
    ]
    report = build_cost_report(sessions, group_by="framework")
    usds = [r.total_usd for r in report.rows]
    assert usds == sorted(usds, reverse=True)
    assert report.rows[0].group == "crewai"


def test_empty_sessions_is_empty_report() -> None:
    report = build_cost_report([], group_by="framework", days=7)
    assert report.rows == []
    assert report.total_sessions == 0
    assert report.total_tokens == 0
    assert report.total_usd == 0.0
    assert report.days == 7


def test_invalid_group_by_raises() -> None:
    with pytest.raises(ValueError):
        build_cost_report([_session()], group_by="nonsense")


def test_report_to_dict_shape() -> None:
    report = build_cost_report(
        [_session(framework=AgentFramework.CREWAI, tokens=100, usd=2.0)],
        group_by="framework",
        days=30,
    )
    data = report.to_dict()
    assert data["group_by"] == "framework"
    assert data["days"] == 30
    assert data["totals"] == {"sessions": 1, "total_tokens": 100, "total_usd": 2.0}
    assert data["rows"][0]["group"] == "crewai"
    assert data["rows"][0]["cost_per_successful_goal"] == 2.0


def test_parse_sessions_keeps_valid_and_skips_malformed() -> None:
    good = _session(tokens=10, usd=0.1).model_dump(mode="json")
    items = [good, {}, {"foo": "bar"}]  # two rows missing the required agent_id
    sessions, skipped = parse_sessions(items)
    assert len(sessions) == 1
    assert sessions[0].total_tokens == 10
    assert skipped == 2


def test_parse_sessions_empty() -> None:
    sessions, skipped = parse_sessions([])
    assert sessions == []
    assert skipped == 0
