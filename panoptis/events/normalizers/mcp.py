"""Normalizer for MCP JSON-RPC messages (paired by server session + JSON-RPC id)."""

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


class MCPNormalizer(Normalizer):
    name = "mcp"
    version = "2"
    source_kinds = frozenset({"mcp.message"})
    maturity = "EXPERIMENTAL"

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        return self._declared_key(obs, "run_id")

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        result = NormalizeResult()
        calls: list[tuple[str, str, dict[str, RawObservation]]] = []
        open_requests: dict[tuple[str, str], dict[str, RawObservation]] = {}
        notes: list[RawObservation] = []
        for obs in source_order(observations):
            p = obs.payload()
            msg = p.get("message") or {}
            jid = obs.declared("jsonrpc_id")
            if jid is None:
                notes.append(obs)
                continue
            session = obs.declared("mcp_session") or ""
            key = (session, jid)
            if "method" in msg:
                parts: dict[str, RawObservation] = {"request": obs}
                open_requests[key] = parts
                calls.append((session, jid, parts))
            else:
                if key in open_requests:
                    open_requests[key]["response"] = obs
                    del open_requests[key]
                else:
                    parts = {"response": obs}
                    calls.append((session, jid, parts))
        for session, jid, parts in calls:
            result.events.append(self._call(session, jid, parts, ctx, result))
        for obs in notes:
            b = EventBuilder(self, ctx, [obs])
            p = obs.payload()
            msg = p.get("message") or {}
            method = str(msg.get("method") or "unknown")
            b.kind = EventKind.MESSAGE
            b.operation = f"notification:{method}"
            b.object = EntityRef("mcp_server", str(p.get("server")))
            b.input(msg.get("params") or msg, role="message")
            b.status = EventStatus.OK
            b.time = temporal(obs)
            if obs.declared("parent_span_id"):
                b.parents.append(
                    DeclaredLink("parent", "native.span", obs.declared("parent_span_id") or "")
                )
            if obs.declared("run_id"):
                b.run_key = ("native.run", obs.declared("run_id") or "")
            result.events.append(b.build())
        return result

    def _call(
        self,
        session: str,
        jid: str,
        parts: dict[str, RawObservation],
        ctx: NormalizeContext,
        result: NormalizeResult,
    ) -> Any:
        req, res = parts.get("request"), parts.get("response")
        anchor_obs = req or res
        assert anchor_obs is not None  # nosec B101 — a group always holds at least one message
        obs = [o for o in (req, res) if o]
        b = EventBuilder(self, ctx, obs)
        rq = (req.payload().get("message") or {}) if req else {}
        rs = (res.payload().get("message") or {}) if res else {}
        server = (req or res).payload().get("server")  # type: ignore[union-attr]
        method = rq.get("method", "unknown")
        params = rq.get("params") or {}
        if method == "tools/call":
            b.kind = EventKind.TOOL_INVOCATION
            tool_name = str(params.get("name") or "unknown_tool")
            b.operation = tool_name
            b.object = EntityRef("tool", f"mcp/{server}/{tool_name}")
            b.input(params.get("arguments") or {}, role="arguments", label=tool_name)
        elif method in ("resources/read", "resources/list", "prompts/get", "prompts/list"):
            b.kind = EventKind.RETRIEVAL
            b.operation = method
            if method == "resources/read" and params.get("uri"):
                uri = str(params["uri"])
                b.object = EntityRef("mcp_resource", f"mcp/{server}/{uri}")
            elif method == "prompts/get" and params.get("name"):
                pname = str(params["name"])
                b.object = EntityRef("mcp_prompt", f"mcp/{server}/{pname}")
            else:
                b.object = EntityRef("mcp_server", str(server))
            b.input(params, role="query")
            b.effects.append(Effect(EffectKind.READ, b.object.canonical))
        else:
            b.kind = EventKind.EXTERNAL_IO
            b.operation = method
            b.object = EntityRef("mcp_server", str(server))
            if params:
                b.input(params, role="params")
        if "result" in rs:
            res_val = rs["result"]
            is_err = bool(res_val.get("isError")) if isinstance(res_val, dict) else False
            if method == "resources/read" and isinstance(res_val, dict) and "contents" in res_val:
                b.output(res_val["contents"], role="resource")
            elif method == "prompts/get" and isinstance(res_val, dict) and "messages" in res_val:
                b.output(res_val["messages"], role="prompt")
            else:
                b.output(res_val, role="result")

            if is_err:
                b.status = EventStatus.ERROR
                err_msg = "Tool execution failed"
                if isinstance(res_val, dict) and isinstance(res_val.get("content"), list):
                    texts = [
                        c.get("text")
                        for c in res_val["content"]
                        if isinstance(c, dict) and c.get("text")
                    ]
                    if texts:
                        err_msg = "; ".join(str(t) for t in texts)
                b.error = {"type": "ToolExecutionError", "message": err_msg}
            else:
                b.status = EventStatus.OK
        elif "error" in rs:
            b.status = EventStatus.ERROR
            err_val = rs["error"]
            if isinstance(err_val, dict):
                b.error = {
                    "type": "JSONRPCError",
                    "code": err_val.get("code"),
                    "message": str(err_val.get("message") or "JSON-RPC error"),
                    "data": err_val.get("data"),
                }
            else:
                b.error = {"type": "JSONRPCError", "message": str(err_val)}
        else:
            b.status = EventStatus.UNKNOWN
            result.diagnostics.append(
                Diagnostic(
                    anchor_obs.obs_id,
                    "warning",
                    "no_response",
                    f"MCP request {jid} without response",
                )
            )  # type: ignore[union-attr]
        anchor = req or res
        assert anchor is not None  # nosec B101 — a group always holds at least one message
        b.source_ids = [("mcp.call", f"{session}/{jid}")]
        if anchor.declared("parent_span_id"):
            b.parents.append(
                DeclaredLink("parent", "native.span", anchor.declared("parent_span_id") or "")
            )
        if anchor.declared("run_id"):
            b.run_key = ("native.run", anchor.declared("run_id") or "")
        b.time = temporal(req, res) if req else temporal(res, None)
        return b.build()
