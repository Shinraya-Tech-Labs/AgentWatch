"""TO-002 / TO-016: the public protect_tool() API."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentwatch.protect import ActionBlocked, ProtectionPipeline
from agentwatch.protect.enforce import protect_tool, protection_context


@pytest.fixture
def pipeline() -> ProtectionPipeline:
    return ProtectionPipeline()


def test_decorator_forms_and_metadata(pipeline):
    calls: list[str] = []

    @protect_tool(pipeline=pipeline)
    def bash(command: str) -> str:
        """Run a shell command."""
        calls.append(command)
        return "ok"

    def plain(command: str) -> str:
        calls.append(command)
        return "ok"

    wrapped = protect_tool(plain, tool="bash", pipeline=pipeline)
    assert bash("ls") == "ok" and wrapped("ls") == "ok"
    assert bash.__name__ == "bash" and bash.__doc__ == "Run a shell command."
    assert calls == ["ls", "ls"]


def test_blocked_call_never_runs(pipeline):
    calls: list[str] = []

    @protect_tool(tool="bash", pipeline=pipeline)
    def bash(command: str) -> str:
        calls.append(command)
        return "ok"

    with pytest.raises(ActionBlocked):
        bash("rm -rf /")
    assert calls == []


def test_async_tool(pipeline):
    calls: list[str] = []

    @protect_tool(tool="bash", pipeline=pipeline)
    async def bash(command: str) -> str:
        calls.append(command)
        return "ok"

    assert asyncio.run(bash("ls")) == "ok"
    with pytest.raises(ActionBlocked):
        asyncio.run(bash("rm -rf /"))
    assert calls == ["ls"]


def test_mutation_maps_back_to_positional_and_keyword_arguments(pipeline):
    seen: list[tuple[Any, ...]] = []
    secret = "ghp_" + "z9y8x7w6v5" * 4

    @protect_tool(pipeline=pipeline)
    def write_file(path: str, /, content: str, *, mode: str = "w") -> str:
        seen.append((path, content, mode))
        return "written"

    write_file("notes.txt", f"key {secret}", mode="a")
    assert seen == [("notes.txt", seen[0][1], "a")]
    assert secret not in seen[0][1]


def test_wrapping_twice_is_a_no_op(pipeline):
    calls: list[str] = []

    def bash(command: str) -> str:
        calls.append(command)
        return "ok"

    once = protect_tool(bash, pipeline=pipeline)
    twice = protect_tool(once, pipeline=pipeline)
    assert twice is once
    twice("ls")
    assert calls == ["ls"]


def test_identity_comes_from_the_protection_context(pipeline):
    @protect_tool(tool="bash", pipeline=pipeline)
    def bash(command: str) -> str:
        return "ok"

    with protection_context(
        agent_id="coder", session_id="run-42", tenant_id="acme", objective="list files"
    ):
        bash("ls")
    s = pipeline.sessions["acme/run-42"]
    assert s.agent_id == "coder" and s.objective == "list files"


def test_tool_exceptions_pass_through(pipeline):
    @protect_tool(pipeline=pipeline)
    def read_file(path: str) -> str:
        raise FileNotFoundError(path)

    with pytest.raises(FileNotFoundError):
        read_file("missing.txt")


def test_varargs_are_preserved(pipeline):
    @protect_tool(pipeline=pipeline)
    def run(*parts: str, sep: str = " ") -> str:
        return sep.join(parts)

    assert run("echo", "hi", sep="-") == "echo-hi"


def test_require_context_blocks_calls_without_identity():
    from agentwatch.protect import ProtectionConfig

    strict = ProtectionPipeline(config=ProtectionConfig(require_context=True))
    calls: list[str] = []

    @protect_tool(tool="bash", pipeline=strict)
    def bash(command: str) -> str:
        calls.append(command)
        return "ok"

    with pytest.raises(ActionBlocked) as exc:
        bash("ls")
    assert exc.value.decision.findings[0].code == "MISSING_CONTEXT"
    with protection_context(agent_id="coder", session_id="s1"):
        assert bash("ls") == "ok"
    assert calls == ["ls"]


def test_default_mode_still_allows_calls_without_context(pipeline):
    @protect_tool(tool="bash", pipeline=pipeline)
    def bash(command: str) -> str:
        return "ok"

    assert bash("ls") == "ok"
    assert "default/default-session" in pipeline.sessions
