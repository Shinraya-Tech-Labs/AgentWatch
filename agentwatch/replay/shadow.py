"""Deterministic shadow replay and tool-output differential isolation.

When an agent fails in production, the first question is *why*: did the model write something
unexpected (stochastic), or would the same agent, shown the same tool results, fail again
(deterministic)? A shadow replay answers it.

The runner re-drives the model against a recorded session while every tool call is answered
from the **recorded evidence** instead of being executed:

* a tool call is looked up by ``(tool name, canonical arguments)`` in a read-only
  :class:`ToolEvidenceStore` and answered with the exact recorded result;
* nothing is ever executed, so replaying cannot touch a database, an API or the file system;
* a call that has no recorded answer is reported as missing evidence and the replay stops,
  because everything after it would depend on a made-up value.

The shadow trace is then compared with the historical one, event by event, on the facts that
matter (event type, tool, argument hash, result hash, status, planner text hash). Identifiers,
timestamps and durations are ignored.

Classification (:class:`ShadowClassification`):

``IDENTICAL_REPLAY``
    The shadow trace matches the historical one exactly and the recorded run did not fail.
``DETERMINISTIC_CORROBORATION``
    The shadow trace matches exactly and the recorded run **failed**: given the recorded tool
    states the failure reproduces, so it is not model noise.
``STOCHASTIC_MODEL_DIVERGENCE``
    The model took a different branch although every tool result it was shown was identical
    to the recorded one.
``INSUFFICIENT_EVIDENCE``
    The replay could not be completed faithfully (a tool call had no recorded result, or the
    recorded result could not be served), so no conclusion about the cause is drawn.

No HTTP mock server is involved: the evidence store is an in-memory lookup built from the
events the replay package already works with.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentwatch.core.schema import (
    AgentEvent,
    EventType,
    ExecutionStatus,
    ToolResultData,
)
from agentwatch.evidence.canonical import canonical_json, sha256_hex
from agentwatch.replay.divergence import normalized_tool_sequence_edit_distance

logger = logging.getLogger(__name__)

# Event types that are answers from the outside world. They are served from evidence, never
# asked of the model.
_EVIDENCE_TYPES = frozenset({EventType.TOOL_RESULT, EventType.TOOL_ERROR})

# Event types that end a run.
_TERMINAL_TYPES = frozenset(
    {
        EventType.SESSION_END,
        EventType.AGENT_END,
        EventType.AGENT_ERROR,
        EventType.TASK_COMPLETE,
        EventType.TASK_FAIL,
    }
)

# Event types and statuses that mean the recorded run went wrong.
_FAILURE_TYPES = frozenset(
    {EventType.TOOL_ERROR, EventType.AGENT_ERROR, EventType.TASK_FAIL, EventType.SAFETY_BLOCK}
)
_FAILURE_STATUSES = frozenset(
    {ExecutionStatus.FAILURE, ExecutionStatus.BLOCKED, ExecutionStatus.TIMEOUT}
)

MAX_REPORTED_DIVERGENCES = 50


class ShadowClassification(StrEnum):
    IDENTICAL_REPLAY = "IDENTICAL_REPLAY"
    DETERMINISTIC_CORROBORATION = "DETERMINISTIC_CORROBORATION"
    STOCHASTIC_MODEL_DIVERGENCE = "STOCHASTIC_MODEL_DIVERGENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


# ─────────────────────────────────────────────
# Configuration and model protocol
# ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ShadowConfig:
    """Settings handed to the model so a shadow run is as repeatable as the model allows.

    ``temperature`` defaults to 0. ``seed`` is passed through for providers that support it.
    ``max_extra_decisions`` is how many decisions the model may make *after* the recorded run
    ended, so a shadow that keeps going shows up as a divergence instead of being cut off.
    """

    temperature: float = 0.0
    seed: int | None = None
    max_extra_decisions: int = 3


@dataclass(frozen=True, slots=True)
class ShadowContext:
    """What the model sees when it is asked for its next decision.

    ``history`` is a copy of the shadow trace so far, including the recorded tool results that
    were served. ``recorded`` is a copy of the decision the original run made at this point
    (``None`` once the original run had ended). Changing either does not affect the replay.
    """

    index: int
    history: tuple[AgentEvent, ...]
    recorded: AgentEvent | None
    config: ShadowConfig


ShadowModel = Callable[[ShadowContext], AgentEvent | None]


def from_step_fn(
    step_fn: Callable[[list[AgentEvent], AgentEvent], AgentEvent | None],
) -> ShadowModel:
    """Adapt an existing replay ``StepFn`` (used by the counterfactual engine) to a shadow model."""

    def model(ctx: ShadowContext) -> AgentEvent | None:
        if ctx.recorded is None:
            return None
        return step_fn(list(ctx.history), ctx.recorded)

    return model


class PromptCompletionModel:
    """A shadow model that re-asks an LLM for the planner text of each recorded decision.

    ``complete(prompt, config)`` is called with the recorded prompt and the shadow
    configuration (temperature, seed) and must return the completion text. Decisions that carry
    no prompt are repeated as recorded, so only the model stage varies. For agents whose tool
    choices come from the model, pass a custom :data:`ShadowModel` instead.
    """

    def __init__(self, complete: Callable[[str, ShadowConfig], str]):
        self._complete = complete

    def __call__(self, ctx: ShadowContext) -> AgentEvent | None:
        recorded = ctx.recorded
        if recorded is None:
            return None
        shadow = recorded.model_copy(deep=True)
        if recorded.prompt_preview and recorded.planner_output_preview is not None:
            shadow.planner_output_preview = self._complete(recorded.prompt_preview, ctx.config)
        return shadow


# ─────────────────────────────────────────────
# Evidence
# ─────────────────────────────────────────────


def arguments_hash(arguments: dict[str, Any] | None) -> str:
    """Stable hash of tool arguments (key order and whitespace never matter)."""
    return sha256_hex(canonical_json(arguments or {}))[:16]


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """One recorded tool answer and the call it answered."""

    tool_name: str
    arguments_hash: str
    result_event: AgentEvent
    source_index: int


class ToolEvidenceStore:
    """Read-only lookup of recorded tool results, keyed by tool name and canonical arguments.

    The same call made twice is answered by the first and then the second recorded result, in
    order. A result can be served once: asking for more answers than were recorded is missing evidence.
    Everything handed out is a deep copy, so no replay can change the historical record.
    """

    def __init__(self, evidence: Sequence[ToolEvidence]):
        self._queues: dict[tuple[str, str], deque[ToolEvidence]] = defaultdict(deque)
        self._by_tool: dict[str, set[str]] = defaultdict(set)
        self._total = len(evidence)
        for item in evidence:
            self._queues[(item.tool_name, item.arguments_hash)].append(item)
            self._by_tool[item.tool_name].add(item.arguments_hash)
        self.orphan_results = 0
        self._served = 0

    @classmethod
    def from_events(cls, events: Sequence[AgentEvent]) -> ToolEvidenceStore:
        """Pair every recorded tool call with its result and index them.

        A result is paired by ``tool_id`` when the call has one, otherwise with the oldest
        unanswered call to the same tool. A result with no call is counted as an orphan.
        """
        pending: list[tuple[str, str, str | None]] = []  # (tool, arguments hash, tool_id)
        evidence: list[ToolEvidence] = []
        orphans = 0

        for index, event in enumerate(events):
            if event.event_type == EventType.TOOL_CALL and event.tool_call:
                call = event.tool_call
                pending.append((call.tool_name, arguments_hash(call.arguments), call.tool_id))
            elif event.event_type in _EVIDENCE_TYPES and event.tool_result:
                result = event.tool_result
                slot = next(
                    (
                        i
                        for i, (name, _, call_id) in enumerate(pending)
                        if name == result.tool_name
                        and (call_id == result.tool_id if (result.tool_id and call_id) else True)
                    ),
                    None,
                )
                if slot is None:
                    orphans += 1
                    continue
                name, args, _ = pending.pop(slot)
                evidence.append(
                    ToolEvidence(
                        tool_name=name,
                        arguments_hash=args,
                        result_event=event.model_copy(deep=True),
                        source_index=index,
                    )
                )

        store = cls(evidence)
        store.orphan_results = orphans
        return store

    def take(self, tool_name: str, arguments: dict[str, Any] | None) -> ToolEvidence | None:
        """Serve the next recorded result for this call, or ``None`` when there is none."""
        queue = self._queues.get((tool_name, arguments_hash(arguments)))
        if not queue:
            return None
        self._served += 1
        item = queue.popleft()
        return ToolEvidence(
            tool_name=item.tool_name,
            arguments_hash=item.arguments_hash,
            result_event=item.result_event.model_copy(deep=True),
            source_index=item.source_index,
        )

    def other_arguments_for(self, tool_name: str) -> int:
        """How many differently-parameterised recorded calls exist for this tool."""
        return len(self._by_tool.get(tool_name, ()))

    @property
    def total(self) -> int:
        return self._total

    @property
    def served(self) -> int:
        return self._served

    @property
    def unused(self) -> int:
        return sum(len(q) for q in self._queues.values())


# ─────────────────────────────────────────────
# Signatures and comparison
# ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EventSignature:
    """The facts of an event that matter when asking "did the same thing happen"."""

    event_type: str
    tool: str | None = None
    arguments: str | None = None
    result: str | None = None
    status: str | None = None
    text: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "event_type": self.event_type,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "status": self.status,
            "text": self.text,
        }


def event_signature(event: AgentEvent) -> EventSignature:
    """Reduce an event to the comparison facts. Ids, timestamps and durations are ignored."""
    tool: str | None = None
    arguments: str | None = None
    result: str | None = None
    if event.tool_call:
        tool = event.tool_call.tool_name
        arguments = arguments_hash(event.tool_call.arguments)
    if event.tool_result:
        tool = event.tool_result.tool_name
        result = sha256_hex(
            canonical_json(
                {
                    "output": event.tool_result.output,
                    "error": event.tool_result.error,
                    "exit_code": event.tool_result.exit_code,
                }
            )
        )[:16]
    text_parts: dict[str, Any] = {}
    if event.planner_output_preview is not None:
        text_parts["planner"] = event.planner_output_preview
    if event.agent_message is not None:
        text_parts["message"] = event.agent_message.content
    text = sha256_hex(canonical_json(text_parts))[:16] if text_parts else None
    return EventSignature(
        event_type=event.event_type.value,
        tool=tool,
        arguments=arguments,
        result=result,
        status=event.status.value,
        text=text,
    )


def historical_failed(events: Sequence[AgentEvent]) -> bool:
    """True when the recorded run went wrong (error events, failed or blocked statuses)."""
    return any(e.event_type in _FAILURE_TYPES or e.status in _FAILURE_STATUSES for e in events)


@dataclass(frozen=True, slots=True)
class ShadowDivergence:
    """One position where the shadow trace differs from the recorded one."""

    index: int
    historical: EventSignature | None
    shadow: EventSignature | None
    fields_changed: tuple[str, ...]
    at_evidence_event: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "historical": self.historical.to_dict() if self.historical else None,
            "shadow": self.shadow.to_dict() if self.shadow else None,
            "fields_changed": list(self.fields_changed),
            "at_evidence_event": self.at_evidence_event,
        }


@dataclass(frozen=True, slots=True)
class MissingEvidence:
    """A tool call the shadow made that has no recorded answer."""

    shadow_index: int
    tool_name: str
    arguments_hash: str
    other_recorded_argument_sets: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow_index": self.shadow_index,
            "tool_name": self.tool_name,
            "arguments_hash": self.arguments_hash,
            "other_recorded_argument_sets": self.other_recorded_argument_sets,
        }


def compare_traces(
    historical: Sequence[AgentEvent], shadow: Sequence[AgentEvent]
) -> list[ShadowDivergence]:
    """Compare two traces position by position. An empty list means they are identical."""
    found: list[ShadowDivergence] = []
    for index in range(max(len(historical), len(shadow))):
        h = historical[index] if index < len(historical) else None
        s = shadow[index] if index < len(shadow) else None
        hs = event_signature(h) if h else None
        ss = event_signature(s) if s else None
        if hs == ss:
            continue
        if hs is None or ss is None:
            changed: tuple[str, ...] = ("missing_in_shadow" if ss is None else "extra_in_shadow",)
        else:
            changed = tuple(
                name
                for name in ("event_type", "tool", "arguments", "result", "status", "text")
                if getattr(hs, name) != getattr(ss, name)
            )
        at_evidence = bool(h and h.event_type in _EVIDENCE_TYPES) or bool(
            s and s.event_type in _EVIDENCE_TYPES
        )
        found.append(ShadowDivergence(index, hs, ss, changed, at_evidence))
        if len(found) >= MAX_REPORTED_DIVERGENCES:
            break
    return found


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────


@dataclass(slots=True)
class ShadowReplayReport:
    classification: ShadowClassification
    summary: str
    historical_failed: bool
    failure_reproduced: bool
    first_divergence_index: int | None
    divergences: list[ShadowDivergence] = field(default_factory=list)
    missing_evidence: list[MissingEvidence] = field(default_factory=list)
    evidence_total: int = 0
    evidence_served: int = 0
    evidence_unused: int = 0
    orphan_results: int = 0
    trajectory_distance: float = 0.0
    config: ShadowConfig = field(default_factory=ShadowConfig)
    shadow_events: list[AgentEvent] = field(default_factory=list)

    @property
    def deterministic(self) -> bool:
        """True when the recorded failure reproduced exactly from the recorded tool states."""
        return self.classification == ShadowClassification.DETERMINISTIC_CORROBORATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "summary": self.summary,
            "historical_failed": self.historical_failed,
            "failure_reproduced": self.failure_reproduced,
            "first_divergence_index": self.first_divergence_index,
            "divergences": [d.to_dict() for d in self.divergences],
            "missing_evidence": [m.to_dict() for m in self.missing_evidence],
            "evidence": {
                "total": self.evidence_total,
                "served": self.evidence_served,
                "unused": self.evidence_unused,
                "orphan_results": self.orphan_results,
            },
            "trajectory_distance": self.trajectory_distance,
            "config": {
                "temperature": self.config.temperature,
                "seed": self.config.seed,
                "max_extra_decisions": self.config.max_extra_decisions,
            },
            "shadow_event_count": len(self.shadow_events),
        }


# ─────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────


def _tool_sequence(events: Sequence[AgentEvent]) -> list[str]:
    return [
        e.tool_call.tool_name for e in events if e.event_type == EventType.TOOL_CALL and e.tool_call
    ]


class ShadowReplayRunner:
    """Replays a recorded session against a model, answering tools from recorded evidence."""

    def __init__(self, model: ShadowModel, config: ShadowConfig | None = None):
        self._model = model
        self._config = config or ShadowConfig()

    def run(self, recorded: Sequence[AgentEvent]) -> ShadowReplayReport:
        historical = [e.model_copy(deep=True) for e in recorded]
        store = ToolEvidenceStore.from_events(historical)
        decisions = [e for e in historical if e.event_type not in _EVIDENCE_TYPES]

        shadow: list[AgentEvent] = []
        missing: list[MissingEvidence] = []
        limit = len(decisions) + max(0, self._config.max_extra_decisions)

        for k in range(limit):
            expected = decisions[k].model_copy(deep=True) if k < len(decisions) else None
            ctx = ShadowContext(
                index=k,
                history=tuple(e.model_copy(deep=True) for e in shadow),
                recorded=expected,
                config=self._config,
            )
            produced = self._model(ctx)
            if produced is None:
                break
            produced = produced.model_copy(deep=True)
            shadow.append(produced)

            if produced.event_type == EventType.TOOL_CALL and produced.tool_call:
                call = produced.tool_call
                served = store.take(call.tool_name, call.arguments)
                if served is None:
                    missing.append(
                        MissingEvidence(
                            shadow_index=len(shadow) - 1,
                            tool_name=call.tool_name,
                            arguments_hash=arguments_hash(call.arguments),
                            other_recorded_argument_sets=store.other_arguments_for(call.tool_name),
                        )
                    )
                    # Nothing real answered this call, so the run cannot continue faithfully.
                    shadow.append(_missing_evidence_event(produced))
                    break
                result_event = served.result_event
                result_event.parent_event_id = produced.event_id
                shadow.append(result_event)
            elif produced.event_type in _TERMINAL_TYPES:
                break

        return self._classify(historical, shadow, store, missing)

    def _classify(
        self,
        historical: list[AgentEvent],
        shadow: list[AgentEvent],
        store: ToolEvidenceStore,
        missing: list[MissingEvidence],
    ) -> ShadowReplayReport:
        failed = historical_failed(historical)
        divergences = compare_traces(historical, shadow)
        first = divergences[0].index if divergences else None
        distance = normalized_tool_sequence_edit_distance(
            _tool_sequence(historical), _tool_sequence(shadow)
        )

        if missing:
            m = missing[0]
            classification = ShadowClassification.INSUFFICIENT_EVIDENCE
            summary = (
                f"The shadow run called '{m.tool_name}' with arguments that were never recorded"
                f" (hash {m.arguments_hash}); replay stopped there. No conclusion about the cause"
                " can be drawn."
            )
            reproduced = False
        elif not divergences:
            reproduced = failed
            if failed:
                classification = ShadowClassification.DETERMINISTIC_CORROBORATION
                summary = (
                    "The recorded failure reproduced exactly when the model was shown the"
                    " recorded tool results. It is deterministic given those tool states, not"
                    " model noise."
                )
            else:
                classification = ShadowClassification.IDENTICAL_REPLAY
                summary = "The shadow trace matches the recorded trace exactly."
        elif divergences[0].at_evidence_event and not _decision_diverged_before(divergences):
            # A recorded result did not come back as recorded: the evidence was not served
            # faithfully, so the comparison proves nothing about the model.
            classification = ShadowClassification.INSUFFICIENT_EVIDENCE
            summary = (
                f"A tool result at position {first} differs from the recorded one although the"
                " call before it matched, so the recorded evidence could not be replayed."
            )
            reproduced = False
        else:
            classification = ShadowClassification.STOCHASTIC_MODEL_DIVERGENCE
            d = divergences[0]
            what = ", ".join(d.fields_changed)
            summary = (
                f"The model diverged at position {d.index} ({what}) although every tool result"
                " it was shown matched the recorded one"
                + ("; the recorded failure did not reproduce." if failed else ".")
            )
            reproduced = False

        return ShadowReplayReport(
            classification=classification,
            summary=summary,
            historical_failed=failed,
            failure_reproduced=reproduced,
            first_divergence_index=first,
            divergences=divergences,
            missing_evidence=missing,
            evidence_total=store.total,
            evidence_served=store.served,
            evidence_unused=store.unused,
            orphan_results=store.orphan_results,
            trajectory_distance=distance,
            config=self._config,
            shadow_events=shadow,
        )


def _decision_diverged_before(divergences: list[ShadowDivergence]) -> bool:
    """True when the earliest difference is on a model decision rather than on served evidence."""
    return not divergences[0].at_evidence_event


def _missing_evidence_event(call_event: AgentEvent) -> AgentEvent:
    """The placeholder that marks where replay stopped. It is not real evidence."""
    call = call_event.tool_call
    name = call.tool_name if call else "unknown"
    return AgentEvent(
        session_id=call_event.session_id,
        agent_id=call_event.agent_id,
        event_type=EventType.TOOL_ERROR,
        status=ExecutionStatus.FAILURE,
        parent_event_id=call_event.event_id,
        tool_result=ToolResultData(
            tool_name=name,
            error="shadow replay: no recorded evidence for this call",
        ),
        metadata={"shadow_missing_evidence": True},
    )


def run_shadow_replay(
    recorded: Sequence[AgentEvent],
    model: ShadowModel,
    config: ShadowConfig | None = None,
) -> ShadowReplayReport:
    """Convenience wrapper: replay ``recorded`` with ``model`` and return the report."""
    return ShadowReplayRunner(model, config).run(recorded)


__all__ = [
    "EventSignature",
    "MissingEvidence",
    "PromptCompletionModel",
    "ShadowClassification",
    "ShadowConfig",
    "ShadowContext",
    "ShadowDivergence",
    "ShadowModel",
    "ShadowReplayReport",
    "ShadowReplayRunner",
    "ToolEvidence",
    "ToolEvidenceStore",
    "arguments_hash",
    "compare_traces",
    "event_signature",
    "from_step_fn",
    "historical_failed",
    "run_shadow_replay",
]
