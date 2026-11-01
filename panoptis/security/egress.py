"""AgentWatch security alias for Egress Guard."""

from __future__ import annotations

from panopti.egress import EgressGuard, SSRFBlockedError

__all__ = ["EgressGuard", "SSRFBlockedError"]
