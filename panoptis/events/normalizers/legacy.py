"""Normalizer for v0.2 ``AgentEvent`` observations (via LegacyTranslator).

Pairs tool.call/tool.result only when the legacy event declared a ``tool_id``; never by
timing. Legacy interpretation events (safety/confidence/...) become UNKNOWN events with
facet ``legacy_interpretation`` so they are visible but never mistaken for behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from panoptis.events.model import (
    Confidence,
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
    status_from,
    temporal,
)
from panoptis.evidence.model import RawObservation
from panoptis.sensors.legacy.translator import INTERPRETATION_TYPES

SIMPLE = {
    "agent.start": (EventKind.LIFECYCLE, "agent_start"),
    "agent.end": (EventKind.LIFECYCLE, "agent_end"),
    "agent.error": (EventKind.FAILURE, "agent_error"),
    "goal.set": (EventKind.EXTERNAL_INPUT, "goal_set"),
    "tool.retry": (EventKind.RECOVERY, "tool_retry"),
    "memory.read": (EventKind.MEMORY_ACCESS, "memory_read"),
    "memory.write": (EventKind.MEMORY_ACCESS, "memory_write"),
    "memory.evict": (EventKind.MEMORY_ACCESS, "memory_evict"),
    "agent.message": (EventKind.MESSAGE, "message"),
    "task.delegate": (EventKind.DELEGATION, "delegate"),
    "task.complete": (EventKind.OPERATION, "task_complete"),
    "task.fail": (EventKind.FAILURE, "task_fail"),
    "checkpoint.create": (EventKind.STATE_MUTATION, "checkpoint_create"),
    "rollback.trigger": (EventKind.STATE_MUTATION, "rollback_trigger"),
    "rollback.complete": (EventKind.STATE_MUTATION, "rollback_complete"),
    "custom": (EventKind.UNKNOWN, "custom"),
    "planner.input": (EventKind.MODEL_INVOCATION, "planner"),
    "planner.output": (EventKind.MODEL_INVOCATION, "planner"),
}


class LegacyNormalizer(Normalizer):
    name = "legacy"
    version = "1"
    source_kinds = frozenset({"legacy.agent_event"})
    maturity = "VALIDATED"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        return self._declared_key(obs, "session_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        ordered = source_order(observations)
        calls: dict[str, RawObservation] = {}
        results: dict[str, RawObservation] = {}
        singles: list[RawObservation] = []
        for obs in ordered:
            p = obs.payload()
            et = p.get("event_type")
            tid = obs.declared("tool_id")
            if et == "tool.call" and tid:
                calls[tid] = obs
            elif et in ("tool.result", "tool.error") and tid:
                results[tid] = obs
            else:
                singles.append(obs)
        for tid in dict.fromkeys([*calls, *results]):
            result.events.append(self._tool(calls.get(tid), results.get(tid), ctx))
        for obs in singles:
            result.events.append(self._single(obs, ctx, result))
        return result

    def _common(self, b: EventBuilder, obs: RawObservation, p: dict[str, Any]) -> None:
        agent = p.get("agent_id")
        if agent:
            b.actor = EntityRef("agent", str(agent))
        if obs.declared("legacy_event_id"):
            b.source_ids.append(("legacy.event", obs.declared("legacy_event_id") or ""))
        if p.get("parent_event_id"):
            b.parents.append(DeclaredLink("parent", "legacy.event", str(p["parent_event_id"])))
        if p.get("session_id"):
            b.run_key = ("legacy.session", str(p["session_id"]))
        b.obs_confidence = Confidence(0.7, "legacy_translation_timestamp_provenance_unknown")
        b.facets.append("legacy")
        if p.get("framework"):
            b.attributes["framework"] = p["framework"]
        tu = p.get("token_usage") or {}
        if tu:
            b.resources = {
                "tokens_in": tu.get("prompt_tokens"),
                "tokens_out": tu.get("completion_tokens"),
                "cost_usd": tu.get("estimated_cost_usd"),
            }
        if p.get("duration_ms") is not None:
            b.resources["latency_ms"] = p["duration_ms"]

    def _tool(
        self, call: RawObservation | None, res: RawObservation | None, ctx: NormalizeContext
    ) -> Any:
        obs = [o for o in (call, res) if o]
        b = EventBuilder(self, ctx, obs)
        cp: dict[str, Any] = call.payload() if call else {}
        rp: dict[str, Any] = res.payload() if res else {}
        self._common(b, (call or res), cp or rp)  # type: ignore[arg-type]
        tc = cp.get("tool_call") or {}
        tr = rp.get("tool_result") or {}
        name = tc.get("tool_name") or tr.get("tool_name") or "unknown"
        b.kind = EventKind.TOOL_INVOCATION
        b.operation = str(name)
        b.object = EntityRef("tool", str(name))
        if tc:
            b.input(tc.get("arguments") or {}, role="arguments")
            if tc.get("raw_command"):
                b.attributes["raw_command"] = tc["raw_command"]
        if tr:
            if tr.get("output") is not None:
                b.output(tr["output"], role="result")
            if tr.get("error") or rp.get("event_type") == "tool.error":
                b.status = EventStatus.ERROR
                b.error = {"message": tr.get("error") or "tool.error"}
            else:
                b.status = status_from(rp.get("status"))
        tid = (call or res).declared("tool_id")  # type: ignore[union-attr]
        b.source_ids.append(("legacy.tool", str(tid)))
        b.time = temporal(call, res) if call else temporal(res, None)
        return b.build()

    def _single(self, obs: RawObservation, ctx: NormalizeContext, result: NormalizeResult) -> Any:
        p: dict[str, Any] = obs.payload()
        et = p.get("event_type", "custom")
        b = EventBuilder(self, ctx, [obs])
        self._common(b, obs, p)
        b.time = temporal(obs)
        b.status = status_from(p.get("status"))
        b.attributes["legacy_event_type"] = et
        if et in INTERPRETATION_TYPES:
            b.kind = EventKind.UNKNOWN
            b.operation = f"legacy_interpretation:{et}"
            b.facets.append("legacy_interpretation")
            b.attributes["legacy_conclusion"] = {
                k: p.get(k) for k in ("safety", "confidence", "metadata") if p.get(k)
            }
            result.diagnostics.append(
                Diagnostic(
                    obs.obs_id,
                    "info",
                    "legacy_interpretation",
                    f"{et} recorded as v0.2 conclusion, not behaviour",
                )
            )
            return b.build()
        if et in ("session.start", "session.end"):
            b.kind = EventKind.LIFECYCLE
            b.operation = f"session_{et.split('.')[1]}"
            b.facets.append("session")
            if p.get("goal"):
                b.input(p["goal"], role="goal")
            return b.build()
        if et in ("tool.call", "tool.result", "tool.error"):
            # unpaired: no tool_id declared, so the call and its result stay separate events
            tc = p.get("tool_call") or p.get("tool_result") or {}
            name = tc.get("tool_name", "unknown")
            b.kind = EventKind.TOOL_INVOCATION
            b.operation = str(name)
            b.object = EntityRef("tool", str(name))
            b.facets.append(f"unpaired_{et.split('.')[1]}")
            if et == "tool.call":
                b.input(tc.get("arguments") or {}, role="arguments")
            else:
                if tc.get("output") is not None:
                    b.output(tc["output"], role="result")
                if et == "tool.error" or tc.get("error"):
                    b.status = EventStatus.ERROR
                    b.error = {"message": tc.get("error") or "tool.error"}
            return b.build()
        kind, op = SIMPLE.get(et, (EventKind.UNKNOWN, et))
        b.kind = kind
        b.operation = op
        if et == "planner.input":
            b.facets.extend(["planning", "unpaired_input"])
            if p.get("prompt_preview"):
                b.input(p["prompt_preview"], role="prompt_preview")
        elif et == "planner.output":
            b.facets.extend(["planning", "unpaired_output"])
            if p.get("planner_output_preview"):
                b.output(p["planner_output_preview"], role="completion_preview")
        mem = p.get("memory") or {}
        if kind == EventKind.MEMORY_ACCESS:
            store = mem.get("memory_type") or "default"
            b.object = EntityRef("memory", str(store))
            access = "write" if et == "memory.write" else "read" if et == "memory.read" else "evict"
            b.attributes.update({"access": access, "key": mem.get("key")})
            b.facets.append(access)
            if mem.get("content") is not None:
                (b.input if access == "write" else b.output)(
                    mem["content"], role="value", label=mem.get("key")
                )
            b.effects.append(
                Effect(
                    EffectKind.WRITE if access != "read" else EffectKind.READ, b.object.canonical
                )
            )
        msg = p.get("agent_message") or {}
        if kind in (EventKind.MESSAGE, EventKind.DELEGATION) and msg:
            b.actor = EntityRef("agent", str(msg.get("sender_agent_id")))
            b.object = EntityRef("agent", str(msg.get("receiver_agent_id")))
            b.input(msg.get("content") or {}, role="message")
            b.output(msg.get("content") or {}, role="message")
        if kind == EventKind.FAILURE:
            b.status = EventStatus.ERROR
        if p.get("goal") and et == "goal.set":
            b.output(p["goal"], role="goal")
        return b.build()
