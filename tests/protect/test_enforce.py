"""TO-002: the shared enforcement boundary."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentwatch.protect import ActionBlocked, ProtectionPipeline, ToolAction
from agentwatch.protect.enforce import aenforce_call, enforce_call


def action(tool: str = "bash", **arguments: Any) -> ToolAction:
    return ToolAction(agent_id="coder", session_id="s1", tool=tool, arguments=arguments)


class Recorder(ProtectionPipeline):
    """Records the order in which the pipeline's phases run."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.order: list[str] = []

    def evaluate(self, a: ToolAction, **kw: Any) -> Any:
        self.order.append("decide")
        return super().evaluate(a, **kw)

    def inspect_output(self, a: ToolAction, result: Any) -> Any:
        self.order.append("inspect_output")
        return super().inspect_output(a, result)


def test_order_is_decide_execute_once_inspect_return():
    p = Recorder()

    def tool(args: dict[str, Any]) -> str:
        p.order.append("execute")
        return "ok"

    assert enforce_call(p, action(command="ls"), tool) == "ok"
    assert p.order == ["decide", "execute", "inspect_output"]


def test_blocked_call_never_executes_and_raises_action_blocked():
    calls = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(ProtectionPipeline(), action(command="rm -rf /"), lambda a: calls.append(a))
    assert calls == []
    assert exc.value.decision.verdict.value == "block"


def test_tool_exceptions_propagate_unchanged():
    def tool(args: dict[str, Any]) -> str:
        raise KeyError("missing row")

    with pytest.raises(KeyError, match="missing row"):
        enforce_call(ProtectionPipeline(), action(command="ls"), tool)


def test_enforcement_failure_before_execution_blocks():
    class Broken(ProtectionPipeline):
        def evaluate(self, a: ToolAction, **kw: Any) -> Any:
            raise RuntimeError("policy store down")

    calls = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(Broken(), action(command="ls"), lambda a: calls.append(a))
    assert calls == []
    f = exc.value.decision.findings[0]
    assert f.code == "ENFORCEMENT_ERROR" and f.evidence["executed"] is False


def test_failure_after_execution_withholds_the_result_and_says_it_ran():
    class BrokenAfter(ProtectionPipeline):
        def _finish(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("breaker store down")

    calls = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(
            BrokenAfter(), action(command="ls"), lambda a: calls.append(a) or "secret result"
        )
    assert len(calls) == 1
    f = exc.value.decision.findings[0]
    assert f.code == "POST_EXECUTION_ERROR" and f.evidence["executed"] is True


def test_async_path_matches_the_sync_contract():
    p = Recorder()

    async def tool(args: dict[str, Any]) -> str:
        p.order.append("execute")
        return "ok"

    assert asyncio.run(aenforce_call(p, action(command="ls"), tool)) == "ok"
    assert p.order == ["decide", "execute", "inspect_output"]

    calls = []

    async def counted(args: dict[str, Any]) -> None:
        calls.append(args)

    with pytest.raises(ActionBlocked):
        asyncio.run(aenforce_call(ProtectionPipeline(), action(command="rm -rf /"), counted))
    assert calls == []
