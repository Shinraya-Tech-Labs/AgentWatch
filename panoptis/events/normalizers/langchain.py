"""Normalizer for LangChain/LangGraph callback observations.

Start/end/error callbacks are paired by the ``run_id`` LangChain declared. Parents come
from ``parent_run_id``. A callback with no parent is a root and defines the run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from panoptis.events.model import (
    DeclaredLink,
    Diagnostic,
    Effect,
    EffectKind,
    EntityRef,
    EventKind,
    EventStatus,
)
from panoptis.events.normalize import (
    EventBuilder,
    NormalizeContext,
    Normalizer,
    NormalizeResult,
    source_order,
    temporal,
)
from panoptis.evidence.model import RawObservation

FAMILIES = {
    "llm": EventKind.MODEL_INVOCATION,
    "chat_model": EventKind.MODEL_INVOCATION,
    "chain": EventKind.OPERATION,
    "tool": EventKind.TOOL_INVOCATION,
    "retriever": EventKind.RETRIEVAL,
}


def _name(serialized: dict[str, Any] | None, fallback: str | None = None) -> str:
    s = serialized or {}
    kwargs = s.get("kwargs") or {}
    for key in ("model", "model_name", "model_id"):
        if kwargs.get(key):
            return str(kwargs[key])
    if s.get("name"):
        return str(s["name"])
    ident = s.get("id")
    if isinstance(ident, list) and ident:
        return str(ident[-1])
    return fallback or "unknown"


class LangChainNormalizer(Normalizer):
    name = "langchain"
    version = "1"
    source_kinds = frozenset(
        {
            f"langchain.{f}_{p}"
            for f in ("llm", "chain", "tool", "retriever")
            for p in ("start", "end", "error")
        }
        | {"langchain.chat_model_start", "langchain.agent_action", "langchain.agent_finish"}
    )
    maturity = "EXPERIMENTAL"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        # declared by the sensor from LangChain's own parent_run_id chain
        return self._declared_key(obs, "root_run_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        groups: dict[str, dict[str, RawObservation]] = {}
        points: list[RawObservation] = []
        for obs in source_order(observations):
            cb = obs.source_kind.removeprefix("langchain.")
            rid = obs.declared("run_id")
            if not rid:
                result.diagnostics.append(
                    Diagnostic(obs.obs_id, "error", "missing_run_id", f"{cb} without run_id")
                )
                continue
            if cb in ("agent_action", "agent_finish"):
                points.append(obs)
                continue
            phase = cb.rsplit("_", 1)[1]
            groups.setdefault(rid, {})[phase] = obs
        for rid, g in groups.items():
            result.events.append(self._pair(rid, g, ctx, result))
        for i, obs in enumerate(points):
            result.events.append(self._point(obs, ctx, i))
        return result

    def _pair(
        self, rid: str, g: dict[str, RawObservation], ctx: NormalizeContext, result: NormalizeResult
    ) -> Any:
        start = g.get("start")
        end = g.get("end") or g.get("error")
        obs = [o for o in (start, end) if o]
        b = EventBuilder(self, ctx, obs)
        anchor = start or end
        assert anchor is not None  # nosec B101 — invariant: group has at least start or end
        family = (start or anchor).source_kind.removeprefix("langchain.").rsplit("_", 1)[0]
        sp: dict[str, Any] = start.payload() if start else {}
        ep: dict[str, Any] = end.payload() if end else {}
        b.kind = FAMILIES.get(family, EventKind.UNKNOWN) if start else EventKind.UNKNOWN
        name = _name(sp.get("serialized"), sp.get("name"))
        b.operation = name
        if b.kind == EventKind.MODEL_INVOCATION:
            b.object = EntityRef("model", name)
            if "prompts" in sp:
                b.input(
                    sp["prompts"] if len(sp["prompts"]) != 1 else sp["prompts"][0], role="prompt"
                )
            if "messages" in sp:
                b.input(sp["messages"], role="prompt")
            gens = ep.get("generations")
            if gens:
                b.output(gens[0] if len(gens) == 1 else gens, role="completion")
            usage = (
                (ep.get("llm_output") or {}).get("token_usage")
                or (ep.get("llm_output") or {}).get("usage")
                or ep.get("token_usage")
                or {}
            )
            b.resources = {
                k: v
                for k, v in {
                    "tokens_in": usage.get("prompt_tokens") or usage.get("input_tokens"),
                    "tokens_out": usage.get("completion_tokens") or usage.get("output_tokens"),
                }.items()
                if v is not None
            }
        elif b.kind == EventKind.TOOL_INVOCATION:
            b.object = EntityRef("tool", name)
            b.input(
                sp.get("inputs") if sp.get("inputs") is not None else sp.get("input_str"),
                role="arguments",
            )
            if "output" in ep:
                b.output(ep["output"], role="result")
            if anchor.declared("tool_call_id"):
                b.source_ids.append(("langchain.tool_call", anchor.declared("tool_call_id") or ""))
        elif b.kind == EventKind.RETRIEVAL:
            b.object = EntityRef("index", name)
            b.input(sp.get("query"), role="query")
            if "documents" in ep:
                b.output(ep["documents"], role="documents")
            b.effects.append(Effect(EffectKind.READ, b.object.canonical))
        elif b.kind == EventKind.OPERATION:
            b.facets.append("chain")
            if sp.get("inputs") is not None:
                b.input(sp["inputs"], role="inputs")
            if ep.get("outputs") is not None:
                b.output(ep["outputs"], role="outputs")
        meta = sp.get("metadata") or {}
        if meta.get("langgraph_node"):
            b.actor = EntityRef("node", str(meta["langgraph_node"]))
            b.attributes["langgraph_step"] = meta.get("langgraph_step")
        b.attributes["tags"] = sp.get("tags")
        b.source_ids.append(("langchain.run", rid))
        parent = anchor.declared("parent_run_id")
        if parent:
            b.parents.append(DeclaredLink("parent", "langchain.run", parent))
        else:
            b.run_key = ("langchain.run", rid)
        if end is None:
            b.status = EventStatus.UNKNOWN
            result.diagnostics.append(
                Diagnostic(
                    anchor.obs_id,
                    "warning",
                    "unpaired_start",
                    f"langchain run {rid} has no end/error callback",
                )
            )
        elif end.source_kind.endswith("_error"):
            b.status = EventStatus.ERROR
            b.error = ep.get("error")
        else:
            b.status = EventStatus.OK
        if start is None:
            result.diagnostics.append(
                Diagnostic(
                    anchor.obs_id,
                    "warning",
                    "unpaired_end",
                    f"langchain run {rid} has no start callback",
                )
            )
        b.time = temporal(start, end) if start else temporal(end, None)
        ev = b.build()
        if not parent:
            from dataclasses import replace

            ev = replace(ev, missing=tuple(m for m in ev.missing if m != "parent"))
        return ev

    def _point(self, obs: RawObservation, ctx: NormalizeContext, index: int) -> Any:
        p: dict[str, Any] = obs.payload()
        b = EventBuilder(self, ctx, [obs], index=0)
        b.kind = EventKind.OPERATION
        cb = obs.source_kind.removeprefix("langchain.")
        b.operation = f"{cb}:{p.get('tool')}" if p.get("tool") else cb
        b.facets.append(cb)
        b.attributes = {"log": p.get("log")}
        if p.get("tool_input") is not None:
            b.output(p["tool_input"], role="tool_input")
        if p.get("return_values") is not None:
            b.output(p["return_values"], role="return_values")
        rid = obs.declared("run_id")
        b.source_ids.append((f"langchain.{cb}", f"{rid}:{obs.obs_id}"))
        if rid:
            b.parents.append(DeclaredLink("parent", "langchain.run", rid))
        b.status = EventStatus.OK
        b.time = temporal(obs)
        b.extra_missing = []
        from dataclasses import replace

        ev = b.build()
        return replace(ev, missing=tuple(m for m in ev.missing if m != "end_time"))
