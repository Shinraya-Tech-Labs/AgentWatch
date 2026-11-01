"""Normalizer for Claude Code stream-json / transcript observations.

* assistant message        → MODEL_INVOCATION (outputs = text), grouped by message id
* tool_use block           → TOOL_INVOCATION (start), paired with its tool_result by tool_use_id
* next assistant message   → declares DEPENDS_ON the tool_uses whose results it received
                             (the protocol sends results in the user turn that precedes it)
* result                   → LIFECYCLE for the session (cost, duration, outcome)
* system/init              → LIFECYCLE (session configuration)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from panoptis.events.model import (
    DeclaredLink,
    Diagnostic,
    EntityRef,
    EventKind,
    EventStatus,
    TemporalInfo,
)
from panoptis.events.normalize import (
    EventBuilder,
    NormalizeContext,
    Normalizer,
    NormalizeResult,
    ordering_seq,
    source_order,
    temporal,
)
from panoptis.evidence.canonical import sha256_hex
from panoptis.evidence.model import RawObservation

ACTOR = EntityRef("agent", "claude-code")


class ClaudeCodeNormalizer(Normalizer):
    name = "claude_code"
    version = "1"
    source_kinds = frozenset(
        {
            "claude_code.system",
            "claude_code.assistant",
            "claude_code.user",
            "claude_code.result",
            "claude_code.unparseable",
            "claude_code.summary",
        }
    )
    maturity = "EXPERIMENTAL"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        return self._declared_key(obs, "session_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        ordered = source_order(observations)
        tool_use: dict[str, tuple[RawObservation, dict[str, Any], str | None]] = {}
        tool_result: dict[str, tuple[RawObservation, dict[str, Any]]] = {}
        messages: dict[str, list[RawObservation]] = {}
        order: list[str] = []
        pending_results: dict[
            str, list[str]
        ] = {}  # session -> tool_use ids answered since last assistant msg
        depends: dict[str, list[str]] = {}
        session_of: dict[str, str | None] = {}
        for obs in ordered:
            p = obs.payload()
            t = p.get("type")
            sid = obs.declared("session_id")
            if t == "assistant":
                mid = obs.declared("message_id") or obs.declared("entry_uuid") or obs.obs_id
                if mid not in messages:
                    messages[mid] = []
                    order.append(mid)
                    depends[mid] = pending_results.pop(sid or "", [])
                    session_of[mid] = sid
                messages[mid].append(obs)
                for block in (p.get("message") or {}).get("content") or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("id")
                    ):
                        tool_use[str(block["id"])] = (obs, block, mid)
            elif t == "user":
                for block in (p.get("message") or {}).get("content") or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id")
                    ):
                        tid = str(block["tool_use_id"])
                        tool_result[tid] = (obs, block)
                        pending_results.setdefault(sid or "", []).append(tid)
                if not any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in (p.get("message") or {}).get("content") or []
                ):
                    result.events.append(self._user_input(obs, p, ctx))
            elif t in ("system", "result", "summary"):
                result.events.append(self._lifecycle(obs, p, ctx))
            else:
                result.diagnostics.append(
                    Diagnostic(
                        obs.obs_id, "warning", "unparsed_line", f"line type {t!r} not interpreted"
                    )
                )
        for mid in order:
            result.events.append(
                self._message(mid, messages[mid], depends.get(mid, []), session_of.get(mid), ctx)
            )
        for tid in dict.fromkeys([*tool_use, *tool_result]):
            result.events.append(
                self._tool(tid, tool_use.get(tid), tool_result.get(tid), ctx, result)
            )
        return result

    def _run(self, b: EventBuilder, sid: str | None) -> None:
        if sid:
            b.run_key = ("claude_code.session", sid)

    def _message(
        self,
        mid: str,
        obs: list[RawObservation],
        deps: list[str],
        sid: str | None,
        ctx: NormalizeContext,
    ) -> Any:
        b = EventBuilder(self, ctx, obs)
        b.kind = EventKind.MODEL_INVOCATION
        b.actor = ACTOR
        texts: list[str] = []
        usage: dict[str, Any] = {}
        model = None
        for o in obs:
            msg = o.payload().get("message") or {}
            model = model or msg.get("model")
            usage = msg.get("usage") or usage
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "thinking":
                    b.facets.append("thinking_present")
        b.operation = f"message:{model or 'unknown'}"
        if model:
            b.object = EntityRef("model", f"anthropic/{model}")
        else:
            b.extra_missing.append("model")
        if texts:
            b.output("\n".join(texts), role="completion")
        b.resources = {
            k: v
            for k, v in {
                "tokens_in": usage.get("input_tokens"),
                "tokens_out": usage.get("output_tokens"),
                "cache_read_tokens": usage.get("cache_read_input_tokens"),
            }.items()
            if v is not None
        }
        b.source_ids = [("claude_code.message", mid)]
        entry = obs[0].declared("entry_uuid")
        if entry:
            b.source_ids.append(("claude_code.entry", entry))
        parent_entry = obs[0].declared("parent_entry_uuid")
        if parent_entry:
            b.parents.append(DeclaredLink("follows", "claude_code.entry", parent_entry))
        ptu = obs[0].declared("parent_tool_use_id")
        if ptu:
            b.parents.append(DeclaredLink("parent", "claude_code.tool_use", ptu))
            b.facets.append("subagent")
        for tid in deps:
            b.parents.append(DeclaredLink("depends_on", "claude_code.tool_use", tid))
        self._run(b, sid)
        b.status = EventStatus.OK
        b.time = TemporalInfo(
            start=obs[0].observed_at,
            end=obs[-1].observed_at if len(obs) > 1 else None,
            basis=obs[0].clock.source if obs[0].observed_at else "missing",
            ordering_key=f"{obs[0].sensor.instance_id}:{ordering_seq(obs[0].source_seq)}",
        )
        return b.build()

    def _tool(
        self,
        tid: str,
        use: tuple[RawObservation, dict[str, Any], str | None] | None,
        res: tuple[RawObservation, dict[str, Any]] | None,
        ctx: NormalizeContext,
        result: NormalizeResult,
    ) -> Any:
        obs = [x[0] for x in (use, res) if x]
        # several tool_uses can come from one assistant line; a stable per-id index keeps event ids distinct
        b = EventBuilder(self, ctx, obs, index=int(sha256_hex(tid)[:8], 16))
        b.kind = EventKind.TOOL_INVOCATION
        b.actor = ACTOR
        sid = (use or res)[0].declared("session_id")  # type: ignore[index]
        if use:
            block = use[1]
            b.operation = str(block.get("name") or "unknown")
            b.object = EntityRef("tool", b.operation)
            b.input(block.get("input") or {}, role="arguments")
            b.parents.append(DeclaredLink("parent", "claude_code.message", str(use[2])))
        else:
            b.operation = "unknown"
            result.diagnostics.append(
                Diagnostic(
                    res[0].obs_id if res else None,
                    "warning",
                    "result_without_use",
                    f"tool_result {tid} without tool_use",
                )
            )
        if res:
            block = res[1]
            content = block.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                )
            if content is not None:
                b.output(content, role="result")
            b.status = EventStatus.ERROR if block.get("is_error") else EventStatus.OK
        else:
            b.status = EventStatus.UNKNOWN
        b.source_ids = [("claude_code.tool_use", tid)]
        self._run(b, sid)
        start = use[0] if use else None
        end = res[0] if res else None
        b.time = temporal(start, end) if start else temporal(end, None)
        return b.build()

    def _user_input(self, obs: RawObservation, p: dict[str, Any], ctx: NormalizeContext) -> Any:
        b = EventBuilder(self, ctx, [obs])
        b.kind = EventKind.EXTERNAL_INPUT
        b.operation = "user_prompt"
        b.actor = EntityRef("human", "user")
        content = (p.get("message") or {}).get("content")
        if content is not None:
            b.output(content if isinstance(content, str) else content, role="input")
        entry = obs.declared("entry_uuid")
        if entry:
            b.source_ids.append(("claude_code.entry", entry))
        if obs.declared("parent_entry_uuid"):
            b.parents.append(
                DeclaredLink(
                    "follows", "claude_code.entry", obs.declared("parent_entry_uuid") or ""
                )
            )
        self._run(b, obs.declared("session_id"))
        b.status = EventStatus.OK
        b.time = temporal(obs)
        return b.build()

    def _lifecycle(self, obs: RawObservation, p: dict[str, Any], ctx: NormalizeContext) -> Any:
        b = EventBuilder(self, ctx, [obs])
        t = p.get("type")
        b.kind = EventKind.LIFECYCLE
        b.actor = ACTOR
        sid = obs.declared("session_id")
        self._run(b, sid)
        if t == "result":
            b.operation = "run:claude-code-session"
            b.facets.append("run")
            b.status = EventStatus.ERROR if p.get("is_error") else EventStatus.OK
            b.resources = {
                k: v
                for k, v in {
                    "cost_usd": p.get("total_cost_usd") or p.get("cost_usd"),
                    "latency_ms": p.get("duration_ms"),
                    "tokens_in": (p.get("usage") or {}).get("input_tokens"),
                    "tokens_out": (p.get("usage") or {}).get("output_tokens"),
                }.items()
                if v is not None
            }
            if p.get("result") is not None:
                b.output(p["result"], role="final_result")
            b.attributes = {
                "outcome": "error" if p.get("is_error") else "ok",
                "subtype": p.get("subtype"),
                "num_turns": p.get("num_turns"),
            }
            if sid:
                b.source_ids = [("claude_code.result", sid)]
        else:
            b.operation = f"{t}:{p.get('subtype') or ''}".rstrip(":")
            b.status = EventStatus.OK
            b.attributes = {
                k: p.get(k)
                for k in ("model", "tools", "cwd", "permissionMode", "subtype")
                if p.get(k) is not None
            }
        b.time = temporal(obs)
        return b.build()
