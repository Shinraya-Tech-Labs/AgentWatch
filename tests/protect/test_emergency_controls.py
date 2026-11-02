"""TO-025: the kill switch and suspensions across the supported runtime topology.

Paths: protect_tool, enforce_call (sync/async), watch() with and without protection,
the MCP gateway; another process sharing the host (kill file) and another process
sharing only the store (ledger); restart; concurrency; in-flight calls, pending
approvals and streams; tenant scoping; the API's authorization and the CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from panoptis.core.watcher import AgentWatchBlockedError, watch
from panoptis.protect import (
    ActionBlocked,
    ApprovalBroker,
    ControlState,
    ProtectionConfig,
    ProtectionPipeline,
    ProtectLedger,
    ToolAction,
    protect_tool,
)
from panoptis.protect.enforce import aenforce_call, enforce_call


def act(
    agent: str = "coder",
    session: str = "s1",
    tenant: str = "default",
    tool: str = "read_file",
    **args: Any,
) -> ToolAction:
    return ToolAction(
        agent_id=agent,
        session_id=session,
        tenant_id=tenant,
        tool=tool,
        arguments=args or {"path": "a.txt"},
    )


def store_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"


# ── every execution path ────────────────────────────────────────────────────────────


def test_kill_switch_blocks_every_execution_path(ledger):
    p = ProtectionPipeline(ledger=ledger)
    runs: list[str] = []

    @protect_tool(pipeline=p, agent_id="coder", session_id="s1")
    def read_file(path: str) -> str:
        runs.append("protect_tool")
        return "x"

    class Agent:
        def run(self, command: str) -> str:
            runs.append(f"watch:{command}")
            return "ok"

    protected_agent = watch(Agent(), protection=p)
    legacy_agent = watch(Agent())

    async def tool(args):
        runs.append("async")
        return "x"

    p.control.trip("drill", actor="test")
    with pytest.raises(ActionBlocked):
        read_file("a.txt")
    with pytest.raises(ActionBlocked):
        enforce_call(p, act(), lambda a: runs.append("sync"))
    with pytest.raises(ActionBlocked):
        asyncio.run(aenforce_call(p, act(), tool))
    with pytest.raises(AgentWatchBlockedError):
        protected_agent.run("echo hi")
    with pytest.raises(AgentWatchBlockedError):  # the legacy gate shares the kill file
        legacy_agent.run("echo hi")
    assert runs == []
    p.control.reset("drill over", actor="test")
    assert read_file("a.txt") == "x" and legacy_agent.run("echo hi") == "ok"


async def test_kill_switch_blocks_the_mcp_gateway(ledger):
    from panoptis.protect.mcp_gateway import MCPGateway

    p = ProtectionPipeline(ledger=ledger)
    sent: list[dict] = []
    replies: list[dict] = []

    async def up(m):
        sent.append(m)

    async def cl(m):
        replies.append(m)

    gw = MCPGateway(p, send_client=cl, send_upstream=up)
    p.control.trip("drill")
    await gw.from_client(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "x"}},
        }
    )
    for _ in range(100):
        if replies:
            break
        await asyncio.sleep(0.02)
    assert sent == [] and replies[0]["result"]["isError"] is True
    assert "KILL_SWITCH" in replies[0]["result"]["_meta"]["agentwatch"]["findings"]


# ── across processes ────────────────────────────────────────────────────────────────

AGENT = textwrap.dedent(
    """
    import json, sys, time
    from panoptis.protect import ProtectionPipeline, ProtectLedger, ToolAction, ActionBlocked
    from panoptis.protect.control import ControlState
    from panoptis.protect.enforce import enforce_call
    store, marker, use_ledger = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
    ledger = ProtectLedger(store)
    p = ProtectionPipeline(ledger=ledger, control=ControlState(ledger if use_ledger else None, refresh_s=0.0))
    print("ready", flush=True)
    i = 0
    while True:
        line = sys.stdin.readline()
        if not line or line.strip() == "quit":
            break
        i += 1
        a = ToolAction(agent_id="coder", session_id="proc", tool="read_file", arguments={"path": str(i)})
        try:
            enforce_call(p, a, lambda args: open(marker, "a").write(args["path"] + "\\n"))
            print("ran", flush=True)
        except ActionBlocked as b:
            print("blocked " + b.decision.findings[0].code, flush=True)
    """
)


def _spawn(tmp_path: Path, store: str, use_ledger: bool, kill_file: Path) -> subprocess.Popen[str]:
    script = tmp_path / "agent.py"
    script.write_text(AGENT, encoding="utf-8")
    env = {**os.environ, "AGENTWATCH_KILL_FILE": str(kill_file), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(script),
            store,
            str(tmp_path / "marker.txt"),
            "1" if use_ledger else "0",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.stdout.readline().strip() == "ready"
    return proc


def _call(proc: subprocess.Popen[str]) -> str:
    proc.stdin.write("go\n")
    proc.stdin.flush()
    return proc.stdout.readline().strip()


def test_another_process_on_this_host_stops_and_resumes_through_the_kill_file(tmp_path):
    kill_file = tmp_path / "protect.kill"
    proc = _spawn(tmp_path, store_url(tmp_path), use_ledger=False, kill_file=kill_file)
    try:
        assert _call(proc) == "ran"
        operator = ControlState(None, kill_switch=None)
        operator.kill_switch.kill_file = kill_file
        operator.trip("incident", actor="ops")
        assert _call(proc) == "blocked KILL_SWITCH"
        operator.reset("resolved", actor="ops")  # removes the file: the other process resumes
        assert _call(proc) == "ran"
    finally:
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        proc.wait(timeout=30)
    assert (tmp_path / "marker.txt").read_text().split() == ["1", "3"]


def test_another_process_sharing_only_the_store_stops_through_the_ledger(tmp_path):
    store = store_url(tmp_path)
    proc = _spawn(tmp_path, store, use_ledger=True, kill_file=tmp_path / "agent-host.kill")
    try:
        assert _call(proc) == "ran"
        lg = ProtectLedger(store)
        operator = ControlState(lg)
        operator.kill_switch.kill_file = tmp_path / "operator-host.kill"  # a different host
        operator.trip("incident", actor="ops")
        assert _call(proc) == "blocked KILL_SWITCH"
        operator.suspend_agent("coder", "investigating", actor="ops")
        operator.reset("resolved", actor="ops")
        assert _call(proc) == "blocked AGENT_SUSPENDED"
        operator.resume_agent("coder", "done", actor="ops")
        assert _call(proc) == "ran"
        lg.store.close()
    finally:
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        proc.wait(timeout=30)


def test_state_survives_a_restart(tmp_path):
    store = store_url(tmp_path)
    lg = ProtectLedger(store)
    c = ControlState(lg)
    c.kill_switch.kill_file = tmp_path / "a.kill"
    c.trip("before restart", actor="ops")
    c.suspend_session("s9", "later", actor="ops", tenant_id="acme")
    lg.store.close()
    fresh = ProtectionPipeline(
        ledger=ProtectLedger(store), control=ControlState(ProtectLedger(store))
    )
    fresh.control.kill_switch.kill_file = tmp_path / "other.kill"
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(fresh, act(), lambda a: None)
    assert exc.value.decision.findings[0].code == "KILL_SWITCH"
    assert fresh.control.status(tenant_id="acme")["suspended_sessions"].keys() == {"s9"}


def test_an_unreadable_kill_file_location_fails_closed(tmp_path, monkeypatch):
    c = ControlState(None)

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(type(c.kill_switch.kill_file), "exists", boom)
    killed, reason = c.local_kill()
    assert killed and "not checkable" in reason


# ── concurrency, in-flight calls, approvals, streams ───────────────────────────────


def test_concurrent_callers_never_start_a_new_call_after_the_trip(ledger):
    """A call that begins after the trip never runs; one admitted just before may finish."""
    p = ProtectionPipeline(ledger=ledger)
    calls: list[tuple[float, bool]] = []  # (when the call began, whether the tool ran)
    lock = threading.Lock()
    tripped: list[float] = []

    def worker(n: int) -> None:
        for i in range(40):
            began = time.perf_counter()
            ran = []
            try:
                enforce_call(p, act(session=f"w{n}", path=f"{n}-{i}"), lambda a: ran.append(1))
            except ActionBlocked:
                pass
            with lock:
                calls.append((began, bool(ran)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    p.control.trip("race", actor="test")
    tripped.append(time.perf_counter())
    for t in threads:
        t.join()
    after = [ran for began, ran in calls if began > tripped[0]]
    assert after and not any(after)
    assert any(ran for began, ran in calls if began < tripped[0])


def test_in_flight_call_finishes_and_later_calls_are_blocked(ledger):
    p = ProtectionPipeline(ledger=ledger)
    started, release = threading.Event(), threading.Event()
    results: list[Any] = []

    def slow(args):
        started.set()
        release.wait(5)
        return "finished"

    t = threading.Thread(target=lambda: results.append(enforce_call(p, act(), slow)))
    t.start()
    started.wait(5)
    p.control.trip("mid-call", actor="test")
    release.set()
    t.join(5)
    assert results == ["finished"]  # documented: a running tool is not interrupted
    with pytest.raises(ActionBlocked):
        enforce_call(p, act(), lambda a: "no")


def test_trip_while_waiting_for_approval_cancels_the_request(ledger):
    broker = ApprovalBroker(ledger, timeout_s=30, poll_s=0.02)
    p = ProtectionPipeline(
        ledger=ledger, approvals=broker, config=ProtectionConfig(approval_timeout_s=30)
    )
    runs: list[int] = []
    errors: list[ActionBlocked] = []
    a = act(tool="bash", command="git push --force origin main")

    def agent():
        try:
            enforce_call(p, a, lambda x: runs.append(1))
        except ActionBlocked as b:
            errors.append(b)

    t = threading.Thread(target=agent)
    t.start()
    for _ in range(200):
        if broker.pending():
            break
        time.sleep(0.02)
    req = broker.pending()[0]
    p.control.trip("stop everything", actor="ops")
    t.join(10)
    assert runs == [] and errors
    assert broker.get(req["request_id"])["state"] == "cancelled"
    # a late approval cannot revive it
    out = broker.resolve(req["request_id"], True, "alice", tenant_id="default")
    assert out.approved is False and out.cancelled


def test_trip_after_approval_but_before_execution_still_blocks(ledger):
    tripper: list[ControlState] = []

    def approver(req):
        tripper[0].trip("approved, then stopped", actor="ops")
        return True

    p = ProtectionPipeline(
        ledger=ledger, approvals=ApprovalBroker(ledger, approver=approver, timeout_s=5)
    )
    tripper.append(p.control)
    runs: list[int] = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(
            p, act(tool="bash", command="git push --force origin main"), lambda x: runs.append(1)
        )
    assert runs == [] and any(f.code == "KILL_SWITCH" for f in exc.value.decision.findings)


def test_trip_ends_a_streamed_result(ledger):
    p = ProtectionPipeline(ledger=ledger)

    def stream(args):
        for i in range(10):
            if i == 3:
                p.control.trip("mid-stream", actor="test")
            yield f"chunk {i}"

    got: list[str] = []
    with pytest.raises(ActionBlocked):
        for c in enforce_call(p, act(), stream):
            got.append(c)
    assert got == ["chunk 0", "chunk 1", "chunk 2"]  # chunk 3 was produced after the trip


# ── suspensions: scoped, authorized, audited ───────────────────────────────────────


def test_suspensions_are_tenant_scoped(ledger):
    p = ProtectionPipeline(ledger=ledger)
    p.control.suspend_agent("coder", "acme only", actor="ops", tenant_id="acme")
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, act(tenant="acme"), lambda a: None)
    assert exc.value.decision.findings[0].code == "AGENT_SUSPENDED"
    assert (
        enforce_call(p, act(tenant="globex"), lambda a: "ok") == "ok"
    )  # same agent id, other tenant
    p.control.suspend_session("s1", "one session", actor="ops", tenant_id="globex")
    with pytest.raises(ActionBlocked):
        enforce_call(p, act(tenant="globex"), lambda a: None)
    assert enforce_call(p, act(tenant="globex", session="s2"), lambda a: "ok") == "ok"


def test_control_actions_need_a_reason_and_are_audited(ledger):
    c = ControlState(ledger)
    with pytest.raises(ValueError):
        c.suspend_agent("coder", "  ", actor="ops")
    c.suspend_agent("coder", "why", actor="ops", tenant_id="acme")
    c.resume_agent("coder", "ok", actor="ops2", tenant_id="acme")
    h = c.history()
    assert [(r["action"], r["actor"], r["tenant_id"]) for r in h[:2]] == [
        ("resume", "ops2", "acme"),
        ("suspend", "ops", "acme"),
    ]


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from panoptis.api import protect_control, server

    lg = ProtectLedger(store_url(tmp_path))
    state = ControlState(lg, refresh_s=0.0)
    state.kill_switch.kill_file = tmp_path / "api.kill"
    protect_control.set_control(state)
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    with TestClient(server.app) as c:
        yield c, state
    protect_control.set_control(None)
    lg.store.close()


def test_api_global_controls_are_operator_only(api):
    c, state = api
    body = {"reason": "drill"}
    assert (
        c.post(
            "/api/v1/protect/control/kill", json=body, headers={"X-API-Key": "key-acme"}
        ).status_code
        == 403
    )
    assert c.post("/api/v1/protect/control/kill", json=body).status_code == 401
    assert not state.status()["killed"]
    r = c.post(
        "/api/v1/protect/control/kill",
        json={"reason": "drill", "actor": "alice"},
        headers={"X-API-Key": "operator-key"},
    )
    assert r.status_code == 200 and state.status()["killed"] and state.local_kill()[0]
    st = c.get("/api/v1/protect/control", headers={"X-API-Key": "key-globex"}).json()
    assert st["killed"] is True and "local_kill_file" not in st  # tenants see that they are stopped
    assert (
        c.post(
            "/api/v1/protect/control/reset", json=body, headers={"X-API-Key": "key-globex"}
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/v1/protect/control/reset", json=body, headers={"X-API-Key": "operator-key"}
        ).status_code
        == 200
    )
    assert (
        c.get("/api/v1/protect/control/history", headers={"X-API-Key": "key-acme"}).status_code
        == 403
    )
    hist = c.get("/api/v1/protect/control/history", headers={"X-API-Key": "operator-key"}).json()[
        "records"
    ]
    assert [r["action"] for r in hist[:2]] == ["reset", "trip"] and hist[1][
        "actor"
    ] == "operator:alice"


def test_api_suspensions_are_scoped_to_the_callers_tenant(api):
    c, state = api
    r = c.post(
        "/api/v1/protect/control/agents/coder/suspend",
        json={"reason": "check", "actor": "bob"},
        headers={"X-API-Key": "key-acme"},
    )
    assert r.status_code == 200 and r.json()["actor"] == "acme:bob"
    acme = c.get("/api/v1/protect/control", headers={"X-API-Key": "key-acme"}).json()
    globex = c.get("/api/v1/protect/control", headers={"X-API-Key": "key-globex"}).json()
    assert set(acme["suspended_agents"]) == {"coder"} and globex["suspended_agents"] == {}
    # globex cannot lift acme's suspension: its resume applies to globex/coder only
    c.post(
        "/api/v1/protect/control/agents/coder/resume",
        json={"reason": "x"},
        headers={"X-API-Key": "key-globex"},
    )
    assert "acme/coder" in state.snapshot(force=True).suspended_agents
    assert (
        c.post(
            "/api/v1/protect/control/sessions/s1/suspend",
            json={"reason": ""},
            headers={"X-API-Key": "key-acme"},
        ).status_code
        == 422
    )


def test_cli_controls(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from panoptis.cli.main import app

    monkeypatch.setenv("AGENTWATCH_KILL_FILE", str(tmp_path / "cli.kill"))
    store = store_url(tmp_path)
    run = CliRunner().invoke
    assert (
        run(
            app,
            ["protect", "control", "kill", "--reason", "drill", "--store", store, "--actor", "ci"],
        ).exit_code
        == 0
    )
    st = json.loads(run(app, ["protect", "control", "status", "--store", store]).stdout)
    assert st["killed"] and st["local_killed"] and st["kill_actor"] == "cli:ci"
    assert (
        run(
            app,
            [
                "protect",
                "control",
                "suspend-agent",
                "coder",
                "--reason",
                "x",
                "--tenant",
                "acme",
                "--store",
                store,
            ],
        ).exit_code
        == 0
    )
    assert (
        run(app, ["protect", "control", "reset", "--reason", "done", "--store", store]).exit_code
        == 0
    )
    st = json.loads(
        run(app, ["protect", "control", "status", "--store", store, "--tenant", "acme"]).stdout
    )
    assert not st["killed"] and set(st["suspended_agents"]) == {"coder"}
    hist = json.loads(run(app, ["protect", "control", "history", "--store", store]).stdout)
    assert [h["action"] for h in hist] == ["reset", "suspend", "trip"]
