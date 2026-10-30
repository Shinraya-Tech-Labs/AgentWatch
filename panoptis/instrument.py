"""Public native instrumentation API (v3).

Usage::

    from panoptis import instrument as aw


    @aw.tool("web_search")
    def search(q): ...


    with aw.run("research"):
        with aw.span("OPERATION", "plan", actor="agent:planner"):
            docs = search("...")

When the program is launched with ``panoptis observe``, observations are written to
the file named by ``AGENTWATCH_OBSERVE_FILE``. Otherwise recording is a no-op unless
:func:`configure` is called with a sink.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from panoptis.evidence.model import ObservationDraft
from panoptis.sensors.base import FileSink, ObservationSink
from panoptis.sensors.native.recorder import (
    Recorder,
    RunHandle,
    Span,
    current_run,
    current_span,
    instrumented_call,
    tag,
)

F = TypeVar("F", bound=Callable[..., Any])


class NullSink:
    def emit(self, draft: ObservationDraft) -> None:
        return None

    def flush(self) -> None:
        return None


_recorder: Recorder | None = None


def configure(
    sink: ObservationSink | None = None, *, tenant_id: str = "default", system: str | None = None
) -> Recorder:
    """Install the process-wide recorder."""
    global _recorder
    _recorder = Recorder(sink or NullSink(), tenant_id=tenant_id, system=system)
    return _recorder


def recorder() -> Recorder:
    global _recorder
    if _recorder is None:
        path = os.environ.get("AGENTWATCH_OBSERVE_FILE")
        _recorder = Recorder(
            FileSink(path) if path else NullSink(), system=os.environ.get("AGENTWATCH_SYSTEM")
        )
    return _recorder


@contextmanager
def run(name: str, run_id: str | None = None, **attributes: Any) -> Iterator[RunHandle | Span]:
    """Start a run. Nested inside an existing run it becomes an OPERATION span."""
    active = current_run()
    if active is not None:
        with recorder().span("OPERATION", name, facets=["run"], **attributes) as span:
            yield span
        return
    with recorder().run(name, run_id=run_id, **attributes) as handle:
        yield handle


def span(kind: str, operation: str, **kwargs: Any) -> Any:
    return recorder().span(kind, operation, **kwargs)


def event(kind: str, operation: str, **kwargs: Any) -> None:
    recorder().event(kind, operation, **kwargs)


def _decorator(
    kind: str, name: str | None, actor: str | None, object_prefix: str
) -> Callable[[F], F]:
    def wrap(fn: F) -> F:
        op = name or fn.__name__
        return instrumented_call(
            recorder, kind, op, fn, actor=actor, object=f"{object_prefix}:{op}"
        )  # type: ignore[return-value]

    return wrap


def tool(name: str | None = None, *, actor: str | None = None) -> Callable[[F], F]:
    """Record each call as a TOOL_INVOCATION of tool ``name``."""
    return _decorator("TOOL_INVOCATION", name, actor, "tool")


def model(name: str, *, actor: str | None = None) -> Callable[[F], F]:
    """Record each call as a MODEL_INVOCATION of model ``name`` (e.g. ``anthropic/claude-sonnet-5``)."""

    def wrap(fn: F) -> F:
        return instrumented_call(
            recorder,
            "MODEL_INVOCATION",
            name,
            fn,
            actor=actor,
            object=f"model:{name}",
            input_role="prompt",
            output_role="completion",
        )  # type: ignore[return-value]

    return wrap


def retriever(name: str, *, actor: str | None = None) -> Callable[[F], F]:
    def wrap(fn: F) -> F:
        return instrumented_call(
            recorder,
            "RETRIEVAL",
            name,
            fn,
            actor=actor,
            object=f"index:{name}",
            input_role="query",
            output_role="documents",
        )  # type: ignore[return-value]

    return wrap


def memory_write(store: str, key: str, value: Any, *, actor: str | None = None) -> None:
    with recorder().span(
        "MEMORY_ACCESS", f"write:{store}", actor=actor, object=f"memory:{store}", facets=["write"]
    ) as s:
        s.input(value, role="value", label=key)
        s.set(key=key, access="write")


def memory_read(store: str, key: str, value: Any, *, actor: str | None = None) -> Any:
    with recorder().span(
        "MEMORY_ACCESS", f"read:{store}", actor=actor, object=f"memory:{store}", facets=["read"]
    ) as s:
        s.set(key=key, access="read")
        s.output(value, role="value", label=key)
    return value


def message(sender: str, receiver: str, content: Any, *, channel: str | None = None) -> None:
    with recorder().span(
        "MESSAGE", f"send:{channel or receiver}", actor=sender, object=receiver
    ) as s:
        s.input(content, role="message")
        s.output(content, role="message")


def delegate(sender: str, receiver: str, task: Any) -> None:
    with recorder().span("DELEGATION", f"delegate:{receiver}", actor=sender, object=receiver) as s:
        s.input(task, role="task")
        s.output(task, role="task")


def artifact(name: str, content: Any, *, actor: str | None = None) -> None:
    """Record that the program produced a named artifact (file, report, patch)."""
    with recorder().span(
        "STATE_MUTATION",
        f"write_artifact:{name}",
        actor=actor,
        object=f"file:{name}",
        facets=["artifact_creation"],
    ) as s:
        s.output(content, role="artifact", label=name)


def external_input(source: str, content: Any) -> None:
    with recorder().span("EXTERNAL_INPUT", f"input:{source}", actor=f"external:{source}") as s:
        s.output(content, role="input", label=source)


__all__ = [
    "artifact",
    "configure",
    "current_run",
    "current_span",
    "delegate",
    "event",
    "external_input",
    "memory_read",
    "memory_write",
    "message",
    "model",
    "recorder",
    "retriever",
    "run",
    "span",
    "tag",
    "tool",
]
