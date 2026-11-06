#!/usr/bin/env python3
"""Measure the persistence path under load, in a real process against a real database.

    python -m benchmarks.persistence_load --url sqlite+aiosqlite:///bench.db
    python -m benchmarks.persistence_load --url postgresql+asyncpg://user:pw@host/db

Reports concurrent write throughput and latency, what the heartbeat costs an idle process, and
how long a restart takes with the rows already stored. Writes go through the same function the
API uses. Nothing here is compared with an earlier version.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import time
import uuid
from pathlib import Path

import psutil

from panoptis.api import server
from panoptis.core.models import init_db
from panoptis.core.schema import AgentEvent, EventType

MIB = 1024 * 1024


def _event(session_id: str, step: int) -> AgentEvent:
    return AgentEvent(
        session_id=session_id, agent_id="bench", event_type=EventType.TOOL_CALL, step_number=step
    )


async def _timed(event: AgentEvent, timings: list[float]) -> None:
    started = time.perf_counter()
    await server._pg_write_event(event)
    timings.append(time.perf_counter() - started)


async def _load(concurrency: int, total: int) -> dict[str, float]:
    """One session per batch, so every batch is also a race for a new session row."""
    timings: list[float] = []
    lost_before = server._persistence["events_not_stored"]
    process = psutil.Process()
    cpu_before = process.cpu_times()
    started = time.perf_counter()
    for batch in range(0, total, concurrency):
        session_id = f"bench-{uuid.uuid4().hex[:12]}"
        count = min(concurrency, total - batch)
        await asyncio.gather(*[_timed(_event(session_id, i), timings) for i in range(count)])
    elapsed = time.perf_counter() - started
    cpu = process.cpu_times()
    timings.sort()
    return {
        "concurrency": concurrency,
        "events": total,
        "seconds": round(elapsed, 2),
        "events_per_second": round(total / elapsed, 1),
        "p50_ms": round(statistics.median(timings) * 1000, 2),
        "p99_ms": round(timings[int(len(timings) * 0.99)] * 1000, 2),
        "max_ms": round(timings[-1] * 1000, 2),
        "cpu_seconds": round(cpu.user + cpu.system - cpu_before.user - cpu_before.system, 2),
        "rss_mib": round(process.memory_info().rss / MIB, 1),
        "not_stored": server._persistence["events_not_stored"] - lost_before,
    }


async def _idle_cost(seconds: float) -> dict[str, float]:
    """CPU of a sleeping process with and without the heartbeat."""
    process = psutil.Process()

    def cpu() -> float:
        times = process.cpu_times()
        return times.user + times.system

    before = cpu()
    await asyncio.sleep(seconds)
    without = cpu() - before
    heartbeat = asyncio.create_task(server._loop_heartbeat())
    before = cpu()
    await asyncio.sleep(seconds)
    with_beat = cpu() - before
    heartbeat.cancel()
    return {
        "seconds": seconds,
        "cpu_seconds_without_heartbeat": round(without, 3),
        "cpu_seconds_with_heartbeat": round(with_beat, 3),
        "percent_of_one_core": round((with_beat - without) / seconds * 100, 3),
    }


async def main_async(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    server._db_session_factory = await init_db(args.url)
    first_start = time.perf_counter() - started
    results: dict = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(),
        "backend": args.url.split(":", 1)[0],
        "first_start_seconds": round(first_start, 3),
        "load": [],
    }
    for concurrency in args.concurrency:
        row = await _load(concurrency, args.events)
        results["load"].append(row)
        print(json.dumps(row), flush=True)
    engine = server._db_session_factory.kw["bind"]
    await engine.dispose()
    started = time.perf_counter()
    server._db_session_factory = await init_db(args.url)
    results["restart_seconds_with_rows"] = round(time.perf_counter() - started, 3)
    results["idle"] = await _idle_cost(args.idle_seconds)
    await server._db_session_factory.kw["bind"].dispose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default=os.getenv("DATABASE_URL"), required=not os.getenv("DATABASE_URL")
    )
    parser.add_argument("--events", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, nargs="*", default=[1, 5, 20, 50])
    parser.add_argument("--idle-seconds", type=float, default=10.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    results = asyncio.run(main_async(args))
    print(json.dumps({k: v for k, v in results.items() if k != "load"}))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
