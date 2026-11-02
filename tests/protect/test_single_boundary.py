"""TO-002: every enforcing integration goes through the one shared pipeline.

An architectural check over the support matrix, plus behavioural proof for the path that
used to decide on its own: ``watch(agent)`` without ``protection=`` now runs its
SafetyEngine gate as the threat stage of a :class:`ProtectionPipeline`, so the kill switch
and the fail-closed/execute-once flow apply to it as well.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect

import pytest

from panoptis.core.watcher import AgentWatchBlockedError, watch
from panoptis.protect import ControlState
from panoptis.protect.pipeline import ProtectionPipeline
from panoptis.protect.support import MATRIX, PRE

# the calls through which code reaches the shared pipeline's decision flow
_BOUNDARY = ("enforce_call(", "aenforce_call(", ".evaluate(", "_legacy_pipeline(")


@pytest.mark.parametrize(
    "entry", [s for s in MATRIX if s.stage == PRE], ids=lambda s: s.integration
)
def test_every_enforcing_integration_calls_the_shared_pipeline(entry):
    module_name, attrs = entry.entry_point, []
    while True:
        try:
            obj = importlib.import_module(module_name)
            break
        except ModuleNotFoundError:
            module_name, attr = module_name.rsplit(".", 1)
            attrs.insert(0, attr)
    for attr in attrs:
        obj = getattr(obj, attr)
    module = inspect.getmodule(obj)
    source = inspect.getsource(module)
    assert any(b in source for b in _BOUNDARY), (
        f"{entry.integration}: {module_name} does not call the shared pipeline"
    )


class Agent:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def run(self, command: str) -> str:
        self.ran.append(command)
        return f"ok: {command}"


class AsyncAgent:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run(self, command: str) -> str:
        self.ran.append(command)
        return f"ok: {command}"


def test_legacy_watch_decides_inside_the_pipeline(monkeypatch):
    seen = []
    real = ProtectionPipeline.evaluate

    def spy(self, action, **kw):
        seen.append((action.tool, action.source))
        return real(self, action, **kw)

    monkeypatch.setattr(ProtectionPipeline, "evaluate", spy)
    agent = watch(Agent())
    assert agent.run("echo hi") == "ok: echo hi"
    with pytest.raises(AgentWatchBlockedError, match="blocked by safety engine"):
        agent.run("rm -rf /")
    assert agent.ran == ["echo hi"]
    assert [t for t, _ in seen] == ["run", "run"] and seen[0][1].endswith(":legacy-gate")


def test_legacy_watch_now_honours_the_kill_switch():
    agent = watch(Agent())
    assert agent.run("echo before") == "ok: echo before"
    ControlState().trip("incident drill")
    with pytest.raises(AgentWatchBlockedError):
        agent.run("echo after")
    assert agent.ran == ["echo before"]


def test_legacy_async_watch_decides_inside_the_pipeline(monkeypatch):
    seen = []
    real = ProtectionPipeline.evaluate

    def spy(self, action, **kw):
        seen.append(action.tool)
        return real(self, action, **kw)

    monkeypatch.setattr(ProtectionPipeline, "evaluate", spy)
    agent = watch(AsyncAgent())

    async def go():
        assert await agent.run("echo hi") == "ok: echo hi"
        with pytest.raises(AgentWatchBlockedError, match="blocked by safety engine"):
            await agent.run("rm -rf /")

    asyncio.run(go())
    assert agent.ran == ["echo hi"] and seen == ["run", "run"]


def test_legacy_gate_executes_the_tool_once():
    agent = watch(Agent())
    for i in range(3):
        agent.run(f"echo {i}")
    assert agent.ran == ["echo 0", "echo 1", "echo 2"]
