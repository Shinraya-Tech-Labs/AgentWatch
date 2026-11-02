"""Approval API for escalated tool calls (TO-019).

``GET  /api/v1/protect/approvals``                 the tenant's recent requests and their state
``GET  /api/v1/protect/approvals/{request_id}``    one request, with the exact action bound to it
``POST /api/v1/protect/approvals/{request_id}``    approve or reject it

Requests are written by the agent's pipeline into its tenant's evidence ledger; this API
reads and decides them in the same store (``AGENTWATCH_STORE``), so the waiting agent
picks the decision up. Every route is scoped to the caller's authenticated tenant: a
request of another tenant does not exist for it. A decision must name the
``action_digest`` it was made for, so an approval can never cover an action the approver
did not see.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panoptis.protect.approvals import ApprovalBroker, ApprovalError
from panoptis.protect.ledger import ProtectLedger

_broker: ApprovalBroker | None = None
_lock = threading.Lock()


def get_broker() -> ApprovalBroker:
    global _broker
    with _lock:
        if _broker is None:
            _broker = ApprovalBroker(ProtectLedger(os.environ.get("AGENTWATCH_STORE") or None))
        return _broker


def set_broker(broker: ApprovalBroker | None) -> None:
    """For tests and embedding: use this broker (and its ledger) instead of the default."""
    global _broker
    with _lock:
        _broker = broker


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    action_digest: str = Field(min_length=8, max_length=200)
    decided_by: str = Field(min_length=1, max_length=200)
    comment: str | None = Field(default=None, max_length=2000)
    edited_arguments: dict[str, Any] | None = None


def build_router(auth: Callable[..., Any], tenant: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/protect/approvals", dependencies=[Depends(auth)])

    @router.get("")
    def list_approvals(
        state: str | None = Query(
            default=None, pattern="^(pending|approved|rejected|expired|cancelled)$"
        ),
        limit: int = Query(default=100, ge=1, le=500),
        tenant_id: str = Depends(tenant),
    ) -> dict[str, Any]:
        rows = get_broker().list(limit=limit, tenant_id=tenant_id)
        if state:
            rows = [r for r in rows if r["state"] == state]
        return {"approvals": rows, "count": len(rows)}

    @router.get("/{request_id}")
    def get_approval(request_id: str, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        doc = get_broker().get(request_id, tenant_id=tenant_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="approval request not found")
        return doc

    @router.post("/{request_id}")
    def decide(
        request_id: str, body: ApprovalDecisionRequest, tenant_id: str = Depends(tenant)
    ) -> dict[str, Any]:
        broker = get_broker()
        if broker.get(request_id, tenant_id=tenant_id) is None:
            raise HTTPException(status_code=404, detail="approval request not found")
        try:
            out = broker.resolve(
                request_id,
                body.approved,
                f"{tenant_id}:{body.decided_by}",
                comment=body.comment,
                edited_arguments=body.edited_arguments,
                tenant_id=tenant_id,
                expected_digest=body.action_digest,
            )
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        doc = broker.get(request_id, tenant_id=tenant_id) or {}
        applied = (
            out.approved == body.approved and out.decided_by == f"{tenant_id}:{body.decided_by}"
        )
        return {**doc, "applied": applied}

    return router
