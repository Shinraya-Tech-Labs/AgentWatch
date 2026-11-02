"""TO-027: the protection API over records written by the real pipeline.

Two tenants' agents run real protected calls (allowed, blocked, mutated, escalated and
approved, loops, intent mismatches, MCP gateway calls) against one shared store; the API
must return each tenant exactly its own records, explain decisions without exposing
secrets or internal rule text, validate input, and fail safely.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from panoptis.protect import (
    ApprovalBroker,
    ControlState,
    PolicyStore,
    ProtectionPipeline,
    ProtectLedger,
    ToolAction,
)
from panoptis.protect.enforce import enforce_call
from panoptis.protect.model import ActionBlocked

H_ACME = {"X-API-Key": "key-acme"}
H_GLOBEX = {"X-API-Key": "key-globex"}
H_OP = {"X-API-Key": "operator-key"}


def _run(
    p: ProtectionPipeline,
    tenant: str,
    tool: str,
    fn=lambda a: "ok",
    session: str = "s1",
    **args: Any,
) -> Any:
    try:
        return enforce_call(
            p,
            ToolAction(
                agent_id="coder", session_id=session, tenant_id=tenant, tool=tool, arguments=args
            ),
            fn,
        )
    except ActionBlocked as b:
        return b


@pytest.fixture
def world(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from panoptis.api import protect_api, protect_approvals, protect_control, protect_rbac, server

    url = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    lg = ProtectLedger(url, seal_every=0)
    control = ControlState(lg, refresh_s=0.0)
    control.kill_switch.kill_file = tmp_path / "kill"
    for mod, setter, value in (
        (protect_api, "set_ledger", lg),
        (protect_approvals, "set_broker", ApprovalBroker(lg, timeout_s=5, poll_s=0.02)),
        (protect_control, "set_control", control),
        (protect_rbac, "set_policy_store", PolicyStore()),
    ):
        getattr(mod, setter)(value)
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")

    agents = ProtectionPipeline(
        ledger=lg,
        approvals=ApprovalBroker(lg, approver=lambda r: True, timeout_s=5),
        control=control,
    )
    agents.start_session("coder", "s1", objective="Summarize README.md", tenant_id="acme")
    _run(agents, "acme", "read_file", path="README.md")
    _run(agents, "acme", "bash", command="rm -rf /")  # blocked: threat
    _run(
        agents, "acme", "write_file", path="n.txt", content="key AKIAIOSFODNN7EXAMPLE"
    )  # mutated (and intent)
    _run(
        agents, "acme", "bash", session="s2", command="git push --force origin main"
    )  # escalated, approved
    for _ in range(3):
        _run(agents, "acme", "list_branches", session="s3", repo=".")  # a loop on the third
    _run(agents, "globex", "read_file", path="globex-secret-plan.txt")
    from panoptis.protect.mcp_gateway import MCPGateway

    async def gateway_call():
        sent: list[dict] = []

        async def up(m):
            sent.append(m)
            if m.get("method") == "tools/call":
                asyncio.get_running_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        gw.from_upstream(
                            {
                                "jsonrpc": "2.0",
                                "id": m["id"],
                                "result": {"content": [{"type": "text", "text": "hi"}]},
                            }
                        )
                    )
                )

        out: list[dict] = []

        async def cl(m):
            out.append(m)

        from panoptis.protect.mcp_gateway import GatewayIdentity

        gw = MCPGateway(
            agents,
            send_client=cl,
            send_upstream=up,
            identity=GatewayIdentity(agent_id="desk", tenant_id="acme", server="fs"),
        )
        await gw.from_client(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            }
        )
        for _ in range(100):
            if out:
                return
            await asyncio.sleep(0.02)

    asyncio.run(gateway_call())
    from panoptis.protect.discovery import UNMONITORED, Discovery

    lg.record(
        "protect.discovery",
        {
            "counts": {UNMONITORED: 1},
            "items": [
                Discovery("mcp_server", "cursor", "fs", UNMONITORED, "/x", 0.9, "direct").to_dict()
            ],
        },
        {},
        tenant_id="acme",
    )
    with TestClient(server.app) as c:
        yield c, lg
    for mod, setter in (
        (protect_api, "set_ledger"),
        (protect_approvals, "set_broker"),
        (protect_control, "set_control"),
        (protect_rbac, "set_policy_store"),
    ):
        getattr(mod, setter)(None)
    lg.store.close()


def get(c, path, headers=H_ACME, **params):
    r = c.get(f"/api/v1/protect{path}", headers=headers, params=params)
    return r


def test_decisions_reflect_the_real_pipeline_and_are_tenant_scoped(world):
    c, _ = world
    acme = get(c, "/decisions", limit=50).json()
    verdicts = [d["verdict"] for d in reversed(acme["decisions"])]
    assert verdicts[:4] == ["allow", "block", "mutate_and_allow", "allow"]
    assert all(d["agent_id"] in ("coder", "desk") for d in acme["decisions"])
    globex = get(c, "/decisions", H_GLOBEX).json()
    assert [d["tool"] for d in globex["decisions"]] == ["read_file"] and globex[
        "tenant_id"
    ] == "globex"
    assert "globex-secret-plan" not in json.dumps(acme)
    blocked = get(c, "/decisions", verdict="block").json()["decisions"]
    assert blocked and all(d["verdict"] == "block" for d in blocked)
    assert any(f["code"] == "FS_DELETE_CRITICAL" for f in blocked[-1]["findings"])
    assert get(c, "/decisions", agent_id="desk").json()["count"] == 1


def test_no_secrets_or_internal_rule_text_in_responses(world):
    c, _ = world
    body = json.dumps(get(c, "/decisions", limit=100).json())
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "regex" not in body and "(?i)" not in body


def test_call_detail_shows_decision_approval_execution_and_output(world):
    c, _ = world
    escalated = next(
        d
        for d in get(c, "/decisions", limit=50).json()["decisions"]
        if d["approval_state"] == "approved"
    )
    detail = get(c, f"/decisions/{escalated['call_id']}").json()
    assert detail["approval_requests"] and detail["approval_decisions"] and detail["executions"]
    assert get(c, f"/decisions/{escalated['call_id']}", H_GLOBEX).status_code == 404  # other tenant


def test_loops_and_intent_events(world):
    c, _ = world
    loops = get(c, "/loops").json()["events"]
    assert loops and loops[0]["findings"][0]["code"] == "DUPLICATE_CALL_LOOP"
    intent = get(c, "/intent").json()["events"]
    assert any(f["code"] == "GOAL_ACTION_MISMATCH" for e in intent for f in e["findings"])
    assert get(c, "/loops", H_GLOBEX).json()["events"] == []


def test_agents_mcp_discovery_and_status(world):
    c, _ = world
    agents = {a["agent_id"]: a for a in get(c, "/agents").json()["agents"]}
    assert set(agents) == {"coder", "desk"} and agents["coder"]["blocked"] >= 1
    s1 = next(s for s in agents["coder"]["sessions"] if s["session_id"] == "s1")
    assert s1["objective"] == "Summarize README.md"
    assert [a["agent_id"] for a in get(c, "/agents", H_GLOBEX).json()["agents"]] == ["coder"]
    mcp = get(c, "/mcp").json()
    assert mcp["servers"][0]["server"] == "fs" and mcp["servers"][0]["decisions"] == 1
    assert get(c, "/mcp", H_GLOBEX).json()["servers"] == []
    disc = get(c, "/discovery").json()
    assert disc["inventory"]["items"][0]["status"] == "UNMONITORED"
    assert get(c, "/discovery", H_GLOBEX).json()["inventory"] is None
    st = get(c, "/status").json()
    assert (
        st["verdicts"]["block"] >= 1
        and st["killed"] is False
        and st["policy"]["configured"] is False
    )


def test_controls_are_reflected_in_status_and_agents(world):
    c, _ = world
    c.post("/api/v1/protect/control/agents/coder/suspend", json={"reason": "check"}, headers=H_ACME)
    agents = {a["agent_id"]: a for a in get(c, "/agents").json()["agents"]}
    assert agents["coder"]["suspended"] is True and agents["desk"]["suspended"] is False
    assert get(c, "/status").json()["suspended_agents"] == 1
    assert get(c, "/status", H_GLOBEX).json()["suspended_agents"] == 0


def test_evidence_verify_head_and_proof_are_tenant_scoped(world):
    c, lg = world
    rep = get(c, "/evidence/verify", seal="true").json()
    assert rep["ok"] and rep["tenant_id"] == "acme" and rep["segments_checked"] >= 1
    head = get(c, "/evidence/head").json()["head"]
    assert head["seq"] == rep["head"]["seq"]
    acme_obs = get(c, "/decisions").json()["decisions"][0]["obs_id"]
    proof = get(c, f"/evidence/proof/{acme_obs}").json()
    from panoptis.evidence.verifier import verify_proof

    assert verify_proof(proof)["ok"]
    assert get(c, f"/evidence/proof/{acme_obs}", H_GLOBEX).status_code == 404
    globex_obs = get(c, "/decisions", H_GLOBEX).json()["decisions"][0]["obs_id"]
    assert get(c, f"/evidence/proof/{globex_obs}", H_GLOBEX).status_code == 409  # not sealed yet


@pytest.mark.parametrize(
    "path,params",
    [
        ("/decisions", {"verdict": "maybe"}),
        ("/decisions", {"limit": 0}),
        ("/decisions", {"limit": 10_000}),
        ("/decisions", {"after": "x" * 40}),
        ("/loops", {"limit": -1}),
    ],
)
def test_malformed_input_is_rejected(world, path, params):
    c, _ = world
    assert get(c, path, **params).status_code == 422


def test_authentication_is_required(world):
    c, _ = world
    for path in (
        "/status",
        "/decisions",
        "/agents",
        "/mcp",
        "/discovery",
        "/evidence/verify",
        "/loops",
        "/intent",
    ):
        assert c.get(f"/api/v1/protect{path}").status_code == 401
        assert c.get(f"/api/v1/protect{path}", headers={"X-API-Key": "wrong"}).status_code == 401


def test_cursor_returns_only_newer_records_oldest_first(world):
    c, lg = world
    first = get(c, "/decisions", limit=500).json()["decisions"]
    oldest = first[-1]["obs_id"]
    page = get(c, "/decisions", after=oldest, limit=2).json()
    assert [d["obs_id"] for d in page["decisions"]] == [d["obs_id"] for d in reversed(first)][1:3]
    assert page["next_after"] == page["decisions"][-1]["obs_id"]


def test_store_failure_is_a_safe_error(world, monkeypatch):
    c, lg = world

    def broken(*a, **k):
        raise RuntimeError("database is locked: /secret/path/agentwatch.db")

    monkeypatch.setattr(lg, "query", broken)
    from fastapi.testclient import TestClient

    from panoptis.api import server

    with TestClient(server.app, raise_server_exceptions=False) as c2:
        r = c2.get("/api/v1/protect/decisions", headers=H_ACME)
    assert r.status_code == 500 and "/secret/path" not in r.text
