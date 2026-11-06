#!/usr/bin/env python3
"""Measure what the trajectory limits really cost in a running process.

Each scenario runs in a fresh process and reports operating-system memory, not just Python
allocations: resident memory (RSS) and, where the platform has it, private bytes. Run::

    python -m benchmarks.trajectory_memory --out benchmarks/results/trajectory_memory

The largest scenarios fill every tenant, and take several minutes and over 2.5 GiB of memory.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from panoptis.protect import ToolAction
from panoptis.protect.ltl_trajectory import LTLTrajectoryVerifier

MIB = 1024 * 1024
NET = "s = socket.socket()\n"
CODE = "import subprocess\nsubprocess.run(['pytest'])\n"
INERT = "print('hello')\n"


def _action(tool: str, tenant: str, session: str, **arguments: Any) -> ToolAction:
    return ToolAction(
        tool=tool, arguments=arguments, session_id=session, tenant_id=tenant, agent_id="agent"
    )


def _memory() -> dict[str, float]:
    info = psutil.Process().memory_info()
    return {
        "rss_mib": info.rss / MIB,
        "private_mib": getattr(info, "private", getattr(info, "uss", 0)) / MIB,
    }


def _coding_session(ltl: LTLTrajectoryVerifier, tenant: str, session: str, steps: int) -> None:
    """A typical agent session: reads an issue, edits files, runs commands."""
    ltl.record_step(_action("get_issue", tenant, session, number=1))
    for step in range(steps):
        path = f"src/module_{step % 8}.py"
        content = CODE if step % 4 == 0 else INERT
        action = _action("write_file", tenant, session, path=path, content=content)
        ltl.verify_action(action)
        ltl.record_step(action)
        command = _action("bash", tenant, session, command="pytest -q")
        ltl.verify_action(command)
        ltl.record_step(command)


def baseline() -> LTLTrajectoryVerifier:
    return LTLTrajectoryVerifier()


def typical() -> LTLTrajectoryVerifier:
    """16 tenants at their fact limit with ordinary coding sessions."""
    ltl = LTLTrajectoryVerifier()
    for tenant in range(16):
        for session in range(ltl.max_facts_per_tenant):
            _coding_session(ltl, f"tenant-{tenant}", f"session-{session:08d}", steps=2)
    return ltl


def one_tenant_full() -> LTLTrajectoryVerifier:
    """One tenant with its facts, paths and filter all in use, worst-case path lengths."""
    ltl = LTLTrajectoryVerifier()
    _fill_tenant(ltl, "tenant-0")
    return ltl


def _fill_tenant(ltl: LTLTrajectoryVerifier, tenant: str) -> None:
    per_session = max(1, ltl.max_tracked_files // ltl.max_facts_per_tenant)
    for session in range(ltl.max_facts_per_tenant + 1):  # one past the table, so one is sealed
        sid = f"{session:032d}"
        ltl.record_step(_action("read_file", tenant, sid, path=".env"))
        ltl.record_step(_action("fetch_url", tenant, sid, url="https://example.test"))
        for file in range(per_session):
            path = f"/{session:06d}/{file:02d}/" + "p" * 240
            ltl.record_step(_action("write_file", tenant, sid, path=path, content=NET + CODE))


def all_tenants_full() -> LTLTrajectoryVerifier:
    """Every budget of every tenant in use: the worst case for security state."""
    ltl = LTLTrajectoryVerifier()
    for tenant in range(ltl.max_tenants):
        _fill_tenant(ltl, f"tenant-{tenant:04d}")
    return ltl


def history_full_of_largest_steps(
    ltl: LTLTrajectoryVerifier | None = None,
) -> LTLTrajectoryVerifier:
    """The step-history limit full of the largest steps it can keep, every string unique."""
    ltl = ltl or LTLTrajectoryVerifier()
    for step in range(ltl.max_history_steps):
        arguments = {
            f"{step:07d}{i:02d}" + "k" * 55: f"{step:07d}{i:02d}" + "v" * 247 for i in range(32)
        }
        ltl.record_step(_action("noop", "tenant-0", f"s{step // 1000}", **arguments))
    return ltl


def everything_full() -> LTLTrajectoryVerifier:
    """The measured worst case: every tenant's budgets and the history limit, all at once."""
    ltl = all_tenants_full()
    limit, ltl.max_sessions = ltl.max_sessions, 1  # small steps would hold part of the limit
    ltl.record_step(_action("noop", "tenant-0", "clear"))
    ltl.max_sessions = limit
    return history_full_of_largest_steps(ltl)


def latency_at_capacity() -> LTLTrajectoryVerifier:
    """Throughput and latency with a tenant's table full, so every new session seals one."""
    ltl = LTLTrajectoryVerifier()
    for session in range(ltl.max_facts_per_tenant):
        ltl.record_step(_action("read_file", "t", f"warm-{session}", path=".env"))
    timings: list[float] = []
    started = time.perf_counter()
    rounds = 50_000
    for i in range(rounds):
        step = _action("write_file", "t", f"new-{i}", path="a.py", content=CODE)
        before = time.perf_counter()
        ltl.verify_action(step)
        ltl.record_step(step)
        timings.append(time.perf_counter() - before)
    elapsed = time.perf_counter() - started
    timings.sort()
    _extra["steps_per_second"] = rounds / elapsed
    _extra["p50_us"] = timings[len(timings) // 2] * 1e6
    _extra["p99_us"] = timings[int(len(timings) * 0.99)] * 1e6
    _extra["max_us"] = timings[-1] * 1e6
    _extra["sealed_sessions"] = ltl.capacity("t")["sealed_sessions"]
    return ltl


_extra: dict[str, Any] = {}
SCENARIOS: dict[str, Callable[[], LTLTrajectoryVerifier]] = {
    "baseline": baseline,
    "one_tenant_full": one_tenant_full,
    "typical_16_tenants": typical,
    "history_full_of_largest_steps": history_full_of_largest_steps,
    "latency_at_capacity": latency_at_capacity,
    "all_tenants_full": all_tenants_full,
    "everything_full": everything_full,
}


def _child(name: str) -> None:
    before = _memory()
    started = time.perf_counter()
    ltl = SCENARIOS[name]()
    elapsed = time.perf_counter() - started
    after = _memory()
    tenants = list(ltl._tenants.values())
    result = {
        "seconds": elapsed,
        "before": before,
        "after": after,
        "grew_rss_mib": after["rss_mib"] - before["rss_mib"],
        "grew_private_mib": after["private_mib"] - before["private_mib"],
        "tenants": len(tenants),
        "sessions_with_facts": sum(len(t.facts) for t in tenants),
        "tracked_files": sum(t.tracked_files for t in tenants),
        "sealed_sessions": sum(t.sealed.count for t in tenants if t.sealed),
        "history_steps": ltl._history_steps,
        "history_sessions": ltl._history_sessions,
        **_extra,
    }
    print(json.dumps(result))


def _report(results: dict[str, Any]) -> str:
    lines = [
        "# Trajectory memory",
        "",
        "Generated by `python -m benchmarks.trajectory_memory`. Each scenario ran in a fresh "
        "process at the default limits. Sizes are operating-system figures.",
        "",
        f"- {results['platform']}; Python {results['python']}; {results['logical_cpus']} logical CPUs",
        "",
        "| Scenario | Tenants | Sessions with facts | Tracked files | Sealed | History steps "
        "| RSS added | Private added | Seconds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in results["scenarios"].items():
        lines.append(
            f"| {name} | {r['tenants']:,} | {r['sessions_with_facts']:,} | {r['tracked_files']:,} "
            f"| {r['sealed_sessions']:,} | {r['history_steps']:,} | {r['grew_rss_mib']:,.1f} MiB "
            f"| {r['grew_private_mib']:,.1f} MiB | {r['seconds']:,.1f} |"
        )
    speed = results["scenarios"].get("latency_at_capacity")
    if speed:
        lines += [
            "",
            "With a tenant's table full, so that every new session seals an older one, one "
            f"verify and record took {speed['p50_us']:.0f} µs at the median and "
            f"{speed['p99_us']:.0f} µs at p99, at {speed['steps_per_second']:,.0f} steps per "
            "second on one thread.",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("benchmarks/results/trajectory_memory"))
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--only", nargs="*", choices=sorted(SCENARIOS))
    args = parser.parse_args()
    if args.scenario:
        _child(args.scenario)
        return 0
    results: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(),
        "scenarios": {},
    }
    saved = args.out / "results.json"
    if args.only and saved.exists():
        results["scenarios"] = json.loads(saved.read_text(encoding="utf-8"))["scenarios"]
    for name in args.only or SCENARIOS:
        run = subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, "-m", "benchmarks.trajectory_memory", "--scenario", name],
            capture_output=True,
            text=True,
            check=True,
        )
        results["scenarios"][name] = json.loads(run.stdout.strip().splitlines()[-1])
        print(name, json.dumps(results["scenarios"][name])[:200], flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(_report(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
