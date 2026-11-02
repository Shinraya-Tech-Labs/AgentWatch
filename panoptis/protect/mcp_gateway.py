"""Standalone MCP gateway: an enforcing proxy between an MCP client and an MCP server (TO-023).

The client connects to the gateway as if it were the server; the gateway connects to the
real ("upstream") server. Every JSON-RPC message is passed through unchanged (ids,
errors, notifications, ``initialize``, capabilities and tool schemas are preserved),
except ``tools/call``:

1. the call is validated and turned into a :class:`ToolAction` (tool name, arguments,
   the gateway's agent/session/tenant);
2. it goes through the one enforcement boundary (:func:`aenforce_call`): identity,
   kill switch, threat analysis, RBAC (with its rate limits), loop detection, intent
   grounding and, when escalated, human approval;
3. only an allowed (approved, possibly mutated) call is forwarded upstream, with the
   arguments the pipeline admitted; a refused call is answered by the gateway with an
   MCP tool error (``isError: true``) and **never reaches the server**;
4. the server's result is inspected and sanitized (prompt-injection quarantine, secret
   redaction, size limits) before the client sees it; the decision and the execution are
   recorded in the evidence ledger (secrets are redacted before storage).

Transports: stdio (:func:`run_stdio`, the gateway spawns the server) and Streamable HTTP
(:func:`build_http_app`, the gateway forwards to a server URL). See docs/MCP_GATEWAY.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import itertools
import json
import logging
import os
import sys
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from panoptis.protect.enforce import aenforce_call
from panoptis.protect.model import ActionBlocked, ToolAction

logger = logging.getLogger(__name__)

JSONRPC = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UPSTREAM_UNAVAILABLE = -32000  # server error range: the upstream server is gone
UPSTREAM_TIMEOUT = -32001
GATEWAY_BUSY = -32002

DEFAULT_MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class ProtocolError(Exception):
    def __init__(self, code: int, message: str, msg_id: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.msg_id = msg_id


class UpstreamError(Exception):
    """The upstream server answered a forwarded call with a JSON-RPC error."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message", "upstream error")))
        self.error = error


class UpstreamClosedError(Exception):
    pass


def error_response(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC, "id": msg_id, "error": err}


def parse_message(
    raw: str | bytes | dict[str, Any], max_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
) -> dict[str, Any]:
    """One JSON-RPC message, validated; :class:`ProtocolError` otherwise."""
    if isinstance(raw, dict):
        msg = raw
    else:
        if len(raw) > max_bytes:
            raise ProtocolError(INVALID_REQUEST, f"message larger than {max_bytes} bytes")
        try:
            msg = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError(PARSE_ERROR, f"parse error: {exc}") from exc
    if isinstance(msg, list):
        raise ProtocolError(INVALID_REQUEST, "JSON-RPC batches are not supported")
    if not isinstance(msg, dict):
        raise ProtocolError(INVALID_REQUEST, "a JSON-RPC message must be an object")
    msg_id = msg.get("id")
    if msg.get("jsonrpc") != JSONRPC:
        raise ProtocolError(INVALID_REQUEST, "jsonrpc must be '2.0'", msg_id)
    if msg_id is not None and (isinstance(msg_id, bool) or not isinstance(msg_id, (str, int))):
        raise ProtocolError(INVALID_REQUEST, "id must be a string or an integer")
    has_method = "method" in msg
    if has_method and not isinstance(msg["method"], str):
        raise ProtocolError(INVALID_REQUEST, "method must be a string", msg_id)
    if not has_method and "result" not in msg and "error" not in msg:
        raise ProtocolError(
            INVALID_REQUEST, "neither a request, a notification nor a response", msg_id
        )
    if "params" in msg and not isinstance(msg["params"], (dict, list)):
        raise ProtocolError(INVALID_REQUEST, "params must be an object or an array", msg_id)
    return msg


def _key(msg_id: Any) -> str:
    return json.dumps(msg_id)  # 1 and "1" are different JSON-RPC ids


def blocked_result(decision: Any) -> dict[str, Any]:
    """The MCP tool result the client gets for a refused call (the server never saw it)."""
    verdict = decision.verdict.value
    text = f"AgentWatch {'blocked' if verdict == 'block' else 'did not run'} this tool call: {decision.reason}"
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
        "_meta": {
            "agentwatch": {
                "verdict": verdict,
                "reason": decision.reason,
                "call_id": decision.action.call_id,
                "findings": [f.code for f in decision.findings if f.recommends.value != "allow"],
            }
        },
    }


@dataclass
class GatewayIdentity:
    agent_id: str = "mcp-client"
    session_id: str | None = None
    tenant_id: str = "default"
    server: str = "upstream"
    objective: str | None = None
    tool_prefix: str = ""  # e.g. "github." so policies can name tools per server


class ToolCallGuard:
    """Turns a ``tools/call`` into a protected call; shared by every transport."""

    def __init__(
        self,
        pipeline: Any,
        identity: GatewayIdentity,
        *,
        call_timeout_s: float = 300.0,
        max_inflight: int = 64,
    ) -> None:
        self.pipeline = pipeline
        self.identity = identity
        self.session_id = identity.session_id or f"mcp-{uuid.uuid4().hex[:12]}"
        self.call_timeout_s = call_timeout_s
        self.max_inflight = max_inflight
        self.inflight = 0
        self._seq = itertools.count(1)
        self.stats = {"calls": 0, "forwarded": 0, "refused": 0, "upstream_errors": 0, "timeouts": 0}

    def action(self, msg: dict[str, Any], session_id: str | None = None) -> ToolAction:
        params = msg.get("params")
        if not isinstance(params, dict):
            raise ProtocolError(
                INVALID_PARAMS, "tools/call params must be an object", msg.get("id")
            )
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProtocolError(INVALID_PARAMS, "tools/call needs a tool name", msg.get("id"))
        args = params.get("arguments", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ProtocolError(
                INVALID_PARAMS, "tools/call arguments must be an object", msg.get("id")
            )
        sid = session_id or self.session_id
        return ToolAction(
            agent_id=self.identity.agent_id,
            session_id=sid,
            tenant_id=self.identity.tenant_id,
            tool=f"{self.identity.tool_prefix}{name}",
            arguments=dict(args),
            objective=self.identity.objective,
            source=f"mcp:{self.identity.server}",
            call_id=f"{sid}-{next(self._seq)}-{uuid.uuid4().hex[:6]}",
            trace_id=sid,
        )

    async def call(
        self,
        msg: dict[str, Any],
        forward: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """The JSON-RPC response for a client ``tools/call``. ``forward`` sends a request upstream and returns its response."""
        msg_id = msg.get("id")
        try:
            action = self.action(msg, session_id)
        except ProtocolError as exc:
            return error_response(msg_id, exc.code, str(exc))
        if self.inflight >= self.max_inflight:
            return error_response(
                msg_id, GATEWAY_BUSY, f"too many tool calls in flight (limit {self.max_inflight})"
            )
        self.stats["calls"] += 1
        self.inflight += 1

        async def invoke(arguments: dict[str, Any]) -> Any:
            params = {**msg["params"], "arguments": arguments}
            if self.identity.tool_prefix:
                params["name"] = msg["params"]["name"]  # upstream knows the unprefixed name
            self.stats["forwarded"] += 1
            resp = await asyncio.wait_for(forward({**msg, "params": params}), self.call_timeout_s)
            if "error" in resp:
                raise UpstreamError(
                    resp["error"]
                    if isinstance(resp["error"], dict)
                    else {"message": str(resp["error"])}
                )
            result = resp.get("result")
            if not isinstance(result, dict):
                raise UpstreamError(
                    {
                        "code": INTERNAL_ERROR,
                        "message": "upstream tools/call result is not an object",
                    }
                )
            return result

        try:
            result = await aenforce_call(self.pipeline, action, invoke)
            return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}
        except ActionBlocked as blocked:
            self.stats["refused"] += 1
            return {"jsonrpc": JSONRPC, "id": msg_id, "result": blocked_result(blocked.decision)}
        except UpstreamError as exc:
            self.stats["upstream_errors"] += 1
            return {"jsonrpc": JSONRPC, "id": msg_id, "error": exc.error}
        except TimeoutError:
            self.stats["timeouts"] += 1
            return error_response(
                msg_id, UPSTREAM_TIMEOUT, f"upstream did not answer within {self.call_timeout_s}s"
            )
        except UpstreamClosedError as exc:
            self.stats["upstream_errors"] += 1
            return error_response(
                msg_id, UPSTREAM_UNAVAILABLE, f"upstream server unavailable: {exc}"
            )
        finally:
            self.inflight -= 1


class MCPGateway:
    """Message-level proxy for one client ↔ server connection (stdio, or any duplex pipe).

    ``send_client`` and ``send_upstream`` deliver one message; the transport calls
    :meth:`from_client`, :meth:`from_upstream` and :meth:`upstream_closed`.
    """

    def __init__(
        self,
        pipeline: Any,
        *,
        send_client: Callable[[dict[str, Any]], Awaitable[None]],
        send_upstream: Callable[[dict[str, Any]], Awaitable[None]],
        identity: GatewayIdentity | None = None,
        call_timeout_s: float = 300.0,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_inflight: int = 64,
    ) -> None:
        self.identity = identity or GatewayIdentity()
        self.guard = ToolCallGuard(
            pipeline, self.identity, call_timeout_s=call_timeout_s, max_inflight=max_inflight
        )
        self.pipeline = pipeline
        self.send_client = send_client
        self.send_upstream = send_upstream
        self.max_message_bytes = max_message_bytes
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}  # forwarded tools/call ids
        self._calls: dict[str, asyncio.Task[None]] = {}  # client tools/call ids being handled
        self._initialize_ids: set[str] = set()
        self.closed: str | None = None
        self.stats = {
            "client_messages": 0,
            "upstream_messages": 0,
            "malformed_client": 0,
            "malformed_upstream": 0,
        }

    @property
    def session_id(self) -> str:
        return self.guard.session_id

    # ── client → gateway ───────────────────────────────────────────────────────────
    async def from_client(self, raw: str | bytes | dict[str, Any]) -> None:
        self.stats["client_messages"] += 1
        try:
            msg = parse_message(raw, self.max_message_bytes)
        except ProtocolError as exc:
            self.stats["malformed_client"] += 1
            await self.send_client(error_response(exc.msg_id, exc.code, str(exc)))
            return
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "tools/call" and msg_id is not None:
            k = _key(msg_id)
            if k in self._calls:
                await self.send_client(
                    error_response(
                        msg_id, INVALID_REQUEST, "a request with this id is already in flight"
                    )
                )
                return
            task = asyncio.ensure_future(self._handle_call(msg))
            self._calls[k] = task
            task.add_done_callback(functools.partial(self._forget_call, k))
            return
        if method == "notifications/cancelled":
            rid = (
                (msg.get("params") or {}).get("requestId")
                if isinstance(msg.get("params"), dict)
                else None
            )
            pending = self._calls.get(_key(rid)) if rid is not None else None
            if pending is not None:
                pending.cancel()  # still deciding (or waiting for approval): it never runs
        if method == "initialize" and msg_id is not None:
            self._initialize_ids.add(_key(msg_id))
            self._record_session(msg.get("params") or {}, None)
        if self.closed:
            if msg_id is not None and method is not None:
                await self.send_client(
                    error_response(
                        msg_id, UPSTREAM_UNAVAILABLE, f"upstream server unavailable: {self.closed}"
                    )
                )
            return
        await self.send_upstream(msg)

    def _forget_call(self, k: str, _task: Any) -> None:
        self._calls.pop(k, None)

    async def _handle_call(self, msg: dict[str, Any]) -> None:
        async def forward(fwd: dict[str, Any]) -> dict[str, Any]:
            if self.closed:
                raise UpstreamClosedError(self.closed)
            k = _key(fwd["id"])
            fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[k] = fut
            try:
                await self.send_upstream(fwd)
                return await fut
            except TimeoutError:
                # tell the server to stop; the call may already have had effects upstream
                with contextlib.suppress(Exception):
                    await self.send_upstream(
                        {
                            "jsonrpc": JSONRPC,
                            "method": "notifications/cancelled",
                            "params": {
                                "requestId": fwd["id"],
                                "reason": "AgentWatch gateway timeout",
                            },
                        }
                    )
                raise
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await self.send_upstream(
                        {
                            "jsonrpc": JSONRPC,
                            "method": "notifications/cancelled",
                            "params": {
                                "requestId": fwd["id"],
                                "reason": "cancelled by the AgentWatch gateway",
                            },
                        }
                    )
                raise
            finally:
                self._pending.pop(k, None)

        try:
            response = await self.guard.call(msg, forward)
        except asyncio.CancelledError:
            return  # the client cancelled: per MCP, no response is sent
        await self.send_client(response)

    # ── upstream → gateway ─────────────────────────────────────────────────────────
    async def from_upstream(self, raw: str | bytes | dict[str, Any]) -> None:
        self.stats["upstream_messages"] += 1
        try:
            msg = parse_message(raw, self.max_message_bytes)
        except ProtocolError as exc:
            self.stats["malformed_upstream"] += 1
            logger.warning("dropping malformed message from the upstream MCP server: %s", exc)
            return
        if "method" not in msg:
            k = _key(msg.get("id"))
            fut = self._pending.get(k)
            if fut is not None:
                if not fut.done():
                    fut.set_result(msg)
                return  # the guard answers the client with the sanitized result
            if k in self._initialize_ids:
                self._initialize_ids.discard(k)
                self._record_session(
                    None, msg.get("result") if isinstance(msg.get("result"), dict) else None
                )
        await self.send_client(msg)

    async def upstream_closed(self, reason: str = "upstream closed the connection") -> None:
        self.closed = reason
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(UpstreamClosedError(reason))

    def _record_session(self, client: dict[str, Any] | None, server: dict[str, Any] | None) -> None:
        meta: dict[str, Any] = {"mcp_server": self.identity.server, "transport": "stdio"}
        if client is not None:
            meta["client_info"] = client.get("clientInfo")
            meta["protocol_version"] = client.get("protocolVersion")
        if server is not None:
            meta["server_info"] = server.get("serverInfo")
            meta["server_capabilities"] = sorted((server.get("capabilities") or {}).keys())
        try:
            self.pipeline.start_session(
                self.identity.agent_id,
                self.session_id,
                objective=self.identity.objective,
                metadata=meta,
                tenant_id=self.identity.tenant_id,
            )
        except ValueError as exc:  # the session belongs to another agent
            logger.warning("MCP gateway session not recorded: %s", exc)

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "server": self.identity.server,
            "agent_id": self.identity.agent_id,
            "tenant_id": self.identity.tenant_id,
            "closed": self.closed,
            "pending_upstream": len(self._pending),
            "calls_in_flight": len(self._calls),
            **self.stats,
            **self.guard.stats,
        }


# ── stdio transport ────────────────────────────────────────────────────────────────


async def run_stdio(
    pipeline: Any,
    command: list[str],
    *,
    identity: GatewayIdentity | None = None,
    call_timeout_s: float = 300.0,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    client_in: Any = None,
    client_out: Any = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> int:
    """Run the gateway on this process's stdin/stdout, with ``command`` as the upstream server.

    Messages are newline-delimited JSON (the MCP stdio transport). The server's stderr is
    passed through to ours. Returns the server's exit code.
    """
    client_in = client_in or sys.stdin.buffer
    client_out = client_out or sys.stdout.buffer
    loop = asyncio.get_running_loop()
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,  # inherit: server logs stay visible
        env=env,
        cwd=cwd,
        limit=max_message_bytes + 1024,
    )
    if proc.stdin is None or proc.stdout is None:  # not an assert: stripped under python -O
        raise RuntimeError("the upstream MCP server's stdio pipes are not available")
    up_in, up_out = proc.stdin, proc.stdout
    out_lock = asyncio.Lock()
    up_lock = asyncio.Lock()

    async def send_client(msg: dict[str, Any]) -> None:
        data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        async with out_lock:
            await asyncio.to_thread(_write_flush, client_out, data)

    async def send_upstream(msg: dict[str, Any]) -> None:
        data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        async with up_lock:
            if up_in.is_closing():
                raise UpstreamClosedError("upstream stdin closed")
            up_in.write(data)
            await up_in.drain()

    gw = MCPGateway(
        pipeline,
        send_client=send_client,
        send_upstream=send_upstream,
        identity=identity,
        call_timeout_s=call_timeout_s,
        max_message_bytes=max_message_bytes,
    )

    async def pump_upstream() -> None:
        while True:
            try:
                line = await up_out.readline()
            except (asyncio.LimitOverrunError, ValueError):
                gw.stats["malformed_upstream"] += 1
                logger.warning("upstream message exceeded %s bytes; dropped", max_message_bytes)
                with contextlib.suppress(Exception):
                    await up_out.readuntil(b"\n")
                continue
            if not line:
                break
            if line.strip():
                await gw.from_upstream(line)
        await gw.upstream_closed(f"upstream exited ({proc.returncode})")

    # the client side is read in a thread: portable across event loops and platforms
    client_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def read_client() -> None:
        try:
            while True:
                line = client_in.readline(max_message_bytes + 2)
                if not line:
                    break
                if len(line) > max_message_bytes and not line.endswith(b"\n"):
                    # drain the rest of an oversized line, then report it
                    while True:
                        more = client_in.readline(65536)
                        if not more or more.endswith(b"\n"):
                            break
                    loop.call_soon_threadsafe(client_queue.put_nowait, b"\x00oversized")
                    continue
                loop.call_soon_threadsafe(client_queue.put_nowait, line)
        finally:
            loop.call_soon_threadsafe(client_queue.put_nowait, None)

    threading.Thread(target=read_client, name="mcp-gateway-stdin", daemon=True).start()
    upstream_task = asyncio.ensure_future(pump_upstream())
    try:
        while True:
            line = await client_queue.get()
            if line is None:
                break
            if line == b"\x00oversized":
                gw.stats["malformed_client"] += 1
                await send_client(
                    error_response(
                        None, INVALID_REQUEST, f"message larger than {max_message_bytes} bytes"
                    )
                )
                continue
            if line.strip():
                await gw.from_client(line)
    finally:
        # client went away: let in-flight calls finish briefly, then stop the server
        if gw._calls:
            await asyncio.wait(list(gw._calls.values()), timeout=5)
        with contextlib.suppress(Exception):
            up_in.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
        upstream_task.cancel()
        with contextlib.suppress(BaseException):
            await upstream_task
    return proc.returncode or 0


def _write_flush(stream: Any, data: bytes) -> None:
    stream.write(data)
    stream.flush()


# ── Streamable HTTP transport ─────────────────────────────────────────────────────

_HOP = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "upgrade",
    "proxy-authorization",
    "x-agentwatch-key",
    "accept-encoding",
}


def build_http_app(
    pipeline: Any,
    upstream_url: str,
    *,
    identity: GatewayIdentity | None = None,
    call_timeout_s: float = 300.0,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    tenant_keys: dict[str, str] | None = None,
    path: str = "/mcp",
) -> Any:
    """An ASGI app proxying MCP Streamable HTTP to ``upstream_url`` with enforcement.

    ``POST`` messages are inspected; ``tools/call`` is enforced and its result sanitized
    (returned as ``application/json``). Other requests, notifications, responses, the
    ``GET`` event stream and ``DELETE`` are proxied unchanged, with the MCP session headers.
    With ``tenant_keys`` (``key -> tenant``), every request must carry ``X-AgentWatch-Key``;
    the tenant comes from the key, never from the client. The key is not forwarded.
    """
    import httpx
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response, StreamingResponse
    from starlette.routing import Route

    ident = identity or GatewayIdentity()
    guards: dict[str, ToolCallGuard] = {}
    client = httpx.AsyncClient(timeout=httpx.Timeout(call_timeout_s, connect=10.0))

    def guard_for(tenant: str) -> ToolCallGuard:
        g = guards.get(tenant)
        if g is None:
            g = ToolCallGuard(
                pipeline,
                GatewayIdentity(**{**vars(ident), "tenant_id": tenant}),
                call_timeout_s=call_timeout_s,
            )
            guards[tenant] = g
        return g

    def tenant_of(request: Request) -> str | None:
        if not tenant_keys:
            return ident.tenant_id
        key = request.headers.get("x-agentwatch-key")
        return tenant_keys.get(key) if key else None

    def fwd_headers(request: Request) -> dict[str, str]:
        return {k: v for k, v in request.headers.items() if k.lower() not in _HOP}

    def back_headers(resp: httpx.Response) -> dict[str, str]:
        keep = {
            "mcp-session-id",
            "mcp-protocol-version",
            "www-authenticate",
            "content-type",
            "cache-control",
        }
        return {k: v for k, v in resp.headers.items() if k.lower() in keep}

    async def read_response(resp: httpx.Response, want_id: Any) -> dict[str, Any]:
        """The JSON-RPC response with ``want_id`` from a JSON or SSE upstream reply."""
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            data_lines: list[str] = []
            size = 0
            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        size = 0
                        try:
                            msg = json.loads(payload)
                        except ValueError:
                            continue
                        if (
                            isinstance(msg, dict)
                            and "method" not in msg
                            and msg.get("id") == want_id
                        ):
                            return msg
                    continue
                if line.startswith("data:"):
                    size += len(line)
                    if size > max_message_bytes:
                        raise UpstreamError(
                            {
                                "code": INTERNAL_ERROR,
                                "message": "upstream event larger than the gateway limit",
                            }
                        )
                    data_lines.append(line[5:].lstrip(" "))
            raise UpstreamClosedError("upstream stream ended without a response")
        body = await resp.aread()
        if len(body) > max_message_bytes:
            raise UpstreamError(
                {
                    "code": INTERNAL_ERROR,
                    "message": "upstream response larger than the gateway limit",
                }
            )
        if resp.status_code >= 400:
            raise UpstreamError(
                {
                    "code": INTERNAL_ERROR,
                    "message": f"upstream HTTP {resp.status_code}",
                    "data": body[:500].decode("utf-8", "replace"),
                }
            )
        try:
            msg = json.loads(body)
        except ValueError as exc:
            raise UpstreamError(
                {"code": INTERNAL_ERROR, "message": "upstream response is not JSON"}
            ) from exc
        if isinstance(msg, list):
            msg = next((m for m in msg if isinstance(m, dict) and m.get("id") == want_id), None)
        if not isinstance(msg, dict):
            raise UpstreamError(
                {"code": INTERNAL_ERROR, "message": "upstream response has no result"}
            )
        return msg

    async def post(request: Request) -> Response:
        tenant = tenant_of(request)
        if tenant is None:
            return JSONResponse(
                error_response(None, INVALID_REQUEST, "missing or invalid X-AgentWatch-Key"),
                status_code=401,
            )
        body = await request.body()
        try:
            msg = parse_message(body, max_bytes=max_message_bytes)
        except ProtocolError as exc:
            status = 413 if "larger than" in str(exc) else 400
            return JSONResponse(error_response(exc.msg_id, exc.code, str(exc)), status_code=status)
        headers = fwd_headers(request)
        if msg.get("method") == "tools/call" and msg.get("id") is not None:
            sess = request.headers.get("mcp-session-id")
            captured: dict[str, str] = {}

            async def forward(fwd: dict[str, Any]) -> dict[str, Any]:
                req = client.build_request(
                    "POST",
                    upstream_url,
                    json=fwd,
                    headers={**headers, "accept": "application/json, text/event-stream"},
                )
                try:
                    resp = await client.send(req, stream=True)
                except httpx.HTTPError as exc:
                    raise UpstreamClosedError(f"{type(exc).__name__}: {exc}") from exc
                try:
                    captured.update(back_headers(resp))
                    return await read_response(resp, fwd["id"])
                finally:
                    await resp.aclose()

            out = await guard_for(tenant).call(
                msg, forward, session_id=f"mcp-http-{sess}" if sess else None
            )
            captured.pop("content-type", None)
            return JSONResponse(out, headers=captured)
        # everything else: a transparent proxy (JSON or SSE, streamed back as is)
        req = client.build_request("POST", upstream_url, content=body, headers=headers)
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            if msg.get("id") is None:
                return Response(status_code=502)
            return JSONResponse(
                error_response(msg.get("id"), UPSTREAM_UNAVAILABLE, f"upstream unavailable: {exc}"),
                status_code=502,
            )
        return StreamingResponse(
            _relay(resp), status_code=resp.status_code, headers=back_headers(resp)
        )

    async def get_or_delete(request: Request) -> Response:
        if tenant_of(request) is None:
            return Response(status_code=401)
        req = client.build_request(
            request.method,
            upstream_url,
            headers=fwd_headers(request),
            timeout=httpx.Timeout(None, connect=10.0),
        )
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError:
            return Response(status_code=502)
        return StreamingResponse(
            _relay(resp), status_code=resp.status_code, headers=back_headers(resp)
        )

    async def _relay(resp: httpx.Response) -> Any:
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    async def health(_: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "upstream": upstream_url,
                "sessions": {t: g.stats for t, g in guards.items()},
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> Any:
        yield
        await client.aclose()

    return Starlette(
        routes=[
            Route(path, post, methods=["POST"]),
            Route(path, get_or_delete, methods=["GET", "DELETE"]),
            Route("/healthz", health, methods=["GET"]),
        ],
        lifespan=lifespan,
    )


# ── configuration ─────────────────────────────────────────────────────────────────


def build_pipeline(env: dict[str, str] | None = None) -> Any:
    """The gateway's pipeline from the standard settings (see docs/MCP_GATEWAY.md).

    Escalated calls wait for a human decision through the approval API/dashboard (the
    same evidence store); without one before the timeout they are not forwarded.
    """
    from panoptis.protect.approvals import ApprovalBroker
    from panoptis.protect.ledger import ProtectLedger
    from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline
    from panoptis.protect.rbac import PolicyStore

    env = dict(os.environ if env is None else env)
    policy_file = env.get("AGENTWATCH_POLICY_FILE")
    ledger = ProtectLedger(env.get("AGENTWATCH_STORE") or None)
    timeout = float(env.get("AGENTWATCH_APPROVAL_TIMEOUT_S") or 120)
    return ProtectionPipeline(
        policy=PolicyStore(path=policy_file) if policy_file else None,
        ledger=ledger,
        approvals=ApprovalBroker(ledger, timeout_s=timeout),
        config=ProtectionConfig(approval_timeout_s=timeout),
    )


def client_config(
    server_name: str, command: list[str], *, agent_id: str | None = None, python: str | None = None
) -> dict[str, Any]:
    """An ``mcpServers`` entry that runs ``command`` behind the gateway (Claude Desktop, Cursor, .mcp.json)."""
    args = ["-m", "panoptis.protect.mcp_gateway", "stdio", "--server-name", server_name]
    if agent_id:
        args += ["--agent-id", agent_id]
    return {
        "mcpServers": {
            server_name: {"command": python or sys.executable, "args": [*args, "--", *command]}
        }
    }


def gateway_status(
    *, limit: int = 200, tenant_id: str = "default", store: str | None = None
) -> dict[str, Any]:
    """Recent decisions made by MCP gateways, per server, tool and verdict."""
    from panoptis.protect.ledger import KIND_DECISION, ProtectLedger

    lg = ProtectLedger(store or os.environ.get("AGENTWATCH_STORE") or None)
    try:
        docs = lg.query((KIND_DECISION,), limit=limit, tenant_id=tenant_id)
    finally:
        lg.store.close()
    servers: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for d in docs:
        action = (d.get("payload") or {}).get("action") or {}
        source = str(action.get("source", ""))
        if not source.startswith("mcp:"):
            continue
        srv = servers.setdefault(source[4:], {"decisions": 0, "verdicts": {}, "tools": {}})
        verdict = d["declared"].get("verdict")
        srv["decisions"] += 1
        srv["verdicts"][verdict] = srv["verdicts"].get(verdict, 0) + 1
        srv["tools"][action.get("tool")] = srv["tools"].get(action.get("tool"), 0) + 1
        if len(recent) < 20:
            recent.append(
                {
                    "server": source[4:],
                    "tool": action.get("tool"),
                    "verdict": verdict,
                    "reason": d["payload"].get("reason"),
                    "session_id": action.get("session_id"),
                }
            )
    return {"tenant_id": tenant_id, "servers": servers, "recent": recent}


def main(argv: list[str] | None = None) -> int:
    """``python -m panoptis.protect.mcp_gateway stdio|http ...`` (also ``panoptis protect mcp-gateway``)."""
    import argparse

    ap = argparse.ArgumentParser(prog="panoptis-mcp-gateway", description="Enforcing MCP gateway")
    sub = ap.add_subparsers(dest="transport", required=True)
    for name in ("stdio", "http"):
        p = sub.add_parser(name)
        p.add_argument("--agent-id", default=os.environ.get("AGENTWATCH_AGENT_ID", "mcp-client"))
        p.add_argument("--session-id", default=os.environ.get("AGENTWATCH_SESSION_ID"))
        p.add_argument("--tenant", default=os.environ.get("AGENTWATCH_TENANT_ID", "default"))
        p.add_argument("--server-name", default="upstream")
        p.add_argument("--objective", default=os.environ.get("AGENTWATCH_OBJECTIVE"))
        p.add_argument("--tool-prefix", default="")
        p.add_argument(
            "--timeout", type=float, default=300.0, help="seconds to wait for the server per call"
        )
        p.add_argument("--max-message-bytes", type=int, default=DEFAULT_MAX_MESSAGE_BYTES)
    sub.choices["stdio"].add_argument(
        "command", nargs=argparse.REMAINDER, help="-- server command and arguments"
    )
    cp = sub.add_parser(
        "config", help="print an mcpServers entry that runs a server behind the gateway"
    )
    cp.add_argument("--server-name", required=True)
    cp.add_argument("--agent-id")
    cp.add_argument("command", nargs=argparse.REMAINDER)
    stp = sub.add_parser("status", help="recent gateway decisions from the evidence store")
    stp.add_argument("--limit", type=int, default=200)
    stp.add_argument("--tenant", default=os.environ.get("AGENTWATCH_TENANT_ID", "default"))
    hp = sub.choices["http"]
    hp.add_argument("--upstream", required=True, help="the MCP server's Streamable HTTP URL")
    hp.add_argument("--host", default="127.0.0.1")
    hp.add_argument("--port", type=int, default=8765)
    ns = ap.parse_args(argv)
    if ns.transport == "config":
        command = ns.command[1:] if ns.command and ns.command[0] == "--" else ns.command
        if not command:
            ap.error("give the server command after --")
        print(json.dumps(client_config(ns.server_name, command, agent_id=ns.agent_id), indent=2))
        return 0
    if ns.transport == "status":
        print(json.dumps(gateway_status(limit=ns.limit, tenant_id=ns.tenant), indent=2))
        return 0
    ident = GatewayIdentity(
        agent_id=ns.agent_id,
        session_id=ns.session_id,
        tenant_id=ns.tenant,
        server=ns.server_name,
        objective=ns.objective,
        tool_prefix=ns.tool_prefix,
    )
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    pipeline = build_pipeline()
    if ns.transport == "stdio":
        command = ns.command[1:] if ns.command and ns.command[0] == "--" else ns.command
        if not command:
            ap.error("give the server command after --")
        return asyncio.run(
            run_stdio(
                pipeline,
                command,
                identity=ident,
                call_timeout_s=ns.timeout,
                max_message_bytes=ns.max_message_bytes,
            )
        )
    import uvicorn

    keys = os.environ.get("AGENTWATCH_TENANT_KEYS")
    tenant_keys = None
    if keys:
        tenant_keys = {
            k.strip(): t.strip()
            for t, _, k in (p.partition("=") for p in keys.split(","))
            if k.strip()
        }
    app = build_http_app(
        pipeline,
        ns.upstream,
        identity=ident,
        call_timeout_s=ns.timeout,
        max_message_bytes=ns.max_message_bytes,
        tenant_keys=tenant_keys,
    )
    if ns.host not in ("127.0.0.1", "localhost", "::1") and not tenant_keys:
        print(
            "refusing to listen on a non-loopback address without AGENTWATCH_TENANT_KEYS",
            file=sys.stderr,
        )
        return 2
    uvicorn.run(app, host=ns.host, port=ns.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
