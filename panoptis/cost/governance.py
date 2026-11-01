from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class BudgetAction(str, Enum):
    APPROVE = "approve"
    REQUIRE_HUMAN = "require_human"
    BLOCK = "block"


@dataclass
class TeamBudget:
    team_id: str
    monthly_cap_usd: Decimal
    used_usd: Decimal = Decimal("0.0")
    auto_approve_ceiling_usd: Decimal = Decimal("5.0")  # actions under this auto-approve
    hard_block_ceiling_usd: Decimal = Decimal("1000.0")  # actions over this block


@dataclass
class AgentBudget:
    agent_id: str
    daily_cap_usd: Decimal
    used_usd: Decimal = Decimal("0.0")


@dataclass
class BudgetDecision:
    action: BudgetAction
    reason: str
    remaining_team_usd: float
    remaining_agent_usd: float


class BudgetGovernance:
    """
    Stack of team-level and agent-level budgets, plus auto-approval flow.
    """

    def __init__(self) -> None:
        self._teams: dict[str, TeamBudget] = {}
        self._agents: dict[str, AgentBudget] = {}
        self._agent_to_team: dict[str, str] = {}

    def configure_team(self, team_id: str, monthly_cap_usd: float | Decimal, **kw) -> TeamBudget:
        if "auto_approve_ceiling_usd" in kw and kw["auto_approve_ceiling_usd"] is not None:
            kw["auto_approve_ceiling_usd"] = Decimal(str(kw["auto_approve_ceiling_usd"]))
        if "hard_block_ceiling_usd" in kw and kw["hard_block_ceiling_usd"] is not None:
            kw["hard_block_ceiling_usd"] = Decimal(str(kw["hard_block_ceiling_usd"]))
        budget = TeamBudget(team_id=team_id, monthly_cap_usd=Decimal(str(monthly_cap_usd)), **kw)
        self._teams[team_id] = budget
        return budget

    def configure_agent(
        self, agent_id: str, team_id: str, daily_cap_usd: float | Decimal
    ) -> AgentBudget:
        budget = AgentBudget(agent_id=agent_id, daily_cap_usd=Decimal(str(daily_cap_usd)))
        self._agents[agent_id] = budget
        self._agent_to_team[agent_id] = team_id
        return budget

    def request(self, agent_id: str, action_cost_usd: float | Decimal) -> BudgetDecision:
        cost_dec = Decimal(str(action_cost_usd))
        agent_b = self._agents.get(agent_id)
        if agent_b is None:
            return BudgetDecision(
                action=BudgetAction.REQUIRE_HUMAN,
                reason="agent_not_configured",
                remaining_team_usd=0.0,
                remaining_agent_usd=0.0,
            )

        team_id = self._agent_to_team.get(agent_id)
        team_b = self._teams.get(team_id) if team_id else None

        # Team monthly cap, agent daily cap, and hard-block ceiling. Shared with
        # commit_human_approval() so both entry points enforce identical limits.
        violation = self._validate_caps(team_b, agent_b, cost_dec)
        if violation is not None:
            return violation

        # Auto-approve small actions
        if team_b and cost_dec <= team_b.auto_approve_ceiling_usd:
            return self._commit(team_b, agent_b, cost_dec, BudgetAction.APPROVE, "auto_approve")

        # Otherwise require human
        return BudgetDecision(
            action=BudgetAction.REQUIRE_HUMAN,
            reason="above_auto_approve_ceiling",
            remaining_team_usd=float(team_b.monthly_cap_usd - team_b.used_usd) if team_b else 0.0,
            remaining_agent_usd=float(agent_b.daily_cap_usd - agent_b.used_usd),
        )

    def _validate_caps(
        self,
        team_b: TeamBudget | None,
        agent_b: AgentBudget,
        cost_dec: Decimal,
    ) -> BudgetDecision | None:
        """Return a BLOCK decision if the action breaches a cap, else None.

        Enforces, in order: the team monthly cap, the agent daily cap, and the
        team hard-block ceiling. Shared by request() and commit_human_approval()
        so a human-approved action is held to the same limits as an auto-checked
        one.
        """
        # Team monthly cap
        if team_b and team_b.used_usd + cost_dec > team_b.monthly_cap_usd:
            return BudgetDecision(
                action=BudgetAction.BLOCK,
                reason="team_monthly_cap_exceeded",
                remaining_team_usd=float(
                    max(Decimal("0.0"), team_b.monthly_cap_usd - team_b.used_usd)
                ),
                remaining_agent_usd=float(
                    max(Decimal("0.0"), agent_b.daily_cap_usd - agent_b.used_usd)
                ),
            )

        # Agent daily cap
        if agent_b.used_usd + cost_dec > agent_b.daily_cap_usd:
            return BudgetDecision(
                action=BudgetAction.BLOCK,
                reason="agent_daily_cap_exceeded",
                remaining_team_usd=float(team_b.monthly_cap_usd - team_b.used_usd)
                if team_b
                else 0.0,
                remaining_agent_usd=float(
                    max(Decimal("0.0"), agent_b.daily_cap_usd - agent_b.used_usd)
                ),
            )

        # Hard-block ceiling on the single action cost
        if team_b and cost_dec > team_b.hard_block_ceiling_usd:
            return BudgetDecision(
                action=BudgetAction.BLOCK,
                reason="action_above_hard_block_ceiling",
                remaining_team_usd=float(team_b.monthly_cap_usd - team_b.used_usd),
                remaining_agent_usd=float(agent_b.daily_cap_usd - agent_b.used_usd),
            )

        return None

    def commit_human_approval(
        self, agent_id: str, action_cost_usd: float | Decimal
    ) -> BudgetDecision:
        cost_dec = Decimal(str(action_cost_usd))
        agent_b = self._agents[agent_id]
        team_b = self._teams[self._agent_to_team[agent_id]]
        # Re-validate caps at commit time. request() returns REQUIRE_HUMAN without
        # reserving the funds, so arbitrary other spend can land between the
        # request and the human's approval. Without re-checking, _commit() would
        # apply the spend unconditionally and drive used_usd past the hard cap.
        violation = self._validate_caps(team_b, agent_b, cost_dec)
        if violation is not None:
            return violation
        return self._commit(team_b, agent_b, cost_dec, BudgetAction.APPROVE, "human_approved")

    def _commit(
        self,
        team_b: TeamBudget | None,
        agent_b: AgentBudget,
        action_cost_usd: Decimal,
        action: BudgetAction,
        reason: str,
    ) -> BudgetDecision:
        if action_cost_usd < 0:
            raise ValueError("action_cost_usd must be non-negative")
        if team_b:
            team_b.used_usd += action_cost_usd
        agent_b.used_usd += action_cost_usd
        return BudgetDecision(
            action=action,
            reason=reason,
            remaining_team_usd=float(team_b.monthly_cap_usd - team_b.used_usd) if team_b else 0.0,
            remaining_agent_usd=float(agent_b.daily_cap_usd - agent_b.used_usd),
        )


__all__ = [
    "TeamBudget",
    "AgentBudget",
    "BudgetAction",
    "BudgetDecision",
    "BudgetGovernance",
]
