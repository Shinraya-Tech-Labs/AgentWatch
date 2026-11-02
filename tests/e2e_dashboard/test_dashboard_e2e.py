# ruff: noqa: S603 - the processes started are the stack under test
"""TO-029: browser E2E of the security dashboard against a running backend.

A real API server (uvicorn) and the production Next.js build (``next start``) run as
their own processes; a real protected agent writes to the shared store. Chromium
(Playwright) drives the dashboard and the tests check that what it displays and the
controls it operates correspond to real backend state:

* a tenant sees its own decisions (verdicts and reasons from the pipeline) and never
  another tenant's;
* approving a pending call in the browser releases the waiting agent: the tool runs once;
* the emergency stop is refused to a tenant key, works with the operator key, blocks the
  agent, and shows the banner; lifting it lets the agent run again.

Opt-in (it builds and starts the frontend): ``AGENTWATCH_E2E_DASHBOARD=1 pytest tests/e2e_dashboard``.
Screenshots are written to ``$AGENTWATCH_E2E_SCREENSHOTS`` when set.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("AGENTWATCH_E2E_DASHBOARD") != "1",
        reason="set AGENTWATCH_E2E_DASHBOARD=1 (builds and starts the frontend)",
    ),
    pytest.mark.skipif(shutil.which("npx") is None, reason="node/npx not installed"),
]

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 120.0) -> None:
    import httpx

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError(url)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dash")
    env = {
        **{k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "AGENTWATCH_API_URL")},
        "AGENTWATCH_STORE": f"sqlite:///{(tmp / 'shared.db').as_posix()}",
        "AGENTWATCH_TENANT_KEYS": "acme=key-acme,globex=key-globex",
        "AGENTWATCH_API_KEY": "operator-key",
        "AGENTWATCH_KILL_FILE": str(tmp / "protect.kill"),
        "AGENTWATCH_EVIDENCE_ANCHORS": str(tmp / "anchors"),
        "AGENTWATCH_FORWARD_EVENTS": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    api_port, web_port = _free_port(), _free_port()
    procs: list[subprocess.Popen[bytes]] = []
    procs.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "panoptis.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--log-level",
                "warning",
            ],
            env=env,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    )
    npx = shutil.which("npx")
    assert npx
    if not (FRONTEND / ".next" / "BUILD_ID").exists():
        subprocess.run(
            [npx, "next", "build"], cwd=FRONTEND, check=True, capture_output=True, timeout=900
        )
    web_env = {**env, "AGENTWATCH_API_URL": f"http://127.0.0.1:{api_port}", "PORT": str(web_port)}
    procs.append(
        subprocess.Popen(
            [npx, "next", "start", "-p", str(web_port), "-H", "127.0.0.1"],
            cwd=FRONTEND,
            env=web_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    )
    try:
        _wait_http(f"http://127.0.0.1:{api_port}/health")
        _wait_http(f"http://127.0.0.1:{web_port}/security")
        yield {
            "api": f"http://127.0.0.1:{api_port}",
            "web": f"http://127.0.0.1:{web_port}",
            "env": env,
            "tmp": tmp,
        }
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()


def _pipeline(stack: dict[str, Any], **kw: Any) -> Any:
    from panoptis.protect import ControlState, ProtectionPipeline, ProtectLedger

    lg = ProtectLedger(stack["env"]["AGENTWATCH_STORE"], seal_every=0)
    control = ControlState(lg, refresh_s=0.0)
    control.kill_switch.kill_file = Path(stack["env"]["AGENTWATCH_KILL_FILE"])
    return ProtectionPipeline(ledger=lg, control=control, **kw), lg


def _act(tenant: str, tool: str, **args: Any) -> Any:
    from panoptis.protect import ToolAction

    return ToolAction(
        agent_id="coder", session_id="dash-s", tenant_id=tenant, tool=tool, arguments=args
    )


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def _shot(page: Any, name: str) -> None:
    out = os.environ.get("AGENTWATCH_E2E_SCREENSHOTS")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(out) / f"{name}.png"), full_page=True)


def _sign_in(page: Any, stack: dict[str, Any], key: str, path: str = "/security") -> None:
    page.goto(stack["web"] + path)
    page.get_by_label("API key").fill(key)
    page.get_by_role("button", name="Sign in").click()


def test_tenant_sees_its_real_decisions_and_never_another_tenants(stack, browser):
    from panoptis.protect.enforce import enforce_call
    from panoptis.protect.model import ActionBlocked

    p, _ = _pipeline(stack)
    enforce_call(p, _act("acme", "read_file", path="README.md"), lambda a: "text")
    with pytest.raises(ActionBlocked):
        enforce_call(p, _act("acme", "bash", command="rm -rf /"), lambda a: None)
    enforce_call(p, _act("globex", "fetch_globex_secret_report", id="g-1"), lambda a: "y")
    page = browser.new_page()
    _sign_in(page, stack, "key-acme")
    page.get_by_text("tenant acme").wait_for()
    table = page.get_by_role("table", name="recent decisions")
    table.get_by_text("rm -rf", exact=False).or_(
        table.get_by_text("FS_DELETE_CRITICAL", exact=False)
    ).first.wait_for()
    assert table.get_by_text("read_file").count() >= 1 and table.get_by_text("bash").count() >= 1
    assert page.get_by_text("fetch_globex_secret_report").count() == 0
    _shot(page, "01-overview-acme")
    page.goto(stack["web"] + "/security/decisions")
    page.get_by_label("verdict").select_option("block")
    page.get_by_role("button", name="details of bash call", exact=False).first.click()
    page.get_by_text(
        "executed: no"
    ).wait_for()  # the backend recorded no execution for the blocked call
    _shot(page, "02-decisions-blocked-detail")
    page.close()


def test_approving_in_the_browser_releases_the_waiting_agent(stack, browser):
    from panoptis.protect import ApprovalBroker, ProtectionConfig
    from panoptis.protect.enforce import enforce_call

    _, lg = _pipeline(stack)
    p, _ = _pipeline(
        stack,
        approvals=ApprovalBroker(lg, timeout_s=90, poll_s=0.05),
        config=ProtectionConfig(approval_timeout_s=90),
    )
    ran: list[str] = []
    done = threading.Event()

    def agent() -> None:
        try:
            enforce_call(
                p,
                _act("acme", "bash", command="git push --force origin release"),
                lambda a: ran.append(a["command"]) or "pushed",
            )
        finally:
            done.set()

    t = threading.Thread(target=agent, daemon=True)
    t.start()
    page = browser.new_page()
    _sign_in(page, stack, "key-acme", "/security/approvals")
    args = page.get_by_label("arguments to approve").first
    args.wait_for(timeout=30_000)
    assert "git push --force origin release" in args.inner_text()
    assert ran == []
    _shot(page, "03-approval-pending")
    page.get_by_label("your name").first.fill("alice")
    page.get_by_role("button", name="Approve").first.click()
    assert done.wait(30)
    assert ran == [
        "git push --force origin release"
    ]  # ran exactly once, after the browser approval
    page.get_by_label("approval state").select_option("approved")
    page.get_by_text("acme:alice", exact=False).first.wait_for()
    _shot(page, "04-approval-approved")
    page.close()


def test_emergency_stop_from_the_browser(stack, browser):
    import httpx

    from panoptis.protect.enforce import enforce_call
    from panoptis.protect.model import ActionBlocked

    p, _ = _pipeline(stack)
    page = browser.new_page()
    _sign_in(page, stack, "key-acme", "/security/controls")
    page.get_by_label("control reason").fill("tenant tries a global stop")
    page.get_by_role("button", name="Emergency stop…").click()
    page.get_by_role("button", name="Confirm: stop all agents").click()
    page.get_by_text("operator only", exact=False).wait_for()
    assert (
        httpx.get(
            f"{stack['api']}/api/v1/protect/control", headers={"X-API-Key": "operator-key"}
        ).json()["killed"]
        is False
    )
    page.get_by_role("button", name="sign out").click()
    _sign_in(page, stack, "operator-key", "/security/controls")
    page.get_by_label("control reason").fill("incident drill")
    page.get_by_role("button", name="Emergency stop…").click()
    page.get_by_role("button", name="Confirm: stop all agents").click()
    page.get_by_text("EMERGENCY STOP ACTIVE", exact=False).first.wait_for(timeout=15_000)
    state = httpx.get(
        f"{stack['api']}/api/v1/protect/control", headers={"X-API-Key": "operator-key"}
    ).json()
    assert state["killed"] is True and state["kill_actor"] == "operator:dashboard"
    with pytest.raises(ActionBlocked):
        enforce_call(p, _act("acme", "read_file", path="a.txt"), lambda a: "x")
    _shot(page, "05-emergency-stop-active")
    page.get_by_label("control reason").fill("drill over")
    page.get_by_role("button", name="Lift emergency stop").click()
    page.get_by_text("Not active", exact=False).wait_for(timeout=15_000)
    assert enforce_call(p, _act("acme", "read_file", path="a.txt"), lambda a: "x") == "x"
    page.close()


def test_disconnected_backend_is_shown_not_hidden(stack, browser):
    page = browser.new_page()
    page.route("**/api/v1/protect/**", lambda route: route.abort())
    _sign_in(page, stack, "key-acme")
    page.get_by_text("disconnected", exact=False).first.wait_for(timeout=15_000)
    _shot(page, "06-disconnected")
    page.close()
