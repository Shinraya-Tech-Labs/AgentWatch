"""The one enforcement boundary every integration calls.

``enforce_call`` (sync) and ``aenforce_call`` (async) run a tool through the pipeline in a
fixed order: inspect and decide, apply an approved mutation, execute once, inspect the
output, return the sanitized result. Integrations (``watch()``, framework adapters,
``protect_tool``, the MCP gateway) only translate their calling convention into a
``ToolAction`` and a callable; they never make security decisions themselves.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import logging
import threading
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from panoptis.protect.model import ActionBlocked, ToolAction
from panoptis.protect.pipeline import ProtectionPipeline

logger = logging.getLogger(__name__)


def enforce_call(
    pipeline: ProtectionPipeline, action: ToolAction, invoke: Callable[[dict[str, Any]], Any]
) -> Any:
    """Decide, then run ``invoke(arguments)`` at most once and return the sanitized result.

    The tool's own exceptions propagate unchanged; a failure inside enforcement raises
    :class:`ActionBlocked` (fail closed).
    """

    state = {"executed": False}

    def run(arguments: dict[str, Any]) -> Any:
        state["executed"] = True
        try:
            return invoke(arguments)
        except Exception as exc:
            raise _ToolError(exc) from exc

    try:
        return pipeline.execute(action, fn=_unused, call=run)
    except ActionBlocked:
        raise
    except _ToolError as err:
        raise err.original from err.original.__cause__
    except Exception as exc:  # noqa: BLE001 - enforcement itself failed
        raise ActionBlocked(
            pipeline.failure_decision(action, exc, executed=state["executed"])
        ) from exc


async def aenforce_call(
    pipeline: ProtectionPipeline,
    action: ToolAction,
    invoke: Callable[[dict[str, Any]], Awaitable[Any]],
) -> Any:
    state = {"executed": False}

    async def run(arguments: dict[str, Any]) -> Any:
        state["executed"] = True
        try:
            return await invoke(arguments)
        except Exception as exc:
            raise _ToolError(exc) from exc

    try:
        return await pipeline.aexecute(action, fn=_unused, call=run)
    except ActionBlocked:
        raise
    except _ToolError as err:
        raise err.original from err.original.__cause__
    except Exception as exc:  # noqa: BLE001
        raise ActionBlocked(
            pipeline.failure_decision(action, exc, executed=state["executed"])
        ) from exc


class _ToolError(Exception):
    """Carries an exception raised by the tool itself through the enforcement layer."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def _unused(**_: Any) -> Any:  # pipeline.execute requires fn; ``call`` is what runs
    raise RuntimeError("unreachable")


# ── identity context ───────────────────────────────────────────────────────────────

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "panoptis_protect_context", default=None
)


@contextlib.contextmanager
def protection_context(
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    objective: str | None = None,
    tenant_id: str | None = None,
    trace_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Set who is acting for the calls made inside the block (nests; inner values win)."""
    outer = _context.get() or {}
    inner = {
        k: v
        for k, v in {
            "agent_id": agent_id,
            "session_id": session_id,
            "objective": objective,
            "tenant_id": tenant_id,
            "trace_id": trace_id,
        }.items()
        if v is not None
    }
    token = _context.set({**outer, **inner})
    try:
        yield _context.get() or {}
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get() or {})


# ── default pipeline ───────────────────────────────────────────────────────────────

_default: ProtectionPipeline | None = None
_default_lock = threading.Lock()


def set_default_pipeline(pipeline: ProtectionPipeline | None) -> None:
    global _default
    with _default_lock:
        _default = pipeline


def get_default_pipeline() -> ProtectionPipeline:
    """The process-wide pipeline (created with defaults on first use)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ProtectionPipeline()
        return _default


# ── protect_tool ───────────────────────────────────────────────────────────────────

_MARK = "_panoptis_protected"


def protect_tool(
    fn: Callable[..., Any] | None = None,
    *,
    tool: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    objective: str | None = None,
    tenant_id: str | None = None,
    pipeline: ProtectionPipeline | None = None,
    source: str = "sdk",
) -> Any:
    """Route every call of a tool through the enforcement boundary.

    Usable as ``protect_tool(fn)``, ``@protect_tool`` or ``@protect_tool(tool="bash", ...)``.
    Identity comes from the arguments given here, else from :func:`protection_context`,
    else defaults (``default-agent`` / ``default-session``). Wrapping an already protected
    tool returns it unchanged.
    """
    if fn is None:
        return lambda f: protect_tool(
            f,
            tool=tool,
            agent_id=agent_id,
            session_id=session_id,
            objective=objective,
            tenant_id=tenant_id,
            pipeline=pipeline,
            source=source,
        )
    if getattr(fn, _MARK, False):
        return fn
    name = tool or getattr(fn, "__name__", None) or type(fn).__name__
    try:
        sig: inspect.Signature | None = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None

    def build(
        args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[ToolAction, Callable[[dict[str, Any]], tuple[tuple[Any, ...], dict[str, Any]]]]:
        named, rebuild = bind_arguments(sig, args, kwargs)
        ctx = current_context()
        given_agent = agent_id or ctx.get("agent_id")
        given_session = session_id or ctx.get("session_id")
        action = ToolAction(
            agent_id=agent_id or ctx.get("agent_id") or "default-agent",
            session_id=session_id or ctx.get("session_id") or "default-session",
            tool=name,
            arguments=named,
            objective=objective or ctx.get("objective"),
            tenant_id=tenant_id or ctx.get("tenant_id") or "default",
            trace_id=ctx.get("trace_id"),
            source=source,
            defaulted_identity=not (given_agent and given_session),
        )
        return action, rebuild

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            action, rebuild = build(args, kwargs)

            async def invoke(arguments: dict[str, Any]) -> Any:
                a, k = rebuild(arguments)
                return await fn(*a, **k)

            return await aenforce_call(pipeline or get_default_pipeline(), action, invoke)

        setattr(awrapper, _MARK, True)
        return awrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        action, rebuild = build(args, kwargs)

        def invoke(arguments: dict[str, Any]) -> Any:
            a, k = rebuild(arguments)
            return fn(*a, **k)

        return enforce_call(pipeline or get_default_pipeline(), action, invoke)

    setattr(wrapper, _MARK, True)
    return wrapper


def bind_arguments(
    sig: inspect.Signature | None, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], Callable[[dict[str, Any]], tuple[tuple[Any, ...], dict[str, Any]]]]:
    """Name a call's arguments; return them and how to rebuild ``(args, kwargs)`` from a
    (possibly mutated) mapping, preserving positional-only and variadic parameters."""
    if sig is None:
        named = {f"arg{i}": v for i, v in enumerate(args)} | dict(kwargs)

        def rebuild_unsigned(m: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
            return tuple(m[f"arg{i}"] for i in range(len(args))), {k: m[k] for k in kwargs}

        return named, rebuild_unsigned
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    named = dict(bound.arguments)
    named.pop("self", None)

    def rebuild(m: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
        new = sig.bind_partial()
        values = {**bound.arguments, **{k: v for k, v in m.items() if k in bound.arguments}}
        new.arguments.update(values)
        return new.args, new.kwargs

    return named, rebuild
