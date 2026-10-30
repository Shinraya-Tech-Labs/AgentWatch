"""LangChain / LangGraph callback sensor.

Unlike the v0.2 adapter, this handler preserves ``run_id`` and ``parent_run_id`` on
every callback so the execution structure LangChain declares can be reconstructed.
It records raw callback payloads only; pairing and mapping happen in the normalizer.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from panoptis.evidence.canonical import to_jsonable
from panoptis.sensors.base import ObservationSink, Sensor

logger = logging.getLogger(__name__)

try:  # inherit from the real base class when LangChain is installed
    from langchain_core.callbacks import BaseCallbackHandler as _Base

    LANGCHAIN_AVAILABLE = True
except ImportError:  # optional dependency
    _Base = object  # type: ignore[assignment,misc]
    LANGCHAIN_AVAILABLE = False

_INSTALL_HINT = (
    "langchain-core is required to attach AgentWatchLangChainSensor to a real LangChain or "
    'LangGraph run. Install it with: pip install "panoptis[langchain]"'
)


def require_langchain() -> None:
    """Raise a clear ImportError when ``langchain-core`` is not installed.

    Importing this module and constructing the sensor never need LangChain (the handler is
    duck-typed); call this when you are about to hand the sensor to a real LangChain run.
    """
    if not LANGCHAIN_AVAILABLE:
        raise ImportError(_INSTALL_HINT)


def _guarded(fn: Callable[..., None]) -> Callable[..., None]:
    """Callbacks run inside the user's chain: swallow and count anything that goes wrong."""

    @functools.wraps(fn)
    def wrapper(self: AgentWatchLangChainSensor, *args: Any, **kwargs: Any) -> None:
        try:
            fn(self, *args, **kwargs)
        except Exception:
            self.ctx.errors += 1
            logger.debug("langchain callback %s failed", fn.__name__, exc_info=True)

    return wrapper


def _safe(value: Any) -> Any:
    try:
        return to_jsonable(value)
    except (TypeError, ValueError):
        return repr(value)


def _usage_from(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        return obj
    return None


def _token_usage(response: Any) -> dict[str, int] | None:
    """Best-effort token counts from ``llm_output`` or message ``usage_metadata``."""
    llm_output = getattr(response, "llm_output", None)
    raw = None
    if isinstance(llm_output, dict):
        raw = _usage_from(llm_output.get("token_usage") or llm_output.get("usage"))
    if raw is None:
        total_in = total_out = 0
        found = False
        for batch in getattr(response, "generations", None) or []:
            for g in batch:
                meta = _usage_from(getattr(getattr(g, "message", None), "usage_metadata", None))
                if meta:
                    found = True
                    total_in += int(meta.get("input_tokens") or 0)
                    total_out += int(meta.get("output_tokens") or 0)
        if found:
            return {"input_tokens": total_in, "output_tokens": total_out}
        return None
    tin = raw.get("prompt_tokens", raw.get("input_tokens"))
    tout = raw.get("completion_tokens", raw.get("output_tokens"))
    out = {}
    if isinstance(tin, int):
        out["input_tokens"] = tin
    if isinstance(tout, int):
        out["output_tokens"] = tout
    return out or None


def _generations(response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    gens = getattr(response, "generations", None)
    if gens:
        texts = []
        for batch in gens:
            for g in batch:
                text = getattr(g, "text", None)
                if not text and getattr(g, "message", None) is not None:
                    text = getattr(g.message, "content", None)
                texts.append(_safe(text))
        out["generations"] = texts
    llm_output = getattr(response, "llm_output", None)
    if llm_output:
        out["llm_output"] = _safe(llm_output)
    usage = _token_usage(response)
    if usage:
        out["token_usage"] = usage
    return out


def _documents(docs: Any) -> list[Any]:
    items = []
    for d in docs or []:
        items.append(
            {
                "page_content": getattr(d, "page_content", _safe(d)),
                "metadata": _safe(getattr(d, "metadata", {})),
            }
        )
    return items


class AgentWatchLangChainSensor(Sensor, _Base):  # type: ignore[misc,valid-type]
    sensor_type = "langchain"
    version = "2"  # 2: declares root_run_id
    raise_error = False
    run_inline = True

    def __init__(self, sink: ObservationSink, tenant_id: str = "default") -> None:
        Sensor.__init__(self, sink, tenant_id)
        # run_id -> root run_id, built only from parent_run_id values LangChain declared
        self._root_of: dict[str, str] = {}
        # run_id -> monotonic start time, for per-run duration
        self._started: dict[str, float] = {}
        self._lock = threading.Lock()

    def _emit(
        self,
        callback: str,
        payload: dict[str, Any],
        run_id: Any,
        parent_run_id: Any,
        **extra_ids: Any,
    ) -> None:
        terminal = callback.endswith(("_end", "_error"))
        ids = {
            "run_id": str(run_id) if run_id else None,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }
        ids.update({k: str(v) for k, v in extra_ids.items() if v})
        # root of the declared parent chain; omitted when the chain is not fully known
        rid, pid = ids["run_id"], ids["parent_run_id"]
        if rid:
            with self._lock:
                root = rid if pid is None else self._root_of.get(pid)
                if root is not None:
                    ids["root_run_id"] = root
                if terminal:
                    # a run's state is released on its terminal callback so long-lived
                    # sensors do not grow without bound
                    self._root_of.pop(rid, None)
                    t0 = self._started.pop(rid, None)
                    if t0 is not None:
                        payload["duration_ms"] = round((time.monotonic() - t0) * 1000, 3)
                elif callback.endswith("_start"):
                    if root is not None:
                        self._root_of[rid] = root
                    self._started[rid] = time.monotonic()
        self.ctx.emit(f"langchain.{callback}", payload, declared_ids=ids)

    # LLMs
    @_guarded
    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kw: Any,
    ) -> None:
        self._emit(
            "llm_start",
            {
                "serialized": _safe(serialized),
                "prompts": _safe(prompts),
                "tags": _safe(tags),
                "metadata": _safe(metadata),
                "invocation_params": _safe(kw.get("invocation_params")),
            },
            run_id,
            parent_run_id,
        )

    @_guarded
    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kw: Any,
    ) -> None:
        msgs = [
            [
                {"type": getattr(m, "type", None), "content": _safe(getattr(m, "content", m))}
                for m in batch
            ]
            for batch in messages
        ]
        self._emit(
            "chat_model_start",
            {
                "serialized": _safe(serialized),
                "messages": msgs,
                "tags": _safe(tags),
                "metadata": _safe(metadata),
                "invocation_params": _safe(kw.get("invocation_params")),
            },
            run_id,
            parent_run_id,
        )

    @_guarded
    def on_llm_end(
        self, response: Any, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit("llm_end", _generations(response), run_id, parent_run_id)

    @_guarded
    def on_llm_error(
        self, error: BaseException, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "llm_error",
            {"error": {"type": type(error).__name__, "message": str(error)}},
            run_id,
            parent_run_id,
        )

    # chains / graphs
    @_guarded
    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kw: Any,
    ) -> None:
        self._emit(
            "chain_start",
            {
                "serialized": _safe(serialized),
                "inputs": _safe(inputs),
                "name": kw.get("name"),
                "tags": _safe(tags),
                "metadata": _safe(metadata),
            },
            run_id,
            parent_run_id,
        )

    @_guarded
    def on_chain_end(
        self, outputs: Any, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit("chain_end", {"outputs": _safe(outputs)}, run_id, parent_run_id)

    @_guarded
    def on_chain_error(
        self, error: BaseException, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "chain_error",
            {"error": {"type": type(error).__name__, "message": str(error)}},
            run_id,
            parent_run_id,
        )

    # tools
    @_guarded
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        inputs: Any = None,
        **kw: Any,
    ) -> None:
        self._emit(
            "tool_start",
            {
                "serialized": _safe(serialized),
                "input_str": _safe(input_str),
                "inputs": _safe(inputs),
                "tags": _safe(tags),
                "metadata": _safe(metadata),
            },
            run_id,
            parent_run_id,
            tool_call_id=kw.get("tool_call_id"),
        )

    @_guarded
    def on_tool_end(
        self, output: Any, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "tool_end", {"output": _safe(getattr(output, "content", output))}, run_id, parent_run_id
        )

    @_guarded
    def on_tool_error(
        self, error: BaseException, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "tool_error",
            {"error": {"type": type(error).__name__, "message": str(error)}},
            run_id,
            parent_run_id,
        )

    # retrievers
    @_guarded
    def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kw: Any,
    ) -> None:
        self._emit(
            "retriever_start",
            {
                "serialized": _safe(serialized),
                "query": query,
                "tags": _safe(tags),
                "metadata": _safe(metadata),
            },
            run_id,
            parent_run_id,
        )

    @_guarded
    def on_retriever_end(
        self, documents: Any, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit("retriever_end", {"documents": _documents(documents)}, run_id, parent_run_id)

    @_guarded
    def on_retriever_error(
        self, error: BaseException, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "retriever_error",
            {"error": {"type": type(error).__name__, "message": str(error)}},
            run_id,
            parent_run_id,
        )

    # agents
    @_guarded
    def on_agent_action(
        self, action: Any, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "agent_action",
            {
                "tool": getattr(action, "tool", None),
                "tool_input": _safe(getattr(action, "tool_input", None)),
                "log": getattr(action, "log", None),
            },
            run_id,
            parent_run_id,
        )

    @_guarded
    def on_agent_finish(
        self, finish: Any, *, run_id: Any, parent_run_id: Any = None, **kw: Any
    ) -> None:
        self._emit(
            "agent_finish",
            {
                "return_values": _safe(getattr(finish, "return_values", None)),
                "log": getattr(finish, "log", None),
            },
            run_id,
            parent_run_id,
        )
