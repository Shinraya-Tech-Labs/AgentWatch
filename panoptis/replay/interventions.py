"""Typed and validated interventions for counterfactual replay.

Supports step-specific interventions at step k:
- ToolResultMutation: mutate or substitute tool outputs.
- SimulatedAPIError: inject simulated API/tool errors or timeouts.
- PromptContextChange: mutate prompt text or context metadata.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus, ToolResultData


class InterventionType(StrEnum):
    TOOL_RESULT_MUTATION = "tool_result_mutation"
    SIMULATED_API_ERROR = "simulated_api_error"
    PROMPT_CONTEXT_CHANGE = "prompt_context_change"


def _validate_step_index(step: Any, name: str = "target_step") -> int:
    if isinstance(step, bool) or not isinstance(step, int):
        raise TypeError(f"{name} must be an integer, got {type(step).__name__}")
    if step < 0:
        raise ValueError(f"{name} must be non-negative (>= 0), got {step}")
    return step


@dataclass(frozen=True, slots=True)
class ToolResultMutation:
    """Intervention replacing or mutating a tool's output at step k."""

    target_step: int
    replacement: Any = None
    mutator: Callable[[Any], Any] | None = None
    tool_name: str | None = None
    tool_id: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        _validate_step_index(self.target_step)
        if self.mutator is not None and not callable(self.mutator):
            raise TypeError(f"mutator must be callable, got {type(self.mutator).__name__}")
        if self.replacement is None and self.mutator is None:
            raise ValueError("ToolResultMutation requires either replacement or mutator")
        if self.tool_name is not None and not isinstance(self.tool_name, str):
            raise TypeError(f"tool_name must be a string, got {type(self.tool_name).__name__}")
        if self.tool_id is not None and not isinstance(self.tool_id, str):
            raise TypeError(f"tool_id must be a string, got {type(self.tool_id).__name__}")

    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.TOOL_RESULT_MUTATION

    def apply(self, event: AgentEvent) -> AgentEvent:
        """Apply mutation to an event in-place or on clone."""
        if self.tool_name and event.tool_call and event.tool_call.tool_name != self.tool_name:
            return event
        if self.tool_id and event.tool_call and event.tool_call.tool_id != self.tool_id:
            return event

        original_output = event.tool_result.output if event.tool_result else None
        if self.mutator is not None:
            new_output = self.mutator(original_output)
        else:
            new_output = copy.deepcopy(self.replacement)

        tool_name = (
            event.tool_call.tool_name
            if event.tool_call
            else (event.tool_result.tool_name if event.tool_result else "unknown")
        )
        if event.tool_result is None:
            event.tool_result = ToolResultData(tool_name=tool_name, output=new_output)
        else:
            event.tool_result.output = new_output
            event.tool_result.error = None

        event.metadata["counterfactual"] = True
        event.metadata["intervention_type"] = self.intervention_type.value
        event.metadata["intervention_notes"] = self.notes
        return event


@dataclass(frozen=True, slots=True)
class SimulatedAPIError:
    """Intervention injecting a synthetic API error or failure at step k."""

    target_step: int
    error_message: str = "Simulated API error"
    error_type: str = "SimulatedAPIError"
    status_code: int | None = None
    tool_name: str | None = None
    tool_id: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        _validate_step_index(self.target_step)
        if not isinstance(self.error_message, str):
            raise TypeError(
                f"error_message must be a string, got {type(self.error_message).__name__}"
            )
        if not isinstance(self.error_type, str):
            raise TypeError(f"error_type must be a string, got {type(self.error_type).__name__}")
        if self.status_code is not None:
            if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
                raise TypeError(
                    f"status_code must be an integer, got {type(self.status_code).__name__}"
                )
        if self.tool_name is not None and not isinstance(self.tool_name, str):
            raise TypeError(f"tool_name must be a string, got {type(self.tool_name).__name__}")
        if self.tool_id is not None and not isinstance(self.tool_id, str):
            raise TypeError(f"tool_id must be a string, got {type(self.tool_id).__name__}")

    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.SIMULATED_API_ERROR

    def apply(self, event: AgentEvent) -> AgentEvent:
        """Apply simulated error to an event."""
        if self.tool_name and event.tool_call and event.tool_call.tool_name != self.tool_name:
            return event
        if self.tool_id and event.tool_call and event.tool_call.tool_id != self.tool_id:
            return event

        tool_name = (
            event.tool_call.tool_name
            if event.tool_call
            else (event.tool_result.tool_name if event.tool_result else "unknown")
        )
        err_msg = f"{self.error_type}: {self.error_message}"
        if self.status_code:
            err_msg += f" (status={self.status_code})"

        if event.tool_result is None:
            event.tool_result = ToolResultData(tool_name=tool_name, output=None, error=err_msg)
        else:
            event.tool_result.output = None
            event.tool_result.error = err_msg

        event.event_type = EventType.TOOL_ERROR
        event.status = ExecutionStatus.FAILURE
        event.metadata["counterfactual"] = True
        event.metadata["intervention_type"] = self.intervention_type.value
        event.metadata["simulated_error_type"] = self.error_type
        event.metadata["simulated_status_code"] = self.status_code
        event.metadata["intervention_notes"] = self.notes
        return event


@dataclass(frozen=True, slots=True)
class PromptContextChange:
    """Intervention changing prompt text or context at step k."""

    target_step: int
    prompt_replacement: str | None = None
    prompt_mutator: Callable[[str], str] | None = None
    context_updates: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        _validate_step_index(self.target_step)
        if self.prompt_replacement is not None and not isinstance(self.prompt_replacement, str):
            raise TypeError(
                f"prompt_replacement must be a string, got {type(self.prompt_replacement).__name__}"
            )
        if self.prompt_mutator is not None and not callable(self.prompt_mutator):
            raise TypeError(
                f"prompt_mutator must be callable, got {type(self.prompt_mutator).__name__}"
            )
        if not isinstance(self.context_updates, dict):
            raise TypeError(
                f"context_updates must be a dictionary, got {type(self.context_updates).__name__}"
            )
        if (
            self.prompt_replacement is None
            and self.prompt_mutator is None
            and not self.context_updates
        ):
            raise ValueError(
                "PromptContextChange requires prompt_replacement, prompt_mutator, or non-empty context_updates"
            )

    @property
    def intervention_type(self) -> InterventionType:
        return InterventionType.PROMPT_CONTEXT_CHANGE

    def apply(self, event: AgentEvent) -> AgentEvent:
        """Apply prompt/context modifications to an event."""
        if self.prompt_mutator is not None:
            curr_prompt = event.prompt_preview or ""
            event.prompt_preview = self.prompt_mutator(curr_prompt)
        elif self.prompt_replacement is not None:
            event.prompt_preview = self.prompt_replacement

        if self.context_updates:
            event.metadata.update(copy.deepcopy(self.context_updates))

        event.metadata["counterfactual"] = True
        event.metadata["intervention_type"] = self.intervention_type.value
        event.metadata["intervention_notes"] = self.notes
        return event


Intervention = ToolResultMutation | SimulatedAPIError | PromptContextChange


__all__ = [
    "Intervention",
    "InterventionType",
    "PromptContextChange",
    "SimulatedAPIError",
    "ToolResultMutation",
]
