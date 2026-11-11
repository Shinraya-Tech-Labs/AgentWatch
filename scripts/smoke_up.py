"""Smoke-test an installed PANOPTIS: `panoptis up`, then a real round trip through it.

    python -I scripts/smoke_up.py [--expect-dashboard]

Run it with the interpreter of a clean install, from outside the repository, so it tests
the installed package. It starts `panoptis up` in a throwaway home and checks:

1. /health answers and (with --expect-dashboard) the dashboard is served at /;
2. the dashboard's local sign-in works for a loopback request;
3. an allowed call runs and a blocked call never runs;
4. a call held for approval waits, is approved through the API, and runs exactly once;
5. the decisions are visible through the API the dashboard uses;
6. the evidence verifies, through the API and through `panoptis protect evidence verify`;
7. a fresh install starts empty, and `panoptis demo --no-serve` / `--clear` only touch the
   separate demo workspace.

Exit status 0 when every check passed. No network beyond 127.0.0.1, no keys.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, claim: str) -> None:
    RESULTS.append((bool(ok), claim))
    print(f"{'PASS' if ok else 'FAIL'}  {claim}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def panoptis_exe() -> list[str]:
    exe = shutil.which("panoptis", path=str(Path(sys.executable).parent))
    return [exe] if exe else [sys.executable, "-m", "panoptis"]


def main() -> int:
    expect_dashboard = "--expect-dashboard" in sys.argv
    home = Path(tempfile.mkdtemp(prefix="panoptis-smoke-"))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AGENTWATCH_", "PANOPTIS_")) and k not in ("DATABASE_URL",)
    }
    env.update(AGENTWATCH_HOME=str(home), PYTHONIOENCODING="utf-8")
    os.environ.clear()
    os.environ.update(env)  # this process's agents use the same home and store
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(home / "server.log", "w", encoding="utf-8")  # noqa: SIM115 - closed in finally
    server = subprocess.Popen(  # noqa: S603 - fixed argv
        [*panoptis_exe(), "up", "--no-browser", "--port", str(port)],
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
                break
            time.sleep(0.5)
        health = httpx.get(f"{base}/health", timeout=5)
        check(health.status_code == 200, "panoptis up starts and /health answers 200")

        page = httpx.get(f"{base}/", timeout=10)
        has_ui = page.status_code == 200 and "PANOPTIS" in page.text
        if expect_dashboard:
            check(has_ui, "the dashboard is served at /")
        session = httpx.get(f"{base}/api/local/session", timeout=5)
        check(session.status_code == 200, "local sign-in answers a loopback request")
        profile = json.loads((home / "local.json").read_text(encoding="utf-8"))
        key = {"X-Api-Key": profile["operator_key"]}
        check(
            httpx.get(f"{base}/api/v1/protect/status", timeout=5).status_code == 401,
            "the API still requires a key",
        )
        first = httpx.get(f"{base}/api/v1/protect/status", headers=key, timeout=10).json()
        check(
            session.json().get("workspace") == "local" and first["recent_decisions"] == 0,
            "a fresh install starts empty (no sample data in the real workspace)",
        )

        from panoptis.protect import (
            ActionBlocked,
            ApprovalBroker,
            ProtectionConfig,
            ProtectionPipeline,
            ProtectLedger,
            protect_tool,
        )

        ledger = ProtectLedger(None)
        pipeline = ProtectionPipeline(
            ledger=ledger,
            approvals=ApprovalBroker(ledger, timeout_s=60, poll_s=0.1),
            config=ProtectionConfig(approval_timeout_s=60),
        )
        ran: list[str] = []

        def shell(command: str) -> str:
            ran.append(command)
            return "ok"

        tool = protect_tool(
            shell, tool="bash", pipeline=pipeline, agent_id="smoke", session_id="smoke-1"
        )
        check(tool(command="ls -la") == "ok" and ran == ["ls -la"], "an allowed call runs")
        try:
            tool(command="rm -rf /")
            check(False, "a blocked call is refused")
        except ActionBlocked:
            check(ran == ["ls -la"], "a blocked call is refused and never runs")

        held = "git push " + "--force origin main"
        out: dict[str, Any] = {}
        worker = threading.Thread(target=lambda: out.setdefault("r", tool(command=held)))
        worker.start()
        pending: list[dict[str, Any]] = []
        for _ in range(100):
            pending = httpx.get(
                f"{base}/api/v1/protect/approvals",
                params={"state": "pending"},
                headers=key,
                timeout=5,
            ).json()["approvals"]
            if pending:
                break
            time.sleep(0.2)
        check(bool(pending) and held not in ran, "a risky call is held and has not run")
        if pending:
            req = pending[0]
            r = httpx.post(
                f"{base}/api/v1/protect/approvals/{req['request_id']}",
                json={
                    "approved": True,
                    "action_digest": req["action_digest"],
                    "decided_by": "smoke",
                },
                headers=key,
                timeout=10,
            )
            check(r.status_code == 200, "the approval is accepted through the API")
        worker.join(30)
        check(ran.count(held) == 1, "the approved call ran exactly once")

        decisions = httpx.get(
            f"{base}/api/v1/protect/decisions", params={"limit": 20}, headers=key, timeout=10
        ).json()["decisions"]
        verdicts = {d["verdict"] for d in decisions if d["agent_id"] == "smoke"}
        check({"allow", "block"} <= verdicts, "the decisions are visible through the API")

        rep = httpx.get(
            f"{base}/api/v1/protect/evidence/verify",
            params={"seal": "true"},
            headers=key,
            timeout=30,
        ).json()
        check(
            bool(rep.get("ok")) and rep.get("observations_checked", 0) > 0,
            "evidence verifies (API)",
        )
        ledger.store.close()
        cli = subprocess.run(  # noqa: S603 - fixed argv
            [*panoptis_exe(), "protect", "evidence", "verify"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        check(cli.returncode == 0, "evidence verifies (panoptis protect evidence verify)")

        before = httpx.get(f"{base}/api/v1/protect/status", headers=key, timeout=10).json()
        seeded = subprocess.run(  # noqa: S603 - fixed argv
            [*panoptis_exe(), "demo", "--no-serve"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        after = httpx.get(f"{base}/api/v1/protect/status", headers=key, timeout=10).json()
        check(
            seeded.returncode == 0
            and (home / "demo" / "agentwatch.db").exists()
            and after["recent_decisions"] == before["recent_decisions"],
            "panoptis demo seeds a separate demo workspace, not the real one",
        )
        cleared = subprocess.run(  # noqa: S603 - fixed argv
            [*panoptis_exe(), "demo", "--clear"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        check(
            cleared.returncode == 0
            and not (home / "demo").exists()
            and (home / "local.json").exists(),
            "panoptis demo --clear removes the demo workspace and nothing else",
        )
    finally:
        server.terminate()
        try:
            server.wait(20)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
        if not all(ok for ok, _ in RESULTS):
            print((home / "server.log").read_text(encoding="utf-8", errors="replace")[-4000:])
        shutil.rmtree(home, ignore_errors=True)
    failed = [c for ok, c in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    raise SystemExit(main())
