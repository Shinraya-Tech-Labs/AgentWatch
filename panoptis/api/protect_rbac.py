"""RBAC inspection API for the protection pipeline (TO-022).

``GET  /api/v1/protect/rbac/policy``                         the active policy, as it applies to the caller's tenant
``GET  /api/v1/protect/rbac/agents/{agent_id}/permissions``  an agent's effective permissions in the caller's tenant
``POST /api/v1/protect/rbac/check``                          would this call be authorized, and if not, why

The policy is the one agents enforce: ``AGENTWATCH_POLICY_FILE`` (re-read when it
changes). Every route is scoped to the caller's authenticated tenant; the check is a dry
run (it counts against no rate limit) and runs the same argument analysis the pipeline
uses, so restricted commands, paths and hosts are explained exactly as they would be
enforced.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from panoptis.protect.model import Verdict, strongest
from panoptis.protect.rbac import PolicyStore, RBACEngine
from panoptis.protect.threats import ThreatAnalyzer

_store: PolicyStore | None = None
_engine: RBACEngine | None = None
_lock = threading.Lock()
_threats = ThreatAnalyzer()


def get_engine() -> RBACEngine:
    global _store, _engine
    with _lock:
        if _engine is None:
            path = os.environ.get("AGENTWATCH_POLICY_FILE")
            _store = PolicyStore(path=path) if path else PolicyStore()
            _engine = RBACEngine(_store)
        return _engine


def set_policy_store(store: PolicyStore | None) -> None:
    """For tests and embedding: inspect this policy store instead of the configured file."""
    global _store, _engine
    with _lock:
        _store = store
        _engine = RBACEngine(store) if store is not None else None


class RBACCheckRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=200)


def build_router(auth: Callable[..., Any], tenant: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/protect/rbac", dependencies=[Depends(auth)])

    @router.get("/policy")
    def policy(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        engine = get_engine()
        engine.store.maybe_reload()
        p = engine.store.policy
        st = engine.store.status()
        return {
            "configured": p is not None,
            "tenant_id": tenant_id,
            "digest": st["digest"],
            "version": st["version"],
            "default": st["default"],
            "loaded_at": st["loaded_at"],
            "last_error": st["last_error"],
            "roles": p.to_dict()["roles"] if p else {},
            # only the bindings in force for the caller's tenant, never another tenant's
            "agents": p.bindings_for(tenant_id) if p else {},
        }

    @router.get("/agents/{agent_id}/permissions")
    def permissions(agent_id: str, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        return get_engine().effective_permissions(agent_id, tenant_id)

    @router.post("/check")
    def check(body: RBACCheckRequest, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        profile = _threats.analyze(body.tool, body.arguments).profile
        findings = get_engine().authorize(
            body.agent_id,
            body.tool,
            body.arguments,
            profile,
            tenant_id=tenant_id,
            session_id=body.session_id,
            dry_run=True,
        )
        verdict = strongest([f.recommends for f in findings])
        denials = [f for f in findings if f.recommends is not Verdict.ALLOW]
        return {
            "tenant_id": tenant_id,
            "agent_id": body.agent_id,
            "tool": body.tool,
            "verdict": verdict.value,
            "authorized": verdict is Verdict.ALLOW,
            "reasons": [f.message for f in denials],
            "findings": [f.to_dict() for f in findings],
        }

    return router
