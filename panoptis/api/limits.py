"""Request and payload limits (TO-010).

* :class:`BodySizeLimitMiddleware`: a pure ASGI middleware. A request whose
  ``Content-Length`` exceeds the limit is answered 413 before any of its body is read; a
  chunked (or lying) body is counted as it streams and cut off with 413 once it passes the
  limit, so the server never buffers more than the limit.
  The same middleware scans JSON bodies as they stream and answers 413 once brackets nest
  deeper than ``AGENTWATCH_MAX_JSON_DEPTH`` plus room for request envelopes, before any
  parser sees them: a deep enough body otherwise exhausts the recursion limit of whatever
  walks it (on Python 3.14 the decoder accepts it and FastAPI's error handler recurses).
* :func:`check_json_limits`: structural bounds for parsed payloads (item count, nesting
  depth, string length), checked iteratively (no recursion limit to exhaust).

Defaults (environment):

    AGENTWATCH_MAX_BODY_BYTES      10485760  (10 MiB)
    AGENTWATCH_MAX_BATCH_ITEMS     5000      observations per batch
    AGENTWATCH_MAX_JSON_DEPTH      64
    AGENTWATCH_MAX_STRING_BYTES    4194304   (4 MiB; larger payloads still fit the blob store)
    AGENTWATCH_WS_MAX_BYTES        1048576   (1 MiB per WebSocket message, via uvicorn)
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def max_body_bytes() -> int:
    return _env_int("AGENTWATCH_MAX_BODY_BYTES", 10 * 1024 * 1024)


def max_batch_items() -> int:
    return _env_int("AGENTWATCH_MAX_BATCH_ITEMS", 5000)


def max_json_depth() -> int:
    return _env_int("AGENTWATCH_MAX_JSON_DEPTH", 64)


def max_string_bytes() -> int:
    return _env_int("AGENTWATCH_MAX_STRING_BYTES", 4 * 1024 * 1024)


def ws_max_bytes() -> int:
    return _env_int("AGENTWATCH_WS_MAX_BYTES", 1024 * 1024)


class PayloadTooLarge(ValueError):  # noqa: N818 - public name
    pass


def check_json_limits(
    value: Any,
    *,
    max_depth: int | None = None,
    max_string: int | None = None,
    max_items: int | None = None,
) -> None:
    """Raise :class:`PayloadTooLarge` if ``value`` nests deeper than ``max_depth``, holds a
    string longer than ``max_string`` bytes, or a container with more than ``max_items``."""
    depth_limit = max_depth if max_depth is not None else max_json_depth()
    string_limit = max_string if max_string is not None else max_string_bytes()
    items_limit = max_items if max_items is not None else 1_000_000
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > depth_limit:
            raise PayloadTooLarge(f"nesting deeper than {depth_limit} levels")
        if isinstance(node, str):
            if (
                len(node) > string_limit
                or len(node.encode("utf-8", "surrogatepass")) > string_limit
            ):
                raise PayloadTooLarge(f"a string is longer than {string_limit} bytes")
        elif isinstance(node, dict):
            if len(node) > items_limit:
                raise PayloadTooLarge(f"an object has more than {items_limit} keys")
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, (list, tuple)):
            if len(node) > items_limit:
                raise PayloadTooLarge(f"an array has more than {items_limit} items")
            stack.extend((v, depth + 1) for v in node)


Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


# envelopes ({"observations": [{"payload": ...}]}) nest a few levels above the payload limit
_ENVELOPE_DEPTH = 64
_JSON_SPECIAL = re.compile(rb'[\\"\[\]{}]')


def raw_json_depth_limit() -> int:
    return max_json_depth() + _ENVELOPE_DEPTH


class _DepthScanner:
    """Bracket nesting of a JSON byte stream, chunk by chunk, strings and escapes aware."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.depth = 0
        self.in_string = False
        self.escaped = False  # a backslash ended the previous chunk

    def feed(self, chunk: bytes) -> bool:
        """True as soon as the nesting goes deeper than the limit."""
        skip = 0 if self.escaped else -1
        self.escaped = False
        for m in _JSON_SPECIAL.finditer(chunk):
            i = m.start()
            if i == skip:
                continue
            c = chunk[i]
            if self.in_string:
                if c == 0x5C:  # backslash: the next byte is escaped
                    if i + 1 >= len(chunk):
                        self.escaped = True
                    else:
                        skip = i + 1
                elif c == 0x22:
                    self.in_string = False
            elif c == 0x22:
                self.in_string = True
            elif c in (0x5B, 0x7B):
                self.depth += 1
                if self.depth > self.limit:
                    return True
            elif c in (0x5D, 0x7D):
                self.depth -= 1
        return False


class BodySizeLimitMiddleware:
    """Reject HTTP request bodies larger than ``limit`` bytes with 413, before buffering them."""

    def __init__(self, app: Callable[..., Awaitable[None]], limit: int | None = None) -> None:
        self.app = app
        self._limit = limit

    @property
    def limit(self) -> int:
        return self._limit if self._limit is not None else max_body_bytes()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit
        scanner: _DepthScanner | None = None
        for name, value in scope.get("headers") or []:
            if name == b"content-type" and b"json" in value.lower():
                scanner = _DepthScanner(raw_json_depth_limit())
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _reply(send, 400, "invalid Content-Length")
                    return
                if declared > limit:
                    await _reply(send, 413, f"request body exceeds {limit} bytes")
                    return
        received = 0
        started = False
        overflow = False
        too_deep = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received, overflow, too_deep
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                received += len(body)
                if received > limit:
                    overflow = True
                    raise _BodyTooLargeError
                if scanner is not None and scanner.feed(body):
                    too_deep = True
                    raise _BodyTooLargeError
            return message

        def refusal() -> str:
            if too_deep:
                return f"nesting deeper than {raw_json_depth_limit()} levels"
            return f"request body exceeds {limit} bytes"

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if not started:
                await _reply(send, 413, refusal())
        except Exception:
            if (overflow or too_deep) and not started:
                # a handler wrapped our signal in its own exception: still a 413
                await _reply(send, 413, refusal())
                return
            raise


class _BodyTooLargeError(Exception):
    pass


async def _reply(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
