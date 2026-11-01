"""Sensor sequence continuity tracking and dynamic coverage profile introspection.

Monitors per-sensor monotonically increasing sequence counters, isolates multi-sensor
streams, detects dropped frame spans [seq_lost_start, seq_lost_end], and models
dynamic capture profiles (drop rate, sampling policy, health status).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from panoptis.contracts.schema import CoverageSnapshot, SequenceGap
from panoptis.events.model import ComputationalEvent


def parse_event_sequence(event: ComputationalEvent) -> tuple[str, int] | None:
    """Extract (sensor_id, sequence_number) from a ComputationalEvent.

    Inspects TemporalInfo.ordering_key (formatted as '{sensor_id}:{seq:012d}')
    with fallback to event.attributes['sensor_id'] and event.attributes['source_seq'].
    Returns None if the event does not carry a monotonic sequence.
    """
    ordering_key = event.time.ordering_key if event.time else ""
    if ordering_key:
        if ":" in ordering_key:
            sensor_id, _, seq_str = ordering_key.rpartition(":")
            if seq_str.isdigit():
                return sensor_id, int(seq_str)
        elif ordering_key.isdigit():
            attrs = event.attributes
            sensor_id = str(
                attrs.get("sensor_id") or attrs.get("sensor_instance_id") or "default_sensor"
            )
            return sensor_id, int(ordering_key)

    # Fallback to attributes
    attrs = event.attributes
    sensor_id = attrs.get("sensor_id") or attrs.get("sensor_instance_id")
    seq = attrs.get("source_seq") or attrs.get("seq")
    if sensor_id is not None and seq is not None:
        try:
            return str(sensor_id), int(seq)
        except (ValueError, TypeError):
            return None

    return None


class SequenceContinuityTracker:
    """Tracks sequence continuity per sensor stream and detects dropped spans.

    Handles:
    - Partitioning events into isolated per-sensor streams.
    - Filtering duplicates (at-least-once delivery or replay).
    - Sorting out-of-order events before continuity evaluation.
    - Computing exact dropped intervals [seq_lost_start, seq_lost_end].
    """

    def __init__(self) -> None:
        # sensor_id -> list of (seq, timestamp, event)
        self._streams: dict[str, list[tuple[int, datetime | None, Any]]] = defaultdict(list)

    def add_event(self, event: ComputationalEvent) -> bool:
        """Add a ComputationalEvent to the tracker. Returns True if sequence was parsed."""
        parsed = parse_event_sequence(event)
        if parsed is None:
            return False
        sensor_id, seq = parsed
        t = event.time.start if event.time else None
        self._streams[sensor_id].append((seq, t, event))
        return True

    def add_sequence(
        self,
        sensor_id: str,
        seq: int,
        timestamp: datetime | None = None,
        event: ComputationalEvent | None = None,
    ) -> None:
        """Explicitly record a sequence observation for a sensor."""
        self._streams[sensor_id].append((seq, timestamp, event))

    def compute_gaps(self, sensor_id: str) -> tuple[SequenceGap, ...]:
        """Compute missing sequence spans for a given sensor stream."""
        items = self._streams.get(sensor_id, [])
        if len(items) < 2:
            return ()

        # Sort by sequence number; break ties with timestamp
        sorted_items = sorted(
            items, key=lambda x: (x[0], x[1].timestamp() if x[1] is not None else 0.0)
        )

        # Filter duplicates (at-least-once network delivery or retry re-emissions)
        deduped: list[tuple[int, datetime | None]] = []
        for seq, t, _ in sorted_items:
            if not deduped or deduped[-1][0] != seq:
                deduped.append((seq, t))

        gaps: list[SequenceGap] = []
        for i in range(1, len(deduped)):
            prev_seq, prev_time = deduped[i - 1]
            curr_seq, curr_time = deduped[i]
            if curr_seq > prev_seq + 1:
                gaps.append(
                    SequenceGap(
                        sensor_id=sensor_id,
                        seq_lost_start=prev_seq + 1,
                        seq_lost_end=curr_seq - 1,
                        count=curr_seq - prev_seq - 1,
                        t_before=prev_time,
                        t_after=curr_time,
                    )
                )

        return tuple(gaps)

    def get_bounds(self, sensor_id: str) -> tuple[int | None, int | None]:
        """Return (seq_min, seq_max) for a sensor stream."""
        items = self._streams.get(sensor_id, [])
        if not items:
            return None, None
        seqs = [x[0] for x in items]
        return min(seqs), max(seqs)

    def get_snapshot(
        self, sensor_id: str, declared: CoverageSnapshot | None = None
    ) -> CoverageSnapshot:
        """Produce a CoverageSnapshot for a sensor stream, merging declared metadata."""
        gaps = self.compute_gaps(sensor_id)
        seq_min, seq_max = self.get_bounds(sensor_id)
        gap_lost_count = sum(g.count for g in gaps)
        declared_dropped = declared.dropped_count if declared else 0
        total_dropped = gap_lost_count + declared_dropped

        return CoverageSnapshot(
            sensor_id=sensor_id,
            authoritative_for=declared.authoritative_for if declared else (),
            active=declared.active if declared else True,
            dropped_count=total_dropped,
            clock_uncertainty_ms=declared.clock_uncertainty_ms if declared else 0.0,
            seq_min=seq_min if seq_min is not None else (declared.seq_min if declared else None),
            seq_max=seq_max if seq_max is not None else (declared.seq_max if declared else None),
            gaps=gaps + (declared.gaps if declared else ()),
            sampling_rate=declared.sampling_rate if declared else None,
            sampling_policy=declared.sampling_policy if declared else None,
        )

    def all_snapshots(self, declared: Sequence[CoverageSnapshot] = ()) -> list[CoverageSnapshot]:
        """Produce CoverageSnapshots for all observed and declared sensors."""
        declared_map = {c.sensor_id: c for c in declared}
        all_sensor_ids = sorted(set(declared_map.keys()) | set(self._streams.keys()))
        snapshots: list[CoverageSnapshot] = []
        for sid in all_sensor_ids:
            dec = declared_map.get(sid)
            snapshots.append(self.get_snapshot(sid, dec))
        return snapshots


def build_coverage_profile(
    events: Sequence[ComputationalEvent],
    declared_coverage: Sequence[CoverageSnapshot] = (),
) -> list[CoverageSnapshot]:
    """Inspect event streams and produce unified CoverageSnapshots with gap detection.

    Merges static declared CoverageSnapshots with dynamically introspected
    sequence continuity metrics and detected gap windows.
    """
    tracker = SequenceContinuityTracker()
    for e in events:
        tracker.add_event(e)
    return tracker.all_snapshots(declared_coverage)


class CoverageProfileRegistry:
    """Registry managing static and introspected capture profiles."""

    def __init__(self, declared_coverage: Sequence[CoverageSnapshot] = ()) -> None:
        self._declared: dict[str, CoverageSnapshot] = {c.sensor_id: c for c in declared_coverage}

    def register(self, snapshot: CoverageSnapshot) -> None:
        """Register or update a declared CoverageSnapshot."""
        self._declared[snapshot.sensor_id] = snapshot

    def introspect(self, events: Sequence[ComputationalEvent]) -> list[CoverageSnapshot]:
        """Introspect events against registered profiles."""
        return build_coverage_profile(events, list(self._declared.values()))
