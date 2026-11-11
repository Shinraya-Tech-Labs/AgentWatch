"""Panopti turnkey domain-specific guard profiles.

Pre-packaged guardrail profiles tailored for the top real-world agent archetypes
identified in industry catalogs (500-AI-Agents-Projects and AI-research-SKILLs).

Governed by ponytail: ultra (pure Python standard library).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GuardProfile:
    """Pre-configured security policy for an agent domain archetype."""

    name: str
    description: str
    confidence_floor: float
    destructive_patterns: list[str] = field(default_factory=list)
    required_keys: list[str] = field(default_factory=list)
    max_argument_values: dict[str, float] = field(default_factory=dict)
    max_consecutive_failures: int = 3


# 1. FinTech & Financial Operations Profile
PROFILE_FINTECH = GuardProfile(
    name="fintech",
    description="Strict monetary transaction caps, idempotency key verification, and negative-value traps.",
    confidence_floor=0.75,
    destructive_patterns=[
        r"(?i)\b(unauthorized_transfer|drain_account|bypass_kyc)\b",
    ],
    required_keys=["idempotency_key"],
    max_argument_values={"amount": 10000.0, "amount_usd": 10000.0},
    max_consecutive_failures=2,
)

# 2. DevOps & Cloud Infrastructure Profile
PROFILE_DEVOPS = GuardProfile(
    name="devops",
    description="Protection against unbounded deletions, production database purges, and destructive cloud mutations.",
    confidence_floor=0.50,
    destructive_patterns=[
        r"(?i)\b(DROP\s+TABLE|TRUNCATE|DELETE\s+FROM)\b",
        r"(?i)\b(rm\s+-rf\s+[/~]|mkfs|dd\s+if=)\b",
        r"(?i)\b(aws\s+s3\s+rb|aws\s+rds\s+delete-db|gcloud\s+projects\s+delete)\b",
        r"(?i)\b(docker\s+system\s+prune\s+-a|kubectl\s+delete\s+ns)\b",
    ],
    max_consecutive_failures=2,
)

# 3. Healthcare & Diagnostic Assistant Profile
PROFILE_HEALTHCARE = GuardProfile(
    name="healthcare",
    description="High epistemic confidence threshold, PII/HIPAA redacting, and hallucinated dosage guards.",
    confidence_floor=0.85,
    destructive_patterns=[
        r"(?i)\b(override_dosage|unverified_prescription|export_patient_records)\b",
    ],
    max_consecutive_failures=1,
)

# 4. Customer Support & RAG Retrieval Profile
PROFILE_SUPPORT_RAG = GuardProfile(
    name="support",
    description="Anti-jailbreak protection, system prompt extraction defense, and output budget caps.",
    confidence_floor=0.60,
    destructive_patterns=[
        r"(?i)\b(ignore\s+all\s+previous\s+instructions|system\s+override|dan\s+mode)\b",
        r"(?i)\b(reveal\s+system\s+prompt|print\s+hidden\s+instructions)\b",
    ],
    max_consecutive_failures=3,
)

PROFILES: dict[str, GuardProfile] = {
    "fintech": PROFILE_FINTECH,
    "devops": PROFILE_DEVOPS,
    "healthcare": PROFILE_HEALTHCARE,
    "support": PROFILE_SUPPORT_RAG,
}


def get_profile(name: str) -> GuardProfile:
    """Retrieve a guard profile by name (defaults to devops if unknown)."""
    return PROFILES.get(name.lower().strip(), PROFILE_DEVOPS)
