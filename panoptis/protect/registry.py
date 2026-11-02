"""Tools registered while an agent runs (TO-016).

``watch(agent, protection=...)`` and ``protect_agent`` protect the tools that exist when
they are called; a tool added to the agent afterwards is not covered. Agents that add
tools at runtime should keep them in a :class:`ProtectedToolRegistry`: every way of
putting a tool in it (``reg[name] = t``, ``update``, ``setdefault``, ``register``) protects
it first, so no registration path skips enforcement. Wrapping is idempotent, so
registering the same tool twice never wraps it twice.
"""

from __future__ import annotations

from collections import UserDict
from collections.abc import Callable
from typing import Any

from panoptis.protect.enforce import protect_tool
from panoptis.protect.pipeline import ProtectionPipeline


class ProtectedToolRegistry(UserDict[str, Any]):
    def __init__(
        self,
        tools: dict[str, Any] | None = None,
        *,
        pipeline: ProtectionPipeline | None = None,
        **ident: Any,
    ) -> None:
        self.pipeline = pipeline
        self.ident = ident
        super().__init__()
        if tools:
            self.update(tools)

    def _protect(self, name: str, tool: Any) -> Any:
        if _is_framework_tool(tool):
            from panoptis.protect.integrations import protect_tools

            return protect_tools(tool, pipeline=self.pipeline, **self.ident)
        if callable(tool):
            return protect_tool(tool, tool=name, pipeline=self.pipeline, **self.ident)
        raise TypeError(f"tool {name!r} is not callable")

    def __setitem__(self, name: str, tool: Any) -> None:
        super().__setitem__(name, self._protect(name, tool))

    def register(self, fn: Callable[..., Any] | None = None, *, name: str | None = None) -> Any:
        """Decorator: ``@registry.register`` or ``@registry.register(name="bash")``."""

        def add(f: Callable[..., Any]) -> Any:
            key = name or getattr(f, "__name__", None) or type(f).__name__
            self[key] = f
            return self[key]

        return add if fn is None else add(fn)


def _is_framework_tool(tool: Any) -> bool:
    mod = type(tool).__module__
    return mod.startswith(("langchain", "smolagents", "autogen")) or (
        hasattr(tool, "args_schema") and hasattr(tool, "invoke")
    )
