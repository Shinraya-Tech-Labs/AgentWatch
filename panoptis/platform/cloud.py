"""
PLT-007 — Multi-Tenant Config.

Team and organization management. Per-team usage analytics.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Organization:
    org_id: str
    name: str
    plan: str = "free"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Team:
    team_id: str
    org_id: str
    name: str
    members: set[str] = field(default_factory=set)


@dataclass
class UsageRecord:
    team_id: str
    when: datetime
    metric: str  # sessions | tokens | usd | actions
    value: float


@dataclass
class UsageSummary:
    team_id: str
    sessions: int = 0
    tokens: int = 0
    usd: float = 0.0
    actions: int = 0


class TenantStore:
    """Minimal multi-tenant control plane."""

    def __init__(self) -> None:
        self._orgs: dict[str, Organization] = {}
        self._teams: dict[str, Team] = {}
        self._user_to_teams: dict[str, set[str]] = defaultdict(set)
        self._usage: list[UsageRecord] = []

    # ── orgs ────────────────────────────────────────────────────────────
    def create_org(self, name: str, *, plan: str = "free") -> Organization:
        org = Organization(org_id=f"org-{uuid.uuid4().hex[:8]}", name=name, plan=plan)
        self._orgs[org.org_id] = org
        return org

    def org(self, org_id: str) -> Organization | None:
        return self._orgs.get(org_id)

    # ── teams ───────────────────────────────────────────────────────────
    def create_team(self, org_id: str, name: str) -> Team:
        if org_id not in self._orgs:
            raise ValueError(f"unknown org: {org_id}")
        team = Team(team_id=f"team-{uuid.uuid4().hex[:8]}", org_id=org_id, name=name)
        self._teams[team.team_id] = team
        return team

    def add_member(self, team_id: str, user_id: str) -> None:
        if team_id not in self._teams:
            raise ValueError(f"unknown team: {team_id}")
        self._teams[team_id].members.add(user_id)
        self._user_to_teams[user_id].add(team_id)

    def teams_for(self, user_id: str) -> list[Team]:
        return [self._teams[tid] for tid in self._user_to_teams.get(user_id, set())]

    # ── usage ───────────────────────────────────────────────────────────
    def record_usage(self, team_id: str, metric: str, value: float) -> UsageRecord:
        rec = UsageRecord(team_id=team_id, when=datetime.now(UTC), metric=metric, value=value)
        self._usage.append(rec)
        return rec

    def summary(self, team_id: str) -> UsageSummary:
        s = UsageSummary(team_id=team_id)
        for r in self._usage:
            if r.team_id != team_id:
                continue
            if r.metric == "sessions":
                s.sessions += int(r.value)
            elif r.metric == "tokens":
                s.tokens += int(r.value)
            elif r.metric == "usd":
                s.usd += r.value
            elif r.metric == "actions":
                s.actions += int(r.value)
        return s


__all__ = ["Organization", "Team", "UsageRecord", "UsageSummary", "TenantStore"]
