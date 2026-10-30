"""Incremental monitor for the irreversible-action safety invariant.

Checks the metric temporal logic rule

    G(destructive -> O[-T,0] human_approved)

i.e. every irreversible operation must be preceded, or accompanied, by a witness
event (default ``human_approved``) whose time lies in ``[t - T, t]``. Both bounds are
inclusive and ``t`` is ``event.time.start``.

The monitor is incremental: witnesses are kept in a timestamp-ordered deque that is
pruned as the stream advances, and an irreversible event is checked only against that
deque, so the trace is never rescanned. Reordering of badly out-of-order streams is
the job of ``EventTimeWatermarkBuffer``; when a late action needs a witness that has
already been pruned, the monitor answers INCONCLUSIVE instead of guessing.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from panoptis.contracts.schema import EvidenceAssessment, EvidenceVerdict
from panoptis.events.model import ComputationalEvent, EventStatus

SAFETY_INVARIANT_BREACH = "SAFETY_INVARIANT_BREACH"
CONTRACT_NAME = "irreversible-action-requires-approval"

# A failed or withdrawn approval is not an approval.
_INVALID_WITNESS_STATUS = frozenset({EventStatus.ERROR, EventStatus.CANCELLED, EventStatus.TIMEOUT})

_RunBucket = tuple[str, str] | None


def _event_time(event: ComputationalEvent) -> datetime | None:
    start = event.time.start
    if start is None:
        return None
    return start if start.tzinfo is not None else start.replace(tzinfo=UTC)


class IrreversibleInvariantMonitor:
    """Streaming checker for ``G(destructive -> O[-T,0] human_approved)``."""

    def __init__(
        self,
        irreversible_operations: Iterable[str],
        witness_operation: str = "human_approved",
        window_seconds: float = 30.0,
        *,
        match_run_key: bool = True,
    ) -> None:
        if isinstance(irreversible_operations, str):
            raise TypeError("irreversible_operations must be an iterable of names, not a string")
        operations = frozenset(irreversible_operations)
        if not operations:
            raise ValueError("irreversible_operations must not be empty")
        if witness_operation in operations:
            raise ValueError("witness_operation cannot also be an irreversible operation")
        if not math.isfinite(window_seconds) or window_seconds < 0:
            raise ValueError("window_seconds must be a finite number >= 0")
        self.irreversible_operations = operations
        self.witness_operation = witness_operation
        self.window_seconds = float(window_seconds)
        self.match_run_key = match_run_key
        self._window = timedelta(seconds=self.window_seconds)
        # run bucket -> witnesses as (time, event_id), ascending by time
        self._witnesses: dict[_RunBucket, deque[tuple[datetime, str]]] = {}
        # run bucket -> latest time of a witness that was pruned away
        self._pruned_through: dict[_RunBucket, datetime] = {}
        self._latest: datetime | None = None

    @property
    def buffered_witnesses(self) -> int:
        """Number of witnesses currently retained (bounded by the window)."""
        return sum(len(d) for d in self._witnesses.values())

    def _bucket(self, event: ComputationalEvent) -> _RunBucket:
        return event.run_key if self.match_run_key else None

    def observe(self, event: ComputationalEvent) -> EvidenceAssessment | None:
        """Feed one event. Returns an assessment for irreversible events, else None."""
        t = _event_time(event)
        if event.operation == self.witness_operation:
            if t is not None and event.status not in _INVALID_WITNESS_STATUS:
                self._add_witness(self._bucket(event), t, event.event_id)
            self._advance(t)
            return None
        self._advance(t)
        if event.operation not in self.irreversible_operations:
            return None
        return self._check(event, t)

    def check_trace(self, events: Sequence[ComputationalEvent]) -> list[EvidenceAssessment]:
        """Replay a finished trace in time order and collect every assessment."""
        floor = datetime.min.replace(tzinfo=UTC)
        ordered = sorted(events, key=lambda e: (_event_time(e) or floor, e.time.ordering_key))
        results = []
        for event in ordered:
            assessment = self.observe(event)
            if assessment is not None:
                results.append(assessment)
        return results

    def _add_witness(self, bucket: _RunBucket, t: datetime, event_id: str) -> None:
        queue = self._witnesses.setdefault(bucket, deque())
        if not queue or t >= queue[-1][0]:
            queue.append((t, event_id))
            return
        # Late witness: rare, so a sorted rebuild is fine.
        merged = sorted([*queue, (t, event_id)], key=lambda item: item[0])
        queue.clear()
        queue.extend(merged)

    def _advance(self, t: datetime | None) -> None:
        """Move the stream clock forward and prune witnesses that can no longer matter."""
        if t is None:
            return
        if self._latest is None or t > self._latest:
            self._latest = t
        horizon = self._latest - self._window
        for bucket, queue in self._witnesses.items():
            while queue and queue[0][0] < horizon:
                dropped = queue.popleft()[0]
                previous = self._pruned_through.get(bucket)
                if previous is None or dropped > previous:
                    self._pruned_through[bucket] = dropped

    def _check(self, event: ComputationalEvent, t: datetime | None) -> EvidenceAssessment:
        if t is None:
            return EvidenceAssessment(
                contract_name=CONTRACT_NAME,
                verdict=EvidenceVerdict.INCONCLUSIVE,
                summary=(
                    f"Irreversible operation '{event.operation}' has no occurrence time; "
                    "approval window cannot be evaluated."
                ),
                assumptions=("Event time is required to evaluate O[-T,0]",),
            )
        bucket = self._bucket(event)
        lower = t - self._window
        # Latest witness w with t - T <= w <= t.
        for w_time, w_id in reversed(self._witnesses.get(bucket, ())):
            if w_time > t:
                continue
            if w_time >= lower:
                return EvidenceAssessment(
                    contract_name=CONTRACT_NAME,
                    verdict=EvidenceVerdict.SUPPORTED,
                    summary=(
                        f"Irreversible operation '{event.operation}' was approved by "
                        f"'{self.witness_operation}' within {self.window_seconds:g}s."
                    ),
                    support_obs_ids=(w_id, event.event_id),
                )
            break
        pruned = self._pruned_through.get(bucket)
        if pruned is not None and pruned >= lower:
            return EvidenceAssessment(
                contract_name=CONTRACT_NAME,
                verdict=EvidenceVerdict.INCONCLUSIVE,
                summary=(
                    f"Irreversible operation '{event.operation}' arrived after witnesses in "
                    "its approval window were pruned; late event, cannot decide."
                ),
                assumptions=("Events later than the retention window need a watermark buffer",),
            )
        return EvidenceAssessment(
            contract_name=CONTRACT_NAME,
            verdict=EvidenceVerdict.REFUTED,
            summary=(
                f"{SAFETY_INVARIANT_BREACH}: irreversible operation '{event.operation}' has no "
                f"'{self.witness_operation}' within the preceding {self.window_seconds:g}s."
            ),
            refute_obs_ids=(event.event_id,),
        )
