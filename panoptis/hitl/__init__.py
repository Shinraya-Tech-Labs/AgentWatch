"""Human-in-the-loop ambient integration for PANOPTIS.

Adds an escalation layer on top of the existing alerting engine: agent actions
are triaged by risk level into one of three interaction patterns — NOTIFY
(fire-and-forget, actionable events only), QUESTION (agent asks a human for
clarification), or REVIEW (human must approve / reject / edit before the action
proceeds). Every human decision is recorded in an audit trail, and a bounded
feedback loop adjusts the per-risk escalation thresholds based on observed
approval patterns.
"""

from panoptis.hitl.decisions import (
    DecisionOutcome,
    HumanDecision,
    InteractionPattern,
    RequestState,
)
from panoptis.hitl.feedback import FeedbackLearner
from panoptis.hitl.orchestrator import HITLOrchestrator, HITLRequest
from panoptis.hitl.thresholds import HITLThresholds

__all__ = [
    "DecisionOutcome",
    "HumanDecision",
    "InteractionPattern",
    "RequestState",
    "FeedbackLearner",
    "HITLOrchestrator",
    "HITLRequest",
    "HITLThresholds",
]
