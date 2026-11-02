"""Protection API: decisions, agents, loops, intent, MCP, discovery and evidence (TO-027).

All routes are versioned under ``/api/v1/protect``, authenticated, and scoped to the
caller's tenant: records of other tenants are never read. They serve the records the
agents' pipelines write to the shared evidence store (``AGENTWATCH_STORE``):

``GET /status``                         one-screen summary for the tenant
``GET /decisions``                      decisions, newest first; filters and an ``after`` cursor
``GET /decisions/{call_id}``            one call: decision, approval, execution, output records
``GET /loops``  ``GET /intent``          decisions with loop / intent-grounding findings
``GET /agents``                         agents and sessions seen, with counts and suspension state
``GET /mcp``                            MCP gateway activity per server
``GET /discovery``                      the latest recorded discovery inventory
``GET /evidence/verify``                verify the tenant's evidence chain (optionally sealing first)
``GET /evidence/head``                  the current head, to keep as an anchor
``GET /evidence/proof/{obs_id}``        inclusion proof of one of the tenant's records

Approvals, RBAC and emergency controls have their own routers
(``protect_approvals``, ``protect_rbac``, ``protect_control``).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from panoptis.protect.ledger import (
    KIND_AGENT,
    KIND_APPROVAL_DECISION,
    KIND_APPROVAL_REQUEST,
    KIND_DECISION,
    KIND_DISCOVERY,
    KIND_EXECUTION,
    KIND_OUTPUT,
    ProtectLedger,
)

_ledger: ProtectLedger | None = None
_lock = threading.Lock()
_HIDDEN_EVIDENCE_KEYS = {"regex", "compiled", "_pattern", "pattern_source"}  # internal rule text
MAX_SCAN = 5000  # records read to answer one aggregate query


def get_ledger() -> ProtectLedger:
    global _ledger
    with _lock:
        if _ledger is None:
            _ledger = ProtectLedger(os.environ.get("AGENTWATCH_STORE") or None)
        return _ledger


def set_ledger(ledger: ProtectLedger | None) -> None:
    global _ledger
    with _lock:
        _ledger = ledger


# ── response schemas ──────────────────────────────────────────────────────────────


class FindingOut(BaseModel):
    stage: str
    code: str
    severity: str
    message: str
    recommends: str
    threat: str | None = None
    evidence: dict[str, Any] = {}


class DecisionOut(BaseModel):
    obs_id: str
    at: str | None
    call_id: str
    tool: str
    agent_id: str
    session_id: str
    source: str | None = None
    verdict: str
    allowed: bool
    risk_score: float
    reason: str
    mode: str | None = None
    would_block: bool | None = None
    policy_version: str | None = None
    threat_rules_version: str | None = None
    mutated: bool = False
    approval_state: str | None = None
    findings: list[FindingOut]


class DecisionPage(BaseModel):
    tenant_id: str
    decisions: list[DecisionOut]
    count: int
    next_after: str | None


def _finding(f: dict[str, Any]) -> FindingOut:
    ev = {k: v for k, v in (f.get("evidence") or {}).items() if k not in _HIDDEN_EVIDENCE_KEYS}
    return FindingOut(
        stage=str(f.get("stage")),
        code=str(f.get("code")),
        severity=str(f.get("severity")),
        message=str(f.get("message")),
        recommends=str(f.get("recommends")),
        threat=f.get("threat"),
        evidence=ev,
    )


def _decision(r: dict[str, Any]) -> DecisionOut:
    p = r.get("payload") or {}
    a = p.get("action") or {}
    return DecisionOut(
        obs_id=r["obs_id"],
        at=r.get("observed_at"),
        call_id=str(p.get("call_id") or a.get("call_id") or ""),
        tool=str(a.get("tool") or r["declared"].get("tool") or ""),
        agent_id=str(a.get("agent_id") or ""),
        session_id=str(a.get("session_id") or ""),
        source=a.get("source"),
        verdict=str(p.get("verdict")),
        allowed=bool(p.get("allowed")),
        risk_score=float(p.get("risk_score") or 0.0),
        reason=str(p.get("reason") or ""),
        mode=p.get("mode"),
        would_block=p.get("would_block"),
        policy_version=p.get("policy_version"),
        threat_rules_version=p.get("threat_rules_version"),
        mutated=bool(p.get("arguments_mutated")),
        approval_state=(p.get("approval") or {}).get("state"),
        findings=[_finding(f) for f in p.get("findings") or []],
    )


def _decisions(
    tenant: str, *, limit: int = MAX_SCAN, after: str | None = None
) -> list[dict[str, Any]]:
    return get_ledger().query(
        (KIND_DECISION,), tenant_id=tenant, limit=limit, after=after, newest_first=after is None
    )


def build_router(
    auth: Callable[..., Any], tenant: Callable[..., str], operator: Callable[..., Any] | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/protect", dependencies=[Depends(auth)])

    @router.get("/decisions", response_model=DecisionPage)
    def decisions(
        verdict: str | None = Query(
            default=None, pattern="^(allow|block|escalate_hitl|mutate_and_allow)$"
        ),
        tool: str | None = Query(default=None, max_length=200),
        agent_id: str | None = Query(default=None, max_length=128),
        session_id: str | None = Query(default=None, max_length=200),
        stage: str | None = Query(default=None, max_length=32),
        code: str | None = Query(default=None, max_length=64),
        after: str | None = Query(
            default=None,
            max_length=26,
            description="return records newer than this obs_id, oldest first",
        ),
        limit: int = Query(default=100, ge=1, le=500),
        tenant_id: str = Depends(tenant),
    ) -> DecisionPage:
        rows = _decisions(
            tenant_id,
            after=after,
            limit=MAX_SCAN if any((verdict, tool, agent_id, session_id, stage, code)) else limit,
        )
        out: list[DecisionOut] = []
        for r in rows:
            d = _decision(r)
            if verdict and d.verdict != verdict:
                continue
            if tool and d.tool != tool:
                continue
            if agent_id and d.agent_id != agent_id:
                continue
            if session_id and d.session_id != session_id:
                continue
            if stage and not any(f.stage == stage and f.recommends != "allow" for f in d.findings):
                continue
            if code and not any(f.code == code for f in d.findings):
                continue
            out.append(d)
            if len(out) >= limit:
                break
        return DecisionPage(
            tenant_id=tenant_id,
            decisions=out,
            count=len(out),
            next_after=out[-1].obs_id if (after is not None and out) else None,
        )

    @router.get("/decisions/{call_id}")
    def call_detail(call_id: str, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        lg = get_ledger()
        recs = lg.find("call_id", call_id, tenant_id=tenant_id)
        if not recs:
            raise HTTPException(status_code=404, detail="no records for this call")
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for r in recs:
            by_kind.setdefault(r["kind"], []).append(r)
        decision = by_kind.get(KIND_DECISION, [])
        return {
            "tenant_id": tenant_id,
            "call_id": call_id,
            "decisions": [_decision(r).model_dump() for r in decision],
            "approval_requests": [r["payload"] for r in by_kind.get(KIND_APPROVAL_REQUEST, [])],
            "approval_decisions": [r["payload"] for r in by_kind.get(KIND_APPROVAL_DECISION, [])],
            "executions": [r["payload"] for r in by_kind.get(KIND_EXECUTION, [])],
            "outputs": [r["payload"] for r in by_kind.get(KIND_OUTPUT, [])],
        }

    def staged(stage_name: str, ignore: set[str]) -> Callable[..., dict[str, Any]]:
        def handler(
            limit: int = Query(default=100, ge=1, le=500), tenant_id: str = Depends(tenant)
        ) -> dict[str, Any]:
            events = []
            for r in _decisions(tenant_id):
                d = _decision(r)
                hits = [f for f in d.findings if f.stage == stage_name and f.code not in ignore]
                if hits:
                    events.append(
                        {
                            "obs_id": d.obs_id,
                            "at": d.at,
                            "call_id": d.call_id,
                            "tool": d.tool,
                            "agent_id": d.agent_id,
                            "session_id": d.session_id,
                            "verdict": d.verdict,
                            "findings": [h.model_dump() for h in hits],
                        }
                    )
                    if len(events) >= limit:
                        break
            return {"tenant_id": tenant_id, "events": events, "count": len(events)}

        return handler

    router.add_api_route("/loops", staged("loop", {"CIRCUIT_OPEN"}), methods=["GET"])
    router.add_api_route("/intent", staged("intent", {"GROUNDED", "NO_OBJECTIVE"}), methods=["GET"])

    @router.get("/agents")
    def agents(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        lg = get_ledger()
        info: dict[str, dict[str, Any]] = {}
        for r in lg.query((KIND_AGENT,), tenant_id=tenant_id, limit=MAX_SCAN, newest_first=False):
            p = r["payload"]
            aid = p.get("agent_id")
            if not aid:
                continue
            a = info.setdefault(
                aid,
                {
                    "agent_id": aid,
                    "sessions": {},
                    "decisions": 0,
                    "blocked": 0,
                    "escalated": 0,
                    "last_seen": None,
                },
            )
            sid = p.get("session_id")
            if sid:
                a["sessions"].setdefault(
                    sid,
                    {
                        "session_id": sid,
                        "objective": None,
                        "metadata": {},
                        "decisions": 0,
                        "blocked": 0,
                    },
                )
                if p.get("objective"):
                    a["sessions"][sid]["objective"] = p["objective"]
                if p.get("metadata"):
                    a["sessions"][sid]["metadata"] = {
                        k: v for k, v in p["metadata"].items() if k != "authorized_context"
                    }
            a["last_seen"] = r.get("observed_at")
        for r in _decisions(tenant_id):
            d = _decision(r)
            a = info.setdefault(
                d.agent_id,
                {
                    "agent_id": d.agent_id,
                    "sessions": {},
                    "decisions": 0,
                    "blocked": 0,
                    "escalated": 0,
                    "last_seen": None,
                },
            )
            s = a["sessions"].setdefault(
                d.session_id,
                {
                    "session_id": d.session_id,
                    "objective": None,
                    "metadata": {},
                    "decisions": 0,
                    "blocked": 0,
                },
            )
            a["decisions"] += 1
            s["decisions"] += 1
            if d.verdict == "block":
                a["blocked"] += 1
                s["blocked"] += 1
            if d.approval_state is not None:
                a["escalated"] += 1
            if a["last_seen"] is None or (d.at and d.at > a["last_seen"]):
                a["last_seen"] = d.at
        state = _control_status(tenant_id)
        for a in info.values():
            a["suspended"] = a["agent_id"] in state["suspended_agents"]
            for s in a["sessions"].values():
                s["suspended"] = s["session_id"] in state["suspended_sessions"]
            a["sessions"] = sorted(a["sessions"].values(), key=lambda s: s["session_id"])
        return {
            "tenant_id": tenant_id,
            "agents": sorted(info.values(), key=lambda a: a["agent_id"]),
            "killed": state["killed"],
        }

    @router.get("/mcp")
    def mcp(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        servers: dict[str, dict[str, Any]] = {}
        recent = []
        for r in _decisions(tenant_id):
            d = _decision(r)
            if not (d.source or "").startswith("mcp:"):
                continue
            name = d.source[4:]
            srv = servers.setdefault(
                name,
                {"server": name, "decisions": 0, "verdicts": {}, "tools": {}, "last_seen": None},
            )
            srv["decisions"] += 1
            srv["verdicts"][d.verdict] = srv["verdicts"].get(d.verdict, 0) + 1
            srv["tools"][d.tool] = srv["tools"].get(d.tool, 0) + 1
            srv["last_seen"] = max(filter(None, [srv["last_seen"], d.at]), default=None)
            if len(recent) < 50:
                recent.append(
                    d.model_dump(
                        include={
                            "obs_id",
                            "at",
                            "tool",
                            "verdict",
                            "reason",
                            "session_id",
                            "source",
                        }
                    )
                )
        return {
            "tenant_id": tenant_id,
            "servers": sorted(servers.values(), key=lambda s: s["server"]),
            "recent": recent,
        }

    @router.get("/discovery")
    def discovery(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        rows = get_ledger().query((KIND_DISCOVERY,), tenant_id=tenant_id, limit=1)
        if not rows:
            return {"tenant_id": tenant_id, "inventory": None, "recorded_at": None}
        return {
            "tenant_id": tenant_id,
            "inventory": rows[0]["payload"],
            "recorded_at": rows[0].get("observed_at"),
        }

    @router.get("/evidence/verify")
    def evidence_verify(
        seal: bool = Query(default=False), tenant_id: str = Depends(tenant)
    ) -> dict[str, Any]:
        rep = get_ledger().verify(seal=seal, tenant_id=tenant_id, anchor=False)
        rep.pop("expected_head", None)
        return rep

    @router.get("/evidence/head")
    def evidence_head(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        return {"tenant_id": tenant_id, "head": get_ledger().store.head(tenant_id)}

    @router.get("/evidence/proof/{obs_id}")
    def evidence_proof(obs_id: str, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        lg = get_ledger()
        obs = lg.store.get_observation(obs_id)
        if obs is None or obs.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="record not found")
        proof = lg.store.inclusion_proof(obs_id)
        if proof is None:
            raise HTTPException(status_code=409, detail="record not sealed yet")
        return proof

    @router.get("/status")
    def status(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        rows = _decisions(tenant_id, limit=1000)
        verdicts: dict[str, int] = {}
        for r in rows:
            v = r["payload"].get("verdict")
            verdicts[v] = verdicts.get(v, 0) + 1
        st = get_ledger().store
        ctl = _control_status(tenant_id)
        from panoptis.api.protect_rbac import get_engine

        policy = get_engine().store.policy
        return {
            "tenant_id": tenant_id,
            "recent_decisions": len(rows),
            "verdicts": verdicts,
            "last_decision_at": rows[0].get("observed_at") if rows else None,
            "killed": ctl["killed"],
            "kill_reason": ctl["kill_reason"],
            "suspended_agents": len(ctl["suspended_agents"]),
            "suspended_sessions": len(ctl["suspended_sessions"]),
            "policy": {
                "configured": policy is not None,
                "digest": policy.digest if policy else None,
            },
            "evidence": {"head": st.head(tenant_id), "segments": len(st.segments(tenant_id))},
        }

    return router


def _control_status(tenant_id: str) -> dict[str, Any]:
    from panoptis.api.protect_control import get_control

    try:
        return get_control().status(tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 - status must render even if the control store is down
        return {
            "killed": None,
            "kill_reason": None,
            "suspended_agents": {},
            "suspended_sessions": {},
        }
