# pylint: disable=unused-argument,protected-access,broad-exception-caught,too-many-arguments,too-many-instance-attributes,too-few-public-methods,too-many-return-statements,no-else-return,missing-function-docstring,keyword-arg-before-vararg,too-many-positional-arguments
"""
AgentWatch Claude Code Adapter
Intercepts Claude Code agent execution, normalizes events to the
Universal Event Schema, and enforces safety policies.

Supports:
  - Subprocess wrapping (wrapping `claude` CLI)
  - SDK-level hooks
  - File system mutation tracking
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    AgentSession,
    EventType,
    ExecutionStatus,
    TokenUsage,
    ToolCallData,
    ToolResultData,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Claude Code event parser
# ─────────────────────────────────────────────


class ClaudeCodeEventParser:
    """
    Parses Claude Code's JSON streaming output format into AgentWatch events.
    Claude Code emits newline-delimited JSON when run with --output-format stream-json
    """

    # Claude Code tool names to canonical names
    TOOL_MAP: dict[str, str] = {
        "Bash": "bash",
        "Read": "file_read",
        "Write": "file_write",
        "Edit": "file_edit",
        "MultiEdit": "file_multi_edit",
        "Glob": "fs_glob",
        "Grep": "fs_grep",
        "LS": "fs_ls",
        "WebSearch": "web_search",
        "WebFetch": "web_fetch",
        "TodoRead": "todo_read",
        "TodoWrite": "todo_write",
        "Task": "subagent_task",
    }

    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        self._step = 0

    def parse_line(self, raw_line: str) -> AgentEvent | None:
        """Parse one JSON line from Claude Code output."""
        raw_line = raw_line.strip()
        if not raw_line:
            return None
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON output from Claude Code")
            return None

        return self._dispatch(data)

    def _dispatch(self, data: dict[str, Any]) -> AgentEvent | None:
        msg_type = data.get("type", "")

        if msg_type == "system":
            return self._parse_system(data)
        elif msg_type == "assistant":
            return self._parse_assistant(data)
        elif msg_type == "tool_use":
            return self._parse_tool_use(data)
        elif msg_type == "tool_result":
            return self._parse_tool_result(data)
        elif msg_type == "result":
            return self._parse_result(data)
        elif msg_type == "error":
            return self._parse_error(data)
        return None

    def _base_event(self, event_type: EventType) -> AgentEvent:
        self._step += 1
        return AgentEvent(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name="claude-code",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=event_type,
            step_number=self._step,
        )

    def _parse_system(self, data: dict[str, Any]) -> AgentEvent:
        event = self._base_event(EventType.AGENT_START)
        subtype = data.get("subtype", "")
        if subtype == "init":
            session_info = data.get("session_id", "")
            event.metadata["claude_session_id"] = session_info
            tools = data.get("tools", [])
            event.metadata["available_tools"] = tools
        return event

    def _parse_assistant(self, data: dict[str, Any]) -> AgentEvent:
        event = self._base_event(EventType.PLANNER_OUTPUT)
        message = data.get("message", {})
        content = message.get("content", [])

        # Extract text blocks as observable planner artifact
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if text_parts:
            full_text = "\n".join(text_parts)
            event.planner_output_preview = full_text[:500]

        # Token usage
        usage = message.get("usage", {})
        if usage:
            event.token_usage = TokenUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            )

        return event

    def _parse_tool_use(self, data: dict[str, Any]) -> AgentEvent:
        event = self._base_event(EventType.TOOL_CALL)
        tool_name = data.get("name", "unknown")
        tool_id = data.get("id", str(uuid.uuid4()))
        args = data.get("input", {})

        canonical = self.TOOL_MAP.get(tool_name, tool_name.lower())

        # Extract raw command for bash calls
        raw_command = None
        affected_resources: list[str] = []

        if tool_name == "Bash":
            raw_command = args.get("command", "")
        elif tool_name in ("Read", "Write", "Edit", "MultiEdit"):
            path = args.get("file_path") or args.get("path", "")
            if path:
                affected_resources.append(path)
        elif tool_name == "Glob":
            affected_resources.append(args.get("pattern", "*"))

        event.tool_call = ToolCallData(
            tool_name=canonical,
            tool_id=tool_id,
            arguments=args,
            raw_command=raw_command,
            affected_resources=affected_resources,
        )
        event.metadata["claude_tool_name"] = tool_name

        return event

    def _parse_tool_result(self, data: dict[str, Any]) -> AgentEvent:
        event = self._base_event(EventType.TOOL_RESULT)
        tool_use_id = data.get("tool_use_id", "")
        content = data.get("content", "")
        is_error = data.get("is_error", False)

        output_str = None
        if isinstance(content, str):
            output_str = content
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            output_str = "\n".join(parts)

        event.tool_result = ToolResultData(
            tool_name="unknown",  # Will be resolved by replay engine
            tool_id=tool_use_id,
            output=output_str[:2000] if output_str else None,
            error=output_str if is_error else None,
        )
        return event

    def _parse_result(self, data: dict[str, Any]) -> AgentEvent:
        event = self._base_event(EventType.AGENT_END)
        subtype = data.get("subtype", "")

        if subtype == "success":
            event.status = ExecutionStatus.SUCCESS
        elif subtype == "error":
            event.status = ExecutionStatus.FAILURE

        result = data.get("result", "")
        if result:
            event.metadata["final_result"] = result[:1000]

        usage = data.get("usage", {})
        if usage:
            event.token_usage = TokenUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            )

        return event

    def _parse_error(self, data: dict[str, Any]) -> AgentEvent:
        event = self._base_event(EventType.AGENT_ERROR)
        event.status = ExecutionStatus.FAILURE
        event.metadata["error"] = data.get("error", "unknown error")
        return event


# ─────────────────────────────────────────────
# Claude Code Adapter
# ─────────────────────────────────────────────


class ClaudeCodeAdapter:
    """
    Wraps Claude Code CLI subprocess execution with full AgentWatch
    observability, safety enforcement, and replay capture.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        safety_engine: SafetyEngine | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        working_dir: str | None = None,
    ):
        self._bus = event_bus or get_event_bus()
        self._safety = safety_engine or SafetyEngine()
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"claude-code-{uuid.uuid4().hex[:8]}"
        self._working_dir = working_dir or os.getcwd()
        self._session: AgentSession | None = None
        self._events: list[AgentEvent] = []
        self._start_time: float = 0.0

    async def run(
        self,
        prompt: str,
        *,
        model: str = "claude-opus-4-5",
        max_turns: int = 50,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        extra_args: list[str] | None = None,
        env_override: dict[str, str] | None = None,
    ) -> AgentSession:
        """
        Execute Claude Code with the given prompt and return the session.
        Streams output and processes each event through safety + bus.
        """
        self._session = AgentSession(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name="claude-code",
            framework=AgentFramework.CLAUDE_CODE,
            goal=prompt[:500],
        )

        # Emit session start
        start_event = AgentEvent(
            session_id=self.session_id,
            agent_id=self.agent_id,
            agent_name="claude-code",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.SESSION_START,
            goal=prompt[:500],
            metadata={"model": model, "max_turns": max_turns},
        )
        await self._emit(start_event)

        # Build command
        cmd = self._build_command(
            prompt=prompt,
            model=model,
            max_turns=max_turns,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            extra_args=extra_args,
        )

        logger.info("Starting Claude Code: %s", " ".join(cmd[:5]) + " ...")
        self._start_time = time.monotonic()

        try:
            await self._run_subprocess(cmd, env_override=env_override)
        except Exception as exc:
            logger.error("Claude Code subprocess failed: %s", exc)
            self._session.status = ExecutionStatus.FAILURE
            error_event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.AGENT_ERROR,
                status=ExecutionStatus.FAILURE,
                metadata={"error": str(exc)},
            )
            await self._emit(error_event)
        finally:
            self._session.ended_at = datetime.now(UTC)
            if self._session.status == ExecutionStatus.RUNNING:
                self._session.status = ExecutionStatus.SUCCESS
            self._session.total_events = len(self._events)

            end_event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.SESSION_END,
                status=self._session.status,
                metadata={"total_events": len(self._events)},
            )
            await self._emit(end_event)

        return self._session

    async def _run_subprocess(
        self,
        cmd: list[str],
        env_override: dict[str, str] | None = None,
    ) -> None:
        env = dict(os.environ)
        if env_override:
            env.update(env_override)

        parser = ClaudeCodeEventParser(
            session_id=self.session_id,
            agent_id=self.agent_id,
        )

        # Windows npm installs a .cmd shim, which CreateProcess cannot find by
        # bare name; resolve through PATHEXT before spawning.
        resolved = shutil.which(cmd[0])
        if resolved is None:
            raise RuntimeError(f"'{cmd[0]}' CLI not found on PATH — install it or add it to PATH.")

        process = await asyncio.create_subprocess_exec(
            resolved,
            *cmd[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
            env=env,
        )

        if process.stdout is None:  # pragma: no cover - defensive
            raise RuntimeError("Claude Code subprocess produced no stdout stream")

        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            event = parser.parse_line(line)
            if event is None:
                continue

            # The CLI runs a tool as soon as it emits the call, so by the time this
            # event is read the tool has already run. A policy violation found here is
            # recorded as a detection, never as a block; enforcement is the PreToolUse
            # hook (panoptis.protect.claude_hook).
            if event.event_type == EventType.TOOL_CALL:
                status_before = event.status
                event = await self._safety.check_event(event)

                if event.is_blocked:
                    event.status = status_before
                    if event.safety is not None:
                        event.safety = event.safety.model_copy(update={"blocked": False})
                    event.metadata = {
                        **(event.metadata or {}),
                        "policy_violation": True,
                        "enforcement": "observation_only",
                    }
                    await self._emit(event)
                    await self._emit(
                        AgentEvent(
                            session_id=self.session_id,
                            agent_id=self.agent_id,
                            framework=AgentFramework.CLAUDE_CODE,
                            event_type=EventType.SAFETY_CHECK,
                            status=ExecutionStatus.FAILURE,
                            safety=event.safety,
                            metadata={
                                "violating_tool": event.tool_call.tool_name
                                if event.tool_call
                                else "unknown",
                                "detected_after_execution": True,
                                "enforcement": "observation_only",
                            },
                        )
                    )
                    continue

            await self._emit(event)

        await process.wait()

        if process.returncode not in (0, None):
            stderr = b""
            if process.stderr:
                stderr = await process.stderr.read()
            raise RuntimeError(
                f"Claude Code exited with code {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )

    def _build_command(
        self,
        prompt: str,
        model: str,
        max_turns: int,
        allowed_tools: list[str] | None,
        disallowed_tools: list[str] | None,
        extra_args: list[str] | None,
    ) -> list[str]:
        cmd = [
            "claude",
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--max-turns",
            str(max_turns),
            "-p",
            prompt,
        ]
        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
        if disallowed_tools:
            cmd += ["--disallowedTools", ",".join(disallowed_tools)]
        if extra_args:
            cmd.extend(extra_args)
        return cmd

    async def _emit(self, event: AgentEvent) -> None:
        self._events.append(event)
        await self._bus.publish(event)

        if self._session and event.token_usage:
            self._session.total_tokens += event.token_usage.total_tokens
            if event.token_usage.estimated_cost_usd:
                self._session.estimated_cost_usd += event.token_usage.estimated_cost_usd

    @property
    def events(self) -> list[AgentEvent]:
        return list(self._events)

    @property
    def session(self) -> AgentSession | None:
        return self._session


# ─────────────────────────────────────────────
# Context manager wrapper
# ─────────────────────────────────────────────


@asynccontextmanager
async def watch_claude_code(
    prompt: str,
    *,
    model: str = "claude-opus-4-5",
    safety_engine: SafetyEngine | None = None,
    session_id: str | None = None,
) -> AsyncIterator[ClaudeCodeAdapter]:
    """
    Context manager for watching Claude Code execution.

    Example:
        async with watch_claude_code("Write a hello world script") as watcher:
            session = await watcher.run(...)
    """
    adapter = ClaudeCodeAdapter(
        safety_engine=safety_engine,
        session_id=session_id,
    )
    try:
        yield adapter
    finally:
        # Persist session if storage is configured
        if adapter.session:
            logger.info(
                "Session %s complete: %d events, %d tokens",
                adapter.session_id,
                adapter.session.total_events,
                adapter.session.total_tokens,
            )
