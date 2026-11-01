"""
OBS-010 - Production Traffic Sampling.

Strategies:
    - HeadSampler: simple probability sampling on session start
    - FailureAlwaysSampler: capture all failures, sample successes at rate
    - ReservoirSampler: ensure rare events are kept (algorithm R)
    - TailSampler: keep entire trace if final latency exceeded threshold
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus


@dataclass
class SamplingDecision:
    keep: bool
    reason: str = ""


class HeadSampler:
    """Probabilistic head-based sampler."""

    def __init__(self, rate: float = 0.1):
        self.rate = max(0.0, min(1.0, rate))

    def should_sample(self, event: AgentEvent) -> SamplingDecision:
        if event.event_type == EventType.SESSION_START:
            keep = random.random() < self.rate  # noqa: S311  # nosec B311 — trace sampling, not crypto
            return SamplingDecision(keep=keep, reason=f"head_rate={self.rate}")
        return SamplingDecision(keep=True, reason="non-start_passthrough")


class FailureAlwaysSampler:
    """Always keep failures. Sample successes at `success_rate`."""

    def __init__(self, success_rate: float = 0.05):
        self.success_rate = max(0.0, min(1.0, success_rate))
        # Track session decisions so trailing events of a sampled session stay
        self._session_decisions: dict[str, bool] = {}

    def should_sample(self, event: AgentEvent) -> SamplingDecision:
        sid = event.session_id
        if event.event_type == EventType.SESSION_END:
            failed = event.status in (
                ExecutionStatus.FAILURE,
                ExecutionStatus.BLOCKED,
                ExecutionStatus.TIMEOUT,
            )
            if failed:
                self._session_decisions[sid] = True
                return SamplingDecision(keep=True, reason="failure")
            keep = random.random() < self.success_rate  # noqa: S311  # nosec B311 — trace sampling, not crypto
            self._session_decisions[sid] = keep
            return SamplingDecision(keep=keep, reason=f"success_rate={self.success_rate}")

        # Mid-session: defer to a previous decision, else keep
        prior = self._session_decisions.get(sid)
        if prior is None:
            return SamplingDecision(keep=True, reason="undetermined_keep")
        return SamplingDecision(keep=prior, reason="session_decision")


class ReservoirSampler:
    """
    Algorithm R reservoir sampling - guarantees rare events stay in the buffer
    even under unbounded stream length. Size k.
    """

    def __init__(self, k: int = 1000):
        self.k = k
        self._reservoir: list[AgentEvent] = []
        self._seen = 0

    def add(self, event: AgentEvent) -> bool:
        """Return True if the event was kept in the reservoir."""
        self._seen += 1
        if len(self._reservoir) < self.k:
            self._reservoir.append(event)
            return True
        j = random.randint(0, self._seen - 1)  # noqa: S311  # nosec B311 — reservoir sampling, not crypto
        if j < self.k:
            self._reservoir[j] = event
            return True
        return False

    def sample(self) -> list[AgentEvent]:
        return list(self._reservoir)

    def __len__(self) -> int:
        return len(self._reservoir)


class TailSampler:
    """Keep entire trace if it exceeds a latency threshold (ms)."""

    def __init__(self, latency_threshold_ms: float = 5000.0, buffer_size: int = 2000):
        self.threshold = latency_threshold_ms
        self._buffers: dict[str, deque] = {}
        self.buffer_size = buffer_size

    def push(self, event: AgentEvent) -> None:
        buf = self._buffers.setdefault(event.session_id, deque(maxlen=self.buffer_size))
        buf.append(event)

    def evaluate(self, session_id: str, total_latency_ms: float) -> SamplingDecision:
        if total_latency_ms >= self.threshold:
            return SamplingDecision(keep=True, reason=f"slow:{total_latency_ms:.0f}ms")
        # Drop the buffer
        self._buffers.pop(session_id, None)
        return SamplingDecision(keep=False, reason="fast_path")

    def flush(self, session_id: str) -> list[AgentEvent]:
        return list(self._buffers.pop(session_id, []))


__all__ = [
    "SamplingDecision",
    "HeadSampler",
    "FailureAlwaysSampler",
    "ReservoirSampler",
    "TailSampler",
]
