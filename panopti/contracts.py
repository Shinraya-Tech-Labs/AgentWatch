"""Panopti Invariant Contracts: Pre-execution temporal logic and invariants for AI agents."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

try:
    from panoptis.core.watcher import AgentWatchBlockedError
except (ImportError, ModuleNotFoundError):

    class AgentWatchBlockedError(Exception):  # type: ignore[no-redef]
        def __init__(
            self,
            message: str,
            tool_name: str = "",
            reasons: list[str] | None = None,
        ) -> None:
            super().__init__(message)
            self.tool_name = tool_name
            self.reasons = reasons or [message]


logger = logging.getLogger("panopti.contracts")


class ContractViolationError(AgentWatchBlockedError):
    """Raised when an agent's proposed action violates an invariant contract."""

    def __init__(self, message: str, contract_name: str, tool_name: str) -> None:
        super().__init__(message, tool_name=tool_name, reasons=[message])
        self.contract_name = contract_name


@dataclass(frozen=True)
class ExecutionEvent:
    """Historical execution record of an allowed tool call."""

    step: int
    tool_name: str
    arguments: dict[str, Any]
    success: bool = True


class Contract(ABC):
    """Abstract base class for all Panopti invariant contracts."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the contract rule."""

    @abstractmethod
    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        history: list[ExecutionEvent],
    ) -> tuple[bool, str | None]:
        """Evaluate whether tool invocation is allowed given the execution history.

        Returns:
            (allowed: bool, violation_reason: str | None)
        """


class RequiresPrior(Contract):
    """Enforces that target tool cannot execute unless a prior tool succeeded."""

    def __init__(
        self,
        target_tool: str,
        required_prior: str,
        within_steps: int | None = None,
        custom_message: str | None = None,
    ) -> None:
        self.target_tool = target_tool
        self.required_prior = required_prior
        self.within_steps = within_steps
        self.custom_message = custom_message

    @property
    def name(self) -> str:
        return f"RequiresPrior({self.target_tool} -> {self.required_prior})"

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        history: list[ExecutionEvent],
    ) -> tuple[bool, str | None]:
        if tool_name != self.target_tool:
            return True, None

        # Inspect history for required_prior
        found_step: int | None = None
        for event in reversed(history):
            if event.tool_name == self.required_prior and event.success:
                found_step = event.step
                break

        if found_step is None:
            msg = (
                self.custom_message
                or f"Contract violation: '{self.target_tool}' requires prior successful execution of '{self.required_prior}', which was never executed."
            )
            return False, msg

        if self.within_steps is not None:
            current_step = len(history)
            if (current_step - found_step) > self.within_steps:
                msg = (
                    self.custom_message
                    or f"Contract violation: '{self.target_tool}' requires '{self.required_prior}' within the last {self.within_steps} steps (last executed {current_step - found_step} steps ago)."
                )
                return False, msg

        return True, None


class Disallows(Contract):
    """Unconditionally or conditionally disallows a specific tool invocation."""

    def __init__(
        self,
        target_tool: str,
        reason: str = "Tool is explicitly disallowed by policy",
    ) -> None:
        self.target_tool = target_tool
        self.reason = reason

    @property
    def name(self) -> str:
        return f"Disallows({self.target_tool})"

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        history: list[ExecutionEvent],
    ) -> tuple[bool, str | None]:
        if tool_name == self.target_tool:
            return False, f"Contract violation: {self.reason}"
        return True, None


class MaxFrequency(Contract):
    """Limits the number of times a tool may be invoked within a sliding step window."""

    def __init__(
        self,
        target_tool: str,
        max_invocations: int,
        window_steps: int = 10,
        custom_message: str | None = None,
    ) -> None:
        self.target_tool = target_tool
        self.max_invocations = max_invocations
        self.window_steps = window_steps
        self.custom_message = custom_message

    @property
    def name(self) -> str:
        return f"MaxFrequency({self.target_tool} <= {self.max_invocations}/{self.window_steps})"

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        history: list[ExecutionEvent],
    ) -> tuple[bool, str | None]:
        if tool_name != self.target_tool:
            return True, None

        recent_history = history[-self.window_steps :] if history else []
        count = sum(1 for e in recent_history if e.tool_name == self.target_tool)

        if count >= self.max_invocations:
            msg = (
                self.custom_message
                or f"Contract violation: Tool '{self.target_tool}' exceeded maximum frequency ({self.max_invocations} calls per {self.window_steps} steps)."
            )
            return False, msg

        return True, None


class ContractRegistry:
    """Manages and evaluates active invariant contracts."""

    def __init__(self) -> None:
        self._contracts: list[Contract] = []
        self._history: list[ExecutionEvent] = []
        self._step_counter: int = 0

    def add(self, contract: Contract) -> None:
        """Register an invariant contract."""
        self._contracts.append(contract)

    def record_success(self, tool_name: str, arguments: dict[str, Any] | None = None) -> None:
        """Record a successful tool execution into history."""
        self._step_counter += 1
        self._history.append(
            ExecutionEvent(
                step=self._step_counter,
                tool_name=tool_name,
                arguments=arguments or {},
                success=True,
            )
        )

    def evaluate(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> tuple[bool, str | None]:
        """Check all registered contracts against a proposed tool invocation."""
        args = arguments or {}
        for contract in self._contracts:
            allowed, reason = contract.evaluate(tool_name, args, self._history)
            if not allowed:
                return False, reason
        return True, None

    def reset(self) -> None:
        """Reset historical events while preserving registered contracts."""
        self._history.clear()
        self._step_counter = 0
