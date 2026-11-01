"""Regression tests for CST-007 budget governance commit path.

Issue #540: commit_human_approval() committed spend unconditionally, so a
human-approved action could push used_usd past the team/agent cap when other
spend landed between request() (which reserves nothing) and the approval.
The commit path must re-validate the same caps request() enforces.
"""

from __future__ import annotations

from panoptis.cost.governance import BudgetAction, BudgetGovernance


def test_commit_human_approval_within_caps_still_commits():
    g = BudgetGovernance()
    g.configure_team("t", monthly_cap_usd=100.0)
    g.configure_agent("a", "t", daily_cap_usd=100.0)
    dec = g.commit_human_approval("a", 20.0)
    assert dec.action == BudgetAction.APPROVE
    assert float(g._teams["t"].used_usd) == 20.0
    assert float(g._agents["a"].used_usd) == 20.0


def test_commit_human_approval_blocks_when_exceeding_team_cap():
    g = BudgetGovernance()
    g.configure_team("t", monthly_cap_usd=100.0)
    g.configure_agent("a", "t", daily_cap_usd=1000.0)  # agent cap high -> team cap binds
    # A $20 action needs human sign-off; funds are NOT reserved at request time.
    assert g.request("a", 20.0).action == BudgetAction.REQUIRE_HUMAN
    # Other spend eats most of the team budget before the human approves.
    g.commit_human_approval("a", 85.0)
    assert float(g._teams["t"].used_usd) == 85.0
    # Approving the original $20 would total $105 > $100 -> must block, not commit.
    dec = g.commit_human_approval("a", 20.0)
    assert dec.action == BudgetAction.BLOCK
    assert "team" in dec.reason
    assert float(g._teams["t"].used_usd) == 85.0  # unchanged — no silent overspend


def test_commit_human_approval_blocks_when_exceeding_agent_cap():
    g = BudgetGovernance()
    g.configure_team("t", monthly_cap_usd=1000.0)  # team cap high -> agent cap binds
    g.configure_agent("a", "t", daily_cap_usd=100.0)
    g.commit_human_approval("a", 85.0)
    dec = g.commit_human_approval("a", 20.0)
    assert dec.action == BudgetAction.BLOCK
    assert "agent" in dec.reason
    assert float(g._agents["a"].used_usd) == 85.0  # unchanged


def test_commit_human_approval_exactly_at_cap_is_allowed():
    g = BudgetGovernance()
    g.configure_team("t", monthly_cap_usd=100.0)
    g.configure_agent("a", "t", daily_cap_usd=100.0)
    g.commit_human_approval("a", 80.0)
    dec = g.commit_human_approval("a", 20.0)  # 80 + 20 == 100, not over
    assert dec.action == BudgetAction.APPROVE
    assert float(g._teams["t"].used_usd) == 100.0
