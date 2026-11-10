"""LegacyTranslator: v0.2 ``AgentEvent`` → v3 observation, with explicit loss accounting.

The legacy event is stored *as received* (it is the evidence). The translation result
records what a v3 consumer cannot learn from it: missing parents, point-in-time events
without durations, timestamps of unknown provenance, and legacy interpretations
(safety verdicts, confidence scores) that are not observations at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from panoptis.evidence.model import ClockInfo, ObservationDraft, SensorRef, parse_ts

TRANSLATOR_VERSION = "1"

# v0.2 event types that carry PANOPTIS v0.2 *conclusions*, not observations.
INTERPRETATION_TYPES = frozenset(
    {
        "safety.check",
        "safety.block",
        "safety.approve",
        "safety.escalate",
        "confidence.score",
        "anomaly.detected",
        "goal.drift",
        "memory.contradiction",
        "reasoning.style_fingerprint",
        "reasoning.style_swap",
    }
)
KNOWN_TYPES = (
    frozenset(
        {
            "session.start",
            "session.end",
            "agent.start",
            "agent.end",
            "agent.error",
            "planner.input",
            "planner.output",
            "goal.set",
            "tool.call",
            "tool.result",
            "tool.error",
            "tool.retry",
            "memory.read",
            "memory.write",
            "memory.evict",
            "agent.message",
            "task.delegate",
            "task.complete",
            "task.fail",
            "checkpoint.create",
            "rollback.trigger",
            "rollback.complete",
            "custom",
        }
    )
    | INTERPRETATION_TYPES
)


class TranslationStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_LOSS = "ACCEPTED_WITH_LOSS"
    REJECTED = "REJECTED"


@dataclass
class TranslationResult:
    status: TranslationStatus
    draft: ObservationDraft | None = None
    lost_fields: list[str] = field(default_factory=list)
    inferred_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "lost_fields": self.lost_fields,
            "inferred_fields": self.inferred_fields,
            "warnings": self.warnings,
            "errors": self.errors,
        }


class LegacyTranslator:
    sensor_type = "legacy"

    def __init__(self, instance_id: str = "legacy-api-v1", tenant_id: str = "default") -> None:
        self.sensor = SensorRef(self.sensor_type, TRANSLATOR_VERSION, instance_id)
        self.tenant_id = tenant_id

    def translate(self, event: Any, *, tenant_id: str | None = None) -> TranslationResult:
        try:
            data: dict[str, Any] = (
                event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
            )
        except Exception as exc:  # noqa: BLE001 - any failure to read the input is a rejection
            return TranslationResult(
                TranslationStatus.REJECTED, errors=[f"unreadable legacy event: {exc}"]
            )

        errors = []
        raw_type = data.get("event_type")
        etype = str(getattr(raw_type, "value", raw_type)) if raw_type else None
        if not etype:
            errors.append("missing event_type")
        elif etype not in KNOWN_TYPES:
            errors.append(f"unknown legacy event_type {etype!r}")
        if not data.get("event_id"):
            errors.append("missing event_id")
        if errors:
            return TranslationResult(TranslationStatus.REJECTED, errors=errors)

        lost: list[str] = []
        warnings: list[str] = []
        ids = {
            "legacy_event_id": data.get("event_id"),
            "session_id": data.get("session_id"),
            "agent_id": data.get("agent_id"),
            "parent_event_id": data.get("parent_event_id"),
            "task_id": data.get("task_id"),
            "trace_id": data.get("trace_id"),
        }
        tool_id = (data.get("tool_call") or {}).get("tool_id") or (
            data.get("tool_result") or {}
        ).get("tool_id")
        if tool_id:
            ids["tool_id"] = tool_id
        if not data.get("parent_event_id"):
            lost.append("parent")  # v0.2 adapters never populated parent_event_id
        if etype in ("tool.call", "tool.result") and not tool_id:
            lost.append("call_result_pairing")
        if etype in ("planner.input", "planner.output"):
            lost.append("call_result_pairing")
        if data.get("duration_ms") is None and etype not in (
            "session.start",
            "session.end",
            "agent.message",
        ):
            lost.append("duration")
        lost.append("timestamp_provenance")  # v0.2 timestamps default to event construction time
        if not data.get("session_id"):
            lost.append("run")
        if etype in INTERPRETATION_TYPES:
            warnings.append(
                f"{etype} is a v0.2 interpretation, not an observation; it is kept as evidence of what v0.2 concluded"
            )
        if data.get("safety") and etype not in INTERPRETATION_TYPES:
            warnings.append(
                "embedded v0.2 safety verdict retained in payload but not treated as an observation"
            )
        if data.get("confidence") and etype != "confidence.score":
            warnings.append(
                "embedded v0.2 confidence score retained in payload but not treated as an observation"
            )

        observed = data.get("timestamp")
        draft = ObservationDraft(
            sensor=self.sensor,
            source_kind="legacy.agent_event",
            payload=data,
            source_seq=None,
            observed_at=parse_ts(observed) if observed else None,
            clock=ClockInfo(source="legacy_unverified", clock_id=f"legacy:{data.get('agent_id')}"),
            declared_ids={k: str(v) for k, v in ids.items() if v},
            tenant_id=tenant_id or self.tenant_id,
        )
        status = TranslationStatus.ACCEPTED_WITH_LOSS if lost else TranslationStatus.ACCEPTED
        return TranslationResult(
            status, draft=draft, lost_fields=sorted(set(lost)), warnings=warnings
        )
