"""Panopti bench: measure the hot-path overhead of the hypervisor checks.

Times three things an agent action passes through, using only the standard library:
a destructive-pattern regex check, a circuit breaker ``observe()`` on a healthy event,
and a Merkle witness ``record()``. Every call is timed on its own with
``time.perf_counter_ns`` so the report can show p50, p95 and p99 latency in microseconds.
"""

from __future__ import annotations

import re
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_ITERATIONS = 10_000
_WARMUP_CALLS = 50

# A healthy statement: the steady-state case is a check that does not match.
_HEALTHY_TEXT = "SELECT id, name FROM users WHERE id = 42"


@dataclass(frozen=True)
class OpResult:
    """Timing summary for one benchmarked operation."""

    name: str
    iterations: int
    total_ms: float
    ops_per_sec: float
    p50_us: float
    p95_us: float
    p99_us: float


def percentiles(samples_ns: list[int]) -> tuple[float, float, float]:
    """Return (p50, p95, p99) of nanosecond samples, in microseconds."""
    if len(samples_ns) == 1:
        only = samples_ns[0] / 1000
        return only, only, only
    cuts = statistics.quantiles(samples_ns, n=100, method="inclusive")
    return cuts[49] / 1000, cuts[94] / 1000, cuts[98] / 1000


def time_operation(name: str, operation: Callable[[], object], iterations: int) -> OpResult:
    """Call ``operation`` ``iterations`` times, timing each call."""
    for _ in range(min(iterations, _WARMUP_CALLS)):
        operation()

    samples: list[int] = []
    clock = time.perf_counter_ns
    for _ in range(iterations):
        start = clock()
        operation()
        samples.append(clock() - start)

    total_ns = sum(samples)
    p50, p95, p99 = percentiles(samples)
    return OpResult(
        name=name,
        iterations=iterations,
        total_ms=total_ns / 1_000_000,
        ops_per_sec=iterations / (total_ns / 1_000_000_000) if total_ns else float("inf"),
        p50_us=p50,
        p95_us=p95,
        p99_us=p99,
    )


def run_bench(iterations: int = DEFAULT_ITERATIONS) -> list[OpResult]:
    """Benchmark the three hot-path operations and return one result each."""
    if iterations < 1:
        raise ValueError(f"iterations must be at least 1, got {iterations}")

    from panoptis.circuit_breaker import CircuitBreaker, CircuitThresholds
    from panoptis.core.schema import AgentEvent, EventType, ExecutionStatus
    from panopti.court import WitnessChain
    from panopti.presets.profiles import get_profile

    patterns = [re.compile(p) for p in get_profile("devops").destructive_patterns]

    def invariant_match() -> bool:
        return any(p.search(_HEALTHY_TEXT) for p in patterns)

    # Thresholds are far above anything a run here reaches, so the breaker stays CLOSED.
    breaker = CircuitBreaker(
        "panopti-bench",
        CircuitThresholds(max_consecutive_errors=iterations + _WARMUP_CALLS + 1),
    )
    healthy_event = AgentEvent(
        session_id="panopti-bench",
        agent_id="bench-agent",
        event_type=EventType.TOOL_RESULT,
        status=ExecutionStatus.SUCCESS,
    )

    chain = WitnessChain("panopti-bench")
    payload = {"query": _HEALTHY_TEXT}

    return [
        time_operation("Invariant regex match", invariant_match, iterations),
        time_operation(
            "Circuit breaker observe", lambda: breaker.observe(healthy_event), iterations
        ),
        time_operation(
            "Merkle witness record",
            lambda: chain.record("bench-agent", "tool_call", payload),
            iterations,
        ),
    ]


def format_bench_report(results: list[OpResult], iterations: int) -> str:
    """Render results as a plain-text table."""
    name_width = max(len(r.name) for r in results)
    header = (
        f"{'Operation':<{name_width}}  {'Total (ms)':>10}  {'Ops/sec':>12}  "
        f"{'p50 (µs)':>9}  {'p95 (µs)':>9}  {'p99 (µs)':>9}"
    )
    lines = [f"PANOPTI BENCH: {iterations:,} iterations per operation", header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.name:<{name_width}}  {r.total_ms:>10.2f}  {r.ops_per_sec:>12,.0f}  "
            f"{r.p50_us:>9.2f}  {r.p95_us:>9.2f}  {r.p99_us:>9.2f}"
        )
    return "\n".join(lines)
