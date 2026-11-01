"""The one enforcement boundary every integration calls.

``enforce_call`` (sync) and ``aenforce_call`` (async) run a tool through the pipeline in a
fixed order: inspect and decide, apply an approved mutation, execute once, inspect the
output, return the sanitized result. Integrations (``watch()``, framework adapters,
``protect_tool``, the MCP gateway) only translate their calling convention into a
``ToolAction`` and a callable; they never make security decisions themselves.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agentwatch.protect.model import ActionBlocked, ToolAction
from agentwatch.protect.pipeline import ProtectionPipeline

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
