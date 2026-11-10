"""
PANOPTIS Event Bus
Central publish/subscribe system for all agent events.
Supports sync and async handlers, filtering, and persistence hooks.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from panoptis.core.schema import AgentEvent, EventType

logger = logging.getLogger(__name__)

# Handler types
SyncHandler = Callable[[AgentEvent], None]
AsyncHandler = Callable[[AgentEvent], Coroutine[Any, Any, None]]
AnyHandler = SyncHandler | AsyncHandler


class EventFilter:
    """Filter events before dispatching to a handler."""

    def __init__(
        self,
        event_types: set[EventType] | None = None,
        session_ids: set[str] | None = None,
        agent_ids: set[str] | None = None,
        frameworks: set[str] | None = None,
    ) -> None:
        """Build a filter from optional allow-lists.

        Args:
            event_types: Only pass events whose type is in this set.
            session_ids: Only pass events for these session IDs.
            agent_ids: Only pass events from these agent IDs.
            frameworks: Only pass events from these framework name strings.
        """
        self.event_types = event_types
        self.session_ids = session_ids
        self.agent_ids = agent_ids
        self.frameworks = frameworks

    def matches(self, event: AgentEvent) -> bool:
        """Return True if the event passes all configured filters.

        Args:
            event: The event to test.

        Returns:
            True when the event should be delivered to the handler.
        """
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.session_ids and event.session_id not in self.session_ids:
            return False
        if self.agent_ids and event.agent_id not in self.agent_ids:
            return False
        if self.frameworks and event.framework.value not in self.frameworks:
            return False
        return True


class HandlerRegistration:
    """Internal record for a subscribed event handler."""

    def __init__(
        self,
        handler_id: str,
        handler: AnyHandler,
        event_filter: EventFilter | None = None,
        is_async: bool = False,
    ) -> None:
        """Register handler metadata on the bus.

        Args:
            handler_id: Unique identifier for this subscription.
            handler: Callable invoked on matching events.
            event_filter: Optional extra filter applied before dispatch.
            is_async: True when ``handler`` is an async coroutine function.
        """
        self.handler_id = handler_id
        self.handler = handler
        self.event_filter = event_filter
        self.is_async = is_async
        self.call_count = 0
        self.error_count = 0
        self.registered_at = datetime.now(UTC)


class EventBus:
    """
    Central event bus for PANOPTIS.

    Usage:
        bus = EventBus()

        @bus.subscribe(EventType.TOOL_CALL)
        async def on_tool_call(event: AgentEvent):
            ...

        await bus.publish(event)
    """

    def __init__(self) -> None:
        self._background: set[asyncio.Task] = set()
        """Create an empty bus with in-memory event logging."""
        self._handlers: dict[str, HandlerRegistration] = {}
        self._type_index: dict[EventType, set[str]] = defaultdict(set)
        self._global_handlers: set[str] = set()  # subscribed to all events
        self._lock = asyncio.Lock()
        self._event_log: list[AgentEvent] = []
        self._max_log_size = 10_000
        self._stats: dict[str, int] = defaultdict(int)

    def subscribe(
        self,
        *event_types: EventType,
        handler_id: str | None = None,
        event_filter: EventFilter | None = None,
    ) -> Callable[[AnyHandler], AnyHandler]:
        """Decorator to subscribe a handler to one or more event types.

        Args:
            *event_types: Event types to listen for; empty means all events.
            handler_id: Optional stable ID; defaults to the function qualname.
            event_filter: Optional filter applied after type matching.

        Returns:
            Decorator that registers the wrapped function on the bus.
        """

        def decorator(fn: AnyHandler) -> AnyHandler:
            _id = handler_id or f"{fn.__module__}.{fn.__qualname__}"

            # CodeRabbit: Clean stale registration if ID is reused
            if _id in self._handlers:
                self.unsubscribe(_id)

            is_async = inspect.iscoroutinefunction(fn)
            reg = HandlerRegistration(
                handler_id=_id,
                handler=fn,
                event_filter=event_filter,
                is_async=is_async,
            )
            self._handlers[_id] = reg

            if event_types:
                for et in event_types:
                    self._type_index[et].add(_id)
            else:
                self._global_handlers.add(_id)

            logger.debug("Registered handler %s for %s", _id, event_types or "ALL")
            return fn

        return decorator

    def subscribe_fn(
        self,
        fn: AnyHandler,
        *event_types: EventType,
        handler_id: str | None = None,
        event_filter: EventFilter | None = None,
    ) -> str:
        """Subscribe a callable without using the decorator syntax.

        Args:
            fn: Sync or async handler receiving each published event.
            *event_types: Event types to listen for; empty means all events.
            handler_id: Optional stable ID for later unsubscription.
            event_filter: Optional filter applied after type matching.

        Returns:
            The handler ID assigned to this subscription.
        """
        _id = handler_id or f"{fn.__module__}.{fn.__qualname__}.{id(fn)}"

        # CodeRabbit: Clean stale registration if ID is reused
        if _id in self._handlers:
            self.unsubscribe(_id)

        is_async = inspect.iscoroutinefunction(fn)
        reg = HandlerRegistration(
            handler_id=_id,
            handler=fn,
            event_filter=event_filter,
            is_async=is_async,
        )
        self._handlers[_id] = reg

        if event_types:
            for et in event_types:
                self._type_index[et].add(_id)
        else:
            self._global_handlers.add(_id)

        return _id

    def unsubscribe(self, handler_id: str) -> None:
        """Remove a handler and clear it from all type indexes.

        Args:
            handler_id: ID returned by :meth:`subscribe_fn` or the decorator.
        """
        if handler_id not in self._handlers:
            return
        self._handlers.pop(handler_id, None)
        self._global_handlers.discard(handler_id)
        for ids in self._type_index.values():
            ids.discard(handler_id)

    async def publish(self, event: AgentEvent) -> None:
        """Publish an event to all matching handlers.

        The lock guards all mutations to _event_log, _stats, and the
        handler index snapshots. Handler dispatch runs outside the lock
        so that I/O-bound or slow handlers do not block concurrent
        publish calls.

        Args:
            event: The normalized event to fan out and log.
        """
        async with self._lock:
            # Log to in-memory buffer
            self._event_log.append(event)
            if len(self._event_log) > self._max_log_size:
                self._event_log = self._event_log[-self._max_log_size :]

            self._stats["total_published"] += 1
            self._stats[f"type.{event.event_type.value}"] += 1

            # Snapshot handler IDs under the lock so subscribe/unsubscribe
            # that happens concurrently does not mutate the set mid-iteration.
            handler_ids: set[str] = set()
            handler_ids.update(self._global_handlers)
            handler_ids.update(self._type_index.get(event.event_type, set()))
            handlers_to_dispatch = [
                self._handlers[hid]
                for hid in handler_ids
                if hid in self._handlers and self._handler_accepts(self._handlers[hid], event)
            ]

        # Dispatch outside the lock to avoid holding it during handler I/O.
        # Each handler gets its own deep copy: a handler that mutates its event (e.g. to redact
        # it) must not change what other handlers — or the persisted record — observe.
        tasks = [self._dispatch(reg, event.model_copy(deep=True)) for reg in handlers_to_dispatch]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _handler_accepts(reg: HandlerRegistration, event: AgentEvent) -> bool:
        """Return True if the handler's optional event filter accepts the event."""
        event_filter = reg.event_filter
        if event_filter is None:
            return True
        return event_filter.matches(event)

    async def _dispatch(self, reg: HandlerRegistration, event: AgentEvent) -> None:
        """Invoke one handler, running sync callables in a thread pool.

        Args:
            reg: Handler registration to invoke.
            event: Event passed to the handler.
        """
        try:
            if reg.is_async:
                await reg.handler(event)  # type: ignore
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    reg.handler,
                    event,  # type: ignore
                )
            reg.call_count += 1
        except Exception as exc:
            reg.error_count += 1
            logger.error(
                "Handler %s failed for event %s: %s",
                reg.handler_id,
                event.event_id,
                exc,
                exc_info=True,
            )

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """Done-callback attached to fire-and-forget tasks created by publish_sync.

        Retrieves the task result so that any unhandled exception is surfaced to
        the logger rather than silently discarded. CancelledError is ignored
        because task cancellation is a normal lifecycle event, not a bug.

        Args:
            task: The completed asyncio.Task whose result we inspect.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "publish_sync task raised an unhandled exception: %s",
                exc,
                exc_info=exc,
            )

    def publish_sync(self, event: AgentEvent) -> None:
        """Publish from synchronous code without awaiting.

        Schedules on the running loop when one exists; otherwise runs
        :meth:`publish` via :func:`asyncio.run`.

        When a running loop is present the created Task has a done-callback
        attached so that any exception raised inside an async handler is
        logged instead of being silently swallowed.

        Args:
            event: The event to publish.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.publish(event))
            return
        task = loop.create_task(self.publish(event))
        self._background.add(task)  # the loop keeps only a weak reference
        task.add_done_callback(self._background.discard)
        task.add_done_callback(self._log_task_exception)

    async def drain(self, timeout: float) -> int:
        """Wait for events published without awaiting, on this loop, to reach their handlers.

        Args:
            timeout: Seconds to wait before giving up. Unfinished work keeps running.

        Returns:
            How many are still in flight, 0 when everything finished.
        """
        loop = asyncio.get_running_loop()
        self._background -= {t for t in self._background if t.get_loop().is_closed()}
        pending = {t for t in self._background if t.get_loop() is loop and not t.done()}
        if pending:
            _, pending = await asyncio.wait(pending, timeout=timeout)
        return len(pending)

    def get_recent_events(
        self,
        limit: int = 100,
        event_type: EventType | None = None,
        session_id: str | None = None,
    ) -> list[AgentEvent]:
        """Return recent events from the in-memory log, newest first.

        Args:
            limit: Maximum number of events to return.
            event_type: Optional filter by event type.
            session_id: Optional filter by session ID.

        Returns:
            Matching events, most recent first.
        """
        events = list(reversed(self._event_log))
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if session_id:
            events = [e for e in events if e.session_id == session_id]
        return events[:limit]

    def stats(self) -> dict[str, int]:
        """Return publish counters keyed by metric name.

        Returns:
            Copy of internal stats (e.g. ``total_published``, ``type.*``).
        """
        result = dict(self._stats)
        result["active_subscribers"] = self.handler_count()
        return result

    def handler_count(self) -> int:
        """Return the number of registered handlers."""
        return len(self._handlers)


# Singleton bus instance
_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-wide singleton :class:`EventBus`.

    Returns:
        Shared bus instance, created on first call.
    """
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus
