"""Emergency controls API (TO-025).

``GET  /api/v1/protect/control``                          kill-switch state and the caller tenant's suspensions
``GET  /api/v1/protect/control/history``                  the control audit trail (operator)
``POST /api/v1/protect/control/kill``     {"reason"}       stop every agent (operator)
``POST /api/v1/protect/control/reset``    {"reason"}       lift the emergency stop (operator)
``POST /api/v1/protect/control/agents/{agent_id}/suspend|resume``      {"reason"}
``POST /api/v1/protect/control/sessions/{session_id}/suspend|resume``  {"reason"}

The global switch is operator-only. Suspensions are scoped to the caller's authenticated
tenant: a tenant can stop its own agents and sessions, never another tenant's. Every
action is an append-only ``protect.control`` record naming its actor; agents sharing the
store (``AGENTWATCH_STORE``) pick it up before their next tool call, and agents on this
host see the kill switch immediately through the kill file.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panoptis.protect.control import ControlState
from panoptis.protect.ledger import ProtectLedger

_state: ControlState | None = None
_lock = threading.Lock()


def get_control() -> ControlState:
    global _state
    with _lock:
        if _state is None:
            _state = ControlState(
                ProtectLedger(os.environ.get("AGENTWATCH_STORE") or None), refresh_s=0.0
            )
        return _state


def set_control(state: ControlState | None) -> None:
    global _state
    with _lock:
        _state = state


class ControlRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    actor: str = Field(default="api", min_length=1, max_length=200)


def build_router(
    auth: Callable[..., Any], operator: Callable[..., Any], tenant: Callable[..., str]
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/protect/control")

    @router.get("", dependencies=[Depends(auth)])
    def status(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        st = get_control().status(tenant_id=tenant_id)
        st.pop("local_kill_file", None)  # a server path: not a tenant's business
        return {"tenant_id": tenant_id, **st}

    @router.get("/history", dependencies=[Depends(operator)])
    def history(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        rows = get_control().history(limit)
        return {"records": rows, "count": len(rows)}

    @router.post("/kill", dependencies=[Depends(operator)])
    def kill(body: ControlRequest) -> dict[str, Any]:
        return get_control().trip(body.reason, actor=f"operator:{body.actor}")

    @router.post("/reset", dependencies=[Depends(operator)])
    def reset(body: ControlRequest) -> dict[str, Any]:
        return get_control().reset(body.reason, actor=f"operator:{body.actor}")

    def scoped(kind: str, op: str) -> Callable[..., dict[str, Any]]:
        def handler(
            target: str, body: ControlRequest, tenant_id: str = Depends(tenant)
        ) -> dict[str, Any]:
            c = get_control()
            fn = getattr(c, f"{op}_{kind}")
            try:
                return fn(
                    target, body.reason, actor=f"{tenant_id}:{body.actor}", tenant_id=tenant_id
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        return handler

    for kind, path in (("agent", "agents/{target}"), ("session", "sessions/{target}")):
        for op in ("suspend", "resume"):
            router.add_api_route(
                f"/{path}/{op}", scoped(kind, op), methods=["POST"], dependencies=[Depends(auth)]
            )

    return router
