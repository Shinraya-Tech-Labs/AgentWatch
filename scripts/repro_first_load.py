"""Reproduce the dashboard's first load against a brand-new store.

    python scripts/repro_first_load.py [--rounds 5]

For each round: a fresh, empty PANOPTIS home; `panoptis up`; then, as soon as /health
answers, the three requests the home screen sends at once (status, decisions, approvals),
several times in parallel. Any non-200 answer, or a database error in the server log,
fails the round. Exit status 0 only when every round is clean.

This is how a first `panoptis up` failed on Windows + Python 3.14 with "table
aw3_observations already exists" and "database is locked".
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ERRORS = re.compile(r"already exists|database is locked|OperationalError|Traceback", re.I)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def panoptis() -> list[str]:
    exe = shutil.which("panoptis", path=str(Path(sys.executable).parent))
    return [exe] if exe else [sys.executable, "-m", "panoptis"]


def one_round(n: int, parallel: int) -> list[str]:
    home = Path(tempfile.mkdtemp(prefix="panoptis-first-load-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTWATCH_", "PANOPTIS_"))}
    env.update(AGENTWATCH_HOME=str(home), PYTHONIOENCODING="utf-8")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = home.parent / f"{home.name}.log"
    problems: list[str] = []
    with open(log_path, "w", encoding="utf-8") as log:
        server = subprocess.Popen(  # noqa: S603 - fixed argv
            [*panoptis(), "up", "--no-browser", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(240):
                try:
                    if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if server.poll() is not None:
                    problems.append(f"round {n}: server exited with {server.returncode}")
                    break
                time.sleep(0.25)
            key = {
                "X-Api-Key": json.loads((home / "local.json").read_text("utf-8"))["operator_key"]
            }
            paths = [
                "/api/v1/protect/status",
                "/api/v1/protect/decisions?limit=25",
                "/api/v1/protect/approvals?state=pending",
            ] * parallel
            with httpx.Client(timeout=60) as client, cf.ThreadPoolExecutor(len(paths)) as pool:
                for path, r in zip(
                    paths, pool.map(lambda p: client.get(base + p, headers=key), paths)
                ):
                    if r.status_code != 200:
                        problems.append(f"round {n}: {path} -> {r.status_code} {r.text[:200]}")
        finally:
            server.terminate()
            try:
                server.wait(20)
            except subprocess.TimeoutExpired:
                server.kill()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if ERRORS.search(line):
            problems.append(f"round {n}: server log: {line.strip()[:240]}")
    shutil.rmtree(home, ignore_errors=True)
    log_path.unlink(missing_ok=True)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--parallel", type=int, default=3, help="copies of the 3 home-screen calls")
    ns = ap.parse_args()
    failed = 0
    for n in range(1, ns.rounds + 1):
        problems = one_round(n, ns.parallel)
        print(f"round {n}: {'FAIL' if problems else 'ok'}", flush=True)
        for p in problems[:12]:
            print("   ", p, flush=True)
        failed += bool(problems)
    print(f"{ns.rounds - failed}/{ns.rounds} rounds clean")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
