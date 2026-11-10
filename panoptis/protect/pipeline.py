"""The authoritative enforcement pipeline.

One code path decides each call routed to it, whichever integration proposed it (SDK decorator,
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

from panoptis.circuit_breaker.breaker import CircuitBreaker, CircuitState
from panoptis.circuit_breaker.thresholds import CircuitThresholds
from panoptis.core.schema import AgentEvent, AgentFramework, EventType, ExecutionStatus
from panoptis.protect.approvals import ApprovalBroker, action_digest
from panoptis.protect.control import ControlState
from panoptis.protect.intent import GroundingConfig, IntentGrounder
from panoptis.protect.ledger import (
    KIND_AGENT,
    KIND_DECISION,
    KIND_EXECUTION,
    KIND_OUTPUT,
    ProtectLedger,
)
from panoptis.protect.loops import LoopConfig, LoopDetector
from panoptis.protect.model import (
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
from panoptis.protect.output import OutputInspector
from panoptis.protect.rbac import PolicyStore, RBACEngine
from panoptis.protect.threats import THREAT_RULES_VERSION, ThreatAnalyzer

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
_AGENT_ID = re.compile(r"^[A-Za-z0-9_.:@/\-]{1,128}$")


@dataclass
class ProtectionConfig:
    fail_closed: bool = True
    # "enforce" (default) blocks, mutates and sanitizes. "observe" is a compatibility mode:
    # every call is decided and recorded (mode="observe", would_block) but still runs with
    # its original arguments and unsanitized output. The kill switch and suspensions are
    # enforced in both modes.
    mode: str = "enforce"
    # what to do when a finding asks for human approval but no approval channel exists
    escalate_without_approver: Verdict = Verdict.BLOCK
    approval_timeout_s: float = 120.0
    require_registered_agents: bool = False
    bind_sessions: bool = True  # a session id belongs to the first agent that uses it
    # block calls whose agent/session identity was not supplied (no default identities)
    require_context: bool = False
    output_inspection: bool = True
    on_injection: str = "quarantine"  # quarantine | withhold | flag
    record_allowed: bool = True  # also record ALLOW decisions (full audit trail)
    trip_breaker_after_loops: int = 2  # confirmed loops in a session before the breaker opens
    breaker_cooldown_s: float = 30.0
    breaker_consecutive_errors: int = 5
    enable_wasi_sandbox: bool = False
    sandbox_config: Any = None
    enable_counterfactual_forking: bool = False
    counterfactual_config: Any = None
    enable_ifc: bool = False
    enable_ltl_trajectory: bool = False
    enable_integrity_guard: bool = False
    enable_temporal_simulation: bool = False
    temporal_simulation_config: Any = None
    enable_stego_neutralizer: bool = False
    enable_crescendo_drift: bool = False
    enable_memory_sentry: bool = False
    enable_rag_sanitizer: bool = False

    @classmethod
    def sovereign(cls, **kwargs: Any) -> ProtectionConfig:
        """Sovereign tier configuration: activates multi-turn IFC lattice, LTL trajectory,
        Crescendo drift, steganography neutralization, and runtime integrity."""
        return cls(
            enable_ifc=True,
            enable_ltl_trajectory=True,
            enable_integrity_guard=True,
            enable_stego_neutralizer=True,
            enable_crescendo_drift=True,
            enable_memory_sentry=True,
            enable_rag_sanitizer=True,
            **kwargs,
        )

    @classmethod
    def enclave(cls, **kwargs: Any) -> ProtectionConfig:
        """Enclave tier configuration: sovereign tier plus micro-sandboxing,
        counterfactual speculative execution, and 16-way temporal simulation."""
        return cls.sovereign(
            enable_wasi_sandbox=True,
            enable_counterfactual_forking=True,
            enable_temporal_simulation=True,
            **kwargs,
        )


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
        sandbox: Any = None,
        counterfactual: Any = None,
        ifc: Any = None,
        ltl: Any = None,
        integrity: Any = None,
        temporal_simulator: Any = None,
    ) -> None:
        self.config = config or ProtectionConfig()
        if self.config.mode not in ("enforce", "observe"):
            raise ValueError(f"mode must be 'enforce' or 'observe', not {self.config.mode!r}")
        if self.config.mode == "observe":
            logger.warning(
                "PANOPTIS protection is in OBSERVE mode: calls are recorded, nothing is "
                "blocked, rewritten or sanitized (except by the kill switch)"
            )
        self.policy = policy or PolicyStore()
        self.rbac = RBACEngine(self.policy)
        self.ledger = ledger
        self.approvals = approvals
        self.control = control or ControlState(ledger)
        self.threats = threats or ThreatAnalyzer()
        self.loops = loops or LoopDetector(LoopConfig())
        self.intent = intent or IntentGrounder(GroundingConfig())
        self.output = output or OutputInspector(on_injection=self.config.on_injection)
        if sandbox is not None:
            self.sandbox = sandbox
        elif self.config.enable_wasi_sandbox:
            from panoptis.protect.wasi_sandbox import WasiSandbox, WasiSandboxConfig

            self.sandbox = WasiSandbox(self.config.sandbox_config or WasiSandboxConfig())
        else:
            self.sandbox = None
        if counterfactual is not None:
            self.counterfactual = counterfactual
        elif self.config.enable_counterfactual_forking:
            from panoptis.protect.counterfactual import (
                CounterfactualConfig,
                CounterfactualEngine,
            )

            self.counterfactual = CounterfactualEngine(
                self.config.counterfactual_config or CounterfactualConfig()
            )
        else:
            self.counterfactual = None
        if ifc is not None:
            self.ifc = ifc
        elif self.config.enable_ifc:
            from panoptis.protect.ifc_lattice import InformationFlowEngine

            self.ifc = InformationFlowEngine()
        else:
            self.ifc = None
        if ltl is not None:
            self.ltl = ltl
        elif self.config.enable_ltl_trajectory:
            from panoptis.protect.ltl_trajectory import LTLTrajectoryVerifier

            self.ltl = LTLTrajectoryVerifier()
        else:
            self.ltl = None
        if integrity is not None:
            self.integrity = integrity
        elif self.config.enable_integrity_guard:
            from panoptis.protect.integrity import RuntimeIntegrityGuard

            self.integrity = RuntimeIntegrityGuard()
            self.integrity.seal_function("evaluate", self, "evaluate")
            self.integrity.seal_function("execute", self, "execute")
            self.integrity.seal_function("rbac_authorize", self.rbac, "authorize")
        else:
            self.integrity = None
        if temporal_simulator is not None:
            self.temporal_simulator = temporal_simulator
        elif self.config.enable_temporal_simulation:
            from panoptis.protect.temporal_simulator import (
                FastForwardSimulationEngine,
                SimulationConfig,
            )

            self.temporal_simulator = FastForwardSimulationEngine(
                self.config.temporal_simulation_config or SimulationConfig()
            )
        else:
            self.temporal_simulator = None
        if self.config.enable_stego_neutralizer:
            from panoptis.protect.stego_neutralizer import StegoNeutralizer

            # arguments and results are real content: NFKC would rewrite it silently
            self.stego: StegoNeutralizer | None = StegoNeutralizer(
                normalize_unicode=False, preserve_markdown_breaks=True
            )
        else:
            self.stego = None
        if self.config.enable_crescendo_drift:
            from panoptis.protect.crescendo_drift import CrescendoDriftMonitor

            self.crescendo: CrescendoDriftMonitor | None = CrescendoDriftMonitor()
        else:
            self.crescendo = None
        if self.config.enable_memory_sentry:
            from panoptis.protect.memory_sentry import MemorySentry

            self.memory_sentry: MemorySentry | None = MemorySentry()
        else:
            self.memory_sentry = None
        if self.config.enable_rag_sanitizer:
            from panoptis.protect.rag_sanitizer import RAGSanitizer

            self.rag_sanitizer: RAGSanitizer | None = RAGSanitizer()
        else:
            self.rag_sanitizer = None
        self.telemetry = Telemetry()
        self.sessions: dict[str, SessionInfo] = {}
        self.registered_agents: set[str] = set()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._loop_hits: OrderedDict[str, int] = OrderedDict()
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
            tenant_id=tenant_id,
        )
        return info

    def set_objective(self, session_id: str, objective: str, *, tenant_id: str = "default") -> None:
        """Change a started session's objective (recorded). An unknown session is an error."""
        scope = f"{tenant_id}/{session_id}"
        with self._lock:
            info = self.sessions.get(scope)
            if info is None:
                raise KeyError(f"session {session_id!r} has not been started")
            info.objective = objective
        self._record(
            KIND_AGENT,
            {
                "event": "objective_set",
                "agent_id": info.agent_id,
                "session_id": session_id,
                "objective": objective,
            },
            {"agent_id": info.agent_id, "session_id": session_id},
            tenant_id=tenant_id,
        )

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
        if self.config.enable_integrity_guard and self.integrity is not None:
            findings += timed("integrity", lambda: self.integrity.verify_integrity()[1])
        if self.config.enable_ltl_trajectory and self.ltl is not None:
            findings += timed("ltl", lambda: self.ltl.verify_action(action))
        if not _blocked(findings):
            profile_box: list[Any] = []

            def threat() -> list[Finding]:
                report = self.threats.analyze(action.tool, action.arguments)
                profile_box.append(report.profile)
                nonlocal mutated
                mutated = report.mutated_arguments
                return report.findings

            findings += timed("threat", threat)
            if self.config.enable_ifc and self.ifc is not None:
                findings += timed(
                    "ifc",
                    lambda: self.ifc.audit_tool_call(
                        action.tool, action.arguments, scope=action.scope
                    )[1],
                )
            stego = self.stego
            if stego is not None and not _blocked(findings):

                def stego_check() -> list[Finding]:
                    nonlocal mutated
                    target_args = mutated if mutated is not None else action.arguments
                    stego_res = stego.neutralize(target_args)
                    if stego_res.bits_neutralized_est > 0 or stego_res.had_covert_channel:
                        mutated = stego_res.cleaned_payload
                        if stego_res.findings:
                            return stego_res.findings
                        return [
                            Finding(
                                Stage.THREAT,
                                "STEGO_WHITESPACE_CLEANSED",
                                Severity.LOW,
                                f"Cleansed {stego_res.bits_neutralized_est} trailing whitespace / covert bits from tool arguments",
                                {"bits": stego_res.bits_neutralized_est},
                                None,
                                Verdict.MUTATE_AND_ALLOW,
                            )
                        ]
                    return []

                findings += timed("stego", stego_check)
            if self.config.enable_crescendo_drift and self.crescendo is not None:

                def crescendo_check() -> list[Finding]:
                    if self.crescendo is None:
                        return []
                    assessment = self.crescendo.record_and_evaluate_turn(
                        action.session_id,
                        user_input=objective or "",
                        action_name=action.tool,
                        arguments=action.arguments,
                    )
                    if assessment.verdict is Verdict.BLOCK:
                        return [
                            Finding(
                                Stage.THREAT,
                                f"CRESCENDO_{assessment.status.value}",
                                Severity.CRITICAL,
                                f"Crescendo drift detected: {assessment.reason}",
                                {
                                    "cumulative_drift": round(assessment.cumulative_drift, 3),
                                    "velocity": round(assessment.velocity, 3),
                                    "consecutive_rises": assessment.consecutive_risk_increases,
                                },
                                None,
                                Verdict.BLOCK,
                            )
                        ]
                    return []

                findings += timed("crescendo", crescendo_check)
            if self.config.enable_memory_sentry and self.memory_sentry is not None:

                def mem_check() -> list[Finding]:
                    if self.memory_sentry is None:
                        return []
                    if action.tool in ("store_memory", "remember", "save_fact", "add_memory"):
                        content = str(
                            action.arguments.get("content", action.arguments.get("value", ""))
                        )
                        insp = self.memory_sentry.inspect_write(
                            content=content,
                            author_id=action.agent_id,
                            session_id=action.session_id,
                            is_direct_user_input=False,
                        )
                        if insp.verdict is Verdict.BLOCK:
                            risk_code = (
                                insp.risk_type.value.upper() if insp.risk_type else "SUSPECTED"
                            )
                            return [
                                Finding(
                                    Stage.THREAT,
                                    f"MEMORY_POISON_{risk_code}",
                                    Severity.CRITICAL,
                                    f"Memory poisoning attempt: {insp.reason}",
                                    {
                                        "risk_type": (
                                            insp.risk_type.value if insp.risk_type else "unknown"
                                        )
                                    },
                                    None,
                                    Verdict.BLOCK,
                                )
                            ]
                    return []

                findings += timed("memory_sentry", mem_check)
            profile = profile_box[0] if profile_box else None
            if profile is not None:
                findings += timed(
                    "rbac",
                    lambda: self.rbac.authorize(
                        action.agent_id,
                        action.tool,
                        action.arguments,
                        profile,
                        tenant_id=action.tenant_id,
                        session_id=action.session_id,
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
                        context=(session.metadata.get("authorized_context") if session else None),
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

            if (
                self.config.enable_temporal_simulation
                and self.temporal_simulator is not None
                and not _blocked(findings)
            ):
                session_ctx = (
                    self.ltl.session_context(action)
                    if self.ltl is not None
                    else {"has_read_sensitive": False, "has_untrusted_ingest": False}
                )
                findings += timed(
                    "temporal_simulation",
                    lambda: self.temporal_simulator.simulate_action(action, session_ctx).findings,
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
            threat_rules_version=THREAT_RULES_VERSION,
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
        if self.config.require_context and action.defaulted_identity:
            return [
                Finding(
                    Stage.IDENTITY,
                    "MISSING_CONTEXT",
                    Severity.CRITICAL,
                    "No agent/session identity was supplied for this call and the policy requires one",
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

    def _emergency(self, action: ToolAction) -> list[Finding]:
        """Kill switch and suspensions: checked when deciding and again just before running."""
        hit = self.control.emergency(action.tenant_id, action.agent_id, action.session_id)
        if hit is None:
            return []
        code, message = hit
        snap = self.control.snapshot()
        meta: dict[str, Any] = {"actor": snap.kill_actor} if code == "KILL_SWITCH" else {}
        if code == "AGENT_SUSPENDED":
            meta = dict(snap.suspended_agents.get(f"{action.tenant_id}/{action.agent_id}") or {})
        elif code == "SESSION_SUSPENDED":
            meta = dict(
                snap.suspended_sessions.get(f"{action.tenant_id}/{action.session_id}") or {}
            )
        return [
            Finding(Stage.KILLSWITCH, code, Severity.CRITICAL, message, meta, None, Verdict.BLOCK)
        ]

    def _control(self, action: ToolAction) -> list[Finding]:
        stop = self._emergency(action)
        if stop:
            return stop
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

        def abort() -> str | None:
            stop = self._emergency(decision.action)
            return stop[0].message if stop else None

        outcome = self.approvals.wait(req, abort=abort)
        decision.approval = {
            "request_id": req.request_id,
            "state": "approved"
            if outcome.approved
            else (
                "cancelled"
                if outcome.cancelled
                else ("expired" if outcome.timed_out else "rejected")
            ),
            **outcome.to_dict(),
        }
        if outcome.approved and action_digest(decision.action) != req.action_digest:
            # the action changed after approval was requested: the approval does not cover it
            decision.findings.append(
                Finding(
                    Stage.HITL,
                    "APPROVAL_MISMATCH",
                    Severity.CRITICAL,
                    "The action changed after approval was requested; the approval does not cover it",
                    {"request_id": req.request_id},
                    None,
                    Verdict.BLOCK,
                )
            )
            decision.verdict = Verdict.BLOCK
            return
        if not outcome.approved:
            decision.findings.append(
                Finding(
                    Stage.HITL,
                    "APPROVAL_CANCELLED"
                    if outcome.cancelled
                    else ("APPROVAL_TIMEOUT" if outcome.timed_out else "APPROVAL_REJECTED"),
                    Severity.HIGH,
                    f"{'Cancelled' if outcome.cancelled else ('No decision before the deadline' if outcome.timed_out else 'Rejected')} by {outcome.decided_by}"
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
                n = self._loop_hits.pop(a.scope, 0) + 1
                self._loop_hits[a.scope] = n
                while len(self._loop_hits) > self.loops.config.max_sessions:
                    self._loop_hits.popitem(last=False)
                if n >= self.config.trip_breaker_after_loops:
                    self.breaker(a.scope).trip(f"loop:{loop_findings[0].code}")
        if not decision.allowed:
            self.loops.record_result(
                a.call_id, success=False, output={"blocked": decision.reason}, session_id=a.scope
            )
        if decision.allowed and not self.config.record_allowed:
            self.telemetry.emit(_summary(decision))
            return
        doc = decision.to_dict()
        doc["arguments_mutated"] = decision.mutated_arguments is not None
        doc["mode"] = self.config.mode
        if self.config.mode == "observe":
            doc["would_block"] = not decision.allowed
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
        if self.stego is not None and inspection.sanitized_output is not None:
            stego_res = self.stego.neutralize(inspection.sanitized_output)
            if stego_res.had_covert_channel:
                inspection.sanitized_output = stego_res.cleaned_payload
                inspection.findings.extend(stego_res.findings)
        if (
            self.rag_sanitizer is not None
            and result is not None
            and not inspection.withheld
            and action.tool
            in ("search", "fetch_web", "read_kb", "query_docs", "rag_query", "web_search")
        ):
            from panoptis.protect.rag_sanitizer import RAGPoisonStatus

            # the inspected text, not the raw result: this output replaces it below, and must
            # not bring back what output inspection redacted
            inspected = inspection.sanitized_output
            rag_res = self.rag_sanitizer.sanitize_chunk(
                str(result if inspected is None else inspected), source_id=action.tool
            )
            if (
                rag_res.status == RAGPoisonStatus.POISON_DETECTED_BLOCKED
                or rag_res.verdict is Verdict.BLOCK
            ):
                inspection.findings.append(
                    Finding(
                        Stage.OUTPUT,
                        "RAG_POISON_DETECTED",
                        Severity.CRITICAL,
                        f"Retrieved document poisoned: {rag_res.status.value}",
                        {"imperative_ratio": round(rag_res.imperative_entropy_ratio, 3)},
                        None,
                        Verdict.BLOCK,
                    )
                )
                if self.config.fail_closed:
                    inspection.safe = False
                    inspection.withheld = True
                    inspection.sanitized_output = f"[AGENTWATCH: retrieval from '{action.tool}' withheld due to adversarial prompt injection]"
            elif rag_res.detected_injections:
                inspection.findings.append(
                    Finding(
                        Stage.OUTPUT,
                        "RAG_POISON_NEUTRALIZED",
                        Severity.HIGH,
                        f"Retrieved document sanitized: {len(rag_res.detected_injections)} injection patterns defused",
                        {"injections": rag_res.detected_injections},
                        None,
                        Verdict.MUTATE_AND_ALLOW,
                    )
                )
                inspection.sanitized_output = rag_res.sanitized_text
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

    def _finish(
        self,
        action: ToolAction,
        *,
        success: bool,
        result: Any,
        started: float | None = None,
        error: BaseException | None = None,
        mutated: bool = False,
    ) -> None:
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 3) if started is not None else None
        )
        self.record_execution(
            action,
            success=success,
            result=result,
            duration_ms=duration_ms,
            error=getattr(error, "original", error),  # the tool's own exception, not a wrapper
            mutated=mutated,
        )

    def record_execution(
        self,
        action: ToolAction,
        *,
        success: bool,
        result: Any = None,
        duration_ms: float | None = None,
        error: BaseException | str | None = None,
        mutated: bool = False,
    ) -> None:
        """Record that ``action`` ran, for runtimes that execute the tool themselves
        (a Claude Code hook sees the call before and after, but never runs it)."""
        error_name = error if isinstance(error, str) or error is None else type(error).__name__
        self._record(
            KIND_EXECUTION,
            {
                "call_id": action.call_id,
                "tool": action.tool,
                "agent_id": action.agent_id,
                "session_id": action.session_id,
                "outcome": "success" if success else "failure",
                "error": error_name,
                "duration_ms": duration_ms,
                "arguments_mutated": mutated,
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
        self.loops.record_result(
            action.call_id, success=success, output=result, session_id=action.scope
        )
        if self.ltl is not None and success:
            self.ltl.record_step(action, result=result)
        if self.ifc is not None and success and result is not None:
            self.ifc.record_result(action.tool, result, scope=action.scope)
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
        decision = self.evaluate(action, wait_for_approval=not self.observing)
        args = self._admit(action, decision)
        try:
            started = time.perf_counter()
            if self.counterfactual is not None:
                cf_res = self.counterfactual.fork_and_evaluate(
                    action,
                    lambda **kw: call(kw) if call is not None else fn(**kw),
                    args,
                )
                if not cf_res.is_safe:
                    finding = (
                        cf_res.findings[0]
                        if cf_res.findings
                        else Finding(
                            Stage.COUNTERFACTUAL,
                            "COUNTERFACTUAL_BLOCKED",
                            Severity.CRITICAL,
                            "Speculative counterfactual execution violated invariants",
                            recommends=Verdict.BLOCK,
                        )
                    )
                    block_decision = Decision(
                        action=action,
                        verdict=Verdict.BLOCK,
                        risk_score=1.0,
                        findings=[finding],
                    )
                    raise ActionBlocked(block_decision)
                if cf_res.promoted:
                    result = cf_res.output
                elif self.sandbox is not None:
                    sandbox_res = self.sandbox.run_function(
                        lambda: call(args) if call is not None else fn(**args),
                        rollback_on_error=True,
                    )
                    result = sandbox_res.output
                else:
                    result = call(args) if call is not None else fn(**args)
            elif self.sandbox is not None:
                sandbox_res = self.sandbox.run_function(
                    lambda: call(args) if call is not None else fn(**args),
                    rollback_on_error=True,
                )
                result = sandbox_res.output
            else:
                result = call(args) if call is not None else fn(**args)
        except Exception as exc:
            self._finish(
                action,
                success=False,
                result=repr(exc),
                started=started,
                error=exc,
                mutated=args is not action.arguments,
            )
            raise
        self._finish(
            action,
            success=_looks_successful(result),
            result=result,
            started=started,
            mutated=args is not action.arguments,
        )
        if not self.config.output_inspection:
            return result
        if self.observing:
            if not (inspect.isgenerator(result) or inspect.isasyncgen(result)):
                self.inspect_output(action, result)  # recorded, not applied
            return result
        if inspect.isgenerator(result):
            return self._inspect_stream(action, result)
        return self.inspect_output(action, result).sanitized_output

    async def aexecute(
        self,
        action: ToolAction,
        fn: Callable[..., Awaitable[Any]],
        *,
        call: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> Any:
        try:
            decision = await asyncio.to_thread(
                functools.partial(self.evaluate, wait_for_approval=not self.observing), action
            )
        except asyncio.CancelledError:
            # the caller went away while a human was being asked: withdraw the request
            if self.approvals is not None:
                self.approvals.cancel_for_call(action.call_id)
            raise
        args = self._admit(action, decision)
        try:
            started = time.perf_counter()
            result = await (call(args) if call is not None else fn(**args))
        except asyncio.CancelledError as exc:
            # cancelled mid-flight: the tool may have had side effects; record it, then propagate
            self._finish(
                action,
                success=False,
                result="cancelled",
                started=started,
                error=exc,
                mutated=args is not action.arguments,
            )
            raise
        except Exception as exc:
            self._finish(
                action,
                success=False,
                result=repr(exc),
                started=started,
                error=exc,
                mutated=args is not action.arguments,
            )
            raise
        self._finish(
            action,
            success=_looks_successful(result),
            result=result,
            started=started,
            mutated=args is not action.arguments,
        )
        if not self.config.output_inspection:
            return result
        if self.observing:
            if not (inspect.isgenerator(result) or inspect.isasyncgen(result)):
                await asyncio.to_thread(self.inspect_output, action, result)
            return result
        if inspect.isasyncgen(result):
            return self._ainspect_stream(action, result)
        if inspect.isgenerator(result):
            return self._inspect_stream(action, result)
        return (await asyncio.to_thread(self.inspect_output, action, result)).sanitized_output

    @property
    def observing(self) -> bool:
        return self.config.mode == "observe"

    def _admit(self, action: ToolAction, decision: Decision) -> dict[str, Any]:
        """The arguments the tool runs with, or :class:`ActionBlocked`."""
        emergency = any(f.stage is Stage.KILLSWITCH for f in decision.findings)
        if not decision.allowed and (not self.observing or emergency):
            raise ActionBlocked(decision)
        # an emergency stop or suspension issued while the call was being decided (or waited
        # for approval) still prevents it: checked again right before the tool runs
        stop = self._emergency(action)
        if stop:
            decision.findings.extend(stop)
            decision.verdict = Verdict.BLOCK
            raise ActionBlocked(decision)
        approved_for = (decision.approval or {}).get("action_digest")
        if approved_for and decision.allowed and approved_for != action_digest(action):
            # the arguments were changed between the approval and the execution
            decision.verdict = Verdict.BLOCK
            raise ActionBlocked(decision)
        self._claim(action.call_id)
        if self.observing or decision.mutated_arguments is None:
            return action.arguments
        return decision.mutated_arguments

    def _stop_stream(self, action: ToolAction) -> None:
        stop = self._emergency(action)
        if stop:
            decision = Decision(action=action, verdict=Verdict.BLOCK, risk_score=1.0, findings=stop)
            self._after_decision(decision)
            raise ActionBlocked(decision)

    def _inspect_stream(self, action: ToolAction, stream: Any) -> Any:
        """A streamed result: every chunk is inspected and sanitized before it is yielded;
        an emergency stop ends the stream (the agent gets no further chunks)."""
        for chunk in stream:
            self._stop_stream(action)
            yield self.inspect_output(action, chunk).sanitized_output

    async def _ainspect_stream(self, action: ToolAction, stream: Any) -> Any:
        async for chunk in stream:
            self._stop_stream(action)
            yield (await asyncio.to_thread(self.inspect_output, action, chunk)).sanitized_output

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
            "mode": self.config.mode,
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
