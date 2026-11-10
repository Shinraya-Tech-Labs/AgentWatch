"""Native instrumentation for Python applications.

A small, explicit API that records spans of work with their inputs, outputs and the
parent/dependency structure *as the application declares it*. Parent links come from
the context stack (the span that was active when a child started) and from explicit
``span.link(...)`` calls for multi-parent dependencies.

This module is a sensor: it records, it does not analyse, and it never raises into the
application because of a PANOPTIS failure.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar

from panoptis.evidence.canonical import to_jsonable
from panoptis.sensors.base import ListSink, ObservationSink, Sensor

F = TypeVar("F", bound=Callable[..., Any])

_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar("aw_span", default=None)
_current_run: contextvars.ContextVar[RunHandle | None] = contextvars.ContextVar(
    "aw_run", default=None
)
# (parent span id at tag time, attributes): applied to spans started directly inside ``tag()``
_tags: contextvars.ContextVar[tuple[str | None, dict[str, Any]] | None] = contextvars.ContextVar(
    "aw_tags", default=None
)


def _jsonable(value: Any) -> tuple[Any, bool]:
    """Return (json_value, faithful). ``faithful`` is False when a repr fallback was used."""
    try:
        converted = to_jsonable(value)
    except (TypeError, ValueError):
        return repr(value), False
    faithful = not (isinstance(converted, str) and not isinstance(value, str))
    return converted, faithful


# Runtime object identity (ADR-0017). A value a span produced, passed as the very same Python
# object into a later span, is a declared reference to that produced instance. Scalars and short
# strings are not tracked: the interpreter shares them between unrelated producers.
_IDENTITY_MIN_STR = 16
_IDENTITY_MAX_OBJECTS = 20_000
_IDENTITY_SCAN = 64  # container elements inspected per input


def _trackable(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) >= _IDENTITY_MIN_STR
    return isinstance(value, (dict, list, tuple, set, bytes)) and bool(value)


class RunHandle:
    def __init__(
        self, recorder: Recorder, name: str, run_id: str, attributes: dict[str, Any]
    ) -> None:
        self.recorder = recorder
        self.name = name
        self.run_id = run_id
        self.attributes = attributes
        self.outcome: str | None = None
        self.subject_id: str | None = None
        self._ordinals: dict[tuple[str, str], int] = {}
        # id(obj) -> (obj, ref); ref None when two producers returned the same object
        self._produced: dict[int, tuple[Any, str | None]] = {}

    def register_output(self, value: Any, ref: str) -> None:
        if not _trackable(value) or len(self._produced) >= _IDENTITY_MAX_OBJECTS:
            return
        known = self._produced.get(id(value))
        if known is not None and known[0] is value:
            if known[1] != ref:
                self._produced[id(value)] = (value, None)  # ambiguous: several producers
            return
        self._produced[id(value)] = (value, ref)  # keeps the object alive: ids stay unique

    def references(self, value: Any) -> list[str]:
        """Produced instances this input value is (or contains, one or two levels deep)."""
        out: list[str] = []

        def ref_of(v: Any) -> str | None:
            known = self._produced.get(id(v))
            return known[1] if known is not None and known[0] is v else None

        whole = ref_of(value)
        if whole:
            return [whole]
        level1 = list(value.values()) if isinstance(value, dict) else value
        if isinstance(level1, (list, tuple)):
            for v in level1[:_IDENTITY_SCAN]:
                r = ref_of(v)
                if r:
                    out.append(r)
                elif isinstance(v, (list, tuple)):
                    out.extend(r2 for x in v[:_IDENTITY_SCAN] if (r2 := ref_of(x)))
        return list(dict.fromkeys(out))

    def next_ordinal(self, kind: str, operation: str) -> int:
        key = (kind, operation)
        self._ordinals[key] = self._ordinals.get(key, 0) + 1
        return self._ordinals[key]

    def set_outcome(self, outcome: str) -> None:
        self.outcome = outcome


class Span:
    def __init__(
        self,
        recorder: Recorder,
        kind: str,
        operation: str,
        *,
        actor: str | None,
        object: str | None,
        run: RunHandle | None,
        parent: Span | None,
    ) -> None:
        self.recorder = recorder
        self.kind = kind
        self.operation = operation
        self.actor = actor
        self.object = object
        self.run = run
        self.parent = parent
        self.span_id = uuid.uuid4().hex[:16]
        self.inputs: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.attributes: dict[str, Any] = {}
        self.resources: dict[str, Any] = {}
        self.facets: list[str] = []
        self.status = "ok"
        self.error: dict[str, Any] | None = None
        self.call_key: str | None = None
        self._t0 = time.perf_counter()

    def input(
        self,
        value: Any,
        role: str = "input",
        label: str | None = None,
        *,
        source: Span | str | list[Span | str] | None = None,
    ) -> Span:
        """Record a consumed value. ``source`` declares which produced value(s) it is: a Span
        (its first output), a reference from ``Span.ref()``, or a list of them. Without it, the
        SDK declares a source only when the very same object was produced by an earlier span
        of this run (runtime identity); equal but distinct objects get no declared source."""
        jv, faithful = _jsonable(value)
        item: dict[str, Any] = {"role": role, "label": label, "value": jv, "faithful": faithful}
        declared = [
            s.ref() if isinstance(s, Span) else str(s)
            for s in (source if isinstance(source, list) else [source] if source else [])
        ]
        if not declared and self.run is not None:
            declared = self.run.references(value)
        if declared:
            item["sources"] = declared
        self.inputs.append(item)
        return self

    def output(self, value: Any, role: str = "output", label: str | None = None) -> Span:
        jv, faithful = _jsonable(value)
        self.outputs.append({"role": role, "label": label, "value": jv, "faithful": faithful})
        if self.run is not None:
            self.run.register_output(value, self.ref(len(self.outputs) - 1))
        return self

    def ref(self, slot: int = 0) -> str:
        """Reference to this span's ``slot``-th output, for ``Span.input(..., source=...)``."""
        return f"native.span:{self.span_id}/o{slot}"

    def link(self, other: Span | str, relation: str = "depends_on") -> Span:
        """Declare an extra dependency (multi-parent) on another span."""
        other_id = other.span_id if isinstance(other, Span) else str(other)
        self.links.append({"span_id": other_id, "relation": relation})
        return self

    def set(self, **attributes: Any) -> Span:
        self.attributes.update({k: _jsonable(v)[0] for k, v in attributes.items()})
        return self

    def usage(self, **resources: Any) -> Span:
        self.resources.update(resources)
        return self

    def facet(self, *names: str) -> Span:
        self.facets.extend(names)
        return self

    def fail(self, error: BaseException | str, status: str = "error") -> Span:
        self.status = status
        if isinstance(error, BaseException):
            self.error = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(traceback.format_exception(error))[-2000:],
            }
        else:
            self.error = {"type": "Error", "message": str(error)}
        return self


class Recorder(Sensor):
    """Records native spans into a sink."""

    sensor_type = "native"
    version = "1"

    def __init__(
        self,
        sink: ObservationSink | None = None,
        tenant_id: str = "default",
        system: str | None = None,
    ) -> None:
        super().__init__(sink or ListSink(), tenant_id)
        self.system = system

    # -- runs -----------------------------------------------------------------
    @contextmanager
    def run(
        self,
        name: str,
        run_id: str | None = None,
        *,
        subject_id: str | None = None,
        **attributes: Any,
    ) -> Iterator[RunHandle]:
        """``subject_id`` declares whose data the run processes; every observation of the run
        carries it, so the subject can later be erased by crypto-shredding (ADR-0011)."""
        handle = RunHandle(
            self,
            name,
            run_id or uuid.uuid4().hex,
            {k: _jsonable(v)[0] for k, v in attributes.items()},
        )
        handle.subject_id = subject_id
        token = _current_run.set(handle)
        span_token = _current_span.set(None)
        self.ctx.emit(
            "native.run.start",
            {"name": name, "attributes": handle.attributes, "system": self.system},
            declared_ids={"run_id": handle.run_id, "subject_id": handle.subject_id},
        )
        error: BaseException | None = None
        try:
            yield handle
        except BaseException as exc:
            error = exc
            raise
        finally:
            outcome = handle.outcome or ("error" if error else "ok")
            payload: dict[str, Any] = {"name": name, "outcome": outcome}
            if error is not None:
                payload["error"] = {"type": type(error).__name__, "message": str(error)}
            self.ctx.emit(
                "native.run.end",
                payload,
                declared_ids={"run_id": handle.run_id, "subject_id": handle.subject_id},
            )
            _current_span.reset(span_token)
            _current_run.reset(token)
            self.ctx.sink.flush()

    # -- spans ----------------------------------------------------------------
    @contextmanager
    def span(
        self,
        kind: str,
        operation: str,
        *,
        actor: str | None = None,
        object: str | None = None,
        inputs: dict[str, Any] | None = None,
        links: list[Span | str] | None = None,
        facets: list[str] | None = None,
        replayable: bool = False,
        **attributes: Any,
    ) -> Iterator[Span]:
        """Record a span. ``replayable`` marks an instrumented call whose result a replay
        controller can serve or substitute; only such spans get a call key."""
        parent = _current_span.get()
        run = _current_run.get()
        span = Span(self, kind, operation, actor=actor, object=object, run=run, parent=parent)
        if actor is None and parent is not None:
            # inherit actor only when the application nested this span inside the actor's span
            span.actor = parent.actor
            span.attributes["actor_basis"] = "inherited_from_parent_span"
        for role, value in (inputs or {}).items():
            span.input(value, role=role)
        for other in links or []:
            span.link(other)
        span.facets.extend(facets or [])
        tagged = _tags.get()
        if tagged is not None and tagged[0] == (parent.span_id if parent else None):
            span.set(**tagged[1])
        span.set(**attributes)
        if run is not None and replayable:
            span.call_key = f"{kind}|{operation}|{run.next_ordinal(kind, operation)}"
        self._emit_start(span)
        token = _current_span.set(span)
        try:
            yield span
        except BaseException as exc:
            if span.status == "ok":
                span.fail(exc)
            raise
        finally:
            _current_span.reset(token)
            self._emit_end(span)

    def _ids(self, span: Span) -> dict[str, str | None]:
        return {
            "span_id": span.span_id,
            "parent_span_id": span.parent.span_id if span.parent else None,
            "run_id": span.run.run_id if span.run else None,
            "subject_id": span.run.subject_id if span.run else None,
        }

    def _emit_start(self, span: Span) -> None:
        self.ctx.emit(
            "native.span.start",
            {
                "kind": span.kind,
                "operation": span.operation,
                "actor": span.actor,
                "object": span.object,
                "inputs": span.inputs,
                "links": span.links,
                "facets": span.facets,
                "attributes": span.attributes,
                "call_key": span.call_key,
            },
            declared_ids=self._ids(span),
        )

    def _emit_end(self, span: Span) -> None:
        self.ctx.emit(
            "native.span.end",
            {
                "status": span.status,
                "outputs": span.outputs,
                "links": span.links,
                "error": span.error,
                "resources": span.resources,
                "attributes": span.attributes,
                "facets": span.facets,
                "late_inputs": span.inputs,
                "duration_ms": (time.perf_counter() - span._t0) * 1000.0,
            },
            declared_ids=self._ids(span),
        )

    def event(
        self,
        kind: str,
        operation: str,
        *,
        actor: str | None = None,
        object: str | None = None,
        **payload: Any,
    ) -> None:
        """A point event (message, external input, failure) with no duration."""
        parent = _current_span.get()
        run = _current_run.get()
        body = {k: _jsonable(v)[0] for k, v in payload.items()}
        self.ctx.emit(
            "native.point",
            {
                "kind": kind,
                "operation": operation,
                "actor": actor or (parent.actor if parent else None),
                "object": object,
                "data": body,
            },
            declared_ids={
                "event_id": uuid.uuid4().hex[:16],
                "parent_span_id": parent.span_id if parent else None,
                "run_id": run.run_id if run else None,
                "subject_id": run.subject_id if run else None,
            },
        )


# ---------------------------------------------------------------------------
# Replay hooks: decorated calls consult the active replay controller, if any.
# ---------------------------------------------------------------------------

_replay: contextvars.ContextVar[Any] = contextvars.ContextVar("aw_replay", default=None)


@contextmanager
def tag(**attributes: Any) -> Iterator[None]:
    """Attach attributes to spans started *directly* inside this block (not their children)."""
    parent = _current_span.get()
    token = _tags.set(
        (parent.span_id if parent else None, {k: _jsonable(v)[0] for k, v in attributes.items()})
    )
    try:
        yield
    finally:
        _tags.reset(token)


def current_span() -> Span | None:
    return _current_span.get()


def current_run() -> RunHandle | None:
    return _current_run.get()


def set_replay_controller(controller: Any) -> contextvars.Token[Any]:
    return _replay.set(controller)


def reset_replay_controller(token: contextvars.Token[Any]) -> None:
    _replay.reset(token)


def instrumented_call(
    recorder_getter: Callable[[], Recorder],
    kind: str,
    operation: str,
    fn: Callable[..., Any],
    *,
    actor: str | None = None,
    object: str | None = None,
    input_role: str = "arguments",
    output_role: str = "result",
) -> Callable[..., Any]:
    """Wrap ``fn`` so each call is a span; replay controllers may serve its result."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            rec = recorder_getter()
            with rec.span(kind, operation, actor=actor, object=object, replayable=True) as span:
                span.input(_call_args(fn, args, kwargs), role=input_role)
                controller = _replay.get()
                if controller is not None:
                    served, value = controller.serve(span)
                    if served:
                        span.output(value, role=output_role)
                        return value
                result = await fn(*args, **kwargs)
                span.output(result, role=output_role)
                return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        rec = recorder_getter()
        with rec.span(kind, operation, actor=actor, object=object, replayable=True) as span:
            span.input(_call_args(fn, args, kwargs), role=input_role)
            controller = _replay.get()
            if controller is not None:
                served, value = controller.serve(span)
                if served:
                    span.output(value, role=output_role)
                    return value
            result = fn(*args, **kwargs)
            span.output(result, role=output_role)
            return result

    return wrapper


def _call_args(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except (TypeError, ValueError):
        return {"args": list(args), "kwargs": kwargs}


def utcnow() -> datetime:
    return datetime.now(UTC)
