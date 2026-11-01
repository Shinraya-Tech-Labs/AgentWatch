"""
OBS-002 — Live Session Dashboard support.

Real WebSocket stream from EventBus → connected clients.
Auto-creates session on first event. No polling, no seed data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import AgentEvent, EventType

logger = logging.getLogger(__name__)


@dataclass
class LiveSession:
    """Lightweight session shadow built from streamed events."""

    session_id: str
    started_at: datetime
    agent_id: str = ""
    framework: str = "custom"
    event_count: int = 0
    last_event_type: str = ""
    last_event_at: datetime | None = None
    status: str = "running"


class LiveStreamHub:
    """
    Auto-create sessions from streamed events and broadcast to subscribers.

    Usage:
        hub = LiveStreamHub()
        hub.attach(event_bus)
        async for payload in hub.subscribe():
            ws.send(payload)
    """

    def __init__(self, max_history: int = 200):
        self._sessions: dict[str, LiveSession] = {}
        self._queues: list[asyncio.Queue] = []
        self._history: list[dict[str, Any]] = []
        self.max_history = max_history
        self._handler_id: str | None = None

    # ── attachment ─────────────────────────────────────────────────────────

    def attach(self, bus: EventBus | None = None) -> None:
        bus = bus or get_event_bus()
        self._handler_id = bus.subscribe_fn(self._on_event)

    def detach(self, bus: EventBus | None = None) -> None:
        if self._handler_id is None:
            return
        bus = bus or get_event_bus()
        bus.unsubscribe(self._handler_id)
        self._handler_id = None

    async def _on_event(self, event: AgentEvent) -> None:
        self._ensure_session(event)
        session = self._sessions[event.session_id]
        session.event_count += 1
        session.last_event_type = event.event_type.value
        session.last_event_at = event.timestamp
        if event.event_type == EventType.SESSION_END:
            session.status = event.status.value

        payload = {
            "type": "event",
            "event": event.model_dump_for_storage(),
            "session": session.__dict__
            | {
                "started_at": session.started_at.isoformat(),
                "last_event_at": session.last_event_at.isoformat()
                if session.last_event_at
                else None,
            },
        }
        self._history.append(payload)
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history :]

        for q in list(self._queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.debug("LiveStreamHub: dropping for slow consumer")

    def _ensure_session(self, event: AgentEvent) -> None:
        if event.session_id in self._sessions:
            return
        self._sessions[event.session_id] = LiveSession(
            session_id=event.session_id,
            started_at=datetime.now(UTC),
            agent_id=event.agent_id,
            framework=event.framework.value,
        )

    # ── subscription ───────────────────────────────────────────────────────

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._queues.append(q)
        # Send recent history first
        for payload in self._history[-50:]:
            await q.put(payload)
        try:
            while True:
                payload = await q.get()
                yield json.dumps(payload, default=str)
        finally:
            self._queues.remove(q)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sessions": [
                {
                    **s.__dict__,
                    "started_at": s.started_at.isoformat(),
                    "last_event_at": s.last_event_at.isoformat() if s.last_event_at else None,
                }
                for s in self._sessions.values()
            ],
            "history_size": len(self._history),
        }


_default_hub: LiveStreamHub | None = None


def get_live_hub() -> LiveStreamHub:
    global _default_hub
    if _default_hub is None:
        _default_hub = LiveStreamHub()
        _default_hub.attach()
    return _default_hub


__all__ = ["LiveStreamHub", "LiveSession", "get_live_hub"]
