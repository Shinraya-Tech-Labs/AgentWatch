"""The authoritative enforcement pipeline.

One code path decides every tool call, whichever integration proposed it (SDK decorator,
framework adapter, MCP gateway over stdio or HTTP, CLI)::

    proposal
      → identity & session validation      (Stage.IDENTITY)
      → kill switch / suspension / breaker  (Stage.KILLSWITCH, Stage.LOOP)
      → RBAC authorization                  (Stage.RBAC)
      → threat & argument analysis          (Stage.THREAT)
      → loop & stuck-state detection        (Stage.LOOP)
      → intent grounding                    (Stage.INTENT)
      → human approval, if escalated        (Stage.HITL)
      → verdict ─ BLOCK: the tool is never called
                └ ALLOW / MUTATE_AND_ALLOW: the tool runs exactly once
      → post-execution inspection & sanitization (Stage.OUTPUT)
      → evidence record (Merkle ledger) and telemetry

Failure behaviour is explicit: an exception inside any enforcement stage produces a BLOCK
(``fail_closed=True``, the default); an escalation with no approval channel is blocked; an
approval that times out is a rejection. A call id executes at most once.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agentwatch.circuit_breaker.breaker import CircuitBreaker, CircuitState
from agentwatch.circuit_breaker.thresholds import CircuitThresholds
from agentwatch.core.schema import AgentEvent, AgentFramework, EventType, ExecutionStatus
from agentwatch.protect.approvals import ApprovalBroker
from agentwatch.protect.control import ControlState
from agentwatch.protect.intent import GroundingConfig, IntentGrounder
from agentwatch.protect.ledger import KIND_AGENT, KIND_DECISION, KIND_OUTPUT, ProtectLedger
from agentwatch.protect.loops import LoopConfig, LoopDetector
from agentwatch.protect.model import (
    ActionBlocked,
    Decision,
    Finding,
    OutputInspection,
    Severity,
    Stage,
    ToolAction,
    Verdict,
    combine_risk,
    strongest,
)
from agentwatch.protect.output import OutputInspector
from agentwatch.protect.rbac import PolicyStore, RBACEngine
from agentwatch.protect.threats import ThreatAnalyzer

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
_AGENT_ID = re.compile(r"^[A-Za-z0-9_.:@/\-]{1,128}$")


@dataclass
class ProtectionConfig:
    fail_closed: bool = True
    # what to do when a finding asks for human approval but no approval channel exists
    escalate_without_approver: Verdict = Verdict.BLOCK
    approval_timeout_s: float = 120.0
    require_registered_agents: bool = False
    bind_sessions: bool = True  # a session id belongs to the first agent that uses it
    output_inspection: bool = True
    on_injection: str = "quarantine"  # quarantine | withhold | flag
    record_allowed: bool = True  # also record ALLOW decisions (full audit trail)
    trip_breaker_after_loops: int = 2  # confirmed loops in a session before the breaker opens
    breaker_cooldown_s: float = 30.0
    breaker_consecutive_errors: int = 5


@dataclass
class SessionInfo:
    session_id: str
    agent_id: str
    tenant_id: str = "default"
    objective: str | None = None
    started_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    decisions: int = 0
    blocked: int = 0
    escalated: int = 0
    loops: int = 0

    @property
    def scope(self) -> str:
        return f"{self.tenant_id}/{self.session_id}"


class Telemetry:
    """In-process ring buffer of recent decisions and per-stage latency, plus listeners."""

    def __init__(self, size: int = 2000) -> None:
        self.recent: deque[dict[str, Any]] = deque(maxlen=size)
        self.latency: dict[str, deque[float]] = {}
        self.counts: dict[str, int] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def emit(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.recent.append(record)
            v = record.get("verdict")
            if v:
                self.counts[v] = self.counts.get(v, 0) + 1
            for stage, ms in (record.get("timings_ms") or {}).items():
                self.latency.setdefault(stage, deque(maxlen=5000)).append(ms)
        for fn in list(self._listeners):
            try:
                fn(record)
            except Exception:  # noqa: BLE001 - a listener must never break enforcement
                logger.debug("telemetry listener failed", exc_info=True)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            lat = {}
            for stage, q in self.latency.items():
                xs = sorted(q)
                if xs:
                    lat[stage] = {
                        "n": len(xs),
                        "p50": xs[len(xs) // 2],
                        "p95": xs[min(len(xs) - 1, int(len(xs) * 0.95))],
                        "max": xs[-1],
                    }
            return {"counts": dict(self.counts), "latency_ms": lat, "buffered": len(self.recent)}


class ProtectionPipeline:
    def __init__(
        self,
        *,
        policy: PolicyStore | None = None,
        ledger: ProtectLedger | None = None,
        approvals: ApprovalBroker | None = None,
        control: ControlState | None = None,
        threats: ThreatAnalyzer | None = None,
        loops: LoopDetector | None = None,
        intent: IntentGrounder | None = None,
        output: OutputInspector | None = None,
        config: ProtectionConfig | None = None,
    ) -> None:
        self.config = config or ProtectionConfig()
        self.policy = policy or PolicyStore()
        self.rbac = RBACEngine(self.policy)
        self.ledger = ledger
        self.approvals = approvals
        self.control = control or ControlState(ledger)
        self.threats = threats or ThreatAnalyzer()
        self.loops = loops or LoopDetector(LoopConfig())
        self.intent = intent or IntentGrounder(GroundingConfig())
        self.output = output or OutputInspector(on_injection=self.config.on_injection)
        self.telemetry = Telemetry()
        self.sessions: dict[str, SessionInfo] = {}
        self.registered_agents: set[str] = set()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._loop_hits: dict[str, int] = {}
        self._executed: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.RLock()

    # ── agents and sessions ────────────────────────────────────────────────────────
    def register_agent(self, agent_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        if not _AGENT_ID.match(agent_id):
            raise ValueError(f"invalid agent id {agent_id!r}")
        with self._lock:
            self.registered_agents.add(agent_id)
        self._record(
            KIND_AGENT,
            {"event": "register", "agent_id": agent_id, "metadata": metadata or {}},
            {"agent_id": agent_id},
        )

    def start_session(
        self,
        agent_id: str,
        session_id: str,
        *,
        objective: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str = "default",
    ) -> SessionInfo:
        scope = f"{tenant_id}/{session_id}"
        with self._lock:
            existing = self.sessions.get(scope)
            if existing and existing.agent_id != agent_id and self.config.bind_sessions:
                raise ValueError(f"session {session_id!r} belongs to agent {existing.agent_id!r}")
            info = existing or SessionInfo(
                session_id=session_id, agent_id=agent_id, tenant_id=tenant_id
            )
            if objective:
                info.objective = objective
            info.metadata.update(metadata or {})
            self.sessions[scope] = info
        self._record(
            KIND_AGENT,
            {
                "event": "session_start",
                "agent_id": agent_id,
                "session_id": session_id,
                "objective": objective,
                "metadata": metadata or {},
            },
            {"agent_id": agent_id, "session_id": session_id},
        )
        return info

    def set_objective(self, session_id: str, objective: str, *, tenant_id: str = "default") -> None:
        scope = f"{tenant_id}/{session_id}"
        with self._lock:
            if scope in self.sessions:
                self.sessions[scope].objective = objective

    def breaker(self, scope: str) -> CircuitBreaker:
        with self._lock:
            b = self._breakers.get(scope)
            if b is None:
                b = CircuitBreaker(
                    scope,
                    CircuitThresholds(
                        max_total_tokens=0,
                        max_consecutive_errors=self.config.breaker_consecutive_errors,
                        max_total_errors=0,
                        max_hallucinations=0,
                        min_confidence=0.0,
                        half_open_probe_successes=1,
                        max_tool_repeats=0,
                        max_parameter_oscillations=0,
                        cooldown_seconds=self.config.breaker_cooldown_s,
                    ),
                )
                self._breakers[scope] = b
            return b

    # ── decision ───────────────────────────────────────────────────────────────────
    def evaluate(self, action: ToolAction, *, wait_for_approval: bool = True) -> Decision:
        """Run every pre-execution stage and return the verdict (waits for HITL if escalated)."""
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        findings: list[Finding] = []
        mutated: dict[str, Any] | None = None

        def timed(stage: str, fn: Callable[[], list[Finding]]) -> list[Finding]:
            s0 = time.perf_counter()
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - enforcement must fail closed, not crash open
                logger.exception("protection stage %s failed", stage)
                sev = Severity.CRITICAL if self.config.fail_closed else Severity.LOW
                rec = Verdict.BLOCK if self.config.fail_closed else Verdict.ALLOW
                return [
                    Finding(
                        Stage.PIPELINE,
                        "STAGE_ERROR",
                        sev,
                        f"{stage} stage failed ({type(exc).__name__}: {exc}); failing {'closed' if self.config.fail_closed else 'open'}",
                        {"stage": stage},
                        None,
                        rec,
                    )
                ]
            finally:
                timings[stage] = round((time.perf_counter() - s0) * 1000, 3)

        session = self._session_for(action)
        objective = action.objective or (session.objective if session else None)

        findings += timed("identity", lambda: self._identity(action))
        findings += timed("control", lambda: self._control(action))
        if not _blocked(findings):
            profile_box: list[Any] = []

            def threat() -> list[Finding]:
                report = self.threats.analyze(action.tool, action.arguments)
                profile_box.append(report.profile)
                nonlocal mutated
                mutated = report.mutated_arguments
                return report.findings

            findings += timed("threat", threat)
            profile = profile_box[0] if profile_box else None
            if profile is not None:
                findings += timed(
                    "rbac",
                    lambda: self.rbac.authorize(
                        action.agent_id, action.tool, action.arguments, profile
                    ),
                )
                findings += timed(
                    "loop",
                    lambda: self.loops.check(
                        action.scope, action.call_id, action.tool, action.arguments
                    ),
                )
                findings += timed(
                    "intent",
                    lambda: self.intent.ground(
                        objective,
                        action.tool,
                        action.arguments,
                        profile,
                        reasoning=action.reasoning,
                    ),
                )
            elif not _blocked(findings):
                findings.append(
                    Finding(
                        Stage.PIPELINE,
                        "NO_PROFILE",
                        Severity.CRITICAL,
                        "Arguments could not be analysed",
                        {},
                        None,
                        Verdict.BLOCK,
                    )
                )

        verdict = strongest([f.recommends for f in findings])
        if verdict is Verdict.MUTATE_AND_ALLOW and mutated is None:
            verdict = Verdict.ALLOW
        decision = Decision(
            action=action,
            verdict=verdict,
            risk_score=combine_risk(findings),
            findings=findings,
            mutated_arguments=mutated if verdict is Verdict.MUTATE_AND_ALLOW else None,
            timings_ms=timings,
            policy_version=self.policy.policy.digest if self.policy.policy else None,
        )

        if verdict is Verdict.ESCALATE_HITL:
            h0 = time.perf_counter()
            self._escalate(decision, mutated, wait=wait_for_approval)
            timings["hitl_wait"] = round((time.perf_counter() - h0) * 1000, 3)
        timings["total"] = round((time.perf_counter() - t0) * 1000, 3)
        self._after_decision(decision)
        return decision

    def _session_for(self, action: ToolAction) -> SessionInfo | None:
        with self._lock:
            return self.sessions.get(action.scope)

    def _identity(self, action: ToolAction) -> list[Finding]:
        out: list[Finding] = []
        ev = {"agent_id": action.agent_id, "session_id": action.session_id}
        if not _AGENT_ID.match(action.agent_id or ""):
            return [
                Finding(
                    Stage.IDENTITY,
                    "INVALID_AGENT_ID",
                    Severity.CRITICAL,
                    "Missing or malformed agent id",
                    ev,
                    None,
                    Verdict.BLOCK,
                )
            ]
        if not action.session_id or len(action.session_id) > 200:
            return [
                Finding(
                    Stage.IDENTITY,
                    "INVALID_SESSION_ID",
                    Severity.CRITICAL,
                    "Missing or malformed session id",
                    ev,
                    None,
                    Verdict.BLOCK,
                )
            ]
        if not action.tool or len(action.tool) > 256:
            return [
                Finding(
                    Stage.IDENTITY,
                    "INVALID_TOOL",
                    Severity.CRITICAL,
                    "Missing or malformed tool name",
                    ev,
                    None,
                    Verdict.BLOCK,
                )
            ]
        if not isinstance(action.arguments, dict):
            return [
                Finding(
                    Stage.IDENTITY,
                    "INVALID_ARGUMENTS",
                    Severity.CRITICAL,
                    "Tool arguments must be an object",
                    ev,
                    None,
                    Verdict.BLOCK,
                )
            ]
        with self._lock:
            if (
                self.config.require_registered_agents
                and action.agent_id not in self.registered_agents
            ):
                return [
                    Finding(
                        Stage.IDENTITY,
                        "UNREGISTERED_AGENT",
                        Severity.CRITICAL,
                        f"Agent '{action.agent_id}' is not registered",
                        ev,
                        None,
                        Verdict.BLOCK,
                    )
                ]
            s = self.sessions.get(action.scope)
            if s is None:
                self.sessions[action.scope] = SessionInfo(
                    session_id=action.session_id,
                    tenant_id=action.tenant_id,
                    agent_id=action.agent_id,
                    objective=action.objective,
                )
            elif s.agent_id != action.agent_id and self.config.bind_sessions:
                return [
                    Finding(
                        Stage.IDENTITY,
                        "SESSION_AGENT_MISMATCH",
                        Severity.CRITICAL,
                        f"Session '{action.session_id}' belongs to agent '{s.agent_id}', not '{action.agent_id}'",
                        ev,
                        None,
                        Verdict.BLOCK,
                    )
                ]
        return out

    def _control(self, action: ToolAction) -> list[Finding]:
        killed, reason = self.control.local_kill()
        snap = self.control.snapshot()
        if killed or snap.killed:
            return [
                Finding(
                    Stage.KILLSWITCH,
                    "KILL_SWITCH",
                    Severity.CRITICAL,
                    f"Emergency stop is active: {reason or snap.kill_reason}",
                    {"actor": snap.kill_actor},
                    None,
                    Verdict.BLOCK,
                )
            ]
        if action.agent_id in snap.suspended_agents:
            meta = snap.suspended_agents[action.agent_id]
            return [
                Finding(
                    Stage.KILLSWITCH,
                    "AGENT_SUSPENDED",
                    Severity.CRITICAL,
                    f"Agent '{action.agent_id}' is suspended: {meta.get('reason')}",
                    meta,
                    None,
                    Verdict.BLOCK,
                )
            ]
        if action.session_id in snap.suspended_sessions:
            meta = snap.suspended_sessions[action.session_id]
            return [
                Finding(
                    Stage.KILLSWITCH,
                    "SESSION_SUSPENDED",
                    Severity.CRITICAL,
                    f"Session '{action.session_id}' is suspended: {meta.get('reason')}",
                    meta,
                    None,
                    Verdict.BLOCK,
                )
            ]
        b = self.breaker(action.scope)
        if b.state is CircuitState.OPEN:
            # let an elapsed cooldown move the breaker to HALF_OPEN (this call is the probe)
            b.observe(
                AgentEvent(
                    session_id=action.scope,
                    agent_id=action.agent_id,
                    framework=AgentFramework.CUSTOM,
                    event_type=EventType.TOOL_CALL,
                    status=ExecutionStatus.RUNNING,
                )
            )
        if b.is_tripped:
            st = b.status()
            return [
                Finding(
                    Stage.LOOP,
                    "CIRCUIT_OPEN",
                    Severity.CRITICAL,
                    f"Circuit breaker for session '{action.session_id}' is {st['state']} ({_last_reason(b)}); retry after cooldown",
                    {"state": st["state"], "cooldown_remaining": st["cooldown_remaining"]},
                    None,
                    Verdict.BLOCK,
                )
            ]
        return []

    def _escalate(self, decision: Decision, mutated: dict[str, Any] | None, *, wait: bool) -> None:
        if self.approvals is None:
            v = self.config.escalate_without_approver
            decision.findings.append(
                Finding(
                    Stage.HITL,
                    "NO_APPROVAL_CHANNEL",
                    Severity.HIGH,
                    f"Human approval required but no approval channel is configured; {v.value}",
                    {},
                    None,
                    v,
                )
            )
            decision.verdict = v
            decision.approval = {"state": "unavailable"}
            return
        req = self.approvals.request(decision, timeout_s=self.config.approval_timeout_s)
        decision.approval = {
            "request_id": req.request_id,
            "state": "pending",
            "expires_at": req.expires_at,
        }
        if not wait:
            return
        outcome = self.approvals.wait(req)
        decision.approval = {
            "request_id": req.request_id,
            "state": "approved"
            if outcome.approved
            else ("expired" if outcome.timed_out else "rejected"),
            **outcome.to_dict(),
        }
        if not outcome.approved:
            decision.findings.append(
                Finding(
                    Stage.HITL,
                    "APPROVAL_TIMEOUT" if outcome.timed_out else "APPROVAL_REJECTED",
                    Severity.HIGH,
                    f"{'No decision before the deadline' if outcome.timed_out else 'Rejected'} by {outcome.decided_by}"
                    + (f": {outcome.comment}" if outcome.comment else ""),
                    {"request_id": req.request_id},
                    None,
                    Verdict.BLOCK,
                )
            )
            decision.verdict = Verdict.BLOCK
            return
        if outcome.edited_arguments is not None:
            # re-inspect what the human changed; a human can relax escalations, not hard blocks
            report = self.threats.analyze(decision.action.tool, outcome.edited_arguments)
            rbac = self.rbac.authorize(
                decision.action.agent_id,
                decision.action.tool,
                outcome.edited_arguments,
                report.profile,
            )
            hard = [f for f in [*report.findings, *rbac] if f.recommends is Verdict.BLOCK]
            if hard:
                decision.findings.extend(hard)
                decision.verdict = Verdict.BLOCK
                return
            decision.mutated_arguments = report.mutated_arguments or outcome.edited_arguments
            decision.verdict = Verdict.MUTATE_AND_ALLOW
        else:
            decision.verdict = Verdict.MUTATE_AND_ALLOW if mutated is not None else Verdict.ALLOW
            decision.mutated_arguments = mutated
        decision.findings.append(
            Finding(
                Stage.HITL,
                "APPROVED",
                Severity.INFO,
                f"Approved by {outcome.decided_by}"
                + (f": {outcome.comment}" if outcome.comment else ""),
                {"request_id": req.request_id},
                None,
                decision.verdict,
            )
        )

    def _after_decision(self, decision: Decision) -> None:
        a = decision.action
        loop_findings = [
            f
            for f in decision.findings
            if f.stage is Stage.LOOP
            and f.recommends is not Verdict.ALLOW
            and f.code != "CIRCUIT_OPEN"
        ]
        with self._lock:
            s = self.sessions.get(a.scope)
            if s is not None and s.agent_id == a.agent_id:
                s.decisions += 1
                s.blocked += decision.verdict is Verdict.BLOCK
                s.escalated += any(f.recommends is Verdict.ESCALATE_HITL for f in decision.findings)
                s.loops += bool(loop_findings)
            if loop_findings:
                n = self._loop_hits.get(a.scope, 0) + 1
                self._loop_hits[a.scope] = n
                if n >= self.config.trip_breaker_after_loops:
                    self.breaker(a.scope).trip(f"loop:{loop_findings[0].code}")
        if not decision.allowed:
            self.loops.record_result(a.call_id, success=False, output={"blocked": decision.reason})
        if decision.allowed and not self.config.record_allowed:
            self.telemetry.emit(_summary(decision))
            return
        doc = decision.to_dict()
        doc["arguments_mutated"] = decision.mutated_arguments is not None
        self._record(
            KIND_DECISION,
            doc,
            {
                "call_id": a.call_id,
                "agent_id": a.agent_id,
                "session_id": a.session_id,
                "tool": a.tool,
                "verdict": decision.verdict.value,
                "trace_id": a.trace_id,
                "parent_call_id": a.parent_call_id,
            },
            tenant_id=a.tenant_id,
        )
        self.telemetry.emit(_summary(decision))

    # ── execution ──────────────────────────────────────────────────────────────────
    def _claim(self, call_id: str) -> None:
        with self._lock:
            if call_id in self._executed:
                raise ActionBlocked(_replay_decision(call_id))
            self._executed[call_id] = time.time()
            while len(self._executed) > 100_000:
                self._executed.popitem(last=False)

    def inspect_output(self, action: ToolAction, result: Any) -> OutputInspection:
        t0 = time.perf_counter()
        try:
            inspection = self.output.inspect(action.tool, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("output inspection failed")
            if not self.config.fail_closed:
                return OutputInspection(safe=True, sanitized_output=result)
            inspection = OutputInspection(
                safe=False,
                sanitized_output=f"[AGENTWATCH: the result of '{action.tool}' was withheld: output inspection failed ({type(exc).__name__})]",
                findings=[
                    Finding(
                        Stage.OUTPUT,
                        "STAGE_ERROR",
                        Severity.CRITICAL,
                        f"output inspection failed: {exc}",
                        {},
                        None,
                        Verdict.BLOCK,
                    )
                ],
                withheld=True,
            )
        ms = round((time.perf_counter() - t0) * 1000, 3)
        if inspection.findings:
            self._record(
                KIND_OUTPUT,
                {
                    "call_id": action.call_id,
                    "tool": action.tool,
                    "agent_id": action.agent_id,
                    "session_id": action.session_id,
                    **inspection.to_dict(),
                    "inspect_ms": ms,
                },
                {
                    "call_id": action.call_id,
                    "agent_id": action.agent_id,
                    "session_id": action.session_id,
                    "tool": action.tool,
                    "trace_id": action.trace_id,
                },
                tenant_id=action.tenant_id,
            )
        self.telemetry.emit(
            {
                "type": "output",
                "call_id": action.call_id,
                "agent_id": action.agent_id,
                "session_id": action.session_id,
                "tool": action.tool,
                "safe": inspection.safe,
                "findings": [f.code for f in inspection.findings],
                "timings_ms": {"output": ms},
            }
        )
        return inspection

    def _finish(self, action: ToolAction, *, success: bool, result: Any) -> None:
        self.loops.record_result(action.call_id, success=success, output=result)
        b = self.breaker(action.scope)
        b.observe(
            AgentEvent(
                session_id=action.scope,
                agent_id=action.agent_id,
                framework=AgentFramework.CUSTOM,
                event_type=EventType.TOOL_RESULT if success else EventType.TOOL_ERROR,
                status=ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILURE,
            )
        )

    def execute(
        self,
        action: ToolAction,
        fn: Callable[..., Any],
        *,
        call: Callable[[dict[str, Any]], Any] | None = None,
    ) -> Any:
        """Decide, run ``fn(**arguments)`` (or ``call(arguments)``) at most once, inspect the result.

        Raises :class:`ActionBlocked` when the verdict does not allow the call; ``fn`` is
        then never invoked.
        """
        decision = self.evaluate(action)
        if not decision.allowed:
            raise ActionBlocked(decision)
        self._claim(action.call_id)
        args = (
            decision.mutated_arguments
            if decision.mutated_arguments is not None
            else action.arguments
        )
        try:
            result = call(args) if call is not None else fn(**args)
        except Exception as exc:
            self._finish(action, success=False, result=repr(exc))
            raise
        self._finish(action, success=_looks_successful(result), result=result)
        if not self.config.output_inspection:
            return result
        return self.inspect_output(action, result).sanitized_output

    async def aexecute(
        self,
        action: ToolAction,
        fn: Callable[..., Awaitable[Any]],
        *,
        call: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> Any:
        decision = await asyncio.to_thread(self.evaluate, action)
        if not decision.allowed:
            raise ActionBlocked(decision)
        self._claim(action.call_id)
        args = (
            decision.mutated_arguments
            if decision.mutated_arguments is not None
            else action.arguments
        )
        try:
            result = await (call(args) if call is not None else fn(**args))
        except Exception as exc:
            self._finish(action, success=False, result=repr(exc))
            raise
        self._finish(action, success=_looks_successful(result), result=result)
        if not self.config.output_inspection:
            return result
        return (await asyncio.to_thread(self.inspect_output, action, result)).sanitized_output

    def guard(
        self,
        tool: str | None = None,
        *,
        agent_id: str = "default-agent",
        session_id: str | Callable[[], str] = "default-session",
        objective: str | Callable[[], str | None] | None = None,
    ) -> Callable[[F], F]:
        """Decorator: every call of the function goes through the pipeline."""

        def deco(fn: F) -> F:
            name = tool or fn.__name__
            sig = inspect.signature(fn)

            def action_for(args: tuple[Any, ...], kwargs: dict[str, Any]) -> ToolAction:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                sid = session_id() if callable(session_id) else session_id
                obj = objective() if callable(objective) else objective
                return ToolAction(
                    agent_id=agent_id,
                    session_id=sid,
                    tool=name,
                    arguments=dict(bound.arguments),
                    objective=obj,
                    source="sdk",
                )

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def awrapper(*args: Any, **kwargs: Any) -> Any:
                    return await self.aexecute(action_for(args, kwargs), fn)

                return awrapper  # type: ignore[return-value]

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return self.execute(action_for(args, kwargs), fn)

            return wrapper  # type: ignore[return-value]

        return deco

    def failure_decision(
        self, action: ToolAction, exc: BaseException, *, executed: bool = False
    ) -> Decision:
        """The BLOCK decision for an action whose enforcement itself failed (recorded).

        ``executed`` says whether the tool had already run: then its result is withheld,
        and the record says so instead of claiming the action was prevented.
        """
        if executed:
            f = Finding(
                Stage.OUTPUT,
                "POST_EXECUTION_ERROR",
                Severity.CRITICAL,
                f"The tool ran, but enforcement failed afterwards ({type(exc).__name__}: {exc}); its result is withheld",
                {"error": type(exc).__name__, "executed": True},
                None,
                Verdict.BLOCK,
            )
        else:
            f = Finding(
                Stage.PIPELINE,
                "ENFORCEMENT_ERROR",
                Severity.CRITICAL,
                f"Enforcement failed ({type(exc).__name__}: {exc}); the tool did not run",
                {"error": type(exc).__name__, "executed": False},
                None,
                Verdict.BLOCK,
            )
        decision = Decision(action=action, verdict=Verdict.BLOCK, risk_score=1.0, findings=[f])
        try:
            self._after_decision(decision)
        except Exception:  # noqa: BLE001 - recording must not mask the block
            logger.exception("could not record the fail-closed decision")
        return decision

    # ── evidence ───────────────────────────────────────────────────────────────────
    def _record(
        self,
        kind: str,
        payload: dict[str, Any],
        declared: dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.record(kind, payload, declared, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001
            # The decision already stands; losing its evidence record is reported, not hidden.
            logger.exception("failed to record %s in the protect ledger", kind)
            self.telemetry.emit({"type": "ledger_error", "kind": kind})

    def status(self) -> dict[str, Any]:
        with self._lock:
            sessions = [
                vars(s)
                | {
                    "breaker": self._breakers[s.scope].status()["state"]
                    if s.scope in self._breakers
                    else "closed"
                }
                for s in self.sessions.values()
            ]
        return {
            "sessions": sessions,
            "registered_agents": sorted(self.registered_agents),
            "policy": self.policy.status(),
            "control": self.control.snapshot().to_dict(),
            "telemetry": self.telemetry.stats(),
            "config": {
                k: (v.value if isinstance(v, Verdict) else v) for k, v in vars(self.config).items()
            },
        }


def _blocked(findings: list[Finding]) -> bool:
    return any(f.recommends is Verdict.BLOCK for f in findings)


def _last_reason(b: CircuitBreaker) -> str:
    h = b.history
    return h[-1].reason if h else "tripped"


def _looks_successful(result: Any) -> bool:
    if isinstance(result, dict):
        if (
            result.get("isError") is True
            or result.get("error") not in (None, "", False)
            or result.get("success") is False
        ):
            return False
        code = result.get("exit_code", result.get("returncode", result.get("code")))
        if isinstance(code, int) and code != 0:
            return False
    return True


def _summary(d: Decision) -> dict[str, Any]:
    return {
        "type": "decision",
        "call_id": d.action.call_id,
        "agent_id": d.action.agent_id,
        "session_id": d.action.session_id,
        "tool": d.action.tool,
        "source": d.action.source,
        "verdict": d.verdict.value,
        "risk_score": d.risk_score,
        "reason": d.reason,
        "codes": [f.code for f in d.findings if f.severity.rank >= Severity.MEDIUM.rank],
        "timings_ms": d.timings_ms,
        "at": d.decided_at,
    }


def _replay_decision(call_id: str) -> Decision:
    a = ToolAction(agent_id="unknown", session_id="unknown", tool="unknown", call_id=call_id)
    f = Finding(
        Stage.EXECUTION,
        "DUPLICATE_EXECUTION",
        Severity.CRITICAL,
        f"Call {call_id} was already executed; refusing to run it twice",
        {},
        None,
        Verdict.BLOCK,
    )
    return Decision(action=a, verdict=Verdict.BLOCK, risk_score=1.0, findings=[f])
