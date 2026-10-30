"""Core value types for the human-in-the-loop layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class InteractionPattern(str, Enum):
    """How a given action is surfaced to humans."""

    NOTIFY = "notify"  # fire-and-forget; no response expected
    QUESTION = "question"  # agent asks for clarification; a text answer is expected
    REVIEW = "review"  # human must approve / reject / edit before proceeding


class RequestState(str, Enum):
    """Lifecycle of a single HITL request."""

    PENDING = "pending"  # created, awaiting a human decision
    ANSWERED = "answered"  # a QUESTION received a text answer
    APPROVED = "approved"  # a REVIEW was approved
    REJECTED = "rejected"  # a REVIEW was rejected
    EDITED = "edited"  # a REVIEW was approved with modifications
    AUTO_APPROVED = "auto_approved"  # below the escalation threshold; never surfaced
    EXPIRED = "expired"  # no decision arrived before the timeout


class DecisionOutcome(str, Enum):
    """The outcome a human (or the auto-approver) selected for a request."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    ANSWER = "answer"
    AUTO_APPROVE = "auto_approve"


@dataclass
class HumanDecision:
    """An immutable record of a single human (or auto) decision, for audit."""

    when: datetime
    request_id: str
    outcome: DecisionOutcome
    decided_by: str
    session_id: str
    comment: str | None = None
    edited_payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "when": self.when.isoformat(),
            "request_id": self.request_id,
            "outcome": self.outcome.value,
            "decided_by": self.decided_by,
            "session_id": self.session_id,
            "comment": self.comment,
            "edited_payload": dict(self.edited_payload) if self.edited_payload else None,
        }

    @classmethod
    def now(
        cls,
        request_id: str,
        outcome: DecisionOutcome,
        decided_by: str,
        session_id: str,
        *,
        comment: str | None = None,
        edited_payload: dict[str, Any] | None = None,
    ) -> HumanDecision:
        return cls(
            when=datetime.now(UTC),
            request_id=request_id,
            outcome=outcome,
            decided_by=decided_by,
            session_id=session_id,
            comment=comment,
            edited_payload=edited_payload,
        )


@dataclass
class _AuditTrail:
    """An append-only list of human decisions."""

    _entries: list[HumanDecision] = field(default_factory=list)

    def record(self, decision: HumanDecision) -> None:
        self._entries.append(decision)

    def all(self) -> list[HumanDecision]:
        return list(self._entries)

    def for_session(self, session_id: str) -> list[HumanDecision]:
        return [d for d in self._entries if d.session_id == session_id]

    def __len__(self) -> int:
        return len(self._entries)
