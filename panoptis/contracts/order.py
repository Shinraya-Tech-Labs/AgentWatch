"""Happens-before partial order graph, interval timestamp logic, and watermarking engine.

Plane B module providing:
- CausalRelation: pairwise happens-before relations (BEFORE, AFTER, CONCURRENT, CONTRADICTORY)
- IntervalPrecedence: strict interval arithmetic (PRECEDES, SUCCEEDS, OVERLAPS)
- CausalCycleError: exception raised when cyclic or contradictory link topologies are detected
- TimestampInterval: per-event bounded uncertainty intervals [t_min, t_max]
- HappensBeforeGraph: Lamport happens-before DAG combining sequence monotonicity and DeclaredLinks
- EventTimeWatermarkBuffer: bounded sliding-window buffer with watermark and recomputation targeting
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from panoptis.contracts.coverage import parse_event_sequence
from panoptis.events.model import ComputationalEvent, DeclaredLink


class CausalRelation(StrEnum):
    """Pairwise relationship between two events in a happens-before partial order."""

    BEFORE = "before"  # a ≺ b (a causally precedes b)
    AFTER = "after"  # b ≺ a (a causally follows b)
    CONCURRENT = "concurrent"  # a ∥ b (causally independent)
    CONTRADICTORY = "contradictory"  # cyclic or contradictory ordering


class IntervalPrecedence(StrEnum):
    """Temporal precedence between two uncertainty intervals."""

    PRECEDES = "precedes"  # a_max < b_min (definite precedence)
    SUCCEEDS = "succeeds"  # b_max < a_min (definite succession)
    OVERLAPS = "overlaps"  # I(a) ∩ I(b) ≠ ∅ (physical order ambiguous)


class IngestionStatus(StrEnum):
    """Classification of an event upon ingestion into the watermark buffer."""

    ACCEPTED = "accepted"  # On-time or within lateness bound
    LATE = "late"  # Behind watermark, but within retention horizon
    DROPPED_TOO_OLD = "dropped_too_old"  # Behind retention horizon; cannot be retained
    DUPLICATE = "duplicate"  # Event ID already present in buffer


class CausalCycleError(ValueError):
    """Raised when an explicit cycle is detected in the causal graph."""

    def __init__(self, cycle_event_ids: tuple[str, ...], message: str = ""):
        self.cycle_event_ids = cycle_event_ids
        super().__init__(message or f"Causal cycle detected among events: {cycle_event_ids}")


@dataclass(frozen=True, slots=True)
class TimestampInterval:
    """Bounded uncertainty interval for an event occurrence.

    Represents [start_min, start_max] for point events and
    [start_min, end_max] for interval events.
    """

    start_min: datetime
    start_max: datetime
    end_min: datetime | None = None
    end_max: datetime | None = None
    uncertainty_ms: float = 0.0

    @classmethod
    def from_event(
        cls,
        event: ComputationalEvent,
        default_uncertainty_ms: float = 0.0,
    ) -> TimestampInterval | None:
        """Derive interval from ComputationalEvent timestamp and uncertainty fields.

        Returns None if event.time.start is missing. Clamps negative uncertainty to 0.0.
        """
        if event.time is None or event.time.start is None:
            return None

        t_start = event.time.start
        t_end = event.time.end

        # Priority: event.time.uncertainty_ms -> default_uncertainty_ms -> 0.0
        u_ms = (
            event.time.uncertainty_ms
            if event.time.uncertainty_ms is not None
            else default_uncertainty_ms
        )
        delta = timedelta(milliseconds=max(0.0, float(u_ms)))

        start_min = t_start - delta
        start_max = t_start + delta
        end_min = (t_end - delta) if t_end is not None else None
        end_max = (t_end + delta) if t_end is not None else None

        return cls(
            start_min=start_min,
            start_max=start_max,
            end_min=end_min,
            end_max=end_max,
            uncertainty_ms=max(0.0, float(u_ms)),
        )

    @property
    def effective_start_min(self) -> datetime:
        """Earliest possible occurrence moment."""
        return self.start_min

    @property
    def effective_end_max(self) -> datetime:
        """Latest possible occurrence moment."""
        return self.end_max if self.end_max is not None else self.start_max

    def precedes(self, other: TimestampInterval) -> bool:
        """Strict interval precedence: self definitely occurred before other.

        Holds iff self.effective_end_max < other.effective_start_min.
        """
        return self.effective_end_max < other.effective_start_min

    def overlaps(self, other: TimestampInterval) -> bool:
        """True iff intervals overlap (neither definitely precedes the other)."""
        return not (self.precedes(other) or other.precedes(self))

    def separation_seconds_range(self, other: TimestampInterval) -> tuple[float, float]:
        """Compute the [min_delta_sec, max_delta_sec] separation from self to other.

        min_delta = other.effective_start_min - self.effective_end_max
        max_delta = other.effective_end_max - self.effective_start_min
        """
        min_delta = (other.effective_start_min - self.effective_end_max).total_seconds()
        max_delta = (other.effective_end_max - self.effective_start_min).total_seconds()
        return min_delta, max_delta


class HappensBeforeGraph:
    """Lamport happens-before directed acyclic graph over ComputationalEvents.

    Integrates:
    1. Verified same-stream sequence monotonicity (seq(a) < seq(b) -> a ≺ b).
    2. Supported DeclaredLink relationships (parent, follows, responds_to, caused_by, depends_on).
    3. Source-ID resolution to event identifiers.
    4. Deterministic transitive reachability.
    5. Explicit cycle and contradiction detection.
    """

    SUPPORTED_LINK_RELATIONS: frozenset[str] = frozenset(
        {
            "parent",
            "follows",
            "responds_to",
            "caused_by",
            "depends_on",
        }
    )

    def __init__(self, events: Sequence[ComputationalEvent] = ()) -> None:
        self._events: dict[str, ComputationalEvent] = {}
        # (key_space, value) -> event_id
        self._source_index: dict[tuple[str, str], str] = {}
        # direct edges: u -> set of v (meaning u ≺ v)
        self._adj: dict[str, set[str]] = defaultdict(set)
        # reverse edges: v -> set of u
        self._rev_adj: dict[str, set[str]] = defaultdict(set)
        # reachability cache: u -> set of all descendants reachable from u
        self._descendants: dict[str, set[str]] = {}
        self._has_cycle: bool = False
        self._cycle_nodes: tuple[str, ...] = ()

        if events:
            self.build(events)

    @property
    def cycle_nodes(self) -> tuple[str, ...]:
        return self._cycle_nodes

    def has_cycle(self) -> bool:
        return self._has_cycle

    def build(self, events: Sequence[ComputationalEvent]) -> None:
        """Construct the causal graph from events, resolving links and sequences."""
        self._events.clear()
        self._source_index.clear()
        self._adj.clear()
        self._rev_adj.clear()
        self._descendants.clear()
        self._has_cycle = False
        self._cycle_nodes = ()

        # 1. Register events and index source IDs
        for e in events:
            self._events[e.event_id] = e
            # Index by primary event_id
            self._source_index[("event_id", e.event_id)] = e.event_id
            self._source_index[("", e.event_id)] = e.event_id
            # Index by each declared source ID
            for sid in e.source_ids:
                if len(sid) >= 2:
                    self._source_index[(sid[0], sid[1])] = e.event_id

        # 2. Build intra-stream sequence edges
        stream_events: dict[str, list[tuple[int, ComputationalEvent]]] = defaultdict(list)
        for e in self._events.values():
            parsed = parse_event_sequence(e)
            if parsed is not None:
                sensor_id, seq = parsed
                stream_events[sensor_id].append((seq, e))

        for sensor_id, items in stream_events.items():
            if len(items) < 2:
                continue
            # Sort by sequence number
            sorted_items = sorted(items, key=lambda x: x[0])
            # Add monotonic edges between distinct successive sequence numbers
            prev_seq: int | None = None
            prev_event: ComputationalEvent | None = None
            for seq, ev in sorted_items:
                if prev_seq is not None and prev_event is not None and seq > prev_seq:
                    self._add_edge(prev_event.event_id, ev.event_id)
                prev_seq = seq
                prev_event = ev

        # 3. Build inter-event DeclaredLink edges
        for e in self._events.values():
            for link in e.parents:
                if link.relation.lower() not in self.SUPPORTED_LINK_RELATIONS:
                    continue
                # Resolve link antecedent (the event that caused/preceded e)
                antecedent_id = self._resolve_link(link)
                if antecedent_id and antecedent_id in self._events:
                    # Link meaning: antecedent happened before e (antecedent ≺ e)
                    self._add_edge(antecedent_id, e.event_id)

        # 4. Detect cycles and build reachability
        self._detect_cycles_and_build_reachability()

    def _resolve_link(self, link: DeclaredLink) -> str | None:
        """Resolve a DeclaredLink to a registered event_id."""
        # 1. Exact match on (key_space, value)
        target = self._source_index.get((link.key_space, link.value))
        if target:
            return target

        # 2. Match on value as event_id
        if link.value in self._events:
            return link.value

        # 3. Fallback: match on value across any key_space if unique
        candidates = [
            eid
            for (ks, val), eid in self._source_index.items()
            if val == link.value and ks != "event_id" and ks != ""
        ]
        if len(set(candidates)) == 1:
            return candidates[0]

        return None

    def _add_edge(self, u: str, v: str) -> None:
        """Add directed edge u -> v (u ≺ v), ignoring self-loops directly."""
        if u == v:
            self._has_cycle = True
            if u not in self._cycle_nodes:
                self._cycle_nodes = self._cycle_nodes + (u,)
            return
        self._adj[u].add(v)
        self._rev_adj[v].add(u)

    def _detect_cycles_and_build_reachability(self) -> None:
        """Perform topological validation and construct transitive reachability sets."""
        all_nodes = set(self._events.keys())
        # In-degree computation
        in_degree = {u: len(self._rev_adj[u]) for u in all_nodes}
        queue = deque([u for u in all_nodes if in_degree[u] == 0])
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for nxt in self._adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited_count < len(all_nodes):
            self._has_cycle = True
            # Find nodes participating in cycles (in_degree > 0)
            cycle_nodes = sorted(u for u, deg in in_degree.items() if deg > 0)
            self._cycle_nodes = tuple(cycle_nodes)

        # Compute transitive reachability via DFS from each node
        for node in all_nodes:
            reach: set[str] = set()
            stack = list(self._adj[node])
            while stack:
                curr = stack.pop()
                if curr not in reach:
                    reach.add(curr)
                    stack.extend(self._adj[curr])
            self._descendants[node] = reach

    def happens_before(self, a_id: str, b_id: str) -> bool:
        """Return True iff a ≺ b (a causally precedes b)."""
        if a_id == b_id or a_id not in self._events or b_id not in self._events:
            return False
        return b_id in self._descendants.get(a_id, set())

    def relate(self, a_id: str, b_id: str) -> CausalRelation:
        """Determine pairwise causal relation between events a and b."""
        if a_id not in self._events or b_id not in self._events:
            return CausalRelation.CONCURRENT

        if a_id == b_id:
            return CausalRelation.CONCURRENT

        a_prec_b = self.happens_before(a_id, b_id)
        b_prec_a = self.happens_before(b_id, a_id)

        if a_prec_b and b_prec_a:
            return CausalRelation.CONTRADICTORY

        # If either node is part of a detected cycle
        if self._has_cycle and (a_id in self._cycle_nodes or b_id in self._cycle_nodes):
            if a_prec_b or b_prec_a:
                return CausalRelation.CONTRADICTORY

        if a_prec_b:
            return CausalRelation.BEFORE
        if b_prec_a:
            return CausalRelation.AFTER

        return CausalRelation.CONCURRENT

    def check_acyclic(self) -> None:
        """Raise CausalCycleError if the graph contains any cycles."""
        if self._has_cycle:
            raise CausalCycleError(self._cycle_nodes)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of ingesting an event into EventTimeWatermarkBuffer."""

    event_id: str
    status: IngestionStatus
    watermark: datetime | None
    affected_target_ids: tuple[str, ...]
    is_late: bool


class EventTimeWatermarkBuffer:
    """Bounded sliding-window event-time buffer for out-of-order trace ingestion.

    Maintains:
    - Event-time watermark: W(t) = max(event_time) - lateness_bound
    - Retention horizon: H(t) = W(t) - max_window_seconds
    - Deterministic classification: ACCEPTED, LATE, DROPPED_TOO_OLD, DUPLICATE
    - Precise affected target tracking for incremental recomputation
    - Bounded memory eviction
    """

    def __init__(
        self,
        lateness_bound_seconds: float = 5.0,
        max_window_seconds: float = 600.0,
    ) -> None:
        self.lateness_bound = timedelta(seconds=max(0.0, lateness_bound_seconds))
        self.max_window = timedelta(seconds=max(0.0, max_window_seconds))
        self._events: dict[str, ComputationalEvent] = {}
        self._max_event_time: datetime | None = None
        self._evicted_count: int = 0

    @property
    def watermark(self) -> datetime | None:
        """Current event-time watermark."""
        if self._max_event_time is None:
            return None
        return self._max_event_time - self.lateness_bound

    @property
    def retention_horizon(self) -> datetime | None:
        """Earliest event timestamp retained in the buffer."""
        wm = self.watermark
        if wm is None:
            return None
        return wm - self.max_window

    @property
    def retained_events(self) -> list[ComputationalEvent]:
        """All currently retained events."""
        return list(self._events.values())

    @property
    def evicted_count(self) -> int:
        """Total number of events evicted over buffer lifetime."""
        return self._evicted_count

    def ingest(
        self,
        event: ComputationalEvent,
        target_operations: Sequence[str] = (),
    ) -> IngestionResult:
        """Ingest a single event into the watermark buffer.

        Classifies event arrival, updates watermark, stores event, and identifies
        retained target events whose observation windows overlap this arrival.
        """
        eid = event.event_id
        if eid in self._events:
            return IngestionResult(
                event_id=eid,
                status=IngestionStatus.DUPLICATE,
                watermark=self.watermark,
                affected_target_ids=(),
                is_late=False,
            )

        t_start = event.time.start if event.time else None

        # Check retention horizon if watermark is active
        wm = self.watermark
        horizon = self.retention_horizon
        if t_start is not None and horizon is not None and t_start < horizon:
            return IngestionResult(
                event_id=eid,
                status=IngestionStatus.DROPPED_TOO_OLD,
                watermark=wm,
                affected_target_ids=(),
                is_late=True,
            )

        # Classify LATE vs ACCEPTED
        is_late = bool(wm is not None and t_start is not None and t_start < wm)
        status = IngestionStatus.LATE if is_late else IngestionStatus.ACCEPTED

        # Retain event
        self._events[eid] = event

        # Advance max event time and watermark if forward progress
        if t_start is not None:
            if self._max_event_time is None or t_start > self._max_event_time:
                self._max_event_time = t_start

        # Identify affected target operations for recomputation
        target_ops = set(target_operations)
        affected: list[str] = []

        if target_ops and t_start is not None:
            for existing_id, existing_evt in self._events.items():
                if existing_evt.operation in target_ops:
                    t_exist = existing_evt.time.start if existing_evt.time else None
                    if t_exist is not None:
                        # Check window overlap: abs(t_exist - t_start) <= max_window
                        if (
                            abs((t_exist - t_start).total_seconds())
                            <= self.max_window.total_seconds()
                        ):
                            affected.append(existing_id)

            # If the newly ingested event is itself a target operation
            if event.operation in target_ops and eid not in affected:
                affected.append(eid)

        return IngestionResult(
            event_id=eid,
            status=status,
            watermark=self.watermark,
            affected_target_ids=tuple(dict.fromkeys(affected)),
            is_late=is_late,
        )

    def evict_expired(self) -> int:
        """Evict events strictly older than the retention horizon.

        Returns the number of evicted events.
        """
        horizon = self.retention_horizon
        if horizon is None:
            return 0

        to_remove = [
            eid
            for eid, ev in self._events.items()
            if ev.time and ev.time.start and ev.time.start < horizon
        ]
        for eid in to_remove:
            del self._events[eid]

        count = len(to_remove)
        self._evicted_count += count
        return count
