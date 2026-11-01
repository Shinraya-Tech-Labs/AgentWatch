"""HITL orchestrator: triage actions, surface them to humans, collect decisions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from panoptis.core.schema import AgentEvent, RiskLevel
from panoptis.hitl.decisions import (
    DecisionOutcome,
    HumanDecision,
    InteractionPattern,
    RequestState,
    _AuditTrail,
)
from panoptis.hitl.feedback import FeedbackLearner
from panoptis.hitl.thresholds import HITLThresholds

logger = logging.getLogger(__name__)


@dataclass
class HITLRequest:
    """A single action awaiting (or having received) a human decision."""

    request_id: str
    session_id: str
    risk_level: RiskLevel
    pattern: InteractionPattern | None
    summary: str
    payload: dict[str, Any]
    state: RequestState
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    question: str | None = None
    answer: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.state not in (RequestState.PENDING,)

    @property
    def is_actionable(self) -> bool:
        """Whether the agent may proceed with the original action."""
        return self.state in (
            RequestState.APPROVED,
            RequestState.EDITED,
            RequestState.AUTO_APPROVED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "risk_level": self.risk_level.value,
            "pattern": self.pattern.value if self.pattern else None,
            "summary": self.summary,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "question": self.question,
            "answer": self.answer,
        }


class HITLError(RuntimeError):
    """Raised when a HITL operation is invalid (e.g. deciding a resolved request)."""


class HITLOrchestrator:
    """Coordinates human-in-the-loop escalation for agent actions.

    Routes surfaced requests through the existing alerting engine (if supplied),
    records every decision in an audit trail, and optionally feeds decisions to a
    :class:`FeedbackLearner` so escalation thresholds can adapt over time.
    """

    def __init__(
        self,
        thresholds: HITLThresholds | None = None,
        *,
        alerting_engine: Any | None = None,
        feedback_learner: FeedbackLearner | None = None,
    ) -> None:
        self.thresholds = thresholds or HITLThresholds()
        self._alerting = alerting_engine
        self._feedback = feedback_learner
        self._requests: dict[str, HITLRequest] = {}
        self._audit = _AuditTrail()

    # ------------------------------------------------------------- creation

    async def submit(
        self,
        event: AgentEvent,
        *,
        summary: str | None = None,
        question: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> HITLRequest:
        """Triage an event and create a HITL request.

        The risk level (from ``event.safety``) selects an interaction pattern via
        the thresholds. SAFE / unmapped levels are auto-approved immediately and
        never surfaced. NOTIFY requests are delivered fire-and-forget. QUESTION
        and REVIEW requests are left PENDING for a human decision.
        """
        risk = event.safety.risk_level if event.safety else RiskLevel.SAFE
        pattern = self.thresholds.pattern_for(risk)
        request_id = f"hitl-{uuid.uuid4().hex[:12]}"
        text = summary or self._default_summary(event, risk)

        # If the caller explicitly wants a question, force the QUESTION pattern
        # (an agent asking for clarification is a QUESTION regardless of risk).
        if question is not None:
            pattern = InteractionPattern.QUESTION

        req = HITLRequest(
            request_id=request_id,
            session_id=event.session_id,
            risk_level=risk,
            pattern=pattern,
            summary=text,
            payload=dict(payload or {}),
            state=RequestState.PENDING,
            question=question,
        )
        self._requests[request_id] = req

        if pattern is None:
            # Below the escalation threshold: auto-approve, never surface.
            self._resolve(
                req,
                RequestState.AUTO_APPROVED,
                DecisionOutcome.AUTO_APPROVE,
                decided_by="system",
            )
            return req

        if pattern is InteractionPattern.NOTIFY:
            await self._deliver(req)
            # A notify is informational; the agent proceeds without waiting.
            self._resolve(
                req,
                RequestState.AUTO_APPROVED,
                DecisionOutcome.AUTO_APPROVE,
                decided_by="system",
                comment="notify-only",
            )
            return req

        # QUESTION / REVIEW: surface and leave pending for a human.
        await self._deliver(req)
        return req

    # ------------------------------------------------------------- decisions

    def approve(
        self, request_id: str, decided_by: str, *, comment: str | None = None
    ) -> HITLRequest:
        req = self._pending(request_id, InteractionPattern.REVIEW)
        self._resolve(
            req, RequestState.APPROVED, DecisionOutcome.APPROVE, decided_by, comment=comment
        )
        return req

    def reject(
        self, request_id: str, decided_by: str, *, comment: str | None = None
    ) -> HITLRequest:
        req = self._pending(request_id, InteractionPattern.REVIEW)
        self._resolve(
            req, RequestState.REJECTED, DecisionOutcome.REJECT, decided_by, comment=comment
        )
        return req

    def edit(
        self,
        request_id: str,
        decided_by: str,
        edited_payload: dict[str, Any],
        *,
        comment: str | None = None,
    ) -> HITLRequest:
        req = self._pending(request_id, InteractionPattern.REVIEW)
        req.payload = dict(edited_payload)
        self._resolve(
            req,
            RequestState.EDITED,
            DecisionOutcome.EDIT,
            decided_by,
            comment=comment,
            edited_payload=edited_payload,
        )
        return req

    def answer(self, request_id: str, decided_by: str, answer: str) -> HITLRequest:
        req = self._pending(request_id, InteractionPattern.QUESTION)
        req.answer = answer
        self._resolve(
            req,
            RequestState.ANSWERED,
            DecisionOutcome.ANSWER,
            decided_by,
            comment=answer,
        )
        return req

    def expire(self, request_id: str) -> HITLRequest:
        """Mark a pending request as expired (no decision arrived in time)."""
        req = self._requests.get(request_id)
        if req is None:
            raise HITLError(f"unknown request {request_id}")
        if req.state is not RequestState.PENDING:
            raise HITLError(f"request {request_id} is already resolved ({req.state.value})")
        self._resolve(req, RequestState.EXPIRED, DecisionOutcome.REJECT, decided_by="system")
        return req

    # ------------------------------------------------------------- queries

    def get(self, request_id: str) -> HITLRequest | None:
        return self._requests.get(request_id)

    def pending(self) -> list[HITLRequest]:
        return [r for r in self._requests.values() if r.state is RequestState.PENDING]

    def audit_trail(self) -> list[HumanDecision]:
        return self._audit.all()

    def audit_for_session(self, session_id: str) -> list[HumanDecision]:
        return self._audit.for_session(session_id)

    # ------------------------------------------------------------- internals

    def _pending(self, request_id: str, expected: InteractionPattern) -> HITLRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise HITLError(f"unknown request {request_id}")
        if req.state is not RequestState.PENDING:
            raise HITLError(f"request {request_id} is already resolved ({req.state.value})")
        if req.pattern is not expected:
            raise HITLError(
                f"request {request_id} is a {req.pattern.value if req.pattern else None} "
                f"request, not {expected.value}"
            )
        return req

    def _resolve(
        self,
        req: HITLRequest,
        state: RequestState,
        outcome: DecisionOutcome,
        decided_by: str,
        *,
        comment: str | None = None,
        edited_payload: dict[str, Any] | None = None,
    ) -> None:
        req.state = state
        decision = HumanDecision.now(
            req.request_id,
            outcome,
            decided_by,
            req.session_id,
            comment=comment,
            edited_payload=edited_payload,
        )
        self._audit.record(decision)
        # Only genuine human REVIEW decisions inform the feedback learner.
        if self._feedback is not None and outcome in (
            DecisionOutcome.APPROVE,
            DecisionOutcome.REJECT,
            DecisionOutcome.EDIT,
        ):
            self._feedback.record(req.risk_level, outcome)

    async def _deliver(self, req: HITLRequest) -> None:
        """Deliver a surfaced request through the alerting engine, if configured.

        Best-effort: a delivery failure must not break request bookkeeping. The
        request still exists and can be decided directly even if no notification
        went out.
        """
        if self._alerting is None:
            return
        try:
            event = req.payload.get("_event")
            if event is not None and hasattr(self._alerting, "alert_event"):
                await self._alerting.alert_event(event)
        except Exception as exc:  # noqa: BLE001 - delivery must not break control flow
            logger.warning("HITL notification delivery failed for %s: %s", req.request_id, exc)

    @staticmethod
    def _default_summary(event: AgentEvent, risk: RiskLevel) -> str:
        tool = event.tool_call.tool_name if event.tool_call else event.event_type.value
        return f"HITL {risk.value}: {tool} in session {event.session_id}"
