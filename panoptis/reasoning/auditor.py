"""Reasoning step auditor with optional LLM-judge callback."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, cast

from panoptis.core.schema import (
    AgentEvent,
    EventType,
    ReasoningStyleFingerprint,
    StyleSwapAlert,
)
from panoptis.reasoning.fingerprint import (
    StyleFingerprint,
    detect_mid_session_change,
    fingerprint,
)

JudgeCallback = Callable[[str, AgentEvent], Awaitable[dict[str, Any]]]

# Substrings that mark a shell/tool command as destructive. Kept intentionally
# conservative and lowercase; matched against the command + string arguments.
_DESTRUCTIVE_PATTERNS = (
    "rm -rf",
    "rm -r ",
    "rmdir",
    "drop table",
    "drop database",
    "truncate ",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "> /dev/sd",
    ":(){:|:&};:",
)


@dataclass
class StepAudit:
    step_index: int
    event_id: str
    score: float
    verdict: str
    rationale: str
    evidence: list[str] = field(default_factory=list)
    # Transparency fields: surface *why* a step scored the way it did.
    model_used: str = "heuristic"
    risk_signals: list[str] = field(default_factory=list)
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "step_index": self.step_index,
            "event_id": self.event_id,
            "score": round(self.score, 3),
            "verdict": self.verdict,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "model_used": self.model_used,
            "risk_signals": self.risk_signals,
            "confidence_breakdown": {
                key: round(value, 3) for key, value in self.confidence_breakdown.items()
            },
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass
class AuditSummary:
    session_id: str
    average_score: float
    weakest_step: int | None
    strongest_step: int | None
    audits: list[StepAudit]

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "average_score": round(self.average_score, 3),
            "weakest_step": self.weakest_step,
            "strongest_step": self.strongest_step,
            "audits": [audit.to_dict() for audit in self.audits],
        }


@dataclass
class FingerprintReport:
    """Bundle of style-fingerprint + swap-detection results for a session."""

    session_id: str
    fingerprint: ReasoningStyleFingerprint
    swap_alert: StyleSwapAlert

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "fingerprint": self.fingerprint.model_dump(),
            "swap_alert": self.swap_alert.model_dump(),
        }


class ReasoningAuditor:
    """
    Audits observable reasoning artifacts.

    If a judge callback is provided, it is treated as the LLM-as-judge layer.
    Without one, the auditor falls back to a deterministic heuristic so the
    system remains usable in offline and test environments.
    """

    def __init__(self, judge: JudgeCallback | None = None):
        self._judge = judge

    async def audit_session(self, events: list[AgentEvent]) -> AuditSummary:
        audits: list[StepAudit] = []
        observable = [
            event
            for event in events
            if event.event_type
            in (EventType.PLANNER_OUTPUT, EventType.TOOL_CALL, EventType.TOOL_RESULT)
        ]

        for index, event in enumerate(observable):
            audits.append(await self.audit_step(index, event))

        scores = [audit.score for audit in audits] or [1.0]
        weakest = min(audits, key=lambda item: item.score).step_index if audits else None
        strongest = max(audits, key=lambda item: item.score).step_index if audits else None
        session_id = events[0].session_id if events else ""
        return AuditSummary(
            session_id=session_id,
            average_score=mean(scores),
            weakest_step=weakest,
            strongest_step=strongest,
            audits=audits,
        )

    async def audit_step(self, step_index: int, event: AgentEvent) -> StepAudit:
        start = time.perf_counter()
        prompt = self._build_prompt(event)
        if self._judge:
            judged = await self._judge(prompt, event)
            audit = StepAudit(
                step_index=step_index,
                event_id=event.event_id,
                score=float(cast(float, judged.get("score", 0.5))),
                verdict=str(judged.get("verdict", "uncertain")),
                rationale=str(judged.get("rationale", "No rationale returned.")),
                evidence=[str(item) for item in cast(list[Any], judged.get("evidence", []))],
                model_used=str(judged.get("model_used", "llm-judge")),
                confidence_breakdown={
                    str(key): float(value)
                    for key, value in cast(
                        dict[str, Any], judged.get("confidence_breakdown", {})
                    ).items()
                },
            )
        else:
            audit = self._heuristic_audit(step_index, event)

        # Risk signals and latency are populated uniformly regardless of whether
        # the score came from the LLM judge or the heuristic fallback.
        audit.risk_signals = self.detect_risk_signals(event)
        audit.latency_ms = (time.perf_counter() - start) * 1000.0
        return audit

    @staticmethod
    def detect_risk_signals(event: AgentEvent) -> list[str]:
        """Surface human-readable risk signals for a step, e.g. destructive
        commands or overly-broad wildcards, so a block is explainable."""
        signals: list[str] = []
        tool_call = event.tool_call
        if tool_call is not None:
            command = (tool_call.raw_command or "").lower()
            arg_text = (
                " ".join(
                    str(value).lower()
                    for value in tool_call.arguments.values()
                    if isinstance(value, str)
                )
                if tool_call.arguments
                else ""
            )
            haystack = f"{command} {arg_text}".strip()
            if any(pattern in haystack for pattern in _DESTRUCTIVE_PATTERNS):
                signals.append("destructive_command")
            if haystack.startswith("sudo") or "sudo " in haystack:
                signals.append("privilege_escalation")
            if "*" in haystack:
                signals.append("broad_wildcard")
            if any(token in haystack for token in ("curl ", "wget ", "http://", "https://")):
                signals.append("external_fetch")
        if event.tool_result is not None and event.tool_result.error:
            signals.append("tool_error")
        if event.is_blocked:
            signals.append("blocked_action")
        return signals

    def fingerprint_session(
        self,
        events: list[AgentEvent],
        *,
        distance_threshold: float = 1.0,
        split_ratio: float = 0.5,
    ) -> FingerprintReport:
        """Compute the session style fingerprint and detect mid-session swaps.

        Args:
            events: All events recorded for the session (any timeline order is
                fine — the mid-session split is positional on the list you pass).
            distance_threshold: Euclidean-style distance above which a fingerprint
                delta is reported as a probable mid-session model swap.
            split_ratio: Where to split the events list into halves when
                comparing the first vs second half.

        Returns:
            A :class:`FingerprintReport` containing both the full-session
            fingerprint and the swap-detection alert.
        """
        full = fingerprint(events)
        detected, distance = detect_mid_session_change(
            events,
            split_ratio=split_ratio,
            distance_threshold=distance_threshold,
        )
        # Compute both halves for diagnostic context, even when we cannot
        # actually compare them — this lets downstream consumers see *why*
        # detection returned ``False``.
        cut = int(len(events) * split_ratio)
        first, second = events[:cut], events[cut:]
        plans_first = sum(1 for e in first if e.event_type == EventType.PLANNER_OUTPUT)
        plans_second = sum(1 for e in second if e.event_type == EventType.PLANNER_OUTPUT)
        first_fp = self._pydantic_fingerprint(fingerprint(first), sample_size=plans_first)
        second_fp = self._pydantic_fingerprint(fingerprint(second), sample_size=plans_second)
        session_id = events[0].session_id if events else ""
        reason: str | None = None
        if not detected:
            if len(events) < 6:
                reason = "insufficient_events"
            elif plans_first == 0 or plans_second == 0:
                # Genuine insufficient planner signal: one half has no planner
                # output at all, so distance==0 is meaningless.
                reason = "insufficient_planner_signal"
            elif distance == 0.0:
                # Identical planner fingerprints on both sides ⇒ identical
                # style, distinct from missing-planner-signal case above.
                reason = "style_identical"
            else:
                reason = "below_threshold"
        plans_count = sum(1 for e in events if e.event_type == EventType.PLANNER_OUTPUT)
        fp_pydantic = self._pydantic_fingerprint(full, sample_size=plans_count)
        return FingerprintReport(
            session_id=session_id,
            fingerprint=fp_pydantic,
            swap_alert=StyleSwapAlert(
                session_id=session_id,
                detected=detected,
                distance=round(distance, 4),
                threshold=distance_threshold,
                first_half=first_fp,
                second_half=second_fp,
                reason=reason,
            ),
        )

    @staticmethod
    def _pydantic_fingerprint(
        fp: StyleFingerprint,
        *,
        sample_size: int | None = None,
    ) -> ReasoningStyleFingerprint:
        """Bridge from the dataclass fingerprint to the schema model."""
        instance = ReasoningStyleFingerprint.from_dataclass(fp)
        if sample_size is not None:
            instance = instance.model_copy(update={"sample_size": sample_size})
        return instance

    def _build_prompt(self, event: AgentEvent) -> str:
        return (
            "Judge the quality of this agent reasoning artifact on a 0-1 scale.\n"
            f"Event type: {event.event_type.value}\n"
            f"Planner output: {event.planner_output_preview or ''}\n"
            f"Tool call: {event.tool_call.model_dump() if event.tool_call else ''}\n"
            f"Tool result: {event.tool_result.model_dump() if event.tool_result else ''}\n"
            "Return JSON with score, verdict, rationale, and evidence."
        )

    def _heuristic_audit(self, step_index: int, event: AgentEvent) -> StepAudit:
        score = 0.55
        evidence: list[str] = []

        if event.planner_output_preview:
            score += 0.2
            evidence.append("planner_output_present")
            if len(event.planner_output_preview.split()) >= 8:
                score += 0.1
                evidence.append("planner_output_specific")

        if event.tool_call:
            evidence.append(f"tool:{event.tool_call.tool_name}")
            if event.tool_call.raw_command or event.tool_call.arguments:
                score += 0.1
                evidence.append("tool_arguments_present")

        if event.tool_result:
            evidence.append("tool_result_present")
            if event.tool_result.error:
                score -= 0.25
                evidence.append("tool_result_error")
            else:
                score += 0.05

        if event.is_blocked:
            score -= 0.2
            evidence.append("blocked_action")

        score = max(0.0, min(score, 1.0))
        verdict = "sound" if score >= 0.75 else "acceptable" if score >= 0.5 else "weak"
        rationale = (
            "Heuristic audit based on observability artifacts because no external judge "
            "callback is configured."
        )

        # Interpretable per-dimension decomposition (does not alter `score`).
        planner_text = event.planner_output_preview or ""
        has_planner = bool(planner_text)
        is_specific = len(planner_text.split()) >= 8
        has_tool_args = bool(
            event.tool_call and (event.tool_call.raw_command or event.tool_call.arguments)
        )
        has_error = bool(event.tool_result and event.tool_result.error)
        confidence_breakdown = {
            "relevance": min(
                1.0, 0.4 + (0.35 if has_planner else 0.0) + (0.25 if has_tool_args else 0.0)
            ),
            "safety": max(
                0.0, 1.0 - (0.5 if has_error else 0.0) - (0.5 if event.is_blocked else 0.0)
            ),
            "coherence": min(
                1.0, 0.5 + (0.4 if is_specific else 0.0) + (0.1 if event.tool_result else 0.0)
            ),
        }

        return StepAudit(
            step_index=step_index,
            event_id=event.event_id,
            score=score,
            verdict=verdict,
            rationale=rationale,
            evidence=evidence,
            model_used="heuristic",
            confidence_breakdown=confidence_breakdown,
        )
