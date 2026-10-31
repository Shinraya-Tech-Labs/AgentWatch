"""Adversarial Missingness Injectors (Issue #737 / AWBench-Sufficiency).

Systematic combinatoric perturbation harness:
- Random telemetry packet drops (1% to 40%).
- Sensor disabling (simulating crash, network partition, or uninstrumented components).
- Clock drift (+/- 500ms) and interval uncertainty expansion.
- Out-of-order arrival jitter.
- Critical attribute redaction (PII/compliance scrubbing).
- Conflicting source attestation.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from agentwatch.contracts.schema import CoverageSnapshot
from agentwatch.events.model import ComputationalEvent, TemporalInfo


@dataclass(frozen=True, slots=True)
class PerturbationConfig:
    """Configurable parameters for adversarial perturbation injection."""

    packet_drop_rate: float = 0.0  # 0.01 to 0.40
    disabled_sensors: tuple[str, ...] = ()
    clock_drift_ms: float = 0.0  # e.g. 500.0
    jitter_reorder_prob: float = 0.0
    redact_attributes: tuple[str, ...] = ()
    conflicting_source_attestation: bool = False
    seed: int = 42


@dataclass(frozen=True, slots=True)
class PerturbationResult:
    """Result of injecting perturbations into a workflow trace."""

    events: tuple[ComputationalEvent, ...]
    coverage: tuple[CoverageSnapshot, ...]
    dropped_event_ids: tuple[str, ...]
    disabled_sensors: tuple[str, ...]
    has_unobserved_required_telemetry: bool


def apply_adversarial_perturbations(
    events: Sequence[ComputationalEvent],
    target_operation: str,
    required_operation: str,
    known_sensors: Sequence[str],
    config: PerturbationConfig,
) -> PerturbationResult:
    """Deterministically applies configured adversarial perturbations to trace events."""
    rng = random.Random(config.seed)  # noqa: S311

    dropped_ids: set[str] = set()
    dropped_counts_by_sensor: dict[str, int] = defaultdict(int)
    disabled_set = set(config.disabled_sensors)
    surviving_events: list[ComputationalEvent] = []
    unobserved_required = False

    for event in events:
        # Check if sensor is disabled
        attrs = event.attributes
        sensor_id = attrs.get("sensor_id", "sensor_default")

        if sensor_id in disabled_set:
            dropped_ids.add(event.event_id)
            dropped_counts_by_sensor[sensor_id] += 1
            if event.operation == required_operation:
                unobserved_required = True
            continue

        # Check random packet drop (never drop target operation so the obligation is active)
        if event.operation != target_operation and rng.random() < config.packet_drop_rate:
            dropped_ids.add(event.event_id)
            dropped_counts_by_sensor[sensor_id] += 1
            if event.operation == required_operation:
                unobserved_required = True
            continue

        # Modify event copy with clock drift, jitter, and redaction
        new_event = _perturb_single_event(event, rng, config)
        surviving_events.append(new_event)

    # Apply out-of-order jitter if configured
    if config.jitter_reorder_prob > 0.0 and len(surviving_events) > 1:
        # Probabilistically swap adjacent non-causally constrained events
        for i in range(len(surviving_events) - 1):
            if rng.random() < config.jitter_reorder_prob:
                surviving_events[i], surviving_events[i + 1] = (
                    surviving_events[i + 1],
                    surviving_events[i],
                )

    # Build coverage snapshots
    coverage_snapshots: list[CoverageSnapshot] = []
    for s_id in known_sensors:
        is_active = s_id not in disabled_set
        # Determine operations authoritative for this sensor
        authoritative = [e.operation for e in events if e.attributes.get("sensor_id") == s_id]
        if not authoritative:
            authoritative = [required_operation]

        dropped = dropped_counts_by_sensor.get(s_id, 0)
        coverage_snapshots.append(
            CoverageSnapshot(
                sensor_id=s_id,
                active=is_active,
                authoritative_for=tuple(set(authoritative)),
                dropped_count=dropped,
                clock_uncertainty_ms=max(10.0, config.clock_drift_ms),
            )
        )

    return PerturbationResult(
        events=tuple(surviving_events),
        coverage=tuple(coverage_snapshots),
        dropped_event_ids=tuple(sorted(dropped_ids)),
        disabled_sensors=tuple(sorted(disabled_set)),
        has_unobserved_required_telemetry=unobserved_required,
    )


def _perturb_single_event(
    event: ComputationalEvent,
    rng: random.Random,
    config: PerturbationConfig,
) -> ComputationalEvent:
    attrs = dict(event.attributes)

    # Redact attributes
    for r_attr in config.redact_attributes:
        attrs.pop(r_attr, None)

    # Invert or corrupt attestation if configured
    if config.conflicting_source_attestation and "transaction_id" in attrs:
        # Inject conflicting second key space
        attrs["conflicting_alt_id"] = f"conflict_{rng.randint(100, 999)}"

    # Incur clock drift
    new_time = event.time
    if config.clock_drift_ms > 0.0 and event.time.start:
        drift_delta_s = rng.uniform(-config.clock_drift_ms, config.clock_drift_ms) / 1000.0
        drifted_start = event.time.start + timedelta(seconds=drift_delta_s)
        new_time = TemporalInfo(
            start=drifted_start,
            end=event.time.end,
            basis=event.time.basis,
            uncertainty_ms=event.time.uncertainty_ms + config.clock_drift_ms,
        )

    return ComputationalEvent(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        interp_id=event.interp_id,
        normalizer=event.normalizer,
        derived_from=event.derived_from,
        kind=event.kind,
        operation=event.operation,
        time=new_time,
        status=event.status,
        actor=event.actor,
        object=event.object,
        facets=event.facets,
        inputs=event.inputs,
        outputs=event.outputs,
        effects=event.effects,
        source_ids=event.source_ids,
        parents=event.parents,
        run_key=event.run_key,
        error_json=event.error_json,
        resources_json=event.resources_json,
        attributes_json=json.dumps(attrs),
        confidence_observation=event.confidence_observation,
        confidence_attribution=event.confidence_attribution,
        missing=event.missing,
    )
