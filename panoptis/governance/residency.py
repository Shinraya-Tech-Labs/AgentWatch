"""
CMP-007 — Data Residency Controls.

EU-only, US-only, specific-region routing. Selects the correct storage
endpoint at runtime based on a user/team residency requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Region(str, Enum):
    EU_WEST = "eu-west-1"
    EU_CENTRAL = "eu-central-1"
    US_EAST = "us-east-1"
    US_WEST = "us-west-2"
    APAC_SINGAPORE = "ap-southeast-1"
    APAC_TOKYO = "ap-northeast-1"


_GEO_GROUP = {
    "EU": {Region.EU_WEST, Region.EU_CENTRAL},
    "US": {Region.US_EAST, Region.US_WEST},
    "APAC": {Region.APAC_SINGAPORE, Region.APAC_TOKYO},
}


@dataclass
class ResidencyPolicy:
    name: str
    allowed_regions: set[Region]
    fallback_region: Region | None = None


@dataclass
class ResidencyDecision:
    region: Region
    reason: str


class ResidencyRouter:
    def __init__(self) -> None:
        self._endpoints: dict[Region, str] = {}
        self._policies: dict[str, ResidencyPolicy] = {}

    def register_endpoint(self, region: Region, endpoint: str) -> None:
        self._endpoints[region] = endpoint

    def add_policy(self, key: str, policy: ResidencyPolicy) -> None:
        self._policies[key] = policy

    def route(self, key: str, *, current_user_region: Region | None = None) -> ResidencyDecision:
        policy = self._policies.get(key)
        if policy is None:
            # No policy: use the user's region if available, else US_EAST
            chosen = current_user_region or Region.US_EAST
            return ResidencyDecision(region=chosen, reason="no_policy_default")

        # If the current region is allowed, use it
        if current_user_region in policy.allowed_regions:
            return ResidencyDecision(region=current_user_region, reason="user_region_allowed")

        # Else pick the first allowed region (deterministic order)
        ordered = sorted(policy.allowed_regions, key=lambda r: r.value)
        if ordered:
            return ResidencyDecision(region=ordered[0], reason="policy_primary")
        if policy.fallback_region:
            return ResidencyDecision(region=policy.fallback_region, reason="policy_fallback")
        return ResidencyDecision(region=Region.US_EAST, reason="hard_default")

    def endpoint_for(self, region: Region) -> str | None:
        return self._endpoints.get(region)


def eu_only_policy() -> ResidencyPolicy:
    return ResidencyPolicy(name="eu_only", allowed_regions=_GEO_GROUP["EU"])


def us_only_policy() -> ResidencyPolicy:
    return ResidencyPolicy(name="us_only", allowed_regions=_GEO_GROUP["US"])


__all__ = [
    "Region",
    "ResidencyPolicy",
    "ResidencyDecision",
    "ResidencyRouter",
    "eu_only_policy",
    "us_only_policy",
]
