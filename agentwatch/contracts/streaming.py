"""Incremental Sliding-Window Stream Processor for Asynchronous Contract Evaluation.

Implements real-time open-world temporal contract verification with sub-millisecond
per-event latency, bounded memory retention, and out-of-order ingestion resilience.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from agentwatch.contracts.coverage import (
    SequenceContinuityTracker,
)
from agentwatch.contracts.order import (
    EventTimeWatermarkBuffer,
)
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
from agentwatch.events.model import ComputationalEvent

logger = logging.getLogger("agentwatch.contracts.streaming")


class ObligationState(StrEnum):
    """Lifecycle state of a temporal contract obligation."""

    PENDING = "pending"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


@dataclass(frozen=True, slots=True)
class StreamAssessmentDelta:
    """Incremental state transition emitted when an event affects contract state."""

    contract_name: str
    target_event_id: str
    previous_state: ObligationState
    current_state: ObligationState
    verdict: EvidenceVerdict
    witness_ids: tuple[str, ...] = ()
    missing_facts: tuple[MissingFact, ...] = ()
    remedies: tuple[str, ...] = ()
    summary: str = ""
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract_name,
            "target_event_id": self.target_event_id,
            "previous_state": self.previous_state.value,
            "current_state": self.current_state.value,
            "verdict": self.verdict.value,
            "witness_ids": list(self.witness_ids),
            "missing_facts": [f.to_dict() for f in self.missing_facts],
            "remedies": list(self.remedies),
            "summary": self.summary,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(slots=True)
class PendingObligation:
    """An active temporal obligation awaiting fulfillment or deadline expiration."""

    obligation_id: str
    contract: EvidenceContract
    target_event: ComputationalEvent
    join_val: Any
    window_start: datetime
    deadline: datetime
    state: ObligationState = ObligationState.PENDING
    witness_ids: list[str] = field(default_factory=list)
    missing_facts: list[MissingFact] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)
    summary: str = ""


class StreamingContractEvaluator:
    """Real-time sliding-window contract evaluation stream processor.

    Maintains active pending obligations in bounded memory, handles out-of-order
    event ingestion using watermarks, and resolves obligations to 4-valued epistemic verdicts.
    """

    def __init__(
        self,
        contracts: Sequence[EvidenceContract] | None = None,
        coverage: Sequence[CoverageSnapshot] | None = None,
        lateness_tolerance_seconds: float = 5.0,
        max_window_seconds: float = 600.0,
        continuity_tracker: SequenceContinuityTracker | None = None,
        watermark_buffer: EventTimeWatermarkBuffer | None = None,
    ) -> None:
        self._declared_coverage: list[CoverageSnapshot] = list(coverage or ())
        self.lateness_tolerance = timedelta(seconds=max(0.0, lateness_tolerance_seconds))
        self.max_window = timedelta(seconds=max(0.0, max_window_seconds))

        self._contracts: dict[str, EvidenceContract] = {}
        if contracts:
            for c in contracts:
                self.register_contract(c)

        self.continuity_tracker = continuity_tracker or SequenceContinuityTracker()
        self._watermark_buffer = watermark_buffer or EventTimeWatermarkBuffer(
            lateness_bound_seconds=lateness_tolerance_seconds,
            max_window_seconds=max_window_seconds,
        )

        # Fast lookup indices for bounded memory and O(1) matching
        self._events: dict[str, ComputationalEvent] = {}
        self._events_by_op_join: dict[tuple[str, Any], list[ComputationalEvent]] = defaultdict(list)
        self._events_timeline: deque[tuple[datetime, str]] = deque()

        self._pending: dict[str, PendingObligation] = {}
        self._pending_by_req_join: dict[tuple[str, Any], list[str]] = defaultdict(list)
        self._target_deltas: dict[tuple[str, str], StreamAssessmentDelta] = {}
        self._target_events_by_contract: dict[str, list[ComputationalEvent]] = defaultdict(list)

        self._max_event_time: datetime | None = None
        self._watermark: datetime | None = None

    @property
    def watermark(self) -> datetime | None:
        """Current stream event-time watermark."""
        return self._watermark

    @property
    def pending_count(self) -> int:
        """Number of active pending obligations."""
        return len(self._pending)

    @property
    def retained_count(self) -> int:
        """Number of retained events in the sliding-window buffer."""
        return len(self._events)

    def register_contract(self, contract: EvidenceContract) -> None:
        """Register a temporal evidence contract for streaming evaluation."""
        self._contracts[contract.name] = contract
        rule_window = timedelta(seconds=contract.rule.max_window_seconds)
        if rule_window > self.max_window:
            self.max_window = rule_window

    def register_coverage(self, snapshot: CoverageSnapshot) -> None:
        """Register an authoritative coverage snapshot for a sensor."""
        self._declared_coverage.append(snapshot)

    def get_pending_obligations(self) -> list[PendingObligation]:
        """Return a list of all currently active pending obligations."""
        return list(self._pending.values())

    def reset(self) -> None:
        """Reset state, buffers, and tracked obligations."""
        self._events.clear()
        self._events_by_op_join.clear()
        self._events_timeline.clear()
        self._pending.clear()
        self._pending_by_req_join.clear()
        self._target_deltas.clear()
        self._target_events_by_contract.clear()
        self._max_event_time = None
        self._watermark = None

    def ingest(self, event: ComputationalEvent) -> list[StreamAssessmentDelta]:
        """Ingest a single event into the sliding-window stream processor.

        Returns any state transitions (StreamAssessmentDelta) triggered by this event.
        """
        eid = event.event_id
        if eid in self._events:
            return []

        # 1. Monotonic continuity tracker update
        self.continuity_tracker.add_event(event)

        # 2. Extract event timestamp
        t_start = event.time.start if event.time else None
        if t_start is not None and t_start.tzinfo is None:
            t_start = t_start.replace(tzinfo=UTC)

        # 3. Watermark update
        if t_start is not None:
            if self._max_event_time is None or t_start > self._max_event_time:
                self._max_event_time = t_start
            self._watermark = self._max_event_time - self.lateness_tolerance

        # 4. Store event in fast lookup indices
        self._events[eid] = event
        if t_start is not None:
            self._events_timeline.append((t_start, eid))

        attrs = event.attributes
        self._events_by_op_join[(event.operation, None)].append(event)
        for contract in self._contracts.values():
            if contract.rule.join_on:
                jkey = contract.rule.join_on[0]
                if jkey in attrs:
                    self._events_by_op_join[(event.operation, attrs[jkey])].append(event)

        deltas: list[StreamAssessmentDelta] = []

        # 5. Check if incoming event fulfills any pending obligations
        req_keys = [(event.operation, None)]
        for contract in self._contracts.values():
            if contract.rule.join_on:
                jkey = contract.rule.join_on[0]
                if jkey in attrs:
                    req_keys.append((event.operation, attrs[jkey]))

        matched_ob_ids: set[str] = set()
        for rk in req_keys:
            for ob_id in self._pending_by_req_join.get(rk, []):
                if ob_id in self._pending and ob_id not in matched_ob_ids:
                    ob = self._pending[ob_id]
                    if self._matches_obligation(event, ob, t_start):
                        matched_ob_ids.add(ob_id)
                        ob.state = ObligationState.SUPPORTED
                        witnesses = tuple(
                            dict.fromkeys(
                                list(ob.target_event.derived_from) + list(event.derived_from)
                            )
                        )
                        delta = StreamAssessmentDelta(
                            contract_name=ob.contract.name,
                            target_event_id=ob.target_event.event_id,
                            previous_state=ObligationState.PENDING,
                            current_state=ObligationState.SUPPORTED,
                            verdict=EvidenceVerdict.SUPPORTED,
                            witness_ids=witnesses,
                            summary=(
                                f"Contract satisfied: target operation '{ob.contract.rule.target_operation}' "
                                f"had matching '{ob.contract.rule.required_operation}' within allowed window."
                            ),
                            timestamp=t_start,
                        )
                        deltas.append(delta)
                        self._target_deltas[(ob.contract.name, ob.target_event.event_id)] = delta

        # Clean up fulfilled obligations
        for ob_id in matched_ob_ids:
            ob = self._pending.pop(ob_id, None)
            if ob:
                rk = (ob.contract.rule.required_operation, ob.join_val)
                if rk in self._pending_by_req_join and ob_id in self._pending_by_req_join[rk]:
                    self._pending_by_req_join[rk].remove(ob_id)
                rk_none = (ob.contract.rule.required_operation, None)
                if (
                    rk_none in self._pending_by_req_join
                    and ob_id in self._pending_by_req_join[rk_none]
                ):
                    self._pending_by_req_join[rk_none].remove(ob_id)

        # 6. Check if incoming event is a target for any registered contract
        for contract in self._contracts.values():
            rule = contract.rule
            if event.operation == rule.target_operation:
                self._target_events_by_contract[contract.name].append(event)
                jkey = rule.join_on[0] if rule.join_on else None
                jval = attrs.get(jkey) if jkey else None

                if rule.operator == ContractOperator.PRECEDING_EVENT:
                    target_delta = self._evaluate_preceding_target(event, contract, jval, t_start)
                    if target_delta is not None:
                        deltas.append(target_delta)
                        self._target_deltas[(contract.name, event.event_id)] = target_delta
                elif rule.operator == ContractOperator.EVENTUALLY_WITHIN:
                    target_delta = self._evaluate_eventually_target(event, contract, jval, t_start)
                    if target_delta is not None:
                        deltas.append(target_delta)
                        self._target_deltas[(contract.name, event.event_id)] = target_delta

        # 7. Watermark progression: expire overdue obligations
        expired_deltas = self._expire_overdue_obligations()
        deltas.extend(expired_deltas)

        # 8. Bounded memory eviction
        self._evict_expired()

        return deltas

    def _matches_obligation(
        self,
        event: ComputationalEvent,
        ob: PendingObligation,
        t_start: datetime | None,
    ) -> bool:
        """Check if an event satisfies a pending obligation."""
        if event.operation != ob.contract.rule.required_operation:
            return False
        if ob.join_val is not None:
            jkey = ob.contract.rule.join_on[0] if ob.contract.rule.join_on else None
            if jkey and event.attributes.get(jkey) != ob.join_val:
                return False
        if t_start is not None:
            return ob.window_start <= t_start <= ob.deadline
        return True

    def _evaluate_preceding_target(
        self,
        target: ComputationalEvent,
        contract: EvidenceContract,
        jval: Any,
        t_start: datetime | None,
    ) -> StreamAssessmentDelta | None:
        """Evaluate a target event under PRECEDING_EVENT operator."""
        rule = contract.rule
        candidates = self._events_by_op_join.get((rule.required_operation, jval)) or (
            self._events_by_op_join.get((rule.required_operation, None)) if jval is None else []
        )

        matching_candidate: ComputationalEvent | None = None
        window_sec = rule.max_window_seconds
        for c in reversed(candidates):
            c_time = c.time.start if c.time else None
            if c_time is not None and c_time.tzinfo is None:
                c_time = c_time.replace(tzinfo=UTC)

            if t_start is not None and c_time is not None:
                delta_sec = (t_start - c_time).total_seconds()
                if 0.0 <= delta_sec <= window_sec:
                    matching_candidate = c
                    break

        if matching_candidate is not None:
            witnesses = tuple(
                dict.fromkeys(list(matching_candidate.derived_from) + list(target.derived_from))
            )
            return StreamAssessmentDelta(
                contract_name=contract.name,
                target_event_id=target.event_id,
                previous_state=ObligationState.PENDING,
                current_state=ObligationState.SUPPORTED,
                verdict=EvidenceVerdict.SUPPORTED,
                witness_ids=witnesses,
                summary=(
                    f"Contract satisfied: target operation '{rule.target_operation}' "
                    f"had preceding '{rule.required_operation}' within {rule.max_window_seconds}s."
                ),
                timestamp=t_start,
            )

        # Preceding event not found in current buffer.
        # Register pending obligation to allow out-of-order ingestion within lateness tolerance.
        ob_id = f"{contract.name}:{target.event_id}"
        window_start = (
            t_start - timedelta(seconds=rule.max_window_seconds)
            if t_start is not None
            else datetime.min.replace(tzinfo=UTC)
        )
        deadline = t_start + self.lateness_tolerance if t_start is not None else datetime.now(UTC)

        ob = PendingObligation(
            obligation_id=ob_id,
            contract=contract,
            target_event=target,
            join_val=jval,
            window_start=window_start,
            deadline=deadline,
        )
        self._pending[ob_id] = ob
        rk = (rule.required_operation, jval)
        self._pending_by_req_join[rk].append(ob_id)
        return None

    def _evaluate_eventually_target(
        self,
        target: ComputationalEvent,
        contract: EvidenceContract,
        jval: Any,
        t_start: datetime | None,
    ) -> StreamAssessmentDelta | None:
        """Evaluate a target event under EVENTUALLY_WITHIN operator."""
        rule = contract.rule
        candidates = self._events_by_op_join.get((rule.required_operation, jval)) or (
            self._events_by_op_join.get((rule.required_operation, None)) if jval is None else []
        )

        matching_candidate: ComputationalEvent | None = None
        window_sec = rule.max_window_seconds
        for c in candidates:
            c_time = c.time.start if c.time else None
            if c_time is not None and c_time.tzinfo is None:
                c_time = c_time.replace(tzinfo=UTC)

            if t_start is not None and c_time is not None:
                delta_sec = (c_time - t_start).total_seconds()
                if 0.0 <= delta_sec <= window_sec:
                    matching_candidate = c
                    break

        if matching_candidate is not None:
            witnesses = tuple(
                dict.fromkeys(list(target.derived_from) + list(matching_candidate.derived_from))
            )
            return StreamAssessmentDelta(
                contract_name=contract.name,
                target_event_id=target.event_id,
                previous_state=ObligationState.PENDING,
                current_state=ObligationState.SUPPORTED,
                verdict=EvidenceVerdict.SUPPORTED,
                witness_ids=witnesses,
                summary=(
                    f"Contract satisfied: target operation '{rule.target_operation}' "
                    f"eventually fulfilled by '{rule.required_operation}' within {rule.max_window_seconds}s."
                ),
                timestamp=t_start,
            )

        # Register pending obligation awaiting future fulfilling event
        ob_id = f"{contract.name}:{target.event_id}"
        window_start = t_start or datetime.min.replace(tzinfo=UTC)
        deadline = (
            t_start + timedelta(seconds=rule.max_window_seconds)
            if t_start is not None
            else datetime.now(UTC)
        )

        ob = PendingObligation(
            obligation_id=ob_id,
            contract=contract,
            target_event=target,
            join_val=jval,
            window_start=window_start,
            deadline=deadline,
        )
        self._pending[ob_id] = ob
        rk = (rule.required_operation, jval)
        self._pending_by_req_join[rk].append(ob_id)
        return None

    def _expire_overdue_obligations(self) -> list[StreamAssessmentDelta]:
        """Check watermark and resolve any pending obligations past their deadline."""
        if self._watermark is None:
            return []

        expired_deltas: list[StreamAssessmentDelta] = []
        for ob_id, ob in list(self._pending.items()):
            if self._watermark > ob.deadline:
                delta = self._resolve_expired_obligation(ob)
                expired_deltas.append(delta)
                self._target_deltas[(ob.contract.name, ob.target_event.event_id)] = delta
                self._pending.pop(ob_id, None)
                rk = (ob.contract.rule.required_operation, ob.join_val)
                if rk in self._pending_by_req_join and ob_id in self._pending_by_req_join[rk]:
                    self._pending_by_req_join[rk].remove(ob_id)
                rk_none = (ob.contract.rule.required_operation, None)
                if (
                    rk_none in self._pending_by_req_join
                    and ob_id in self._pending_by_req_join[rk_none]
                ):
                    self._pending_by_req_join[rk_none].remove(ob_id)

        return expired_deltas

    def _resolve_expired_obligation(self, ob: PendingObligation) -> StreamAssessmentDelta:
        """Resolve an unfulfilled pending obligation into a definitive epistemic verdict."""
        rule = ob.contract.rule
        active_cov = self.continuity_tracker.all_snapshots(self._declared_coverage)
        auth_sensors = {
            s.sensor_id: s
            for s in active_cov
            if s.active and rule.required_operation in s.authoritative_for
        }

        # 1. Localized Sequence Gaps overlapping the window
        for s in auth_sensors.values():
            for gap in s.gaps:
                if gap.overlaps_window(ob.window_start, ob.deadline):
                    fact = MissingFact(
                        field_name="sequence_gap",
                        reason=MissingReason.SEQUENCE_GAP,
                        required_for=rule.required_operation,
                        candidate_sensors=(s.sensor_id,),
                        recoverability=Recoverability.INSTRUMENTABLE,
                    )
                    return StreamAssessmentDelta(
                        contract_name=ob.contract.name,
                        target_event_id=ob.target_event.event_id,
                        previous_state=ObligationState.PENDING,
                        current_state=ObligationState.INCONCLUSIVE,
                        verdict=EvidenceVerdict.INCONCLUSIVE,
                        missing_facts=(fact,),
                        remedies=(get_remedy_for_missing_fact(fact),),
                        summary=(
                            f"Inconclusive: target '{rule.target_operation}' occurred without required '{rule.required_operation}', "
                            f"but authoritative sensor '{s.sensor_id}' experienced sequence gap "
                            f"[{gap.seq_lost_start}..{gap.seq_lost_end}] ({gap.count} frames lost) "
                            f"overlapping the required observation window."
                        ),
                        timestamp=self._watermark,
                    )

        # 2. Dropped frames without specific localized gap bounds
        for s in auth_sensors.values():
            if s.dropped_count > 0 and not s.gaps:
                fact = MissingFact(
                    field_name="sequence_gap",
                    reason=MissingReason.SEQUENCE_GAP,
                    required_for=rule.required_operation,
                    candidate_sensors=(s.sensor_id,),
                    recoverability=Recoverability.INSTRUMENTABLE,
                )
                return StreamAssessmentDelta(
                    contract_name=ob.contract.name,
                    target_event_id=ob.target_event.event_id,
                    previous_state=ObligationState.PENDING,
                    current_state=ObligationState.INCONCLUSIVE,
                    verdict=EvidenceVerdict.INCONCLUSIVE,
                    missing_facts=(fact,),
                    remedies=(get_remedy_for_missing_fact(fact),),
                    summary=(
                        f"Inconclusive: target '{rule.target_operation}' occurred without required '{rule.required_operation}', "
                        f"but authoritative sensor '{s.sensor_id}' reported {s.dropped_count} dropped frames."
                    ),
                    timestamp=self._watermark,
                )

        # 3. Probabilistic sampling on authoritative sensor
        for s in auth_sensors.values():
            if s.sampling_rate is not None and s.sampling_rate < 1.0:
                fact = MissingFact(
                    field_name="event_stream",
                    reason=MissingReason.SAMPLED_OUT,
                    required_for=rule.required_operation,
                    candidate_sensors=(s.sensor_id,),
                    recoverability=Recoverability.MAYBE,
                )
                return StreamAssessmentDelta(
                    contract_name=ob.contract.name,
                    target_event_id=ob.target_event.event_id,
                    previous_state=ObligationState.PENDING,
                    current_state=ObligationState.INCONCLUSIVE,
                    verdict=EvidenceVerdict.INCONCLUSIVE,
                    missing_facts=(fact,),
                    remedies=(get_remedy_for_missing_fact(fact),),
                    summary=(
                        f"Inconclusive: required '{rule.required_operation}' may have been sampled out "
                        f"(sampling rate {s.sampling_rate:.1%}) on authoritative sensor '{s.sensor_id}'."
                    ),
                    timestamp=self._watermark,
                )

        # 4. Authoritative sensor attest clean zero drops -> REFUTED
        if auth_sensors and ob.contract.require_authoritative_log_for_refutation:
            sensor_names = ", ".join(auth_sensors.keys())
            refute_obs = tuple(ob.target_event.derived_from)
            return StreamAssessmentDelta(
                contract_name=ob.contract.name,
                target_event_id=ob.target_event.event_id,
                previous_state=ObligationState.PENDING,
                current_state=ObligationState.REFUTED,
                verdict=EvidenceVerdict.REFUTED,
                witness_ids=refute_obs,
                summary=(
                    f"Refuted: target '{rule.target_operation}' executed without required '{rule.required_operation}'. "
                    f"Authoritative sensors ({sensor_names}) attest complete coverage with zero dropped events."
                ),
                timestamp=self._watermark,
            )

        # 5. Missing authoritative sensor -> INCONCLUSIVE (Open-world)
        jkey = rule.join_on[0] if rule.join_on else None
        fact = MissingFact(
            field_name=jkey or "event_id",
            reason=MissingReason.SENSOR_DISABLED
            if not self._declared_coverage
            else MissingReason.FIELD_ABSENT,
            required_for=rule.required_operation,
            candidate_sensors=(f"{rule.required_operation}_sensor",),
            recoverability=Recoverability.INSTRUMENTABLE,
        )
        return StreamAssessmentDelta(
            contract_name=ob.contract.name,
            target_event_id=ob.target_event.event_id,
            previous_state=ObligationState.PENDING,
            current_state=ObligationState.INCONCLUSIVE,
            verdict=EvidenceVerdict.INCONCLUSIVE,
            missing_facts=(fact,),
            remedies=(get_remedy_for_missing_fact(fact),),
            summary=(
                f"Inconclusive: target '{rule.target_operation}' was observed, but required '{rule.required_operation}' "
                f"cannot be proven or refuted because no authoritative sensor attests its absence."
            ),
            timestamp=self._watermark,
        )

    def _evict_expired(self) -> None:
        """Evict events that have fallen outside the sliding-window retention horizon."""
        if self._watermark is None:
            return

        horizon = self._watermark - self.max_window
        while self._events_timeline:
            t_evt, eid = self._events_timeline[0]
            if t_evt < horizon:
                self._events_timeline.popleft()
                evt = self._events.pop(eid, None)
                if evt:
                    op = evt.operation
                    # Remove from fast indices
                    for k in list(self._events_by_op_join.keys()):
                        if k[0] == op:
                            self._events_by_op_join[k] = [
                                e for e in self._events_by_op_join[k] if e.event_id != eid
                            ]
                            if not self._events_by_op_join[k]:
                                del self._events_by_op_join[k]
            else:
                break

    def assess(self, contract_name: str | None = None) -> list[EvidenceAssessment]:
        """Synthesize overall EvidenceAssessment for registered contracts."""
        # Force expiration of any remaining pending obligations
        if self._pending:
            self._max_event_time = self._max_event_time or datetime.now(UTC)
            self._watermark = self._max_event_time + self.max_window + self.lateness_tolerance
            expired = self._expire_overdue_obligations()
            for d in expired:
                self._target_deltas[(d.contract_name, d.target_event_id)] = d

        contracts_to_assess = (
            [self._contracts[contract_name]]
            if contract_name and contract_name in self._contracts
            else list(self._contracts.values())
        )

        assessments: list[EvidenceAssessment] = []
        for contract in contracts_to_assess:
            cname = contract.name
            target_evts = self._target_events_by_contract.get(cname, [])

            if not target_evts:
                assessments.append(
                    EvidenceAssessment(
                        contract_name=cname,
                        verdict=EvidenceVerdict.SUPPORTED,
                        summary=f"No target operations ('{contract.rule.target_operation}') occurred; contract vacuously satisfied.",
                        assumptions=("Vacuous satisfaction over empty target set",),
                    )
                )
                continue

            target_deltas = [
                self._target_deltas.get((cname, t.event_id))
                for t in target_evts
                if (cname, t.event_id) in self._target_deltas
            ]

            # Aggregate verdict priority: REFUTED > INCONCLUSIVE > CONFLICTING > SUPPORTED
            refuted = [d for d in target_deltas if d and d.verdict == EvidenceVerdict.REFUTED]
            inconclusive = [
                d for d in target_deltas if d and d.verdict == EvidenceVerdict.INCONCLUSIVE
            ]
            conflicting = [
                d for d in target_deltas if d and d.verdict == EvidenceVerdict.CONFLICTING_EVIDENCE
            ]
            supported = [d for d in target_deltas if d and d.verdict == EvidenceVerdict.SUPPORTED]

            if refuted:
                refute_ids: list[str] = []
                for d in refuted:
                    refute_ids.extend(d.witness_ids)
                assessments.append(
                    EvidenceAssessment(
                        contract_name=cname,
                        verdict=EvidenceVerdict.REFUTED,
                        summary=refuted[0].summary,
                        refute_obs_ids=tuple(dict.fromkeys(refute_ids)),
                        assumptions=(
                            f"Authoritative log completeness attested for '{contract.rule.required_operation}'",
                        ),
                    )
                )
            elif inconclusive:
                missing: list[MissingFact] = []
                remedies: list[str] = []
                for d in inconclusive:
                    missing.extend(d.missing_facts)
                    remedies.extend(d.remedies)
                assessments.append(
                    EvidenceAssessment(
                        contract_name=cname,
                        verdict=EvidenceVerdict.INCONCLUSIVE,
                        summary=inconclusive[0].summary,
                        missing_facts=tuple(dict.fromkeys(missing)),
                        remedies=tuple(dict.fromkeys(remedies)),
                        assumptions=(
                            "Open-world assumption: absence of evidence is not evidence of absence",
                        ),
                    )
                )
            elif conflicting:
                assessments.append(
                    EvidenceAssessment(
                        contract_name=cname,
                        verdict=EvidenceVerdict.CONFLICTING_EVIDENCE,
                        summary=conflicting[0].summary,
                    )
                )
            elif len(supported) == len(target_evts):
                support_ids: list[str] = []
                for d in supported:
                    support_ids.extend(d.witness_ids)
                assessments.append(
                    EvidenceAssessment(
                        contract_name=cname,
                        verdict=EvidenceVerdict.SUPPORTED,
                        summary=(
                            f"Contract satisfied: target operation '{contract.rule.target_operation}' "
                            f"had corresponding '{contract.rule.required_operation}' within allowed window."
                        ),
                        support_obs_ids=tuple(dict.fromkeys(support_ids)),
                    )
                )
            else:
                assessments.append(
                    EvidenceAssessment(
                        contract_name=cname,
                        verdict=EvidenceVerdict.INCONCLUSIVE,
                        summary="Inconclusive evaluation state for targets.",
                    )
                )

        return assessments

    def get_assessment(self, contract_name: str) -> EvidenceAssessment | None:
        """Get the synthesized assessment for a specific contract."""
        results = self.assess(contract_name)
        return results[0] if results else None

    def evaluate_all(self) -> list[EvidenceAssessment]:
        """Evaluate all registered contracts (alias for assess())."""
        return self.assess()
