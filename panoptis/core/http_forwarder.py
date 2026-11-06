"""HTTP event forwarder — cross-process delivery to the AgentWatch API server.

When watch() wraps an agent in a user script, events are published on that
process's in-memory event bus.  The API server (panoptis serve) has its own
isolated bus.  This module bridges them: it subscribes a handler on the local
bus that POSTs each event to POST /api/v1/events on the running server, which
re-publishes it on the server's bus so it reaches the collector and dashboard.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from panoptis.core.schema import AgentEvent

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://localhost:8000"
_HANDLER_ID = "panoptis.http_forwarder"


class HttpEventForwarder:
    """Async event handler that POSTs events to the AgentWatch API server."""

    def __init__(self, api_url: str = _DEFAULT_API_URL, *, backoff_s: float | None = None) -> None:
        self.api_url = api_url.rstrip("/")
        # After a connection failure, stop trying for ``backoff_s`` seconds. Without this,
        # every event (several per tool call) waited out a connection attempt to a server
        # that is not running, adding seconds to each protected call.
        env = os.getenv("AGENTWATCH_FORWARD_BACKOFF_S")
        self.backoff_s = backoff_s if backoff_s is not None else (float(env) if env else 30.0)
        self._down_until = 0.0
        self.skipped = 0
        self.refused = 0

    @staticmethod
    def _headers() -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = os.getenv("AGENTWATCH_API_KEY")
        if key:
            headers["X-Api-Key"] = key
        return headers

    async def forward(self, event: AgentEvent) -> None:
        if time.monotonic() < self._down_until:
            self.skipped += 1
            return
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.post(
                    f"{self.api_url}/api/v1/events",
                    content=event.model_dump_json(exclude_none=True),
                    headers=self._headers(),
                )
                if resp.status_code >= 400:
                    self.refused += 1
                    logger.warning(
                        "Event forwarder: server refused event with HTTP %d", resp.status_code
                    )
        except httpx.TransportError as exc:
            self._down_until = time.monotonic() + self.backoff_s
            logger.debug(
                "Event forward to %s failed (%s); pausing for %ss",
                self.api_url,
                exc,
                self.backoff_s,
            )
        except Exception as exc:
            logger.debug("Event forward to %s failed: %s", self.api_url, exc)


def register_http_forwarder(bus: object, api_url: str | None = None) -> None:
    """Subscribe an HTTP forwarder on *bus* if not already registered.

    Skips registration when running inside the API server process (detected by
    the presence of the 'api.collector' handler, which the server registers on
    startup).  This prevents a forwarding loop.

    Args:
        bus: The EventBus instance to subscribe to.
        api_url: Override the target URL.  Falls back to the
                 AGENTWATCH_API_URL env var, then http://localhost:8000.
    """
    if os.getenv("AGENTWATCH_FORWARD_EVENTS", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    handlers = getattr(bus, "_handlers", {})
    if "api.collector" in handlers:
        return
    if _HANDLER_ID in handlers:
        return

    url: str = api_url or os.getenv("AGENTWATCH_API_URL") or _DEFAULT_API_URL
    forwarder = HttpEventForwarder(url)
    bus.subscribe_fn(forwarder.forward, handler_id=_HANDLER_ID)  # type: ignore[attr-defined]
    logger.debug("HTTP event forwarder registered → %s", url)
