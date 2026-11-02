"""TO-028: CLI smoke tests against a real local deployment.

A real API server (uvicorn, its own process) and a real protected agent share one
evidence store. The CLI runs as separate processes, in API mode (authorized by the
server, tenant from the key) and in local mode, and the tests check the state it
returns and changes: decisions, agents, approvals that release a waiting agent, the
emergency stop, RBAC checks, evidence verification, invalid configuration, refused
keys and an unavailable server.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from panoptis.protect import (
    ApprovalBroker,
    ControlState,
    PolicyStore,
    ProtectionConfig,
    ProtectionPipeline,
    ProtectLedger,
    ToolAction,
)
from panoptis.protect.enforce import enforce_call
from panoptis.protect.model import ActionBlocked

ROOT = Path(__file__).resolve().parents[2]
POLICY = """
version: "1.0"
default: deny
agents: {coder: [coder]}
roles:
  coder:
    allowed_tools: [read_file, bash]
    restricted_arguments: {bash: {blocked_commands: ["sudo *"]}}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def deployment(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("deploy")
    policy = tmp / "policy.yaml"
    policy.write_text(POLICY, encoding="utf-8")
    env = {
        **{
            k: v
            for k, v in os.environ.items()
            if k not in ("DATABASE_URL", "AGENTWATCH_API_URL", "AGENTWATCH_API_KEY")
        },
        "AGENTWATCH_STORE": f"sqlite:///{(tmp / 'shared.db').as_posix()}",
        "AGENTWATCH_TENANT_KEYS": "acme=key-acme,globex=key-globex",
        "AGENTWATCH_API_KEY": "operator-key",
        "AGENTWATCH_KILL_FILE": str(tmp / "protect.kill"),
        "AGENTWATCH_POLICY_FILE": str(policy),
        "AGENTWATCH_EVIDENCE_ANCHORS": str(tmp / "anchors"),
        "AGENTWATCH_FORWARD_EVENTS": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    port = _free_port()
    server = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "panoptis.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    import httpx

    for _ in range(300):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code < 500:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        server.kill()
        raise RuntimeError(f"API did not start: {server.stderr.read().decode()[-2000:]}")
    yield {"url": url, "env": env, "tmp": tmp}
    server.terminate()
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()


def cli(
    dep: dict[str, Any], *args: str, key: str | None = None, api: bool = True
) -> tuple[int, Any, str]:
    env = dict(dep["env"])
    if api:
        env["AGENTWATCH_API_URL"] = dep["url"]
        if key:
            env["AGENTWATCH_API_KEY"] = key
        else:
            env.pop("AGENTWATCH_API_KEY", None)
    p = subprocess.run(  # noqa: S603 - the CLI under test
        [sys.executable, "-m", "panoptis.cli.main", "protect", *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )  # noqa: S603
    try:
        out = json.loads(p.stdout) if p.stdout.strip().startswith(("{", "[")) else p.stdout
    except ValueError:
        out = p.stdout
    return p.returncode, out, p.stderr


def agent_pipeline(dep: dict[str, Any], **kw: Any) -> ProtectionPipeline:
    lg = ProtectLedger(dep["env"]["AGENTWATCH_STORE"], seal_every=0)
    control = ControlState(lg, refresh_s=0.0)
    control.kill_switch.kill_file = Path(dep["env"]["AGENTWATCH_KILL_FILE"])
    return ProtectionPipeline(
        ledger=lg,
        policy=PolicyStore(path=dep["env"]["AGENTWATCH_POLICY_FILE"]),
        control=control,
        **kw,
    )


def act(tenant: str, tool: str, **args: Any) -> ToolAction:
    return ToolAction(
        agent_id="coder", session_id="cli-s", tenant_id=tenant, tool=tool, arguments=args
    )


def test_status_decisions_and_agents_reflect_real_agent_calls(deployment):
    p = agent_pipeline(deployment)
    enforce_call(p, act("acme", "read_file", path="a.txt"), lambda a: "x")
    with pytest.raises(ActionBlocked):
        enforce_call(p, act("acme", "bash", command="sudo ls"), lambda a: None)
    enforce_call(p, act("globex", "read_file", path="g.txt"), lambda a: "y")
    code, st, _ = cli(deployment, "status", key="key-acme")
    assert (
        code == 0
        and st["tenant_id"] == "acme"
        and st["verdicts"] == {"allow": 1, "block": 1}
        and st["policy"]["configured"]
    )
    code, dec, _ = cli(deployment, "decisions", "--verdict", "block", key="key-acme")
    assert code == 0 and [d["tool"] for d in dec["decisions"]] == ["bash"]
    assert "RBAC_COMMAND_BLOCKED" in {f["code"] for f in dec["decisions"][0]["findings"]}
    code, dec, _ = cli(deployment, "decisions", key="key-globex")
    assert [d["tool"] for d in dec["decisions"]] == ["read_file"]  # only globex's own
    code, ag, _ = cli(deployment, "agents", key="key-acme")
    assert ag["agents"][0]["agent_id"] == "coder" and ag["agents"][0]["blocked"] == 1
    # local mode reads the same store
    code, st_local, _ = cli(deployment, "status", "--tenant", "acme", api=False)
    assert code == 0 and st_local["verdicts"] == st["verdicts"]


def test_cli_approval_releases_a_waiting_agent(deployment):
    lg = ProtectLedger(deployment["env"]["AGENTWATCH_STORE"], seal_every=0)
    p = agent_pipeline(
        deployment,
        approvals=ApprovalBroker(lg, timeout_s=60, poll_s=0.05),
        config=ProtectionConfig(approval_timeout_s=60),
    )
    ran: list[str] = []
    result: dict[str, Any] = {}

    def agent() -> None:
        try:
            result["out"] = enforce_call(
                p,
                act("acme", "bash", command="git push --force origin main"),
                lambda a: ran.append("pushed") or "pushed",
            )
        except ActionBlocked as b:
            result["blocked"] = b

    t = threading.Thread(target=agent)
    t.start()
    pending: list[dict[str, Any]] = []
    for _ in range(100):
        code, listing, _ = cli(deployment, "approvals", "list", key="key-acme")
        pending = listing.get("approvals", []) if isinstance(listing, dict) else []
        if pending:
            break
        time.sleep(0.2)
    assert pending and ran == []
    req = pending[0]
    code, _, err = cli(
        deployment,
        "approvals",
        "approve",
        req["request_id"],
        "--digest",
        req["action_digest"],
        key="key-globex",
    )
    assert code != 0  # another tenant's request does not exist for globex
    code, out, err = cli(
        deployment,
        "approvals",
        "approve",
        req["request_id"],
        "--digest",
        "sha256:" + "0" * 64,
        "--by",
        "alice",
        key="key-acme",
    )
    assert code != 0 and ran == []  # a digest for a different action is refused
    code, out, err = cli(
        deployment,
        "approvals",
        "approve",
        req["request_id"],
        "--digest",
        req["action_digest"],
        "--by",
        "alice",
        key="key-acme",
    )
    assert code == 0, err
    t.join(30)
    assert ran == ["pushed"] and result["out"] == "pushed"


def test_emergency_stop_through_the_cli_is_operator_only_and_blocks_agents(deployment):
    code, _, err = cli(
        deployment, "control", "status", key="key-acme"
    )  # control is local-only in this CLI
    p = agent_pipeline(deployment)
    import httpx

    r = httpx.post(
        f"{deployment['url']}/api/v1/protect/control/kill",
        json={"reason": "drill"},
        headers={"X-API-Key": "key-acme"},
        timeout=10,
    )
    assert r.status_code == 403
    code, out, err = cli(
        deployment, "control", "kill", "--reason", "cli drill", "--actor", "ops", api=False
    )
    assert code == 0, err
    with pytest.raises(ActionBlocked):
        enforce_call(p, act("acme", "read_file", path="a.txt"), lambda a: "x")
    code, st, _ = cli(deployment, "status", key="key-acme")
    assert st["killed"] is True
    code, out, err = cli(
        deployment, "control", "reset", "--reason", "done", "--actor", "ops", api=False
    )
    assert code == 0
    assert enforce_call(p, act("acme", "read_file", path="a.txt"), lambda a: "x") == "x"


def test_rbac_check_and_permissions_through_the_api(deployment):
    code, out, _ = cli(
        deployment,
        "rbac",
        "check",
        "coder",
        "bash",
        "--args",
        '{"command": "sudo ls"}',
        key="key-acme",
    )
    assert code == 1 and out["authorized"] is False and out["reasons"]
    code, out, _ = cli(
        deployment, "rbac", "check", "coder", "read_file", "--args", '{"path": "a"}', key="key-acme"
    )
    assert code == 0 and out["authorized"] is True
    code, out, _ = cli(deployment, "rbac", "permissions", "coder", key="key-acme")
    assert out["roles"] == ["coder"]


def test_evidence_verification_command(deployment):
    code, out, err = cli(
        deployment,
        "evidence",
        "verify",
        "--store",
        deployment["env"]["AGENTWATCH_STORE"],
        "--tenant",
        "acme",
        api=False,
    )
    assert code == 0, err
    assert out["ok"] is True and out["segments_checked"] >= 1


def test_invalid_configuration_is_reported(deployment, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("default: maybe\nroles: {a: {allowed_tool: [x]}}\n", encoding="utf-8")
    code, out, _ = cli(deployment, "rbac", "validate", str(bad), api=False)
    assert code == 1 and out["valid"] is False and len(out["errors"]) == 2
    good = Path(deployment["env"]["AGENTWATCH_POLICY_FILE"])
    code, out, _ = cli(deployment, "rbac", "validate", str(good), api=False)
    assert code == 0 and out["valid"] is True
    code, out, _ = cli(
        deployment,
        "doctor",
        "--policy",
        str(bad),
        "--store",
        deployment["env"]["AGENTWATCH_STORE"],
        api=False,
    )
    assert code == 1 and any(
        c["check"] == "rbac policy" and c["status"] == "fail" for c in out["checks"]
    )


def test_authorization_failure_and_unavailable_service(deployment):
    code, _, err = cli(deployment, "status", key="wrong-key")
    assert code == 3 and "not authorized" in err and "wrong-key" not in err
    code, _, err = cli(deployment, "status")  # no key at all
    assert code == 3
    env = dict(deployment)
    env["url"] = f"http://127.0.0.1:{_free_port()}"
    code, _, err = cli(env, "status", key="key-acme")
    assert code == 4 and "unavailable" in err and "Traceback" not in err
