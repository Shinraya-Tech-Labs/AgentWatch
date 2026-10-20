"""Incremental contract evaluator for streaming ComputationalEvents (Issue #731).

Evaluates EvidenceContract rules incrementally event-by-event, maintaining bounded
memory with TTL eviction, handling out-of-order arrivals, preserving open-world
epistemic semantics, and reporting minimal witnesses.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agentwatch.contracts.remedies import get_remedy_for_missing_fact
from agentwatch.contracts.schema import (
    ContractOperator,
    CoverageSnapshot,
    EvidenceAssessment,
    EvidenceContract,
    EvidenceVerdict,
    MissingFact,
    MissingReason,
    Recoverability,
)
from agentwatch.events.model import ComputationalEvent, DeclaredLink

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CandidateEvent:
    event_id: str
    operation: str
    time: datetime | None
    uncertainty_ms: float
    derived_from: tuple[str, ...]
    source_ids: tuple[tuple[str, str], ...]
    parents: tuple[DeclaredLink, ...]
    attributes: dict[str, Any]


@dataclass(slots=True)
class OpenObligation:
    target: CandidateEvent
    join_val: Any
    deadline: datetime | None
    created_at: datetime | None


class IncrementalContractEvaluator:
    """Incremental state machine for real-time contract evaluation over event streams."""

    def __init__(
        self,
        contract: EvidenceContract,
        coverage: Sequence[CoverageSnapshot] = (),
        max_buffer_size: int = 10_000,
    ) -> None:
        self.contract = contract
        self.rule = contract.rule
        self.coverage = tuple(coverage)
        self.max_buffer_size = max_buffer_size

        self.authoritative_sensors = {
            s.sensor_id: s
            for s in coverage
            if s.active
            and s.dropped_count == 0
            and self.rule.required_operation in s.authoritative_for
        }

        self.max_clock_uncertainty_ms = max([s.clock_uncertainty_ms for s in coverage] + [0.0])

        self._candidates: dict[Any, deque[CandidateEvent]] = defaultdict(deque)
        self._last_seen_required: dict[Any, CandidateEvent] = {}
        self._unresolved_targets: deque[tuple[CandidateEvent, Any]] = deque()
        self._open_obligations: list[OpenObligation] = []
        self._buffered_required: dict[Any, deque[CandidateEvent]] = defaultdict(deque)

        self._target_events_count = 0
        self._support_obs_ids: list[str] = []
        self._refute_obs_ids: list[str] = []
        self._missing_facts: list[MissingFact] = []
        self._remedies: list[str] = []
        self._assumptions: list[str] = []
        self._verdicts: list[EvidenceVerdict] = []
        self._summaries: list[str] = []

        self._overflow = False
        self._events_evaluated = 0
        self._current_time: datetime | None = None
        self._last_prune_time: datetime | None = None
        self._total_buffered_candidates = 0

    @property
    def events_evaluated(self) -> int:
        return self._events_evaluated

    @property
    def has_overflow(self) -> bool:
        return self._overflow

    def buffer_stats(self) -> dict[str, Any]:
        return {
            "total_candidates": self._total_buffered_candidates,
            "unresolved_targets": len(self._unresolved_targets),
            "open_obligations": len(self._open_obligations),
            "has_overflow": self._overflow,
            "events_evaluated": self._events_evaluated,
        }

    def _extract_join_val(self, attrs: dict[str, Any]) -> Any:
        join_key = self.rule.join_on[0] if self.rule.join_on else None
        return attrs.get(join_key) if join_key else None

    def _to_candidate(self, event: ComputationalEvent) -> CandidateEvent:
        return CandidateEvent(
            event_id=event.event_id,
            operation=event.operation,
            time=event.time.start,
            uncertainty_ms=event.time.uncertainty_ms or 0.0,
            derived_from=event.derived_from,
            source_ids=event.source_ids,
            parents=event.parents,
            attributes=event.attributes,
        )

    def _prune_expired_candidates(self, current_time: datetime) -> None:
        """Evicts candidates older than max_window_seconds relative to current stream time."""
        window_td = timedelta(seconds=self.rule.max_window_seconds)
        cutoff = current_time - window_td

        # Prune preceding candidate buffers
        empty_keys = []
        for key, q in self._candidates.items():
            while q and q[0].time and q[0].time < cutoff:
                q.popleft()
                self._total_buffered_candidates = max(0, self._total_buffered_candidates - 1)
            if not q:
                empty_keys.append(key)
        for k in empty_keys:
            del self._candidates[k]

        # Prune buffered required for eventually_within
        empty_keys = []
        for key, q in self._buffered_required.items():
            while q and q[0].time and q[0].time < cutoff:
                q.popleft()
                self._total_buffered_candidates = max(0, self._total_buffered_candidates - 1)
            if not q:
                empty_keys.append(key)
        for k in empty_keys:
            del self._buffered_required[k]

        # Expire open obligations whose deadline has passed
        still_open: list[OpenObligation] = []
        for ob in self._open_obligations:
            if ob.deadline and current_time > ob.deadline:
                self._resolve_expired_obligation(ob)
            else:
                still_open.append(ob)
        self._open_obligations = still_open

    def _resolve_expired_obligation(self, ob: OpenObligation) -> None:
        """Handles an eventually_within obligation whose time window elapsed without required event."""
        target = ob.target
        if self.authoritative_sensors and self.contract.require_authoritative_log_for_refutation:
            sensor_names = ", ".join(self.authoritative_sensors.keys())
            self._refute_obs_ids.extend(target.derived_from)
            self._verdicts.append(EvidenceVerdict.REFUTED)
            self._summaries.append(
                f"Refuted: target '{self.rule.target_operation}' executed without required '{self.rule.required_operation}' "
                f"within max window ({self.rule.max_window_seconds:.1f}s). Authoritative sensors ({sensor_names}) attest complete coverage."
            )
            self._assumptions.append(
                f"Authoritative log completeness for '{self.rule.required_operation}' attested by {sensor_names}"
            )
        else:
            join_key = self.rule.join_on[0] if self.rule.join_on else None
            fact = MissingFact(
                field_name=join_key or "event_id",
                reason=MissingReason.SENSOR_DISABLED
                if not self.coverage
                else MissingReason.FIELD_ABSENT,
                required_for=self.rule.required_operation,
                candidate_sensors=(f"{self.rule.required_operation}_sensor",),
                recoverability=Recoverability.INSTRUMENTABLE,
            )
            self._missing_facts.append(fact)
            self._remedies.append(get_remedy_for_missing_fact(fact))
            self._verdicts.append(EvidenceVerdict.INCONCLUSIVE)
            self._summaries.append(
                f"Inconclusive: target '{self.rule.target_operation}' was observed, but required '{self.rule.required_operation}' "
                f"cannot be proven or refuted because no authoritative sensor attests its absence within window."
            )

    def process_event(self, event: ComputationalEvent) -> None:
        """Incrementally updates state with an incoming ComputationalEvent."""
        self._events_evaluated += 1
        if event.time.uncertainty_ms:
            self.max_clock_uncertainty_ms = max(
                self.max_clock_uncertainty_ms, event.time.uncertainty_ms
            )

        cand = self._to_candidate(event)
        if cand.time:
            if self._current_time is None or cand.time > self._current_time:
                self._current_time = cand.time
            if (
                self._events_evaluated % 64 == 0
                or self._last_prune_time is None
                or (cand.time - self._last_prune_time).total_seconds() >= 1.0
            ):
                self._last_prune_time = cand.time
                self._prune_expired_candidates(self._current_time)

        if self.rule.operator == ContractOperator.PRECEDING_EVENT:
            self._process_preceding(cand)
        elif self.rule.operator == ContractOperator.EVENTUALLY_WITHIN:
            self._process_eventually_within(cand)

    def _process_preceding(self, cand: CandidateEvent) -> None:
        join_val = self._extract_join_val(cand.attributes)

        if cand.operation == self.rule.required_operation:
            self._last_seen_required[join_val] = cand
            # Check if any unresolved target was waiting for this preceding required event
            remaining_targets = deque()
            matched = False
            while self._unresolved_targets:
                target, tgt_join = self._unresolved_targets.popleft()
                if not matched and tgt_join == join_val:
                    # Evaluate target against this required event
                    verdict = self._evaluate_pair_preceding(target, cand)
                    if verdict == EvidenceVerdict.SUPPORTED:
                        matched = True
                        continue
                remaining_targets.append((target, tgt_join))
            self._unresolved_targets = remaining_targets

            # Store in candidates buffer
            if self._total_buffered_candidates >= self.max_buffer_size:
                self._record_overflow()
            else:
                self._candidates[join_val].append(cand)
                self._total_buffered_candidates += 1

        elif cand.operation == self.rule.target_operation:
            self._target_events_count += 1
            if cand.time is None:
                self._record_missing_timestamp()
                return

            candidates = self._candidates.get(join_val)
            if candidates:
                # Find best matching candidate
                matched_verdict = None
                for req in candidates:
                    v = self._evaluate_pair_preceding(cand, req)
                    matched_verdict = v
                    if v in (
                        EvidenceVerdict.SUPPORTED,
                        EvidenceVerdict.REFUTED,
                        EvidenceVerdict.CONFLICTING_EVIDENCE,
                    ):
                        break
                if matched_verdict is None or matched_verdict == EvidenceVerdict.INCONCLUSIVE:
                    self._unresolved_targets.append((cand, join_val))
            elif join_val in self._last_seen_required:
                # Check against last seen required event for this key (which may have been pruned by TTL)
                last_req = self._last_seen_required[join_val]
                v = self._evaluate_pair_preceding(cand, last_req)
                if v == EvidenceVerdict.INCONCLUSIVE:
                    self._unresolved_targets.append((cand, join_val))
            else:
                # Target arrived before candidate; keep for potential out-of-order arrival
                if len(self._unresolved_targets) >= self.max_buffer_size:
                    self._record_overflow()
                else:
                    self._unresolved_targets.append((cand, join_val))

    def _evaluate_pair_preceding(
        self, target: CandidateEvent, req: CandidateEvent
    ) -> EvidenceVerdict:
        req_time = req.time
        target_time = target.time

        if req_time is None or target_time is None:
            self._record_missing_timestamp()
            return EvidenceVerdict.INCONCLUSIVE

        # Clock ambiguity check
        diff_ms = abs((target_time - req_time).total_seconds() * 1000.0)
        has_causal_link = any(p.value in [sid[1] for sid in req.source_ids] for p in target.parents)

        if (
            self.max_clock_uncertainty_ms > 0
            and diff_ms <= self.max_clock_uncertainty_ms
            and not has_causal_link
        ):
            self._verdicts.append(EvidenceVerdict.CONFLICTING_EVIDENCE)
            self._support_obs_ids.extend(target.derived_from + req.derived_from)
            self._summaries.append(
                f"Conflicting evidence: time difference ({diff_ms:.1f}ms) between events is within clock "
                f"uncertainty threshold ({self.max_clock_uncertainty_ms:.1f}ms) with no explicit parent span link."
            )
            self._assumptions.append(
                f"Clock uncertainty of {self.max_clock_uncertainty_ms}ms prevents definitive ordering"
            )
            return EvidenceVerdict.CONFLICTING_EVIDENCE

        delta_sec = (target_time - req_time).total_seconds()
        if delta_sec < 0:
            if (
                has_causal_link
                and self.max_clock_uncertainty_ms > 0
                and diff_ms <= self.max_clock_uncertainty_ms
            ):
                # Causal link proves preceding order despite clock skew
                delta_sec = 0.0
            else:
                self._refute_obs_ids.extend(target.derived_from + req.derived_from)
                self._verdicts.append(EvidenceVerdict.REFUTED)
                self._summaries.append(
                    f"Refuted: '{self.rule.required_operation}' occurred after '{self.rule.target_operation}' (delta: {delta_sec:.2f}s)."
                )
                return EvidenceVerdict.REFUTED

        if delta_sec > self.rule.max_window_seconds:
            self._refute_obs_ids.extend(target.derived_from + req.derived_from)
            self._verdicts.append(EvidenceVerdict.REFUTED)
            self._summaries.append(
                f"Refuted: '{self.rule.required_operation}' preceded target but exceeded max allowed window "
                f"({delta_sec:.1f}s > {self.rule.max_window_seconds:.1f}s)."
            )
            return EvidenceVerdict.REFUTED

        # Preceding event satisfied!
        self._support_obs_ids.extend(target.derived_from + req.derived_from)
        self._verdicts.append(EvidenceVerdict.SUPPORTED)
        return EvidenceVerdict.SUPPORTED

    def _process_eventually_within(self, cand: CandidateEvent) -> None:
        join_val = self._extract_join_val(cand.attributes)

        if cand.operation == self.rule.target_operation:
            self._target_events_count += 1
            if cand.time is None:
                self._record_missing_timestamp()
                return

            # Check if matching required was already buffered
            buf = self._buffered_required.get(join_val)
            matched = False
            if buf:
                while buf:
                    req = buf.popleft()
                    self._total_buffered_candidates = max(0, self._total_buffered_candidates - 1)
                    v = self._evaluate_pair_eventually_within(cand, req)
                    if v == EvidenceVerdict.SUPPORTED:
                        matched = True
                        break
            if not matched:
                deadline = cand.time + timedelta(seconds=self.rule.max_window_seconds)
                if len(self._open_obligations) >= self.max_buffer_size:
                    self._record_overflow()
                else:
                    self._open_obligations.append(
                        OpenObligation(
                            target=cand, join_val=join_val, deadline=deadline, created_at=cand.time
                        )
                    )

        elif cand.operation == self.rule.required_operation:
            # Check open obligations
            matched_idx = None
            for idx, ob in enumerate(self._open_obligations):
                if ob.join_val == join_val:
                    v = self._evaluate_pair_eventually_within(ob.target, cand)
                    if v == EvidenceVerdict.SUPPORTED:
                        matched_idx = idx
                        break
            if matched_idx is not None:
                self._open_obligations.pop(matched_idx)
            else:
                if self._total_buffered_candidates >= self.max_buffer_size:
                    self._record_overflow()
                else:
                    self._buffered_required[join_val].append(cand)
                    self._total_buffered_candidates += 1

    def _evaluate_pair_eventually_within(
        self, target: CandidateEvent, req: CandidateEvent
    ) -> EvidenceVerdict:
        req_time = req.time
        target_time = target.time

        if req_time is None or target_time is None:
            self._record_missing_timestamp()
            return EvidenceVerdict.INCONCLUSIVE

        diff_ms = abs((req_time - target_time).total_seconds() * 1000.0)
        has_causal_link = any(p.value in [sid[1] for sid in target.source_ids] for p in req.parents)

        if (
            self.max_clock_uncertainty_ms > 0
            and diff_ms <= self.max_clock_uncertainty_ms
            and not has_causal_link
        ):
            self._verdicts.append(EvidenceVerdict.CONFLICTING_EVIDENCE)
            self._support_obs_ids.extend(target.derived_from + req.derived_from)
            self._summaries.append(
                f"Conflicting evidence: time difference ({diff_ms:.1f}ms) between events is within clock "
                f"uncertainty threshold ({self.max_clock_uncertainty_ms:.1f}ms) with no explicit parent span link."
            )
            return EvidenceVerdict.CONFLICTING_EVIDENCE

        delta_sec = (req_time - target_time).total_seconds()
        if delta_sec < 0:
            self._refute_obs_ids.extend(target.derived_from + req.derived_from)
            self._verdicts.append(EvidenceVerdict.REFUTED)
            self._summaries.append(
                f"Refuted: '{self.rule.required_operation}' occurred before target when expected after."
            )
            return EvidenceVerdict.REFUTED

        if delta_sec > self.rule.max_window_seconds:
            self._refute_obs_ids.extend(target.derived_from + req.derived_from)
            self._verdicts.append(EvidenceVerdict.REFUTED)
            self._summaries.append(
                f"Refuted: '{self.rule.required_operation}' exceeded max follow-up window ({delta_sec:.1f}s)."
            )
            return EvidenceVerdict.REFUTED

        self._support_obs_ids.extend(target.derived_from + req.derived_from)
        self._verdicts.append(EvidenceVerdict.SUPPORTED)
        return EvidenceVerdict.SUPPORTED

    def _record_missing_timestamp(self) -> None:
        fact = MissingFact(
            field_name="timestamp",
            reason=MissingReason.FIELD_ABSENT,
            required_for=self.rule.required_operation,
            recoverability=Recoverability.INSTRUMENTABLE,
        )
        self._missing_facts.append(fact)
        self._remedies.append(get_remedy_for_missing_fact(fact))
        self._verdicts.append(EvidenceVerdict.INCONCLUSIVE)
        self._summaries.append("Inconclusive: one or more events lack valid start timestamps.")

    def _record_overflow(self) -> None:
        self._overflow = True
        fact = MissingFact(
            field_name="buffer",
            reason=MissingReason.SEQUENCE_GAP,
            required_for="bounded_buffer_overflow",
            candidate_sensors=(),
            recoverability=Recoverability.INSTRUMENTABLE,
        )
        self._missing_facts.append(fact)
        self._remedies.append("Increase buffer capacity or configure stream partition parallelism.")
        self._verdicts.append(EvidenceVerdict.INCONCLUSIVE)
        self._summaries.append(
            "Inconclusive: bounded buffer overflow dropped events from evaluation trace."
        )

    def finalize(self) -> EvidenceAssessment:
        """Finalizes evaluation upon stream termination and computes final EvidenceAssessment."""
        # Process remaining unresolved targets for preceding_event
        while self._unresolved_targets:
            target, _ = self._unresolved_targets.popleft()
            if (
                self.authoritative_sensors
                and self.contract.require_authoritative_log_for_refutation
            ):
                sensor_names = ", ".join(self.authoritative_sensors.keys())
                self._refute_obs_ids.extend(target.derived_from)
                self._verdicts.append(EvidenceVerdict.REFUTED)
                self._summaries.append(
                    f"Refuted: target '{self.rule.target_operation}' executed without required '{self.rule.required_operation}'. "
                    f"Authoritative sensors ({sensor_names}) attest complete coverage with zero dropped events."
                )
                self._assumptions.append(
                    f"Authoritative log completeness for '{self.rule.required_operation}' attested by {sensor_names}"
                )
            else:
                join_key = self.rule.join_on[0] if self.rule.join_on else None
                fact = MissingFact(
                    field_name=join_key or "event_id",
                    reason=MissingReason.SENSOR_DISABLED
                    if not self.coverage
                    else MissingReason.FIELD_ABSENT,
                    required_for=self.rule.required_operation,
                    candidate_sensors=(f"{self.rule.required_operation}_sensor",),
                    recoverability=Recoverability.INSTRUMENTABLE,
                )
                self._missing_facts.append(fact)
                self._remedies.append(get_remedy_for_missing_fact(fact))
                self._verdicts.append(EvidenceVerdict.INCONCLUSIVE)
                self._summaries.append(
                    f"Inconclusive: target '{self.rule.target_operation}' was observed, but required '{self.rule.required_operation}' "
                    f"cannot be proven or refuted because no authoritative sensor attests its absence."
                )

        # Process remaining open obligations for eventually_within
        for ob in self._open_obligations:
            self._resolve_expired_obligation(ob)
        self._open_obligations.clear()

        # Decide final verdict
        if self._target_events_count == 0:
            return EvidenceAssessment(
                contract_name=self.contract.name,
                verdict=EvidenceVerdict.SUPPORTED,
                summary=f"No target operations ('{self.rule.target_operation}') occurred; contract vacuously satisfied.",
                assumptions=("Vacuous satisfaction over empty target set",),
            )

        if EvidenceVerdict.REFUTED in self._verdicts:
            final_verdict = EvidenceVerdict.REFUTED
            summary = next(
                (s for s in self._summaries if s.startswith("Refuted")),
                f"Refuted: contract '{self.contract.name}' violated.",
            )
        elif EvidenceVerdict.CONFLICTING_EVIDENCE in self._verdicts:
            final_verdict = EvidenceVerdict.CONFLICTING_EVIDENCE
            summary = next(
                (s for s in self._summaries if s.startswith("Conflicting")),
                f"Conflicting evidence for contract '{self.contract.name}'.",
            )
        elif self._overflow or EvidenceVerdict.INCONCLUSIVE in self._verdicts:
            final_verdict = EvidenceVerdict.INCONCLUSIVE
            summary = next(
                (s for s in self._summaries if s.startswith("Inconclusive")),
                f"Inconclusive: contract '{self.contract.name}' has unobserved gaps.",
            )
        else:
            final_verdict = EvidenceVerdict.SUPPORTED
            summary = f"Supported: all target operations satisfy contract '{self.contract.name}' with verified witnesses."

        return EvidenceAssessment(
            contract_name=self.contract.name,
            verdict=final_verdict,
            summary=summary,
            support_obs_ids=tuple(dict.fromkeys(self._support_obs_ids)),
            refute_obs_ids=tuple(dict.fromkeys(self._refute_obs_ids)),
            missing_facts=tuple(self._missing_facts),
            remedies=tuple(dict.fromkeys(self._remedies)),
            assumptions=tuple(dict.fromkeys(self._assumptions)),
        )
