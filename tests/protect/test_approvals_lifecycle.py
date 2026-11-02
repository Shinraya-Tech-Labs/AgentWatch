"""TO-019: the human-approval lifecycle.

Execution happens only after a valid approval for the exact requested action; every
other outcome (reject, timeout, cancellation, no approver, changed action, replay, a
decision from another tenant) leaves the tool unrun. The API tests drive a real waiting
agent thread through the HTTP endpoints.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from panoptis.protect import (
    ActionBlocked,
    ApprovalBroker,
    ApprovalError,
    ApprovalOutcome,
    ProtectionConfig,
    ProtectionPipeline,
    ToolAction,
    action_digest,
)
from panoptis.protect.enforce import aenforce_call, enforce_call
from panoptis.protect.ledger import KIND_APPROVAL_DECISION, KIND_APPROVAL_REQUEST

ESCALATED = "git push --force origin main"


def act(command: str = ESCALATED, tenant: str = "default", **kw: Any) -> ToolAction:
    return ToolAction(
        agent_id=kw.pop("agent_id", "coder"),
        session_id=kw.pop("session_id", "s1"),
        tool="bash",
        arguments={"command": command},
        tenant_id=tenant,
        **kw,
    )


def pipeline(ledger, approver=None, timeout_s: float = 2.0) -> ProtectionPipeline:
    return ProtectionPipeline(
        ledger=ledger,
        approvals=ApprovalBroker(ledger, approver=approver, timeout_s=timeout_s, poll_s=0.01),
        config=ProtectionConfig(approval_timeout_s=timeout_s),
    )


# ── identity, binding and evidence ──────────────────────────────────────────────────


def test_the_request_is_bound_to_the_exact_action_and_recorded_in_its_tenant(ledger):
    seen: list[Any] = []
    ran: list[str] = []
    p = pipeline(ledger, approver=lambda r: seen.append(r) or True)
    a = act(tenant="acme")
    enforce_call(p, a, lambda args: ran.append(args["command"]))
    req = seen[0]
    assert req.tenant_id == "acme" and req.call_id == a.call_id
    assert req.agent_id == "coder" and req.session_id == "s1" and req.tool == "bash"
    assert req.arguments == {"command": ESCALATED} and req.action_digest == action_digest(a)
    assert req.expires_at > req.created_at and req.threat_rules_version
    assert ran == [ESCALATED]
    # evidence lives in the requesting tenant, not in the ledger's default tenant
    assert not ledger.query((KIND_APPROVAL_REQUEST,))
    rq = ledger.query((KIND_APPROVAL_REQUEST,), tenant_id="acme")[0]
    dec = ledger.query((KIND_APPROVAL_DECISION,), tenant_id="acme")[0]["payload"]
    assert rq["payload"]["action_digest"] == req.action_digest
    assert dec["approved"] is True and dec["decided_by"] == "approver-callback"
    assert dec["decided_at"] >= req.created_at and dec["action_digest"] == req.action_digest


def test_the_digest_covers_every_bound_field():
    base = act()
    variants = [
        act(command="git push --force origin dev", call_id=base.call_id),
        act(tenant="other", call_id=base.call_id),
        act(agent_id="ops", call_id=base.call_id),
        act(session_id="s2", call_id=base.call_id),
        act(),  # a new call id
    ]
    assert len({action_digest(base), *map(action_digest, variants)}) == 6


# ── outcomes ────────────────────────────────────────────────────────────────────────


def test_reject_never_runs(ledger):
    ran: list[Any] = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(pipeline(ledger, approver=lambda r: False), act(), ran.append)
    assert ran == [] and exc.value.decision.approval["state"] == "rejected"


def test_timeout_never_runs(ledger):
    ran: list[Any] = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(pipeline(ledger, timeout_s=0.1), act(), ran.append)
    assert ran == [] and any(f.code == "APPROVAL_TIMEOUT" for f in exc.value.decision.findings)


def test_no_approval_channel_never_runs(ledger):
    ran: list[Any] = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(ProtectionPipeline(ledger=ledger), act(), ran.append)
    assert ran == [] and exc.value.decision.approval == {"state": "unavailable"}


def test_a_failing_approver_never_runs(ledger):
    def broken(req):
        raise RuntimeError("approval service down")

    ran: list[Any] = []
    with pytest.raises(ActionBlocked):
        enforce_call(pipeline(ledger, approver=broken), act(), ran.append)
    assert ran == []


def test_cancelling_the_caller_cancels_the_request_and_never_runs(ledger):
    p = pipeline(ledger, timeout_s=5.0)
    ran: list[Any] = []
    a = act()

    async def tool(args):
        ran.append(args)

    async def main() -> None:
        task = asyncio.create_task(aenforce_call(p, a, tool))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if p.approvals.pending():
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    time.sleep(0.1)  # the evaluating thread wakes on the cancellation record
    (req,) = p.approvals.list()
    assert req["state"] == "cancelled" and req["call_id"] == a.call_id
    assert ran == []
    # a late approval cannot revive it
    out = p.approvals.resolve(
        req["request_id"], True, "alice", expected_digest=req["action_digest"]
    )
    assert out.cancelled and not out.approved


# ── no reuse ────────────────────────────────────────────────────────────────────────


def test_an_action_changed_after_the_request_is_not_covered(ledger):
    ran: list[Any] = []
    a = act()

    def approver(req):
        a.arguments["command"] = "git push --force origin release"  # changed while pending
        return True

    with pytest.raises(ActionBlocked) as exc:
        enforce_call(pipeline(ledger, approver=approver), a, ran.append)
    assert ran == [] and any(f.code == "APPROVAL_MISMATCH" for f in exc.value.decision.findings)


def test_a_decision_for_a_different_action_is_refused(ledger):
    broker = ApprovalBroker(ledger, timeout_s=5)
    p = ProtectionPipeline(ledger=ledger, approvals=broker)
    d = p.evaluate(act(), wait_for_approval=False)
    rid = d.approval["request_id"]
    with pytest.raises(ApprovalError, match="different action"):
        broker.resolve(rid, True, "alice", expected_digest=action_digest(act()))
    assert broker.get(rid)["state"] == "pending"


def test_an_expired_request_cannot_be_approved(ledger):
    broker = ApprovalBroker(ledger, timeout_s=0.05)
    p = ProtectionPipeline(
        ledger=ledger, approvals=broker, config=ProtectionConfig(approval_timeout_s=0.05)
    )
    rid = p.evaluate(act(), wait_for_approval=False).approval["request_id"]
    time.sleep(0.1)
    out = broker.resolve(rid, True, "alice")
    assert not out.approved and out.timed_out
    assert broker.get(rid)["state"] == "expired"


def test_the_first_decision_wins(ledger):
    broker = ApprovalBroker(ledger, timeout_s=5)
    rid = (
        ProtectionPipeline(ledger=ledger, approvals=broker)
        .evaluate(act(), wait_for_approval=False)
        .approval["request_id"]
    )
    assert broker.resolve(rid, False, "bob").approved is False
    assert broker.resolve(rid, True, "mallory").decided_by == "bob"


def test_an_approved_call_cannot_be_replayed(ledger):
    ran: list[Any] = []
    p = pipeline(ledger, approver=lambda r: True)
    a = act()
    enforce_call(p, a, lambda args: ran.append(1))
    with pytest.raises(ActionBlocked):
        enforce_call(p, a, lambda args: ran.append(2))
    assert ran == [1]


def test_unknown_requests_are_refused(ledger):
    with pytest.raises(ApprovalError):
        ApprovalBroker(ledger).resolve("apr-nope", True, "alice")


def test_approve_with_edits_is_reinspected(ledger):
    received: list[Any] = []
    edit = ApprovalOutcome(
        approved=True, decided_by="alice", edited_arguments={"command": "rm -rf /"}
    )
    with pytest.raises(ActionBlocked):
        enforce_call(pipeline(ledger, approver=lambda r: edit), act(), received.append)
    assert received == [], "a human cannot approve their way past a hard block"


# ── the API, with a real waiting agent ──────────────────────────────────────────────


@pytest.fixture
def api(ledger, monkeypatch):
    from fastapi.testclient import TestClient

    from panoptis.api import protect_approvals, server

    broker = ApprovalBroker(ledger, timeout_s=10, poll_s=0.02)
    protect_approvals.set_broker(broker)
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    # a configured deployment: requests without a valid key are refused
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    with TestClient(server.app) as c:
        yield c, broker
    protect_approvals.set_broker(None)


def _waiting_agent(broker: ApprovalBroker, ledger, a: ToolAction) -> tuple[threading.Thread, dict]:
    p = ProtectionPipeline(
        ledger=ledger, approvals=broker, config=ProtectionConfig(approval_timeout_s=10)
    )
    state: dict[str, Any] = {"ran": []}

    def run() -> None:
        try:
            state["result"] = enforce_call(
                p, a, lambda args: state["ran"].append(args["command"]) or "pushed"
            )
        except ActionBlocked as exc:
            state["blocked"] = exc.decision

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, state


def _pending(client, key: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = client.get(
            "/api/v1/protect/approvals", params={"state": "pending"}, headers={"X-Api-Key": key}
        ).json()["approvals"]
        if rows:
            return rows[0]
        time.sleep(0.02)
    raise AssertionError("no pending approval appeared")


def test_api_approval_releases_exactly_the_waiting_call(api, ledger):
    client, broker = api
    a = act(tenant="acme")
    t, state = _waiting_agent(broker, ledger, a)
    req = _pending(client, "key-acme")
    assert req["call_id"] == a.call_id and req["arguments"] == {"command": ESCALATED}
    assert state["ran"] == [], "nothing runs while the request is pending"
    r = client.post(
        f"/api/v1/protect/approvals/{req['request_id']}",
        json={"approved": True, "action_digest": req["action_digest"], "decided_by": "alice"},
        headers={"X-Api-Key": "key-acme"},
    )
    assert r.status_code == 200 and r.json()["applied"] and r.json()["state"] == "approved"
    t.join(5)
    assert state["ran"] == [ESCALATED] and state["result"] == "pushed"
    dec = ledger.query((KIND_APPROVAL_DECISION,), tenant_id="acme")[0]["payload"]
    assert dec["decided_by"] == "acme:alice" and dec["decided_at"]


def test_api_rejection_and_wrong_digest_and_other_tenants(api, ledger):
    client, broker = api
    a = act(tenant="acme")
    t, state = _waiting_agent(broker, ledger, a)
    req = _pending(client, "key-acme")
    url = f"/api/v1/protect/approvals/{req['request_id']}"
    # another tenant cannot see or decide it
    assert client.get(url, headers={"X-Api-Key": "key-globex"}).status_code == 404
    assert (
        client.post(
            url,
            json={"approved": True, "action_digest": req["action_digest"], "decided_by": "eve"},
            headers={"X-Api-Key": "key-globex"},
        ).status_code
        == 404
    )
    assert (
        client.get("/api/v1/protect/approvals", headers={"X-Api-Key": "key-globex"}).json()["count"]
        == 0
    )
    # a decision for a different action is refused
    bad = client.post(
        url,
        json={"approved": True, "action_digest": "sha256:" + "0" * 64, "decided_by": "alice"},
        headers={"X-Api-Key": "key-acme"},
    )
    assert bad.status_code == 409
    # no credential, no access
    assert client.get("/api/v1/protect/approvals").status_code in (401, 403)
    # the owner rejects it
    r = client.post(
        url,
        json={"approved": False, "action_digest": req["action_digest"], "decided_by": "alice"},
        headers={"X-Api-Key": "key-acme"},
    )
    assert r.json()["state"] == "rejected"
    t.join(5)
    assert state["ran"] == [] and state["blocked"].approval["state"] == "rejected"


def test_list_reports_each_requests_own_record_id_after_decisions(ledger):
    """Regression: a renamed loop variable made list() report the last *decision's* record
    id for every request (and crash when there was no decision)."""
    broker = ApprovalBroker(ledger, approver=lambda r: True, timeout_s=5)
    p = ProtectionPipeline(ledger=ledger, approvals=broker)
    for _ in range(2):
        enforce_call(p, act(), lambda a: "ran")
    requests = {
        r["payload"]["request_id"]: r["obs_id"]
        for r in ledger.query((KIND_APPROVAL_REQUEST,), newest_first=False)
    }
    listed = {row["request_id"]: row["obs_id"] for row in broker.list()}
    assert listed == requests and len(listed) == 2
