"""Human approval for escalated actions (HITL).

An escalated action is held until a human approves or rejects it, the request expires,
or it is cancelled; anything but an approval is a rejection (fail closed). Approvals
work across processes: the request is a ``protect.approval.request`` record in the
requesting tenant's ledger, the dashboard/CLI/API appends a ``protect.approval.decision``
record, and the waiting agent or gateway picks it up. The first decision recorded wins;
later ones are ignored.

Binding and one-time use (TO-019):

* every request carries ``action_digest``: a SHA-256 over the tenant, agent, session,
  call id, tool and canonical arguments. An approver may state the digest it saw
  (``expected_digest``); a decision for a different action is refused.
* the pipeline re-checks the digest against the action it is about to run, so an
  approval never covers arguments that changed after it was requested.
* an approval belongs to one ``call_id``, and the pipeline runs a call id at most once,
  so an approval cannot be replayed for another call.
* a request past its expiry can no longer be approved: deciding it records the timeout.

An approver can also edit the arguments ("approve with changes"); the action then runs
with the edited arguments, which are inspected again before execution.

For single-process use (tests, notebooks, a CLI prompt) a synchronous ``approver``
callable can decide directly.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from panoptis.protect.ledger import KIND_APPROVAL_DECISION, KIND_APPROVAL_REQUEST, ProtectLedger
from panoptis.protect.model import Decision, ToolAction


class ApprovalError(ValueError):
    """A decision that cannot be applied (unknown request, wrong action)."""


def action_digest(action: ToolAction) -> str:
    """What exactly is being approved: tenant, agent, session, call, tool and arguments."""
    doc = {
        "tenant_id": action.tenant_id,
        "agent_id": action.agent_id,
        "session_id": action.session_id,
        "call_id": action.call_id,
        "tool": action.tool,
        "arguments": action.arguments,
    }
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=repr)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    tenant_id: str = "default"
    action_digest: str = ""
    verdict: str = "escalate_hitl"
    policy_version: str | None = None
    threat_rules_version: str | None = None

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
    cancelled: bool = False
    decided_at: float = field(default_factory=time.time)
    action_digest: str | None = None  # the action this decision was made for

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


Approver = Callable[[ApprovalRequest], ApprovalOutcome | bool]

_OUTCOME_FIELDS = (
    "approved",
    "decided_by",
    "comment",
    "edited_arguments",
    "timed_out",
    "request_id",
    "cancelled",
    "decided_at",
    "action_digest",
)


def _outcome(payload: dict[str, Any], request_id: str) -> ApprovalOutcome:
    return ApprovalOutcome(
        approved=bool(payload.get("approved")),
        decided_by=str(payload.get("decided_by")),
        comment=payload.get("comment"),
        edited_arguments=payload.get("edited_arguments"),
        timed_out=bool(payload.get("timed_out")),
        request_id=request_id,
        cancelled=bool(payload.get("cancelled")),
        decided_at=float(payload.get("decided_at") or 0.0),
        action_digest=payload.get("action_digest"),
    )


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
            tenant_id=a.tenant_id,
            action_digest=action_digest(a),
            verdict=decision.verdict.value,
            policy_version=decision.policy_version,
            threat_rules_version=decision.threat_rules_version,
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
                    "tool": a.tool,
                },
                tenant_id=a.tenant_id,
            )
        return req

    def wait(
        self, req: ApprovalRequest, *, abort: Callable[[], str | None] | None = None
    ) -> ApprovalOutcome:
        """Wait for the decision. ``abort`` is polled; a reason it returns (an emergency
        stop) withdraws the request: it is recorded as cancelled and can no longer be approved."""
        if self.approver is not None:
            got = self.approver(req)
            out = (
                got
                if isinstance(got, ApprovalOutcome)
                else ApprovalOutcome(approved=bool(got), decided_by="approver-callback")
            )
            return self.resolve(
                req.request_id,
                out.approved,
                out.decided_by,
                comment=out.comment,
                edited_arguments=out.edited_arguments,
                tenant_id=req.tenant_id,
            )
        while True:
            found = self._lookup(req.request_id, req.tenant_id)
            if found is not None:
                return found
            stop = abort() if abort is not None else None
            if stop:
                cancelled = self.cancel(
                    req.request_id, "system:emergency-stop", tenant_id=req.tenant_id, comment=stop
                )
                if cancelled is not None:
                    return cancelled
            remaining = req.expires_at - time.time()
            if remaining <= 0:
                return self._record(
                    req,
                    ApprovalOutcome(
                        approved=False,
                        decided_by="system:timeout",
                        comment=f"no decision within {int(req.expires_at - req.created_at)}s",
                        timed_out=True,
                    ),
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
        tenant_id: str | None = None,
        expected_digest: str | None = None,
    ) -> ApprovalOutcome:
        """Record a decision. If one was already recorded, that one stands and is returned.

        ``expected_digest`` is the ``action_digest`` the approver was shown; a decision for
        a different action raises :class:`ApprovalError`. Approving an expired request
        records the timeout instead.
        """
        req = self._request(request_id, tenant_id)
        if req is None:
            raise ApprovalError(f"unknown approval request {request_id!r}")
        existing = self._lookup(request_id, req.tenant_id)
        if existing is not None:
            return existing
        if expected_digest is not None and expected_digest != req.action_digest:
            raise ApprovalError(
                "the decision was made for a different action (action_digest mismatch)"
            )
        if approved and time.time() > req.expires_at:
            approved, timed_out = False, True
            comment = f"approval by {decided_by} arrived after the request expired"
            decided_by = "system:timeout"
        return self._record(
            req,
            ApprovalOutcome(
                approved=approved,
                decided_by=decided_by,
                comment=comment,
                edited_arguments=edited_arguments,
                timed_out=timed_out,
            ),
        )

    def cancel(
        self,
        request_id: str,
        decided_by: str = "system:cancelled",
        *,
        tenant_id: str | None = None,
        comment: str = "the requesting call was cancelled",
    ) -> ApprovalOutcome | None:
        """Withdraw a pending request (its caller went away). A decided one is unchanged."""
        req = self._request(request_id, tenant_id)
        if req is None:
            return None
        existing = self._lookup(request_id, req.tenant_id)
        if existing is not None:
            return existing
        return self._record(
            req,
            ApprovalOutcome(
                approved=False,
                decided_by=decided_by,
                comment=comment,
                cancelled=True,
            ),
        )

    def cancel_for_call(self, call_id: str, decided_by: str = "system:cancelled") -> int:
        """Cancel every pending request this process made for ``call_id``."""
        with self._cond:
            reqs = [r for r in self._local.values() if r.call_id == call_id]
        n = 0
        for r in reqs:
            out = self.cancel(r.request_id, decided_by, tenant_id=r.tenant_id)
            n += bool(out and out.cancelled)
        return n

    def _record(self, req: ApprovalRequest, out: ApprovalOutcome) -> ApprovalOutcome:
        out.request_id = req.request_id
        out.action_digest = req.action_digest
        out.decided_at = time.time()
        if self.ledger is not None:
            self.ledger.record(
                KIND_APPROVAL_DECISION,
                out.to_dict(),
                {
                    "approval_request": req.request_id,
                    "call_id": req.call_id,
                    "agent_id": req.agent_id,
                    "session_id": req.session_id,
                    "tool": req.tool,
                },
                tenant_id=req.tenant_id,
            )
            # a concurrent decision may have landed first: the earliest record wins
            first = self._lookup(req.request_id, req.tenant_id, refresh=True)
            if first is not None:
                out = first
        with self._cond:
            self._decided[req.request_id] = out
            self._cond.notify_all()
        return out

    def _request(self, request_id: str, tenant_id: str | None) -> ApprovalRequest | None:
        with self._cond:
            local = self._local.get(request_id)
        if local is not None:
            if tenant_id is not None and local.tenant_id != tenant_id:
                return None  # another tenant's request does not exist for this caller
            return local
        if self.ledger is None:
            return None
        rows = self.ledger.find(
            "approval_request", request_id, (KIND_APPROVAL_REQUEST,), tenant_id=tenant_id
        )
        if not rows:
            return None
        doc = rows[0]["payload"]
        known = set(ApprovalRequest.__dataclass_fields__)
        return ApprovalRequest(**{k: v for k, v in doc.items() if k in known})

    def _lookup(
        self, request_id: str, tenant_id: str | None = None, *, refresh: bool = False
    ) -> ApprovalOutcome | None:
        with self._cond:
            if not refresh and request_id in self._decided:
                return self._decided[request_id]
        if self.ledger is None:
            return None
        rows = self.ledger.find(
            "approval_request", request_id, (KIND_APPROVAL_DECISION,), tenant_id=tenant_id
        )
        if not rows:
            return None
        out = _outcome(rows[0]["payload"], request_id)
        with self._cond:
            self._decided[request_id] = out
        return out

    # ── views ──────────────────────────────────────────────────────────────────────
    def get(self, request_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        req = self._request(request_id, tenant_id)
        if req is None:
            return None
        outcome = self._lookup(request_id, req.tenant_id)
        doc = req.to_dict()
        return {
            **doc,
            "state": _state(doc, outcome),
            "outcome": outcome.to_dict() if outcome else None,
        }

    def list(self, *, limit: int = 100, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Recent requests with their state: pending, approved, rejected, expired or cancelled."""
        if self.ledger is None:
            reqs = sorted(
                (r for r in self._local.values() if tenant_id is None or r.tenant_id == tenant_id),
                key=lambda r: r.created_at,
                reverse=True,
            )[:limit]
            return [
                {
                    **r.to_dict(),
                    "state": _state(r.to_dict(), self._decided.get(r.request_id)),
                    "outcome": (o.to_dict() if (o := self._decided.get(r.request_id)) else None),
                }
                for r in reqs
            ]
        rows = self.ledger.query((KIND_APPROVAL_REQUEST,), limit=limit, tenant_id=tenant_id)
        decisions: dict[str, dict[str, Any]] = {}
        for r in self.ledger.query(
            (KIND_APPROVAL_DECISION,), limit=limit * 4, tenant_id=tenant_id, newest_first=False
        ):
            decisions.setdefault(r["declared"].get("approval_request"), r["payload"])
        out = []
        for row in rows:
            doc = row["payload"]
            d = decisions.get(doc["request_id"])
            outcome = _outcome(d, doc["request_id"]) if d else None
            out.append(
                {
                    **doc,
                    "obs_id": row["obs_id"],
                    "state": _state(doc, outcome),
                    "outcome": outcome.to_dict() if outcome else None,
                }
            )
        return out

    def pending(self, *, tenant_id: str | None = None) -> builtins.list[dict[str, Any]]:
        return [r for r in self.list(limit=500, tenant_id=tenant_id) if r["state"] == "pending"]


def _state(doc: dict[str, Any], outcome: ApprovalOutcome | None) -> str:
    if outcome is None:
        return (
            "expired" if doc.get("expires_at", 0) and time.time() > doc["expires_at"] else "pending"
        )
    if outcome.cancelled:
        return "cancelled"
    if outcome.timed_out:
        return "expired"
    return "approved" if outcome.approved else "rejected"
