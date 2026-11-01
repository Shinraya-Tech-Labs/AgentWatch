"""Human approval for escalated actions (HITL).

An escalated action is held until a human approves or rejects it, or the request expires;
expiry is a rejection (fail closed). Approvals work across processes: the request is a
``protect.approval.request`` record in the ledger, the dashboard/CLI/API appends a
``protect.approval.decision`` record, and the waiting agent or gateway picks it up. The
first decision recorded wins; later ones are reported as conflicts and ignored.

An approver can also edit the arguments ("approve with changes"); the action then runs
with the edited arguments, which are inspected again before execution.

For single-process use (tests, notebooks, a CLI prompt) a synchronous ``approver``
callable can decide directly.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentwatch.protect.ledger import KIND_APPROVAL_DECISION, KIND_APPROVAL_REQUEST, ProtectLedger
from agentwatch.protect.model import Decision


@dataclass
class ApprovalRequest:
    request_id: str
    call_id: str
    agent_id: str
    session_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    risk_score: float
    findings: list[dict[str, Any]]
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


@dataclass
class ApprovalOutcome:
    approved: bool
    decided_by: str
    comment: str | None = None
    edited_arguments: dict[str, Any] | None = None
    timed_out: bool = False
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


Approver = Callable[[ApprovalRequest], ApprovalOutcome | bool]


class ApprovalBroker:
    def __init__(
        self,
        ledger: ProtectLedger | None = None,
        *,
        timeout_s: float = 120.0,
        poll_s: float = 0.25,
        approver: Approver | None = None,
    ) -> None:
        self.ledger = ledger
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.approver = approver
        self._local: dict[str, ApprovalRequest] = {}
        self._decided: dict[str, ApprovalOutcome] = {}
        self._cond = threading.Condition()

    # ── agent side ─────────────────────────────────────────────────────────────────
    def request(self, decision: Decision, *, timeout_s: float | None = None) -> ApprovalRequest:
        a = decision.action
        req = ApprovalRequest(
            request_id=f"apr-{uuid.uuid4().hex[:16]}",
            call_id=a.call_id,
            agent_id=a.agent_id,
            session_id=a.session_id,
            tool=a.tool,
            arguments=a.arguments,
            reason=decision.reason,
            risk_score=decision.risk_score,
            findings=[f.to_dict() for f in decision.findings if f.severity.rank >= 2],
        )
        req.expires_at = req.created_at + (timeout_s or self.timeout_s)
        with self._cond:
            self._local[req.request_id] = req
        if self.ledger is not None:
            self.ledger.record(
                KIND_APPROVAL_REQUEST,
                req.to_dict(),
                {
                    "approval_request": req.request_id,
                    "call_id": a.call_id,
                    "agent_id": a.agent_id,
                    "session_id": a.session_id,
                },
            )
        return req

    def wait(self, req: ApprovalRequest) -> ApprovalOutcome:
        if self.approver is not None:
            got = self.approver(req)
            out = (
                got
                if isinstance(got, ApprovalOutcome)
                else ApprovalOutcome(approved=bool(got), decided_by="approver-callback")
            )
            out.request_id = req.request_id
            return self.resolve(
                req.request_id,
                out.approved,
                out.decided_by,
                comment=out.comment,
                edited_arguments=out.edited_arguments,
            )
        while True:
            found = self._lookup(req.request_id)
            if found is not None:
                return found
            remaining = req.expires_at - time.time()
            if remaining <= 0:
                return self.resolve(
                    req.request_id,
                    False,
                    "system:timeout",
                    comment=f"no decision within {int(self.timeout_s)}s",
                    timed_out=True,
                )
            with self._cond:
                self._cond.wait(min(self.poll_s, remaining))

    # ── operator side ──────────────────────────────────────────────────────────────
    def resolve(
        self,
        request_id: str,
        approved: bool,
        decided_by: str,
        *,
        comment: str | None = None,
        edited_arguments: dict[str, Any] | None = None,
        timed_out: bool = False,
    ) -> ApprovalOutcome:
        """Record a decision. If one was already recorded, that one stands and is returned."""
        existing = self._lookup(request_id)
        if existing is not None:
            return existing
        out = ApprovalOutcome(
            approved=approved,
            decided_by=decided_by,
            comment=comment,
            edited_arguments=edited_arguments,
            timed_out=timed_out,
            request_id=request_id,
        )
        if self.ledger is not None:
            self.ledger.record(
                KIND_APPROVAL_DECISION, out.to_dict(), {"approval_request": request_id}
            )
            # a concurrent decision may have landed first: the earliest record wins
            first = self._lookup(request_id, refresh=True)
            if first is not None:
                out = first
        with self._cond:
            self._decided[request_id] = out
            self._cond.notify_all()
        return out

    def _lookup(self, request_id: str, *, refresh: bool = False) -> ApprovalOutcome | None:
        with self._cond:
            if not refresh and request_id in self._decided:
                return self._decided[request_id]
        if self.ledger is None:
            return None
        rows = self.ledger.find("approval_request", request_id, (KIND_APPROVAL_DECISION,))
        if not rows:
            return None
        p = rows[0]["payload"]
        out = ApprovalOutcome(
            approved=bool(p.get("approved")),
            decided_by=str(p.get("decided_by")),
            comment=p.get("comment"),
            edited_arguments=p.get("edited_arguments"),
            timed_out=bool(p.get("timed_out")),
            request_id=request_id,
        )
        with self._cond:
            self._decided[request_id] = out
        return out

    def get(self, request_id: str) -> dict[str, Any] | None:
        req = self._local.get(request_id)
        doc: dict[str, Any] | None = req.to_dict() if req else None
        if doc is None and self.ledger is not None:
            rows = self.ledger.find("approval_request", request_id, (KIND_APPROVAL_REQUEST,))
            doc = rows[0]["payload"] if rows else None
        if doc is None:
            return None
        outcome = self._lookup(request_id)
        return {
            **doc,
            "state": _state(doc, outcome),
            "outcome": outcome.to_dict() if outcome else None,
        }

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Recent requests with their state: pending, approved, rejected or expired."""
        if self.ledger is None:
            reqs = sorted(self._local.values(), key=lambda r: r.created_at, reverse=True)[:limit]
            return [
                {
                    **r.to_dict(),
                    "state": _state(r.to_dict(), self._decided.get(r.request_id)),
                    "outcome": (o.to_dict() if (o := self._decided.get(r.request_id)) else None),
                }
                for r in reqs
            ]
        reqs = self.ledger.query((KIND_APPROVAL_REQUEST,), limit=limit)
        decisions = {
            r["declared"].get("approval_request"): r["payload"]
            for r in reversed(self.ledger.query((KIND_APPROVAL_DECISION,), limit=limit * 4))
        }
        out = []
        for r in reqs:
            doc = r["payload"]
            d = decisions.get(doc["request_id"])
            outcome = (
                ApprovalOutcome(
                    **{
                        k: d.get(k)
                        for k in (
                            "approved",
                            "decided_by",
                            "comment",
                            "edited_arguments",
                            "timed_out",
                            "request_id",
                        )
                    }
                )
                if d
                else None
            )
            out.append(
                {
                    **doc,
                    "obs_id": r["obs_id"],
                    "state": _state(doc, outcome),
                    "outcome": outcome.to_dict() if outcome else None,
                }
            )
        return out

    def pending(self) -> list[dict[str, Any]]:
        return [r for r in self.list(limit=500) if r["state"] == "pending"]


def _state(doc: dict[str, Any], outcome: ApprovalOutcome | None) -> str:
    if outcome is None:
        return (
            "expired" if doc.get("expires_at", 0) and time.time() > doc["expires_at"] else "pending"
        )
    if outcome.timed_out:
        return "expired"
    return "approved" if outcome.approved else "rejected"
