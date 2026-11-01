"""The HTTP event forwarder must not add a connection attempt to every event when the
API server is down (it used to, adding seconds to each wrapped tool call)."""

from __future__ import annotations

import asyncio
import socket
import time

import httpx

from agentwatch.core.event_bus import EventBus
from agentwatch.core.http_forwarder import _HANDLER_ID, HttpEventForwarder, register_http_forwarder
from agentwatch.core.schema import AgentEvent, EventType


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _event() -> AgentEvent:
    return AgentEvent(session_id="s", agent_id="a", event_type=EventType.TOOL_CALL)


def test_after_a_connection_failure_events_are_skipped_without_connecting(monkeypatch):
    fwd = HttpEventForwarder(f"http://127.0.0.1:{_closed_port()}", backoff_s=60)
    asyncio.run(fwd.forward(_event()))  # fails and opens the backoff window
    attempts = []
    monkeypatch.setattr(httpx.AsyncClient, "post", lambda *a, **k: attempts.append(1))
    t0 = time.perf_counter()
    for _ in range(20):
        asyncio.run(fwd.forward(_event()))
    assert attempts == [] and fwd.skipped == 20
    assert time.perf_counter() - t0 < 1.0


def test_forwarding_resumes_after_the_backoff():
    fwd = HttpEventForwarder(f"http://127.0.0.1:{_closed_port()}", backoff_s=0.0)
    asyncio.run(fwd.forward(_event()))
    asyncio.run(fwd.forward(_event()))
    assert fwd.skipped == 0


def test_forwarding_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AGENTWATCH_FORWARD_EVENTS", "0")
    bus = EventBus()
    register_http_forwarder(bus)
    assert _HANDLER_ID not in bus._handlers
