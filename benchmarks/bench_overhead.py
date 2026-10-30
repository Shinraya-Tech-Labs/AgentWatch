import json
import timeit

from panoptis.core.safety import SafetyEngine
from panoptis.core.watcher import GenericAdapter, watch


# Simple dummy agent function for baseline
def dummy_agent_sync():
    # Simulate minimal work with a tiny CPU loop to avoid zero‑time measurements
    total = 0
    for i in range(100):
        total += i
    return "ok"


# Instrumented call without safety engine
def dummy_agent_watch_no_safety():
    watched = watch(dummy_agent_sync)  # watch without safety
    return watched()


# Instrumented call with safety engine
safety_engine = SafetyEngine()


def dummy_agent_watch_with_safety():
    # Use GenericAdapter directly because watch() does not expose a safety_engine argument.
    adapter = GenericAdapter(dummy_agent_sync, safety_engine=safety_engine)
    watched = adapter.attach()
    return watched()


# Full API round‑trip (starts the FastAPI server and posts an event)
# For the benchmark we mock the server call to avoid external services.
def full_api_round_trip():
    # Simulate a full API round‑trip by invoking the async EventBus.publish
    # with a minimal, correctly‑typed AgentEvent. This exercises the async
    # dispatch path without starting the FastAPI server.
    import asyncio

    from panoptis.core.event_bus import EventBus
    from panoptis.core.schema import AgentEvent, EventType

    async def _publish():
        bus = EventBus()
        # Minimal event – only required fields are provided.
        event = AgentEvent(
            session_id="bench",
            agent_id="bench",
            event_type=EventType.TOOL_CALL,
        )
        await bus.publish(event)

    # Run the coroutine synchronously for the benchmark.
    asyncio.run(_publish())


def run_benchmarks():
    # Number of individual calls to time per batch. Using a larger repeat count
    # yields many per‑call samples so p95/p99 are meaningful.
    # Number of individual benchmark samples (per‑call timings)
    # Using a larger repeat count gives a robust distribution for p95/p99.
    sample_count = 1000
    results = {}
    for name, func in [
        ("baseline", dummy_agent_sync),
        ("watch_no_safety", dummy_agent_watch_no_safety),
        ("watch_with_safety", dummy_agent_watch_with_safety),
        ("full_api", full_api_round_trip),
    ]:
        # Time the function `sample_count` times, recording each individual call duration
        timer = timeit.Timer(func)
        raw = [timer.timeit(number=1) for _ in range(sample_count)]
        # compute statistics over the per‑call samples
        mean = sum(raw) / len(raw)
        p95 = sorted(raw)[int(0.95 * len(raw))]
        p99 = sorted(raw)[int(0.99 * len(raw))]
        results[name] = {
            "mean_ms": round(mean * 1000, 3),
            "p95_ms": round(p95 * 1000, 3),
            "p99_ms": round(p99 * 1000, 3),
        }
    # calculate overhead percentages vs baseline
    base = results["baseline"]["mean_ms"]
    for name, data in results.items():
        if name != "baseline":
            data["overhead_%"] = round(((data["mean_ms"] - base) / base) * 100, 2)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run_benchmarks()
