"""Panopti Guard: Modern decorator and pre-execution interceptor for AI agent tools."""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from agentwatch.causality.decay import (
    ConfidenceDecayTracker,
)
from agentwatch.circuit_breaker.loop_detector import AgentLoopError, LoopDetector
from agentwatch.core.safety import DEFAULT_POLICY, RiskPattern, SafetyEngine
from agentwatch.core.schema import ToolCallData
from agentwatch.core.watcher import AgentWatchBlockedError, watch
from panopti.contracts import (
    Contract,
    ContractRegistry,
    ContractViolationError,
    MaxFrequency,
    RequiresPrior,
)
from panopti.killswitch import EmergencyKillSwitch, EmergencyStopTriggered

try:
    from agentwatch.contracts.streaming import StreamingContractEvaluator
except (ImportError, ModuleNotFoundError):
    StreamingContractEvaluator = Any  # type: ignore[misc,assignment]

logger = logging.getLogger("panopti.guard")

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class GuardDecision:
    """Pre-execution assessment result for a proposed tool action."""

    allowed: bool
    blocked: bool
    risk_level: str
    risk_score: float
    reason: str | None
    reasons: list[str]


class PanoptiGuard:
    """Active runtime guardrail engine."""

    def __init__(
        self,
        critical_confidence_floor: float = 0.40,
        extra_patterns: list[RiskPattern] | None = None,
        kill_switch: EmergencyKillSwitch | None = None,
        streaming_evaluator: StreamingContractEvaluator | None = None,
    ) -> None:
        if extra_patterns:
            policy = replace(DEFAULT_POLICY, custom_patterns=list(extra_patterns))
            self._safety_engine = SafetyEngine(policy=policy)
        else:
            self._safety_engine = SafetyEngine()
        self._loop_detector = LoopDetector(window_size=10, max_repeats=3, max_oscillations=2)
        self._contracts = ContractRegistry()
        self._confidence_tracker = ConfidenceDecayTracker(critical_floor=critical_confidence_floor)
        self._kill_switch = kill_switch
        self._streaming_evaluator = streaming_evaluator

    @property
    def streaming_evaluator(self) -> StreamingContractEvaluator | None:
        """Attached streaming contract evaluator, if configured."""
        return self._streaming_evaluator

    @streaming_evaluator.setter
    def streaming_evaluator(self, value: StreamingContractEvaluator | None) -> None:
        self._streaming_evaluator = value

    @property
    def kill_switch(self) -> EmergencyKillSwitch | None:
        """Attached emergency kill switch instance, if configured."""
        return self._kill_switch

    @kill_switch.setter
    def kill_switch(self, value: EmergencyKillSwitch | None) -> None:
        self._kill_switch = value

    def register_contract(self, contract: Contract) -> None:
        """Register a temporal or state invariant contract."""
        self._contracts.add(contract)

    def record_execution(self, tool_name: str, arguments: dict[str, Any] | None = None) -> None:
        """Record successful tool execution in history for temporal contract tracking."""
        self._contracts.record_success(tool_name, arguments)

    def check(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> GuardDecision:
        """Evaluate a tool invocation against policies, contracts, decay, and loops."""
        args = arguments or {}

        # 0. Emergency kill switch check
        if self._kill_switch and self._kill_switch.is_tripped():
            raise EmergencyStopTriggered(self._kill_switch.reason, self._kill_switch.tripped_at)

        # 1. Compounding confidence decay check
        if confidence is not None:
            step_record = self._confidence_tracker.add_step(confidence)
            if step_record.warning:
                msg = f"{step_record.warning.code}: {step_record.warning.message}"
                return GuardDecision(
                    allowed=False,
                    blocked=True,
                    risk_level="CRITICAL",
                    risk_score=1.0,
                    reason=msg,
                    reasons=[msg],
                )

        # 2. Loop and oscillation check
        loop_warning = self._loop_detector.record(tool_name, args)
        if loop_warning:
            return GuardDecision(
                allowed=False,
                blocked=True,
                risk_level="CRITICAL",
                risk_score=1.0,
                reason=loop_warning,
                reasons=[loop_warning],
            )

        # 2. Invariant contracts check
        contract_allowed, contract_violation = self._contracts.evaluate(tool_name, args)
        if not contract_allowed:
            return GuardDecision(
                allowed=False,
                blocked=True,
                risk_level="HIGH",
                risk_score=0.9,
                reason=contract_violation,
                reasons=[contract_violation or "Contract violation"],
            )

        # 3. Safety policy check
        raw_cmd = args.get("cmd") or args.get("command") or args.get("script") or None
        tool_data = ToolCallData(
            tool_name=tool_name,
            arguments=args,
            raw_command=str(raw_cmd) if raw_cmd else None,
        )
        safety_result = self._safety_engine.evaluate(tool_data)

        risk_val = (
            safety_result.risk_level.value
            if hasattr(safety_result.risk_level, "value")
            else str(safety_result.risk_level)
        ).upper()

        if safety_result.blocked:
            reason = (
                safety_result.reasons[0]
                if safety_result.reasons
                else "Action blocked by safety policy"
            )
            return GuardDecision(
                allowed=False,
                blocked=True,
                risk_level=risk_val,
                risk_score=safety_result.risk_score,
                reason=reason,
                reasons=safety_result.reasons,
            )

        return GuardDecision(
            allowed=True,
            blocked=False,
            risk_level=risk_val,
            risk_score=safety_result.risk_score,
            reason=None,
            reasons=[],
        )

    def tool(
        self,
        name: str | None = None,
        *,
        max_repeats: int = 3,
        requires_prior: str | None = None,
        max_frequency: tuple[int, int] | None = None,
        raise_on_block: bool = True,
    ) -> Callable[[F], F]:
        """Decorator shielding a tool function with pre-execution safety and invariant contracts."""

        def decorator(fn: F) -> F:
            tool_name = name or fn.__name__
            sig = inspect.signature(fn)

            if requires_prior:
                self.register_contract(RequiresPrior(tool_name, requires_prior))
            if max_frequency:
                max_invocations, window_steps = max_frequency
                self.register_contract(
                    MaxFrequency(
                        tool_name,
                        max_invocations=max_invocations,
                        window_steps=window_steps,
                    )
                )

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                arguments = bound.arguments

                decision = self.check(tool_name, arguments)
                if decision.blocked:
                    reason_lower = (decision.reason or "").lower()
                    if "loop detected" in reason_lower or "oscillation" in reason_lower:
                        if raise_on_block:
                            raise AgentLoopError(
                                decision.reason or "Loop detected",
                                pattern=tool_name,
                                occurrences=max_repeats,
                            )
                    if "contract violation" in reason_lower:
                        if raise_on_block:
                            raise ContractViolationError(
                                decision.reason or "Contract violation",
                                contract_name=tool_name,
                                tool_name=tool_name,
                            )
                    if raise_on_block:
                        raise AgentWatchBlockedError(
                            decision.reason or "Blocked by Panopti",
                            tool_name=tool_name,
                            reasons=decision.reasons,
                        )
                    return {"error": decision.reason, "blocked": True}

                result = fn(*args, **kwargs)
                self.record_execution(tool_name, arguments)
                return result

            return wrapper  # type: ignore[return-value]

        return decorator

    @staticmethod
    def watch(agent: Any) -> Any:
        """Attach universal passive/active instrumentation to any supported agent framework."""
        return watch(agent)


# Default singleton instance
guard = PanoptiGuard()
