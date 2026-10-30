"""AgentWatch v3 HTTP API.

Every endpoint reads through :class:`~agentwatch.query.workspace.Workspace`, the read model
shared with the CLI and the frontend. Tenancy comes from the authentication dependency
supplied by the host app; a caller can never choose another tenant.

Re-execution (replay L2/L3, branch execution, executed counterfactuals) runs recorded
commands on the server and is therefore disabled unless ``AGENTWATCH_ALLOW_REEXECUTION=1``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentwatch.contracts.schema import (
    ContractOperator,
    ContractRule,
    EvidenceContract,
    EvidenceVerdict,
)
from agentwatch.contracts.streaming import StreamingContractEvaluator
from agentwatch.events.codec import event_from_dict
from agentwatch.evidence.model import ObservationDraft
from agentwatch.query.workspace import AmbiguousError, NotFoundError, Workspace
from agentwatch.runtime.engine import Engine

_engine: Engine | None = None
_engine_lock = threading.Lock()
_tenant_contracts: dict[str, dict[str, EvidenceContract]] = defaultdict(dict)
_tenant_assessments: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
_contracts_lock = threading.Lock()


def reset_contracts_for_tests() -> None:
    """Reset registered contracts and assessment cache for test isolation."""
    with _contracts_lock:
        _tenant_contracts.clear()
        _tenant_assessments.clear()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            # server default keeps v3 data next to the v0.2 audit log (./data), not in $HOME
            _engine = Engine(
                os.environ.get("AGENTWATCH_STORE") or "sqlite:///data/agentwatch-v3.db"
            )
        return _engine


def set_engine(engine: Engine | None) -> None:
    global _engine
    with _engine_lock:
        _engine = engine


logger = logging.getLogger(__name__)


def _reexecution_allowed() -> bool:
    return os.environ.get("AGENTWATCH_ALLOW_REEXECUTION", "").lower() in ("1", "true", "yes")


class ObservationBatch(BaseModel):
    observations: list[dict[str, Any]] = Field(default_factory=list)


class ReplayRequest(BaseModel):
    run: str
    level: str = "L1"
    live: list[str] = Field(default_factory=list)


class BranchRequest(BaseModel):
    run: str
    at: str
    substitute: Any
    level: str = "L3"
    execute: bool = True


class CounterfactualRequest(BaseModel):
    run: str
    at: str
    alternative: Any
    execute: bool = True


class HypothesisRequest(BaseModel):
    cause: str
    effect: str
    statement: str
    proposer: str = "human"
    relation: str = "CAUSES"
    rationale: str | None = None


class EvidenceRequest(BaseModel):
    kind: str
    direction: str
    summary: str
    refs: list[str] = Field(default_factory=list)
    experiment_id: str | None = None
    reproduction_confidence: float | None = None


class EraseRequest(BaseModel):
    subject: str
    reason: str


class QueryRequest(BaseModel):
    text: str


class ContractRuleRequest(BaseModel):
    operator: str
    target_operation: str
    required_operation: str
    max_window_seconds: float = 600.0
    join_on: list[str] = Field(default_factory=lambda: ["transaction_id"])


class EvidenceContractRequest(BaseModel):
    name: str
    version: str = "1.0"
    rule: ContractRuleRequest
    description: str = ""
    require_authoritative_log_for_refutation: bool = True


class AssessRunRequest(BaseModel):
    contracts: list[str] | None = None
    async_job: bool = False


def build_router(auth: Callable[..., Any], tenant: Callable[..., str]) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])
    if _reexecution_allowed():
        logger.warning(
            "AGENTWATCH_ALLOW_REEXECUTION is on: replay/branch requests re-run a run's recorded "
            "command on this server, and anyone who can ingest observations can supply that "
            "command. Enable only for trusted, single-user deployments (ADR-0014)."
        )

    def ws(tenant_id: str = Depends(tenant)) -> Workspace:
        return Workspace(get_engine(), tenant_id)

    def guard(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except AmbiguousError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (LookupError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    # ── ingestion ──────────────────────────────────────────────────────────
    @router.post("/api/v3/observations", tags=["v3 evidence"])
    def post_observations(
        batch: ObservationBatch, tenant_id: str = Depends(tenant)
    ) -> dict[str, Any]:
        drafts, rejected = [], []
        for i, raw in enumerate(batch.observations):
            try:
                d = ObservationDraft.from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                rejected.append({"index": i, "reason": f"invalid envelope: {exc}"})
                continue
            d.tenant_id = tenant_id  # tenancy comes from authentication, never the payload
            drafts.append(d)
        res = get_engine().ingest(drafts).to_dict()
        res["rejected"] = rejected + res["rejected"]
        return res

    @router.post("/v1/traces", tags=["v3 evidence"])
    async def otlp_traces(request: Request, tenant_id: str = Depends(tenant)) -> Response:
        from agentwatch.sensors.otel import (
            drafts_from_spans,
            spans_from_otlp_json,
            spans_from_otlp_protobuf,
        )

        body = await request.body()
        ctype = request.headers.get("content-type", "")
        try:
            spans = (
                spans_from_otlp_protobuf(body)
                if "protobuf" in ctype
                else spans_from_otlp_json(json.loads(body or b"{}"))
            )
        except RuntimeError as exc:
            raise HTTPException(415, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"invalid OTLP payload: {exc}") from exc
        res = get_engine().ingest(drafts_from_spans(spans, tenant_id=tenant_id))
        return Response(
            content=json.dumps({"partialSuccess": {"rejectedSpans": len(res.rejected)}}),
            media_type="application/json",
        )

    @router.post("/api/v3/legacy/events", tags=["v3 evidence"])
    def legacy_events(
        events: list[dict[str, Any]] = Body(...), tenant_id: str = Depends(tenant)
    ) -> dict[str, Any]:
        results, appended = get_engine().ingest_legacy(events, tenant_id=tenant_id)
        return {"translations": [r.to_dict() for r in results], "append": appended.to_dict()}

    @router.post("/api/v3/process", tags=["v3 evidence"])
    def process(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        return get_engine().process(tenant_id)

    @router.post("/api/v3/evidence/verify", tags=["v3 evidence"])
    def verify(tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        eng = get_engine()
        eng.store.seal_all(tenant_id)
        return eng.store.verify(tenant_id).to_dict()

    @router.post("/api/v3/evidence/erase", tags=["v3 evidence"])
    def erase(req: EraseRequest, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        """Crypto-shred a data subject (irreversible)."""
        return get_engine().erase_subject(tenant_id, req.subject, reason=req.reason, actor="api")

    @router.get("/api/v3/evidence/erasures", tags=["v3 evidence"])
    def erasures(tenant_id: str = Depends(tenant)) -> list[dict[str, Any]]:
        return get_engine().store.erasures(tenant_id)

    @router.get("/api/v3/observations/{obs_id}", tags=["v3 evidence"])
    def observation(obs_id: str, tenant_id: str = Depends(tenant)) -> dict[str, Any]:
        eng = get_engine()
        obs = eng.store.get_observation(obs_id)
        if obs is None or obs.tenant_id != tenant_id:
            raise HTTPException(404, "observation not found")
        return {
            "observation": obs.to_dict(),
            "inclusion_proof": eng.store.inclusion_proof(obs_id),
            "derived_events": [
                {"event_id": e, "interp_id": i} for e, i in eng.store.events_for_observation(obs_id)
            ],
        }

    # ── status ─────────────────────────────────────────────────────────────
    @router.get("/api/v3/status", tags=["v3"])
    def status_(w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.analysis.capabilities import all_capabilities

        return {
            "interp_id": w.interp_id,
            "interpretation": w.store.get_interpretation(w.interp_id),
            "observations": w.store.count_observations(w.tenant_id),
            "capabilities": [c.to_dict() for c in all_capabilities()],
            "reexecution_enabled": _reexecution_allowed(),
            "payload_encryption": w.store.encrypt_payloads,
            # the backend only; the store URL can contain credentials and is not exposed
            "store_backend": w.store.engine.dialect.name,
        }

    @router.get("/api/v3/live", tags=["v3"])
    def live(limit: int = Query(50, le=500), w: Workspace = Depends(ws)) -> dict[str, Any]:
        runs = w.runs(limit=20)
        active = [r for r in runs if r.get("status") == "UNKNOWN" or not r.get("ended_at")]
        events = sorted(
            w.store.events(w.interp_id), key=lambda e: e["time"]["start"] or "", reverse=True
        )[:limit]
        diags = w.diagnostics()
        actors: dict[str, int] = {}
        for e in events:
            if e.get("actor"):
                actors[e["actor"]] = actors.get(e["actor"], 0) + 1
        return {
            "interp_id": w.interp_id,
            "active_runs": active,
            "recent_runs": runs,
            "recent_events": events,
            "actors": actors,
            "unresolved": [d for d in diags if d["level"] != "info"][:100],
            "diagnostic_count": len(diags),
        }

    @router.get("/api/v3/capabilities", tags=["v3"])
    def capabilities() -> list[dict[str, Any]]:
        from agentwatch.analysis.capabilities import all_capabilities

        return [c.to_dict() for c in all_capabilities()]

    # ── runs / events / graph ──────────────────────────────────────────────
    @router.get("/api/v3/runs", tags=["v3 runs"])
    def runs(limit: int = Query(100, le=1000), w: Workspace = Depends(ws)) -> dict[str, Any]:
        return {"interp_id": w.interp_id, "runs": w.runs(limit=limit)}

    @router.get("/api/v3/runs/{ref}", tags=["v3 runs"])
    def run(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.compare.runs import summarize, tree

        def go() -> dict[str, Any]:
            s = summarize(w, ref)
            s["tree"] = [
                {"depth": d, "event_id": e["event_id"]}
                for d, e in tree(w, s["run"]["run_id"], s["events"])
            ]
            s["motif_instances"] = w.derived("motif_instance", s["run"]["run_id"])
            s["profile"] = next(iter(w.derived("behaviour_profile", s["run"]["run_id"])), None)
            s["information_evidence"] = next(
                iter(w.derived("information_evidence", s["run"]["run_id"])), None
            )
            return s

        return guard(go)

    @router.get("/api/v3/runs/{ref}/events", tags=["v3 runs"])
    def run_events(ref: str, kind: str | None = None, w: Workspace = Depends(ws)) -> dict[str, Any]:
        return guard(
            lambda: {
                "run_id": w.resolve_run(ref)["run_id"],
                "events": w.events(w.resolve_run(ref)["run_id"], kind=kind),
            }
        )

    @router.get("/api/v3/runs/{ref}/graph", tags=["v3 graph"])
    def run_graph(
        ref: str,
        view: str = Query("all", pattern="^(all|execution|information|causal)$"),
        w: Workspace = Depends(ws),
    ) -> dict[str, Any]:
        def go() -> dict[str, Any]:
            rid = w.resolve_run(ref)["run_id"]
            if view == "causal":
                from agentwatch.causality.hypotheses import causal_relations

                rels = causal_relations(w)
            else:
                rels = w.relations(rid)
                if view != "all":
                    rels = [r for r in rels if r["view"] == view.upper()]
            nodes = sorted({n for r in rels for n in r["tail"] + r["head"]})
            return {
                "run_id": rid,
                "view": view,
                "nodes": [w.describe_node(n) for n in nodes],
                "relations": rels,
                "stats": w.graph(rid).stats(),
            }

        return guard(go)

    @router.get("/api/v3/runs/{ref}/provenance-evidence", tags=["v3 provenance"])
    def run_provenance_evidence(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        """How the run's information flow is supported, and every value whose source is
        AMBIGUOUS (candidates, evidence, alternatives, certain sources)."""

        def go() -> dict[str, Any]:
            rid = w.resolve_run(ref)["run_id"]
            return {
                "run_id": rid,
                "summary": next(iter(w.derived("information_evidence", rid)), None),
                "ambiguous": w.derived("ambiguous_provenance", rid),
            }

        return guard(go)

    @router.get("/api/v3/instances/{node:path}", tags=["v3 provenance"])
    def instance(node: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        """One information instance: its content identity, producer/consumer, and every
        relation touching it (with evidence type, strength, resolution)."""

        def go() -> dict[str, Any]:
            n = node if node.startswith("inst:") else f"inst:{node}"
            desc = w.describe_node(n)
            eid = desc.get("producer_event") or desc.get("consumer_event")
            g = w.graph(w.event(eid).get("run_id") if eid else None)
            return {
                "instance": desc,
                "incoming": [
                    {**g.relations[r], "other": w.describe_node(o)} for r, o in g.inc.get(n, [])
                ],
                "outgoing": [
                    {**g.relations[r], "other": w.describe_node(o)} for r, o in g.out.get(n, [])
                ],
            }

        return guard(go)

    @router.get("/api/v3/runs/{ref}/motifs", tags=["v3 behaviour"])
    def run_motifs(ref: str, w: Workspace = Depends(ws)) -> list[dict[str, Any]]:
        return guard(lambda: w.derived("motif_instance", w.resolve_run(ref)["run_id"]))

    @router.get("/api/v3/runs/{ref}/branches", tags=["v3 lab"])
    def run_branches(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.lab.branch import branch_history

        return guard(lambda: branch_history(w, ref))

    @router.get("/api/v3/events/{ref}", tags=["v3 runs"])
    def event(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        def go() -> dict[str, Any]:
            e = w.event(ref)
            g = w.graph(e.get("run_id"))
            node = f"event:{e['event_id']}"
            return {
                "event": e,
                "evidence": w.event_evidence(e["event_id"]),
                "incoming": [
                    {**g.relations[r], "other": w.describe_node(o)} for r, o in g.inc.get(node, [])
                ],
                "outgoing": [
                    {**g.relations[r], "other": w.describe_node(o)} for r, o in g.out.get(node, [])
                ],
                "motifs": [
                    m
                    for m in w.derived("motif_instance", e.get("run_id"))
                    if e["event_id"] in m["events"]
                ]
                if e.get("run_id")
                else [],
            }

        return guard(go)

    @router.get("/api/v3/entities", tags=["v3 runs"])
    def entities(w: Workspace = Depends(ws)) -> list[dict[str, Any]]:
        return w.entities()

    @router.get("/api/v3/artifacts/{ref}", tags=["v3 runs"])
    def artifact(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        return guard(lambda: w.artifact(ref))

    @router.get("/api/v3/diagnostics", tags=["v3 runs"])
    def diagnostics(w: Workspace = Depends(ws)) -> list[dict[str, Any]]:
        return w.diagnostics()

    # ── provenance / compare / behaviour ───────────────────────────────────
    @router.get("/api/v3/provenance/{node:path}", tags=["v3 provenance"])
    def provenance(node: str, run: str | None = None, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.provenance.lineage import lineage, render

        def go() -> dict[str, Any]:
            res = lineage(w, node, run_id=w.resolve_run(run)["run_id"] if run else None)
            return {**res, "text": render(res)}

        return guard(go)

    @router.get("/api/v3/dependents/{node:path}", tags=["v3 provenance"])
    def dependents(node: str, run: str | None = None, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.provenance.lineage import dependents as deps

        return guard(lambda: deps(w, node, run_id=w.resolve_run(run)["run_id"] if run else None))

    @router.get("/api/v3/compare", tags=["v3 compare"])
    def compare(a: str, b: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.compare.runs import compare as cmp

        return guard(lambda: cmp(w, a, b))

    @router.get("/api/v3/motifs", tags=["v3 behaviour"])
    def motifs(w: Workspace = Depends(ws)) -> list[dict[str, Any]]:
        from agentwatch.behaviour.motifs import motif_stats

        return motif_stats(w.derived("motif_instance"), w.runs())

    @router.get("/api/v3/genome", tags=["v3 behaviour"])
    def genome(scope: str = "all", w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.behaviour.profile import genome as build
        from agentwatch.query.engine import _scope_profiles

        return guard(lambda: build(_scope_profiles(w, scope), scope))

    @router.get("/api/v3/drift", tags=["v3 behaviour"])
    def drift(baseline: str, candidate: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.behaviour.drift import drift as run_drift
        from agentwatch.query.engine import _scope_profiles

        return guard(lambda: run_drift(_scope_profiles(w, baseline), _scope_profiles(w, candidate)))

    @router.get("/api/v3/states", tags=["v3 behaviour"])
    def states(
        runs: str | None = None, window: int = 4, w: Workspace = Depends(ws)
    ) -> dict[str, Any]:
        from agentwatch.state.latent import infer_states

        return guard(lambda: infer_states(w, runs.split(",") if runs else None, window=window))

    @router.get("/api/v3/forecast/{ref}", tags=["v3 behaviour"])
    def forecast(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.forecasting.trajectory import evaluate
        from agentwatch.forecasting.trajectory import forecast as fc

        return guard(lambda: evaluate(w) if ref == "evaluate" else fc(w, ref))

    # ── causality ──────────────────────────────────────────────────────────
    @router.get("/api/v3/causes/{ref}", tags=["v3 causality"])
    def causes(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.causality.cones import causes as run_causes

        return guard(lambda: run_causes(w, ref))

    @router.get("/api/v3/effects/{ref}", tags=["v3 causality"])
    def effects(ref: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.causality.cones import effects as run_effects

        return guard(lambda: run_effects(w, ref))

    @router.get("/api/v3/hypotheses", tags=["v3 causality"])
    def hypotheses(about: str | None = None, w: Workspace = Depends(ws)) -> list[dict[str, Any]]:
        from agentwatch.causality.hypotheses import list_hypotheses

        return list_hypotheses(w, about)

    @router.post("/api/v3/hypotheses", tags=["v3 causality"])
    def propose(req: HypothesisRequest, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.causality.hypotheses import propose as do

        return guard(
            lambda: do(
                w,
                cause=req.cause,
                effect=req.effect,
                statement=req.statement,
                proposer=req.proposer,
                relation=req.relation,
                rationale=req.rationale,
            )
        )

    @router.post("/api/v3/hypotheses/{hid}/evidence", tags=["v3 causality"])
    def add_evidence(hid: str, req: EvidenceRequest, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.causality.hypotheses import add_evidence as do
        from agentwatch.causality.hypotheses import get

        def go() -> dict[str, Any]:
            get(w, hid)
            do(
                w,
                hid,
                kind=req.kind,
                direction=req.direction,
                summary=req.summary,
                refs=req.refs,
                experiment_id=req.experiment_id,
                reproduction_confidence=req.reproduction_confidence,
            )
            return get(w, hid)

        return guard(go)

    # ── lab ────────────────────────────────────────────────────────────────
    def _require_reexecution(level: str) -> None:
        if level in ("L2", "L3") and not _reexecution_allowed():
            raise HTTPException(
                403,
                "re-execution is disabled on this server (set AGENTWATCH_ALLOW_REEXECUTION=1); L0/L1 replay is available",
            )

    @router.post("/api/v3/replay", tags=["v3 lab"])
    def replay(req: ReplayRequest, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.lab.replay import ReplayUnavailableError
        from agentwatch.lab.replay import replay as do

        level = req.level.upper()
        _require_reexecution(level)
        try:
            return guard(lambda: do(w, req.run, level, live=req.live))
        except ReplayUnavailableError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/v3/branches", tags=["v3 lab"])
    def branch(req: BranchRequest, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.lab.branch import create_branch, execute_branch

        if req.execute:
            _require_reexecution(req.level.upper())

        def go() -> dict[str, Any]:
            br = create_branch(w, req.run, req.at, req.substitute, level=req.level.upper())
            return {
                "branch": br,
                "result": execute_branch(w, br["branch_id"]) if req.execute else None,
            }

        return guard(go)

    @router.get("/api/v3/experiments/{record_id}", tags=["v3 lab"])
    def experiment(record_id: str, w: Workspace = Depends(ws)) -> dict[str, Any]:
        """A recorded lab experiment (branch, replay, counterfactual, intervention)."""
        doc = w.store.experiment(record_id)
        if doc is None or doc.get("tenant_id", w.tenant_id) != w.tenant_id:
            raise HTTPException(404, f"no experiment {record_id!r}")
        return doc

    @router.get("/api/v3/experiments", tags=["v3 lab"])
    def experiments(
        record_type: str = Query("branch"), subject: str | None = None, w: Workspace = Depends(ws)
    ) -> list[dict[str, Any]]:
        """Recorded lab experiments of one type (e.g. branch), optionally for one run."""
        return w.store.experiments(w.tenant_id, record_type, subject)

    @router.post("/api/v3/counterfactual", tags=["v3 lab"])
    def counterfactual(req: CounterfactualRequest, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.lab.branch import counterfactual as cf

        execute = req.execute and _reexecution_allowed()
        res = guard(lambda: cf(w, req.run, req.at, req.alternative, execute=execute))
        if req.execute and not execute:
            res["reexecution_unavailable"] = "disabled on this server; estimate from history only"
        return res

    # ── query ──────────────────────────────────────────────────────────────
    @router.post("/api/v3/query", tags=["v3 query"])
    def query(req: QueryRequest, w: Workspace = Depends(ws)) -> dict[str, Any]:
        from agentwatch.query.engine import QueryError, ask, execute, explain

        try:
            res = execute(w, req.text)
            return {
                "answered": True,
                "structured_query": req.text,
                "compiled_by": "structured",
                "answer": explain(res),
                "evidence": res["evidence"],
                "result": res["result"],
            }
        except QueryError:
            return ask(w, req.text)
        except (NotFoundError, AmbiguousError) as exc:
            return {
                "answered": False,
                "structured_query": req.text,
                "message": str(exc),
                "evidence": [],
            }

    # ── contracts & assessments ───────────────────────────────────────────
    @router.post("/api/v3/contracts", tags=["v3 contracts"])
    def post_contract(
        req: EvidenceContractRequest,
        tenant_id: str = Depends(tenant),
    ) -> dict[str, Any]:
        """Register and validate a declarative temporal evidence contract."""
        try:
            op = ContractOperator(req.rule.operator)
        except ValueError as exc:
            valid_ops = [o.value for o in ContractOperator]
            raise HTTPException(
                400, f"Unsupported contract operator '{req.rule.operator}'. Supported: {valid_ops}"
            ) from exc

        rule = ContractRule(
            operator=op,
            target_operation=req.rule.target_operation,
            required_operation=req.rule.required_operation,
            max_window_seconds=req.rule.max_window_seconds,
            join_on=tuple(req.rule.join_on),
        )
        contract = EvidenceContract(
            name=req.name,
            version=req.version,
            rule=rule,
            description=req.description,
            require_authoritative_log_for_refutation=req.require_authoritative_log_for_refutation,
        )

        with _contracts_lock:
            _tenant_contracts[tenant_id][contract.name] = contract

        return {
            "contract_id": contract.name,
            "name": contract.name,
            "version": contract.version,
            "status": "registered",
            "rule": {
                "operator": contract.rule.operator.value,
                "target_operation": contract.rule.target_operation,
                "required_operation": contract.rule.required_operation,
                "max_window_seconds": contract.rule.max_window_seconds,
                "join_on": list(contract.rule.join_on),
            },
        }

    @router.get("/api/v3/contracts", tags=["v3 contracts"])
    def get_contracts(
        tenant_id: str = Depends(tenant),
    ) -> dict[str, Any]:
        """List registered declarative contracts for the authenticated tenant."""
        with _contracts_lock:
            contracts = [
                {
                    "contract_id": c.name,
                    "name": c.name,
                    "version": c.version,
                    "description": c.description,
                    "rule": {
                        "operator": c.rule.operator.value,
                        "target_operation": c.rule.target_operation,
                        "required_operation": c.rule.required_operation,
                        "max_window_seconds": c.rule.max_window_seconds,
                        "join_on": list(c.rule.join_on),
                    },
                }
                for c in _tenant_contracts[tenant_id].values()
            ]
        return {"contracts": contracts}

    @router.post("/api/v3/runs/{run_id}/assess", tags=["v3 contracts"])
    def assess_run(
        run_id: str,
        req: AssessRunRequest = Body(default_factory=AssessRunRequest),
        tenant_id: str = Depends(tenant),
        w: Workspace = Depends(ws),
    ) -> dict[str, Any]:
        """Trigger asynchronous contract assessment job for an execution run."""
        run = guard(lambda: w.resolve_run(run_id))

        with _contracts_lock:
            tenant_contracts = dict(_tenant_contracts[tenant_id])

        if req.contracts:
            missing = [c for c in req.contracts if c not in tenant_contracts]
            if missing:
                raise HTTPException(404, f"Contracts not found for tenant: {missing}")
            contracts_to_evaluate = [tenant_contracts[c] for c in req.contracts]
        else:
            contracts_to_evaluate = list(tenant_contracts.values())

        if not contracts_to_evaluate:
            raise HTTPException(
                400,
                "No contracts registered for tenant. Register contracts at POST /api/v3/contracts first.",
            )

        raw_events = w.events(run_id=run["run_id"])
        events = [event_from_dict(e) for e in raw_events]

        evaluator = StreamingContractEvaluator(contracts=contracts_to_evaluate)
        for evt in events:
            evaluator.ingest(evt)

        assessments = evaluator.assess()

        # Overall verdict synthesis: REFUTED > INCONCLUSIVE > CONFLICTING > SUPPORTED
        verdicts = [a.verdict for a in assessments]
        if EvidenceVerdict.REFUTED in verdicts:
            overall = EvidenceVerdict.REFUTED
        elif EvidenceVerdict.INCONCLUSIVE in verdicts:
            overall = EvidenceVerdict.INCONCLUSIVE
        elif EvidenceVerdict.CONFLICTING_EVIDENCE in verdicts:
            overall = EvidenceVerdict.CONFLICTING_EVIDENCE
        else:
            overall = EvidenceVerdict.SUPPORTED

        witness_ids: list[str] = []
        for a in assessments:
            witness_ids.extend(a.support_obs_ids)
            witness_ids.extend(a.refute_obs_ids)
        witness_ids = list(dict.fromkeys(witness_ids))

        asm_id = f"asm_{uuid.uuid4().hex[:16]}"
        record = {
            "assessment_id": asm_id,
            "run_id": run["run_id"],
            "tenant_id": tenant_id,
            "status": "completed",
            "verdict": overall.value,
            "results": [a.to_dict() for a in assessments],
            "witness_ids": witness_ids,
            "created_at": datetime.now(UTC).isoformat(),
        }

        with _contracts_lock:
            _tenant_assessments[tenant_id][asm_id] = record

        return record

    @router.get("/api/v3/assessments/{assessment_id}", tags=["v3 contracts"])
    def get_assessment(
        assessment_id: str,
        tenant_id: str = Depends(tenant),
    ) -> dict[str, Any]:
        """Retrieve verdict, minimal witness IDs, and missing facts for an assessment."""
        with _contracts_lock:
            record = _tenant_assessments[tenant_id].get(assessment_id)
        if not record:
            raise HTTPException(404, f"Assessment '{assessment_id}' not found for tenant")
        return record

    @router.get("/api/v3/assessments/{assessment_id}/witness", tags=["v3 contracts"])
    def get_assessment_witness(
        assessment_id: str,
        tenant_id: str = Depends(tenant),
        w: Workspace = Depends(ws),
    ) -> Response:
        """Stream minimal evidence witness payload."""
        with _contracts_lock:
            record = _tenant_assessments[tenant_id].get(assessment_id)
        if not record:
            raise HTTPException(404, f"Assessment '{assessment_id}' not found for tenant")

        witness_ids = record.get("witness_ids", [])
        observations = []
        eng = get_engine()
        for wid in witness_ids:
            obs = eng.store.get_observation(wid)
            if obs:
                observations.append(obs.to_dict())

        def iter_ndjson():
            for obs in observations:
                yield json.dumps(obs) + "\n"

        return StreamingResponse(
            iter_ndjson(),
            media_type="application/x-ndjson",
            headers={"X-Witness-Count": str(len(observations))},
        )

    return router
