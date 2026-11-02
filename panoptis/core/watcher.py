"""
AgentWatch Universal watch() — FOUND-001

One-line agent instrumentation:

    from panoptis import watch
    agent = watch(agent)

Auto-detects the agent's framework, attaches the right adapter,
and returns the agent unchanged. Never blocks or crashes the host agent.

Supported frameworks (detected by class lineage, module name, or duck typing):
  - LangChain         (AgentExecutor, Runnable)
  - LangGraph         (CompiledGraph)
  - CrewAI            (Crew, Agent)
  - AutoGPT           (Agent)
  - AutoGen           (ConversableAgent, GroupChat)
  - Claude Code       (subprocess wrapper, SDK client)
  - OpenAI Agents SDK (Agent, Runner)
  - Smolagents        (CodeAgent, ToolCallingAgent)
  - OpenClaw          (ClawAgent)

Unknown frameworks fall back to GenericAdapter (best-effort wrapping
of any object with `.run`, `.invoke`, `.__call__`, or `astream`).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import uuid
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.http_forwarder import register_http_forwarder
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    EventType,
    ExecutionStatus,
    RiskLevel,
    SafetyCheckData,
    ToolCallData,
)
from panoptis.telemetry.execution_logger import ExecutionLogger

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Public exception
# ─────────────────────────────────────────────


class AgentWatchBlockedError(RuntimeError):
    """Raised by the generic adapter when the safety engine blocks a tool call.

    Attributes:
        reason: Human-readable explanation from the safety engine.
        tool_name: Name of the tool call that was blocked.
        reasons: Full list of matched policy reasons.
    """

    def __init__(
        self,
        reason: str,
        *,
        tool_name: str = "",
        reasons: list[str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tool_name = tool_name
        self.reasons = reasons or [reason]


# ─────────────────────────────────────────────
# Safety helpers
# ─────────────────────────────────────────────

#: Method name substrings that indicate a potential tool invocation.
_TOOL_LIKE_KEYWORDS: frozenset[str] = frozenset(
    {"execute", "run", "call", "invoke", "tool", "kickoff", "step", "stream"}
)


def _is_tool_like(method_name: str) -> bool:
    """Return True if *method_name* looks like a tool-call entry point."""
    name = method_name.lower().lstrip("_")
    return any(kw in name for kw in _TOOL_LIKE_KEYWORDS)


def _build_tool_call_data(method_name: str, args: tuple, kwargs: dict) -> ToolCallData:
    """Build a :class:`ToolCallData` from the raw arguments of a method call.

    Promotes the first string positional argument (or any keyword argument
    named like a command) to ``raw_command`` so the safety engine can scan it.
    """
    _cmd_keys = (
        "command",
        "cmd",
        "shell",
        "exec",
        "bash",
        "script",
        "query",
        "input",
        "task",
        "prompt",
        "message",
    )
    raw_command: str | None = None
    arguments: dict[str, Any] = {}

    # Keyword args — check for command-like keys first
    for key in _cmd_keys:
        if key in kwargs and isinstance(kwargs[key], str):
            raw_command = kwargs[key]
            break

    # Positional args — use first string arg as fallback
    if raw_command is None and args:
        first = args[0]
        if isinstance(first, str):
            raw_command = first

    # Populate arguments dict (use arg0/arg1 keys to avoid triggering the
    # ToolCallData validator that rejects 'command' without raw_command)
    for i, val in enumerate(args):
        arguments[f"arg{i}"] = str(val)
    for k, v in kwargs.items():
        arguments[str(k)] = str(v)

    return ToolCallData(
        tool_name=method_name,
        raw_command=raw_command,
        arguments=arguments,
    )


# ─────────────────────────────────────────────
# Framework detection
# ─────────────────────────────────────────────


def detect_framework(agent: Any) -> AgentFramework:
    """
    Identify the framework backing `agent` without importing any framework.
    Returns AgentFramework.CUSTOM if unknown.
    """
    if agent is None:
        return AgentFramework.CUSTOM

    cls = type(agent)
    mro_names = [f"{c.__module__}.{c.__name__}" for c in cls.__mro__]
    blob = " ".join(mro_names).lower()
    cls_name = cls.__name__.lower()

    # LangGraph before LangChain — LangGraph uses langchain_core internally
    if "langgraph" in blob or "compiledgraph" in cls_name or "stategraph" in cls_name:
        return AgentFramework.CUSTOM  # mapped to custom; specific adapter handles it
    if "langchain" in blob or "agentexecutor" in cls_name or "runnable" in cls_name:
        return AgentFramework.LANGCHAIN
    if "crewai" in blob or cls_name in {"crew", "agent"} and "crewai" in blob:
        return AgentFramework.CREWAI
    if "autogen" in blob or "conversableagent" in cls_name or "groupchat" in cls_name:
        return AgentFramework.CUSTOM
    if "autogpt" in blob:
        return AgentFramework.AUTOGPT
    if "smolagents" in blob or "codeagent" in cls_name or "toolcallingagent" in cls_name:
        return AgentFramework.CUSTOM
    if "openai" in blob and ("agent" in cls_name or "runner" in cls_name):
        return AgentFramework.OPENAI_AGENTS
    if "claude" in blob and "code" in blob:
        return AgentFramework.CLAUDE_CODE
    if "openclaw" in blob or "clawagent" in cls_name:
        return AgentFramework.OPENCLAW

    return AgentFramework.CUSTOM


def detect_framework_label(agent: Any) -> str:
    """Return a human-readable framework label, even for CUSTOM detections."""
    cls = type(agent)
    blob = " ".join(f"{c.__module__}.{c.__name__}" for c in cls.__mro__).lower()
    cls_name = cls.__name__.lower()

    if "langgraph" in blob or "compiledgraph" in cls_name:
        return "langgraph"
    if "autogen" in blob or "conversableagent" in cls_name:
        return "autogen"
    if "smolagents" in blob or cls_name in {"codeagent", "toolcallingagent"}:
        return "smolagents"
    fw = detect_framework(agent)
    return fw.value


# ─────────────────────────────────────────────
# GenericAdapter — fallback wrapper
# ─────────────────────────────────────────────


class _UnavailableSafetyEngine:
    """Stands in for a SafetyEngine whose policy failed to load: every check raises."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def _fail(self) -> Any:
        raise RuntimeError(
            f"safety policy could not be loaded: {type(self.error).__name__}: {self.error}"
        )

    def check_tool_call_sync(self, tool_call: ToolCallData) -> tuple[bool, list[str]]:
        return self._fail()

    async def check_event(self, event: AgentEvent) -> AgentEvent:
        return self._fail()


class GenericAdapter:
    """
    Best-effort wrapper that intercepts the agent's primary entry points
    (`__call__`, `invoke`, `run`, `astream`, `ainvoke`) and emits events.

    Never raises into the host agent — failures are swallowed and logged.
    """

    INTERCEPT_METHODS = (
        "__call__",
        "invoke",
        "ainvoke",
        "run",
        "arun",
        "stream",
        "astream",
        "execute",
        "kickoff",  # CrewAI
        "step",  # AutoGPT-like
    )

    def __init__(
        self,
        agent: Any,
        framework: AgentFramework = AgentFramework.CUSTOM,
        framework_label: str = "custom",
        event_bus: EventBus | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        safety_engine: SafetyEngine | None = None,
        metadata: dict[str, Any] | None = None,
        redact: bool = False,
        safety_timeout_s: float | None = None,
        protection: Any | None = None,
    ):
        self.agent = agent
        self.framework = framework
        self.framework_label = framework_label
        self.bus = event_bus or get_event_bus()
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_id or f"{framework_label}-{uuid.uuid4().hex[:8]}"
        self._step = 0
        self._wrapped_methods: dict[str, Any] = {}
        try:
            self._safety_engine: Any = safety_engine or SafetyEngine()
        except Exception as exc:  # noqa: BLE001
            # The policy could not be loaded. Wrap anyway and block every tool-like
            # call, rather than leaving the agent unwrapped (fail closed).
            logger.error("Safety policy could not be loaded (%s); tool calls will be blocked", exc)
            self._safety_engine = _UnavailableSafetyEngine(exc)
        self._session_metadata: dict[str, Any] = dict(metadata) if metadata else {}
        self._exec_logger = ExecutionLogger(
            agent_id=self.agent_id,
            session_id=self.session_id,
            task_id=self.session_id,
        )
        # CMP-003/004: scrub PII/PHI from tool-call payloads before they are
        # published and persisted. Opt-in to avoid the cost when not required.
        self._redact = redact
        # When set (an panoptis.protect.ProtectionPipeline), tool-like calls go through
        # the shared enforcement boundary instead of this adapter's SafetyEngine gate.
        self._protection = protection
        # Deadline for reaching a safety decision; a decision that does not arrive in
        # time blocks the call. Default: the policy's approval timeout plus margin (the
        # async check may legitimately wait that long for a human). Env override:
        # AGENTWATCH_SAFETY_TIMEOUT_S. The sync path enforces it only when set.
        env_timeout = os.environ.get("AGENTWATCH_SAFETY_TIMEOUT_S")
        self._safety_timeout_s = (
            safety_timeout_s
            if safety_timeout_s is not None
            else (float(env_timeout) if env_timeout else None)
        )

    def _enforcement_failure(self, method_name: str, exc: BaseException) -> AgentWatchBlockedError:
        """The error raised when no valid safety decision could be reached (fail closed)."""
        logger.error(
            "Safety check for %s failed (%s: %s); blocking the call (fail closed)",
            method_name,
            type(exc).__name__,
            exc,
        )
        reason = f"safety_check_failed: {type(exc).__name__}"
        # Make the fail-closed block visible (dashboard, evidence), best effort.
        try:
            self.bus.publish_sync(
                AgentEvent(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    agent_name=self.framework_label,
                    framework=self.framework,
                    event_type=EventType.SAFETY_BLOCK,
                    step_number=self._step,
                    status=ExecutionStatus.BLOCKED,
                    safety=SafetyCheckData(
                        risk_level=RiskLevel.CRITICAL,
                        risk_score=1.0,
                        blocked=True,
                        reasons=[reason],
                    ),
                    metadata={
                        **self._session_metadata,
                        "blocked_tool": method_name,
                        "enforcement_failure": True,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    },
                )
            )
        except Exception as pub_exc:  # noqa: BLE001
            logger.warning(
                "Could not publish the fail-closed block for %s: %s", method_name, pub_exc
            )
        return AgentWatchBlockedError(
            f"Tool call '{method_name}' blocked: the safety check failed "
            f"({type(exc).__name__}); AgentWatch fails closed",
            tool_name=method_name,
            reasons=[reason],
        )

    def _async_deadline(self) -> float:
        if self._safety_timeout_s is not None:
            return self._safety_timeout_s
        approval = getattr(
            getattr(self._safety_engine, "policy", None), "approval_timeout_seconds", 120
        )
        return float(approval) + 30.0

    @staticmethod
    def _valid_sync_verdict(result: Any) -> tuple[bool, list[str]]:
        """A malformed verdict is not a decision: it raises, and the caller blocks."""
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise ValueError(f"invalid safety verdict {result!r}: expected (blocked, reasons)")
        blocked, reasons = result
        if not isinstance(blocked, bool):
            raise ValueError(f"invalid safety verdict: blocked={blocked!r} is not a bool")
        if reasons is None:
            reasons = []
        if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
            raise ValueError("invalid safety verdict: reasons must be a list of strings")
        return blocked, reasons

    @staticmethod
    def _valid_checked_event(checked: Any) -> AgentEvent:
        if not isinstance(checked, AgentEvent):
            raise ValueError(
                f"invalid safety verdict: expected an AgentEvent, got {type(checked).__name__}"
            )
        if checked.safety is None or not isinstance(checked.safety.blocked, bool):
            raise ValueError("invalid safety verdict: the checked event carries no safety decision")
        return checked

    def _sync_decision(self, tool_call: ToolCallData) -> tuple[bool, list[str]]:
        return self._valid_sync_verdict(self._sync_check(tool_call))

    def _sync_check(self, tool_call: ToolCallData) -> Any:
        if self._safety_timeout_s is None:
            return self._safety_engine.check_tool_call_sync(tool_call)
        # Run the check on a worker so a hung check cannot hold the call open; on
        # timeout the call is blocked (the worker is abandoned, the tool never runs).
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="aw-safety")
        try:
            future = pool.submit(self._safety_engine.check_tool_call_sync, tool_call)
            try:
                return future.result(timeout=self._safety_timeout_s)
            except concurrent.futures.TimeoutError as exc:
                raise TimeoutError(f"no safety decision within {self._safety_timeout_s}s") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    # ── shared-pipeline enforcement (protection=...) ─────────────────────────────
    def _action(
        self, method_name: str, original: Any, args: tuple, kwargs: dict
    ) -> tuple[Any, Any]:
        import inspect

        from panoptis.protect.enforce import bind_arguments, current_context
        from panoptis.protect.model import ToolAction

        try:
            sig = inspect.signature(original)
        except (TypeError, ValueError):
            sig = None
        named, rebuild = bind_arguments(sig, args, kwargs)
        ctx = current_context()
        action = ToolAction(
            agent_id=ctx.get("agent_id") or self.agent_id,
            session_id=ctx.get("session_id") or self.session_id,
            tool=method_name,
            arguments=named,
            objective=ctx.get("objective") or self._session_metadata.get("objective"),
            tenant_id=ctx.get("tenant_id") or "default",
            trace_id=ctx.get("trace_id"),
            source=f"adapter:{self.framework_label}",
        )
        return action, rebuild

    def _protected_sync(self, method_name: str, original: Any, args: tuple, kwargs: dict) -> Any:
        from panoptis.protect.enforce import enforce_call

        self._step += 1
        action, rebuild = self._action(method_name, original, args, kwargs)

        def invoke(arguments: dict[str, Any]) -> Any:
            a, k = rebuild(arguments)
            self._emit_safely(
                EventType.AGENT_START, metadata={"method": method_name, "step": self._step}
            )
            result = original(*a, **k)
            self._emit_safely(
                EventType.AGENT_END,
                status=ExecutionStatus.SUCCESS,
                metadata={"method": method_name},
            )
            return result

        return enforce_call(self._protection, action, invoke)

    async def _protected_async(
        self, method_name: str, original: Any, args: tuple, kwargs: dict
    ) -> Any:
        from panoptis.protect.enforce import aenforce_call

        self._step += 1
        action, rebuild = self._action(method_name, original, args, kwargs)

        async def invoke(arguments: dict[str, Any]) -> Any:
            a, k = rebuild(arguments)
            await self._async_emit(
                EventType.AGENT_START, metadata={"method": method_name, "step": self._step}
            )
            result = await original(*a, **k)
            await self._async_emit(
                EventType.AGENT_END,
                status=ExecutionStatus.SUCCESS,
                metadata={"method": method_name},
            )
            return result

        return await aenforce_call(self._protection, action, invoke)

    def _maybe_redact(self, tool_call: ToolCallData) -> ToolCallData:
        """Redact PII/PHI from a tool call when redaction is enabled.

        Applied only when building the event that gets published/persisted —
        never before the safety check, which must see the raw payload.
        """
        if not self._redact:
            return tool_call
        from panoptis.security.redaction import redact_tool_call

        return redact_tool_call(tool_call)

    def _redact_event(self, event: AgentEvent) -> AgentEvent:
        """Return a copy of ``event`` with its tool call scrubbed, if enabled."""
        if not self._redact or event.tool_call is None:
            return event
        return event.model_copy(update={"tool_call": self._maybe_redact(event.tool_call)})

    def attach(self) -> Any:
        """
        Monkey-patch the agent's methods so each call emits AgentEvents.
        Returns the (same) agent object — never a proxy, so identity is preserved.
        """
        emitted_session_start = False
        protected = 0  # tool-like methods that go through enforcement

        for name in self.INTERCEPT_METHODS:
            if not hasattr(self.agent, name):
                continue
            original = getattr(self.agent, name)
            if not callable(original):
                continue
            # Skip already-wrapped methods (idempotent)
            if getattr(original, "_panoptis_wrapped", False):
                protected += _is_tool_like(name)
                continue

            wrapped = self._wrap(name, original)
            wrapped._panoptis_wrapped = True  # type: ignore[attr-defined]
            try:
                setattr(self.agent, name, wrapped)
                self._wrapped_methods[name] = original
            except (AttributeError, TypeError) as exc:
                # Some agents have read-only attrs (e.g. slotted classes). Observation can
                # skip them; enforcement cannot leave a tool-like method unprotected.
                if self._protection is not None and _is_tool_like(name):
                    raise TypeError(
                        f"cannot protect {type(self.agent).__name__}.{name}: the attribute "
                        f"is read-only ({exc}); use protect_tool() on the agent's tools"
                    ) from exc
                logger.debug("Could not wrap %s on %s", name, type(self.agent).__name__)
                continue
            protected += _is_tool_like(name)

            if not emitted_session_start:
                self._emit_safely(
                    EventType.SESSION_START,
                    metadata={"framework": self.framework_label},
                )
                emitted_session_start = True

        if self._protection is not None and not protected:
            raise ValueError(
                f"protection requested but {type(self.agent).__name__} has no tool-like "
                "method to enforce on; use protect_tool() on its tools"
            )

        # Stash the adapter on the agent for later retrieval/diagnostics
        try:
            self.agent._panoptis_adapter = self
        except (AttributeError, TypeError):
            pass

        return self.agent

    def _wrap(self, method_name: str, original: Any) -> Any:
        """Return a wrapper for *original* that emits AgentEvents and logs execution steps.

        Detects whether the original is a coroutine function and returns the
        appropriate async or sync wrapper. Both wrappers apply the safety gate
        for tool-like methods, emit lifecycle events (AGENT_START / AGENT_END /
        AGENT_ERROR), and record structured execution logs via ExecutionLogger.
        """
        import functools
        import inspect

        is_tool_like = _is_tool_like(method_name)

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                """Async wrapper that logs execution and applies the safety gate."""
                if is_tool_like and self._protection is not None:
                    return await self._protected_async(method_name, original, args, kwargs)
                self._step += 1

                import time as _time

                _t0 = _time.monotonic()
                self._exec_logger.log_step(
                    method_name,
                    {"step": self._step, "args_count": len(args), "kwargs": list(kwargs.keys())},
                )

                # ── Safety gate (async path — full check_event with approval) ──
                if is_tool_like:
                    # Safety must evaluate the RAW payload — redacting first would
                    # hide signals (paths, secrets, identifiers) the engine needs.
                    tool_call = _build_tool_call_data(method_name, args, kwargs)
                    safety_event = AgentEvent(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        agent_name=self.framework_label,
                        framework=self.framework,
                        event_type=EventType.TOOL_CALL,
                        step_number=self._step,
                        tool_call=tool_call,
                        metadata=dict(self._session_metadata),
                    )
                    # Decide first; any failure to reach a decision blocks (fail closed).
                    try:
                        checked = self._valid_checked_event(
                            await asyncio.wait_for(
                                self._safety_engine.check_event(safety_event),
                                timeout=self._async_deadline(),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - includes TimeoutError
                        raise self._enforcement_failure(method_name, exc) from exc
                    # Publish the checked event — it carries the full safety
                    # metadata (risk_level, blocked status, reasons). Scrub
                    # PII/PHI only now, after the decision, before persist.
                    # Await so the HTTP forwarder completes before we proceed.
                    try:
                        await self.bus.publish(self._redact_event(checked))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Could not publish the safety event for %s: %s", method_name, exc
                        )
                    if checked.is_blocked:
                        reasons = checked.safety.reasons if checked.safety else []
                        reason_str = "; ".join(reasons) if reasons else "safety policy"
                        raise AgentWatchBlockedError(
                            f"Tool call '{method_name}' blocked by safety engine: {reason_str}",
                            tool_name=method_name,
                            reasons=reasons,
                        )

                # Use await so HTTP forwarding completes before the caller returns.
                await self._async_emit(
                    EventType.AGENT_START,
                    metadata={"method": method_name, "step": self._step},
                )
                try:
                    result = await original(*args, **kwargs)
                    _dur = (_time.monotonic() - _t0) * 1000
                    self._exec_logger.log_execution_complete("success", _dur)
                    await self._async_emit(
                        EventType.AGENT_END,
                        status=ExecutionStatus.SUCCESS,
                        metadata={"method": method_name},
                    )
                    return result
                except AgentWatchBlockedError:
                    raise
                except Exception as exc:
                    import traceback as _tb

                    _dur = (_time.monotonic() - _t0) * 1000
                    self._exec_logger.log_error(
                        str(exc),
                        type(exc).__name__,
                        _tb.format_exc(),
                        {"method": method_name, "step": self._step},
                    )
                    self._exec_logger.log_execution_complete("failure", _dur)
                    await self._async_emit(
                        EventType.AGENT_ERROR,
                        status=ExecutionStatus.FAILURE,
                        metadata={"method": method_name, "error": str(exc)},
                    )
                    raise

            return async_wrapped

        @functools.wraps(original)
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            """Sync wrapper that logs execution and applies the pattern-match safety gate."""
            if is_tool_like and self._protection is not None:
                return self._protected_sync(method_name, original, args, kwargs)
            self._step += 1

            import time as _time

            _t0 = _time.monotonic()
            self._exec_logger.log_step(
                method_name,
                {"step": self._step, "args_count": len(args), "kwargs": list(kwargs.keys())},
            )

            # ── Safety gate (sync path — pattern match only, no approval) ──
            if is_tool_like:
                # Check the raw payload; redact only the event we publish below.
                tool_call = _build_tool_call_data(method_name, args, kwargs)
                # Decide first. Any failure to reach a decision blocks the call
                # (fail closed); it never falls through to running the tool.
                try:
                    blocked, reasons = self._sync_decision(tool_call)
                except Exception as exc:  # noqa: BLE001 - includes TimeoutError
                    raise self._enforcement_failure(method_name, exc) from exc
                # Then publish: telemetry failure must not change the decision.
                try:
                    tc_event = AgentEvent(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        agent_name=self.framework_label,
                        framework=self.framework,
                        event_type=EventType.TOOL_CALL,
                        step_number=self._step,
                        tool_call=self._maybe_redact(tool_call),
                        status=ExecutionStatus.BLOCKED if blocked else ExecutionStatus.RUNNING,
                        safety=SafetyCheckData(
                            risk_level=(RiskLevel.CRITICAL if blocked else RiskLevel.SAFE),
                            risk_score=1.0 if blocked else 0.0,
                            blocked=blocked,
                            reasons=reasons,
                        ),
                        metadata=dict(self._session_metadata),
                    )
                    self.bus.publish_sync(tc_event)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Could not publish the safety event for %s: %s", method_name, exc
                    )
                if blocked:
                    reason_str = "; ".join(reasons) if reasons else "safety policy"
                    raise AgentWatchBlockedError(
                        f"Tool call '{method_name}' blocked by safety engine: {reason_str}",
                        tool_name=method_name,
                        reasons=reasons,
                    )

            self._emit_safely(
                EventType.AGENT_START,
                metadata={"method": method_name, "step": self._step},
            )
            try:
                result = original(*args, **kwargs)
                _dur = (_time.monotonic() - _t0) * 1000
                self._exec_logger.log_execution_complete("success", _dur)
                self._emit_safely(
                    EventType.AGENT_END,
                    status=ExecutionStatus.SUCCESS,
                    metadata={"method": method_name},
                )
                return result
            except AgentWatchBlockedError:
                raise
            except Exception as exc:
                import traceback as _tb

                _dur = (_time.monotonic() - _t0) * 1000
                self._exec_logger.log_error(
                    str(exc),
                    type(exc).__name__,
                    _tb.format_exc(),
                    {"method": method_name, "step": self._step},
                )
                self._exec_logger.log_execution_complete("failure", _dur)
                self._emit_safely(
                    EventType.AGENT_ERROR,
                    status=ExecutionStatus.FAILURE,
                    metadata={"method": method_name, "error": str(exc)},
                )
                raise

        return sync_wrapped

    def _emit_safely(
        self,
        event_type: EventType,
        *,
        status: ExecutionStatus = ExecutionStatus.RUNNING,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event without ever raising into the host agent (sync context)."""
        try:
            merged = {**self._session_metadata, **(metadata or {})}
            event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                agent_name=self.framework_label,
                framework=self.framework,
                event_type=event_type,
                status=status,
                step_number=self._step,
                metadata=merged,
            )
            self.bus.publish_sync(event)
        except Exception as exc:  # noqa: BLE001 — invisible-when-healthy contract
            logger.debug("AgentWatch emit failed (suppressed): %s", exc)

    async def _async_emit(
        self,
        event_type: EventType,
        *,
        status: ExecutionStatus = ExecutionStatus.RUNNING,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event in async context, awaiting all handlers so none are dropped."""
        try:
            merged = {**self._session_metadata, **(metadata or {})}
            event = AgentEvent(
                session_id=self.session_id,
                agent_id=self.agent_id,
                agent_name=self.framework_label,
                framework=self.framework,
                event_type=event_type,
                status=status,
                step_number=self._step,
                metadata=merged,
            )
            await self.bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AgentWatch async emit failed (suppressed): %s", exc)


# ─────────────────────────────────────────────
# Framework-specific adapter wiring
# ─────────────────────────────────────────────


def _attach_langchain(
    agent: Any,
    session_id: str | None,
    bus: EventBus,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Attach an AgentWatchCallbackHandler if the agent supports callbacks."""
    try:
        from panoptis.adapters.langchain import AgentWatchCallbackHandler

        handler = AgentWatchCallbackHandler(session_id=session_id, event_bus=bus)
        if metadata:
            handler._session_metadata = dict(metadata)
        # Newer LangChain: set on `.callbacks`
        if hasattr(agent, "callbacks"):
            existing = getattr(agent, "callbacks", None) or []
            if isinstance(existing, list):
                if not any(isinstance(h, AgentWatchCallbackHandler) for h in existing):
                    existing.append(handler)
                    agent.callbacks = existing
            else:
                # CallbackManager instance — try add_handler
                if hasattr(existing, "add_handler"):
                    try:
                        existing.add_handler(handler)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("LangChain add_handler failed: %s", exc)
        # Stash for diagnostics
        try:
            agent._panoptis_handler = handler
        except (AttributeError, TypeError):
            pass
        return agent
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangChain attach failed, falling back to generic: %s", exc)
        return None


def _attach_langgraph(
    agent: Any,
    session_id: str | None,
    bus: EventBus,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """LangGraph CompiledGraph exposes `invoke`/`astream` — wrap them generically.
    Also try to install a LangChain callback handler since LangGraph reuses it.
    """
    try:
        from panoptis.adapters.langgraph import LangGraphAdapter

        adapter = LangGraphAdapter(agent, event_bus=bus, session_id=session_id)
        if metadata:
            adapter._session_metadata = dict(metadata)
        return adapter.attach()
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangGraph attach failed: %s", exc)
        return None


def _attach_autogen(
    agent: Any,
    session_id: str | None,
    bus: EventBus,
    metadata: dict[str, Any] | None = None,
) -> Any:
    try:
        from panoptis.adapters.autogen import AutoGenAdapter

        adapter = AutoGenAdapter(agent, event_bus=bus, session_id=session_id)
        if metadata:
            adapter._session_metadata = dict(metadata)
        return adapter.attach()
    except Exception as exc:  # noqa: BLE001
        logger.debug("AutoGen attach failed: %s", exc)
        return None


def _attach_smolagents(
    agent: Any,
    session_id: str | None,
    bus: EventBus,
    metadata: dict[str, Any] | None = None,
) -> Any:
    try:
        from panoptis.adapters.smolagents import SmolagentsAdapter

        adapter = SmolagentsAdapter(agent, event_bus=bus, session_id=session_id)
        if metadata:
            adapter._session_metadata = dict(metadata)
        return adapter.attach()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Smolagents attach failed: %s", exc)
        return None


# ─────────────────────────────────────────────
# Public watch() API
# ─────────────────────────────────────────────


def watch(
    agent: Any,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    event_bus: EventBus | None = None,
    metadata: dict[str, Any] | None = None,
    redact: bool = False,
    protection: Any | None = None,
) -> Any:
    """
    Instrument an agent for AgentWatch observability.

    Returns the same agent object (or, for some frameworks, a wrapped object
    that quacks identically). Never crashes the host — if instrumentation
    fails, the original agent is returned unchanged.

    Args:
        agent:       any supported agent or generic callable
        session_id:  optional explicit session ID (auto-generated otherwise)
        agent_id:    optional explicit agent ID
        event_bus:   override the default EventBus (mostly for testing)
        metadata:    optional custom metadata attached to all session events
        redact:      scrub PII/PHI from tool-call payloads before they are
                     published/persisted (generic-adapter path only)
        protection:  an ``panoptis.protect.ProtectionPipeline``: tool-like calls are
                     decided by the shared enforcement pipeline (RBAC, threats, loops,
                     intent, approvals, output sanitization) instead of the legacy gate
    """
    if agent is None:
        logger.warning("watch() called with None — returning None")
        return agent

    bus = event_bus or get_event_bus()
    register_http_forwarder(bus, api_url=None)
    label = detect_framework_label(agent)

    if protection is not None and label in ("langchain", "langgraph", "autogen", "smolagents"):
        # Enforcement for a framework agent is attached to its tool-execution entry points.
        # Errors propagate: asking for protection must never silently yield observation only.
        from panoptis.protect.integrations import protect_agent

        protect_agent(agent, label, pipeline=protection, agent_id=agent_id, session_id=session_id)

    try:
        if label == "langchain":
            attached = _attach_langchain(agent, session_id, bus, metadata)
            if attached is not None:
                return attached
        elif label == "langgraph":
            attached = _attach_langgraph(agent, session_id, bus, metadata)
            if attached is not None:
                return attached
        elif label == "autogen":
            attached = _attach_autogen(agent, session_id, bus, metadata)
            if attached is not None:
                return attached
        elif label == "smolagents":
            attached = _attach_smolagents(agent, session_id, bus, metadata)
            if attached is not None:
                return attached

        # Fallback: generic wrapping
        framework = detect_framework(agent)
        adapter = GenericAdapter(
            agent,
            framework=framework,
            framework_label=label,
            event_bus=bus,
            session_id=session_id,
            agent_id=agent_id,
            metadata=metadata,
            redact=redact,
            protection=protection,
        )
        return adapter.attach()

    except Exception as exc:  # noqa: BLE001 — invisible-when-healthy contract
        if protection is not None:
            # observation may degrade to "unmodified"; enforcement may not
            raise
        logger.warning(
            "watch() instrumentation failed for %s: %s — returning agent unmodified",
            type(agent).__name__,
            exc,
        )
        return agent


__all__ = ["watch", "detect_framework", "detect_framework_label", "GenericAdapter"]
