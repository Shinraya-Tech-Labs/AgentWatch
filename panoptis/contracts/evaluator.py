"""Evaluator for Evidence Sufficiency and Temporal Contracts.

Executes open-world temporal verification over ComputationalEvents,
extracting minimal witnesses and identifying unobserved facts without false alarms.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from panoptis.contracts.coverage import build_coverage_profile
from panoptis.contracts.order import (
    CausalRelation,
    HappensBeforeGraph,
    TimestampInterval,
)
from panoptis.contracts.remedies import get_remedy_for_missing_fact
from panoptis.contracts.schema import (
    ContractOperator,
    CoverageSnapshot,
    EvidenceAssessment,
    EvidenceContract,
    EvidenceVerdict,
    MissingFact,
    MissingReason,
    Recoverability,
)
from panoptis.events.model import ComputationalEvent


def evaluate_contract(
    events: Sequence[ComputationalEvent],
    contract: EvidenceContract,
    coverage: Sequence[CoverageSnapshot] = (),
) -> EvidenceAssessment:
    """Evaluate an EvidenceContract against an execution trace.

    ponytail: pure function, no database lookups or external network calls.
    Returns minimal witness observation IDs and exact missing telemetry gaps.
    """
    rule = contract.rule
    target_events = [e for e in events if e.operation == rule.target_operation]

    if not target_events:
        return EvidenceAssessment(
            contract_name=contract.name,
            verdict=EvidenceVerdict.SUPPORTED,
            summary=f"No target operations ('{rule.target_operation}') occurred; contract vacuously satisfied.",
            assumptions=("Vacuous satisfaction over empty target set",),
        )

    # Construct happens-before partial order DAG and check for causal cycles
    causal_graph = HappensBeforeGraph(events)
    if causal_graph.has_cycle():
        cycle_list = list(causal_graph.cycle_nodes)
        return EvidenceAssessment(
            contract_name=contract.name,
            verdict=EvidenceVerdict.CONFLICTING_EVIDENCE,
            summary=(
                f"Conflicting evidence: causal cycle or contradictory link topology detected "
                f"among events: {cycle_list}."
            ),
            assumptions=("Contradictory causal DAG prevents deterministic temporal evaluation",),
        )

    # Introspect dynamic coverage profile from events and declared coverage
    active_coverage = build_coverage_profile(events, coverage)

    # Index coverage by authoritative operations
    authoritative_sensors = {
        sensor.sensor_id: sensor
        for sensor in active_coverage
        if sensor.active and rule.required_operation in sensor.authoritative_for
    }

    # Extract max clock uncertainty across sensors and events
    max_clock_uncertainty_ms = max(
        [s.clock_uncertainty_ms for s in active_coverage]
        + [(e.time.uncertainty_ms or 0.0) if e.time is not None else 0.0 for e in events]
        + [0.0]
    )

    all_support_obs: list[str] = []
    all_refute_obs: list[str] = []
    missing_facts: list[MissingFact] = []
    remedies: list[str] = []

    for target in target_events:
        # Resolve join key value (e.g. transaction_id)
        target_attrs = target.attributes
        join_key = rule.join_on[0] if rule.join_on else None
        join_val = target_attrs.get(join_key) if join_key else None

        # Compute expected observation window for this target
        target_time = target.time.start if target.time is not None else None
        window_start: datetime | None = None
        window_end: datetime | None = None
        if target_time is not None:
            if rule.operator == ContractOperator.PRECEDING_EVENT:
                window_start = target_time - timedelta(seconds=rule.max_window_seconds)
                window_end = target_time
            elif rule.operator == ContractOperator.EVENTUALLY_WITHIN:
                window_start = target_time
                window_end = target_time + timedelta(seconds=rule.max_window_seconds)

        def check_sensor_gaps_or_sampling() -> EvidenceAssessment | None:
            if not authoritative_sensors:
                return None

            # 1. Check sampling
            for s in authoritative_sensors.values():
                if s.sampling_rate is not None and s.sampling_rate < 1.0:
                    fact = MissingFact(
                        field_name="event_stream",
                        reason=MissingReason.SAMPLED_OUT,
                        required_for=rule.required_operation,
                        candidate_sensors=(s.sensor_id,),
                        recoverability=Recoverability.MAYBE,
                    )
                    return EvidenceAssessment(
                        contract_name=contract.name,
                        verdict=EvidenceVerdict.INCONCLUSIVE,
                        summary=(
                            f"Inconclusive: required '{rule.required_operation}' may have been sampled out "
                            f"(sampling rate {s.sampling_rate:.1%}) on authoritative sensor '{s.sensor_id}'."
                        ),
                        missing_facts=(fact,),
                        remedies=(get_remedy_for_missing_fact(fact),),
                        assumptions=(
                            f"Probabilistic sampling on '{s.sensor_id}' prevents definitive refutation",
                        ),
                    )

            # 2. Check localized sequence gaps overlapping the window
            for s in authoritative_sensors.values():
                for gap in s.gaps:
                    if (
                        window_start is None
                        or window_end is None
                        or gap.overlaps_window(window_start, window_end)
                    ):
                        fact = MissingFact(
                            field_name="sequence_gap",
                            reason=MissingReason.SEQUENCE_GAP,
                            required_for=rule.required_operation,
                            candidate_sensors=(s.sensor_id,),
                            recoverability=Recoverability.INSTRUMENTABLE,
                        )
                        return EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.INCONCLUSIVE,
                            summary=(
                                f"Inconclusive: target '{rule.target_operation}' occurred without required '{rule.required_operation}', "
                                f"but authoritative sensor '{s.sensor_id}' experienced sequence gap "
                                f"[{gap.seq_lost_start}..{gap.seq_lost_end}] ({gap.count} frames lost) "
                                f"overlapping the required observation window."
                            ),
                            missing_facts=(fact,),
                            remedies=(get_remedy_for_missing_fact(fact),),
                            assumptions=(
                                f"Authoritative sensor '{s.sensor_id}' experienced sequence gap [{gap.seq_lost_start}..{gap.seq_lost_end}]",
                            ),
                        )

            # 3. Check aggregate dropped_count without specific gap windows
            for s in authoritative_sensors.values():
                if s.dropped_count > 0 and not s.gaps:
                    fact = MissingFact(
                        field_name="sequence_gap",
                        reason=MissingReason.SEQUENCE_GAP,
                        required_for=rule.required_operation,
                        candidate_sensors=(s.sensor_id,),
                        recoverability=Recoverability.INSTRUMENTABLE,
                    )
                    return EvidenceAssessment(
                        contract_name=contract.name,
                        verdict=EvidenceVerdict.INCONCLUSIVE,
                        summary=(
                            f"Inconclusive: target '{rule.target_operation}' occurred without required '{rule.required_operation}', "
                            f"but authoritative sensor '{s.sensor_id}' reported {s.dropped_count} dropped frames."
                        ),
                        missing_facts=(fact,),
                        remedies=(get_remedy_for_missing_fact(fact),),
                        assumptions=(
                            f"Authoritative sensor '{s.sensor_id}' reported dropped frames ({s.dropped_count})",
                        ),
                    )

            return None

        # Find matching candidate required events
        candidates = [
            e
            for e in events
            if e.operation == rule.required_operation
            and (join_key is None or e.attributes.get(join_key) == join_val)
        ]

        if not candidates:
            # Check if any authoritative sensor experienced sequence gaps or sampling
            gap_assessment = check_sensor_gaps_or_sampling()
            if gap_assessment is not None:
                return gap_assessment

            # Open-world determination: was absence authoritatively captured?
            # Any authoritative sensor remaining has been verified clean within the required window.
            if authoritative_sensors and contract.require_authoritative_log_for_refutation:
                sensor_names = ", ".join(authoritative_sensors.keys())
                all_refute_obs.extend(target.derived_from)
                return EvidenceAssessment(
                    contract_name=contract.name,
                    verdict=EvidenceVerdict.REFUTED,
                    summary=(
                        f"Refuted: target '{rule.target_operation}' executed without required '{rule.required_operation}'. "
                        f"Authoritative sensors ({sensor_names}) attest complete coverage with zero dropped events."
                    ),
                    refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                    assumptions=(
                        f"Authoritative log completeness for '{rule.required_operation}' attested by {sensor_names}",
                    ),
                )
            else:
                # Missing telemetry: inconclusive, NOT a false alarm
                fact = MissingFact(
                    field_name=join_key or "event_id",
                    reason=MissingReason.SENSOR_DISABLED
                    if not coverage
                    else MissingReason.FIELD_ABSENT,
                    required_for=rule.required_operation,
                    candidate_sensors=(f"{rule.required_operation}_sensor",),
                    recoverability=Recoverability.INSTRUMENTABLE,
                )
                missing_facts.append(fact)
                remedies.append(get_remedy_for_missing_fact(fact))
                return EvidenceAssessment(
                    contract_name=contract.name,
                    verdict=EvidenceVerdict.INCONCLUSIVE,
                    summary=(
                        f"Inconclusive: target '{rule.target_operation}' was observed, but required '{rule.required_operation}' "
                        f"cannot be proven or refuted because no authoritative sensor attests its absence."
                    ),
                    missing_facts=tuple(missing_facts),
                    remedies=tuple(remedies),
                    assumptions=(
                        "Open-world assumption: absence of evidence is not evidence of absence",
                    ),
                )

        # Candidate found - verify temporal contract
        target_interval = TimestampInterval.from_event(
            target, default_uncertainty_ms=max_clock_uncertainty_ms
        )
        if target_interval is None:
            fact = MissingFact(
                field_name="timestamp",
                reason=MissingReason.FIELD_ABSENT,
                required_for=rule.required_operation,
                recoverability=Recoverability.INSTRUMENTABLE,
            )
            missing_facts.append(fact)
            remedies.append(get_remedy_for_missing_fact(fact))
            return EvidenceAssessment(
                contract_name=contract.name,
                verdict=EvidenceVerdict.INCONCLUSIVE,
                summary="Inconclusive: one or more events lack valid start timestamps.",
                missing_facts=tuple(missing_facts),
                remedies=tuple(remedies),
            )

        target_satisfied = False
        target_conflicting_assessment: EvidenceAssessment | None = None
        target_refute_assessment: EvidenceAssessment | None = None

        for req in candidates:
            req_interval = TimestampInterval.from_event(
                req, default_uncertainty_ms=max_clock_uncertainty_ms
            )
            if req_interval is None:
                fact = MissingFact(
                    field_name="timestamp",
                    reason=MissingReason.FIELD_ABSENT,
                    required_for=rule.required_operation,
                    recoverability=Recoverability.INSTRUMENTABLE,
                )
                missing_facts.append(fact)
                remedies.append(get_remedy_for_missing_fact(fact))
                return EvidenceAssessment(
                    contract_name=contract.name,
                    verdict=EvidenceVerdict.INCONCLUSIVE,
                    summary="Inconclusive: one or more events lack valid start timestamps.",
                    missing_facts=tuple(missing_facts),
                    remedies=tuple(remedies),
                )

            causal_rel = causal_graph.relate(req.event_id, target.event_id)

            if causal_rel == CausalRelation.CONTRADICTORY:
                return EvidenceAssessment(
                    contract_name=contract.name,
                    verdict=EvidenceVerdict.CONFLICTING_EVIDENCE,
                    summary=(
                        f"Conflicting evidence: causal cycle or contradictory link ordering detected "
                        f"between candidate '{req.event_id}' and target '{target.event_id}'."
                    ),
                    support_obs_ids=tuple(dict.fromkeys(target.derived_from + req.derived_from)),
                    assumptions=("Contradictory causal relationships prevent ordering",),
                )

            if rule.operator == ContractOperator.PRECEDING_EVENT:
                if causal_rel == CausalRelation.BEFORE:
                    # req causally precedes target (req ≺ target)
                    min_delta, _ = req_interval.separation_seconds_range(target_interval)
                    if min_delta > rule.max_window_seconds:
                        gap_assessment = check_sensor_gaps_or_sampling()
                        if gap_assessment is not None:
                            return gap_assessment
                        all_refute_obs.extend(target.derived_from + req.derived_from)
                        target_refute_assessment = EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.REFUTED,
                            summary=(
                                f"Refuted: '{rule.required_operation}' preceded target but exceeded max allowed window "
                                f"({min_delta:.1f}s > {rule.max_window_seconds:.1f}s)."
                            ),
                            refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                        )
                    else:
                        all_support_obs.extend(target.derived_from + req.derived_from)
                        target_satisfied = True
                        break

                elif causal_rel == CausalRelation.AFTER:
                    # target causally precedes req (target ≺ req)
                    gap_assessment = check_sensor_gaps_or_sampling()
                    if gap_assessment is not None:
                        return gap_assessment
                    all_refute_obs.extend(target.derived_from + req.derived_from)
                    target_refute_assessment = EvidenceAssessment(
                        contract_name=contract.name,
                        verdict=EvidenceVerdict.REFUTED,
                        summary=f"Refuted: '{rule.required_operation}' occurred after '{rule.target_operation}' (causally follows target).",
                        refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                    )

                else:
                    # CausalRelation.CONCURRENT (req ∥ target)
                    if req_interval.overlaps(target_interval):
                        fact = MissingFact(
                            field_name="clock_sync",
                            reason=MissingReason.CLOCK_AMBIGUITY,
                            required_for=rule.required_operation,
                            recoverability=Recoverability.INSTRUMENTABLE,
                        )
                        diff_ms = (
                            abs((target.time.start - req.time.start).total_seconds() * 1000.0)
                            if target.time.start and req.time.start
                            else 0.0
                        )
                        max_u = max(
                            req_interval.uncertainty_ms,
                            target_interval.uncertainty_ms,
                            max_clock_uncertainty_ms,
                        )
                        target_conflicting_assessment = EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.CONFLICTING_EVIDENCE,
                            summary=(
                                f"Conflicting evidence: time difference ({diff_ms:.1f}ms) between events is within clock "
                                f"uncertainty threshold ({max_u:.1f}ms) with no explicit parent span link."
                            ),
                            support_obs_ids=tuple(
                                dict.fromkeys(target.derived_from + req.derived_from)
                            ),
                            missing_facts=(fact,),
                            remedies=(get_remedy_for_missing_fact(fact),),
                            assumptions=(
                                f"Clock uncertainty of {max_u}ms prevents definitive ordering",
                            ),
                        )
                    elif req_interval.precedes(target_interval):
                        min_delta, _ = req_interval.separation_seconds_range(target_interval)
                        if min_delta > rule.max_window_seconds:
                            gap_assessment = check_sensor_gaps_or_sampling()
                            if gap_assessment is not None:
                                return gap_assessment
                            all_refute_obs.extend(target.derived_from + req.derived_from)
                            target_refute_assessment = EvidenceAssessment(
                                contract_name=contract.name,
                                verdict=EvidenceVerdict.REFUTED,
                                summary=(
                                    f"Refuted: '{rule.required_operation}' preceded target but exceeded max allowed window "
                                    f"({min_delta:.1f}s > {rule.max_window_seconds:.1f}s)."
                                ),
                                refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                            )
                        else:
                            all_support_obs.extend(target.derived_from + req.derived_from)
                            target_satisfied = True
                            break
                    else:
                        # target strictly precedes req
                        delta_sec = (
                            (req.time.start - target.time.start).total_seconds()
                            if req.time.start and target.time.start
                            else 0.0
                        )
                        gap_assessment = check_sensor_gaps_or_sampling()
                        if gap_assessment is not None:
                            return gap_assessment
                        all_refute_obs.extend(target.derived_from + req.derived_from)
                        target_refute_assessment = EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.REFUTED,
                            summary=f"Refuted: '{rule.required_operation}' occurred after '{rule.target_operation}' (delta: {delta_sec:.2f}s).",
                            refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                        )

            elif rule.operator == ContractOperator.EVENTUALLY_WITHIN:
                if causal_rel == CausalRelation.AFTER:
                    # target causally precedes req (target ≺ req)
                    min_delta, _ = target_interval.separation_seconds_range(req_interval)
                    if min_delta > rule.max_window_seconds:
                        gap_assessment = check_sensor_gaps_or_sampling()
                        if gap_assessment is not None:
                            return gap_assessment
                        all_refute_obs.extend(target.derived_from + req.derived_from)
                        target_refute_assessment = EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.REFUTED,
                            summary=f"Refuted: '{rule.required_operation}' exceeded max follow-up window ({min_delta:.1f}s).",
                            refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                        )
                    else:
                        all_support_obs.extend(target.derived_from + req.derived_from)
                        target_satisfied = True
                        break

                elif causal_rel == CausalRelation.BEFORE:
                    # req causally precedes target (req ≺ target)
                    gap_assessment = check_sensor_gaps_or_sampling()
                    if gap_assessment is not None:
                        return gap_assessment
                    all_refute_obs.extend(target.derived_from + req.derived_from)
                    target_refute_assessment = EvidenceAssessment(
                        contract_name=contract.name,
                        verdict=EvidenceVerdict.REFUTED,
                        summary=f"Refuted: '{rule.required_operation}' occurred before target when expected after.",
                        refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                    )

                else:
                    # Concurrent (req ∥ target)
                    if req_interval.overlaps(target_interval):
                        fact = MissingFact(
                            field_name="clock_sync",
                            reason=MissingReason.CLOCK_AMBIGUITY,
                            required_for=rule.required_operation,
                            recoverability=Recoverability.INSTRUMENTABLE,
                        )
                        diff_ms = (
                            abs((req.time.start - target.time.start).total_seconds() * 1000.0)
                            if req.time.start and target.time.start
                            else 0.0
                        )
                        max_u = max(
                            req_interval.uncertainty_ms,
                            target_interval.uncertainty_ms,
                            max_clock_uncertainty_ms,
                        )
                        target_conflicting_assessment = EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.CONFLICTING_EVIDENCE,
                            summary=(
                                f"Conflicting evidence: time difference ({diff_ms:.1f}ms) between events is within clock "
                                f"uncertainty threshold ({max_u:.1f}ms) with no explicit parent span link."
                            ),
                            support_obs_ids=tuple(
                                dict.fromkeys(target.derived_from + req.derived_from)
                            ),
                            missing_facts=(fact,),
                            remedies=(get_remedy_for_missing_fact(fact),),
                            assumptions=(
                                f"Clock uncertainty of {max_u}ms prevents definitive ordering",
                            ),
                        )
                    elif target_interval.precedes(req_interval):
                        min_delta, _ = target_interval.separation_seconds_range(req_interval)
                        if min_delta > rule.max_window_seconds:
                            gap_assessment = check_sensor_gaps_or_sampling()
                            if gap_assessment is not None:
                                return gap_assessment
                            all_refute_obs.extend(target.derived_from + req.derived_from)
                            target_refute_assessment = EvidenceAssessment(
                                contract_name=contract.name,
                                verdict=EvidenceVerdict.REFUTED,
                                summary=f"Refuted: '{rule.required_operation}' exceeded max follow-up window ({min_delta:.1f}s).",
                                refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                            )
                        else:
                            all_support_obs.extend(target.derived_from + req.derived_from)
                            target_satisfied = True
                            break
                    else:
                        gap_assessment = check_sensor_gaps_or_sampling()
                        if gap_assessment is not None:
                            return gap_assessment
                        all_refute_obs.extend(target.derived_from + req.derived_from)
                        target_refute_assessment = EvidenceAssessment(
                            contract_name=contract.name,
                            verdict=EvidenceVerdict.REFUTED,
                            summary=f"Refuted: '{rule.required_operation}' occurred before target when expected after.",
                            refute_obs_ids=tuple(dict.fromkeys(all_refute_obs)),
                        )

        if not target_satisfied:
            if target_conflicting_assessment is not None:
                return target_conflicting_assessment
            if target_refute_assessment is not None:
                return target_refute_assessment

    return EvidenceAssessment(
        contract_name=contract.name,
        verdict=EvidenceVerdict.SUPPORTED,
        summary=f"Supported: all target operations satisfy contract '{contract.name}' with verified witnesses.",
        support_obs_ids=tuple(dict.fromkeys(all_support_obs)),
        assumptions=("Temporal constraints verified over observable execution witness",),
    )
