"""TO-002: the shared enforcement boundary."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from panoptis.protect import ActionBlocked, ProtectionPipeline, ToolAction
from panoptis.protect.enforce import aenforce_call, enforce_call


def action(tool: str = "bash", **arguments: Any) -> ToolAction:
    return ToolAction(agent_id="coder", session_id="s1", tool=tool, arguments=arguments)


class Recorder(ProtectionPipeline):
    """Records the order in which the pipeline's phases run."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.order: list[str] = []

    def evaluate(self, a: ToolAction, **kw: Any) -> Any:
        self.order.append("decide")
        return super().evaluate(a, **kw)

    def inspect_output(self, a: ToolAction, result: Any) -> Any:
        self.order.append("inspect_output")
        return super().inspect_output(a, result)


def test_order_is_decide_execute_once_inspect_return():
    p = Recorder()

    def tool(args: dict[str, Any]) -> str:
        p.order.append("execute")
        return "ok"

    assert enforce_call(p, action(command="ls"), tool) == "ok"
    assert p.order == ["decide", "execute", "inspect_output"]


def test_blocked_call_never_executes_and_raises_action_blocked():
    calls = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(ProtectionPipeline(), action(command="rm -rf /"), lambda a: calls.append(a))
    assert calls == []
    assert exc.value.decision.verdict.value == "block"


def test_tool_exceptions_propagate_unchanged():
    def tool(args: dict[str, Any]) -> str:
        raise KeyError("missing row")

    with pytest.raises(KeyError, match="missing row"):
        enforce_call(ProtectionPipeline(), action(command="ls"), tool)


def test_enforcement_failure_before_execution_blocks():
    class Broken(ProtectionPipeline):
        def evaluate(self, a: ToolAction, **kw: Any) -> Any:
            raise RuntimeError("policy store down")

    calls = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(Broken(), action(command="ls"), lambda a: calls.append(a))
    assert calls == []
    f = exc.value.decision.findings[0]
    assert f.code == "ENFORCEMENT_ERROR" and f.evidence["executed"] is False


def test_failure_after_execution_withholds_the_result_and_says_it_ran():
    class BrokenAfter(ProtectionPipeline):
        def _finish(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("breaker store down")

    calls = []
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(
            BrokenAfter(), action(command="ls"), lambda a: calls.append(a) or "secret result"
        )
    assert len(calls) == 1
    f = exc.value.decision.findings[0]
    assert f.code == "POST_EXECUTION_ERROR" and f.evidence["executed"] is True


def test_async_path_matches_the_sync_contract():
    p = Recorder()

    async def tool(args: dict[str, Any]) -> str:
        p.order.append("execute")
        return "ok"

    assert asyncio.run(aenforce_call(p, action(command="ls"), tool)) == "ok"
    assert p.order == ["decide", "execute", "inspect_output"]

    calls = []

    async def counted(args: dict[str, Any]) -> None:
        calls.append(args)

    with pytest.raises(ActionBlocked):
        asyncio.run(aenforce_call(ProtectionPipeline(), action(command="rm -rf /"), counted))
    assert calls == []


def test_action_identity_reaches_the_evidence_record(ledger):
    p = ProtectionPipeline(ledger=ledger)
    a = ToolAction(
        agent_id="coder",
        session_id="s1",
        tool="bash",
        arguments={"command": "ls"},
        tenant_id="acme",
        trace_id="trace-7",
        parent_call_id="act-parent",
    )
    enforce_call(p, a, lambda args: "ok")
    acme = type(ledger)(ledger.store, tenant_id="acme")
    rows = acme.query(("protect.decision",))
    assert len(rows) == 1
    declared = rows[0]["declared"]
    assert (
        declared["call_id"] == a.call_id
        and declared["trace_id"] == "trace-7"
        and declared["parent_call_id"] == "act-parent"
    )
    assert rows[0]["payload"]["action"]["tenant_id"] == "acme"
    assert rows[0]["payload"]["policy_version"] is None  # no RBAC policy configured in this test
    assert ledger.query(("protect.decision",)) == [], "nothing recorded under the default tenant"


def test_same_session_id_in_two_tenants_is_two_sessions():
    p = ProtectionPipeline()
    a = ToolAction(
        agent_id="alice-bot",
        session_id="shared",
        tool="bash",
        arguments={"command": "ls"},
        tenant_id="t1",
    )
    b = ToolAction(
        agent_id="bob-bot",
        session_id="shared",
        tool="bash",
        arguments={"command": "ls"},
        tenant_id="t2",
    )
    assert enforce_call(p, a, lambda args: "a") == "a"
    assert enforce_call(p, b, lambda args: "b") == "b", (
        "a session id in another tenant must not be 'owned' by t1's agent"
    )
    intruder = ToolAction(
        agent_id="bob-bot",
        session_id="shared",
        tool="bash",
        arguments={"command": "ls"},
        tenant_id="t1",
    )
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, intruder, lambda args: "x")
    assert exc.value.decision.findings[0].code == "SESSION_AGENT_MISMATCH"


def test_execution_is_recorded_once_per_allowed_call_and_never_for_blocked(ledger):
    p = ProtectionPipeline(ledger=ledger)
    enforce_call(p, action(command="ls"), lambda a: "ok")
    with pytest.raises(ActionBlocked):
        enforce_call(p, action(command="rm -rf /"), lambda a: "never")

    def failing(args: dict[str, Any]) -> str:
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        enforce_call(p, action(command="cat missing.txt"), failing)
    rows = sorted(ledger.query(("protect.execution",)), key=lambda r: r["obs_id"])
    assert [r["payload"]["outcome"] for r in rows] == ["success", "failure"]
    assert rows[1]["payload"]["error"] == "ValueError"
    assert all(r["payload"]["duration_ms"] is not None for r in rows)
    decisions = ledger.query(("protect.decision",))
    assert len(decisions) == 3  # every decision, allowed or not, is recorded once


def test_mutated_arguments_reach_the_tool_and_the_mutation_is_recorded(ledger):
    key = "ghp_" + "a1b2c3d4e5" * 4
    received: list[dict[str, Any]] = []
    p = ProtectionPipeline(ledger=ledger)
    a = ToolAction(
        agent_id="coder",
        session_id="s1",
        tool="write_file",
        arguments={"path": "notes.txt", "content": f"token {key}"},
    )
    enforce_call(p, a, lambda args: received.append(args) or "written")
    assert len(received) == 1
    assert key not in received[0]["content"] and received[0]["path"] == "notes.txt"
    row = ledger.query(("protect.decision",))[0]
    assert row["payload"]["verdict"] == "mutate_and_allow"
    assert row["payload"]["mutation"] == {"changed_paths": ["$.content"]}
    import json

    assert key not in json.dumps(row), "the original secret must not be stored in evidence"


def test_watch_with_protection_routes_tool_calls_through_the_pipeline(ledger):
    from panoptis.core.event_bus import EventBus
    from panoptis.core.watcher import watch

    class Agent:
        def __init__(self) -> None:
            self.ran: list[str] = []

        def run(self, command: str) -> str:
            self.ran.append(command)
            return f"done {command}"

        async def arun(self, command: str) -> str:
            self.ran.append(command)
            return "async done"

    p = ProtectionPipeline(ledger=ledger)
    agent = watch(Agent(), event_bus=EventBus(), agent_id="coder", session_id="w1", protection=p)
    assert agent.run("ls") == "done ls"
    with pytest.raises(ActionBlocked):
        agent.run("rm -rf /")
    assert asyncio.run(agent.arun("pwd")) == "async done"
    assert agent.ran == ["ls", "pwd"]
    sources = {r["payload"]["action"]["source"] for r in ledger.query(("protect.decision",))}
    assert sources == {"adapter:custom"}
    assert {r["payload"]["action"]["agent_id"] for r in ledger.query(("protect.decision",))} == {
        "coder"
    }


def _escalating_pipeline(ledger, approver, timeout_s: float = 2.0):
    from panoptis.protect import ApprovalBroker

    # git push --force is escalated (HIGH) by the threat analyser
    return ProtectionPipeline(
        ledger=ledger,
        approvals=ApprovalBroker(ledger, approver=approver, timeout_s=timeout_s, poll_s=0.01),
    )


def test_escalated_call_runs_once_only_after_approval(ledger):
    order: list[str] = []

    def approver(req):
        order.append("approval")
        return True

    p = _escalating_pipeline(ledger, approver)
    assert (
        enforce_call(
            p,
            action(command="git push --force origin main"),
            lambda a: order.append("execute") or "pushed",
        )
        == "pushed"
    )
    assert order == ["approval", "execute"]
    kinds = [
        r["kind"] for r in ledger.query(("protect.approval.request", "protect.approval.decision"))
    ]
    assert sorted(kinds) == ["protect.approval.decision", "protect.approval.request"]


def test_rejected_escalation_never_executes(ledger):
    calls: list[Any] = []
    p = _escalating_pipeline(ledger, lambda req: False)
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, action(command="git push --force origin main"), lambda a: calls.append(a))
    assert calls == []
    assert any(f.code == "APPROVAL_REJECTED" for f in exc.value.decision.findings)


def test_unanswered_escalation_times_out_and_never_executes(ledger):
    from panoptis.protect import ApprovalBroker, ProtectionConfig

    calls: list[Any] = []
    p = ProtectionPipeline(
        ledger=ledger,
        approvals=ApprovalBroker(ledger, timeout_s=0.1, poll_s=0.01),
        config=ProtectionConfig(approval_timeout_s=0.1),
    )
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, action(command="git push --force origin main"), lambda a: calls.append(a))
    assert calls == []
    assert any(f.code == "APPROVAL_TIMEOUT" for f in exc.value.decision.findings)


def test_approver_edits_are_reinspected_and_reach_the_tool(ledger):
    from panoptis.protect import ApprovalOutcome

    received: list[dict[str, Any]] = []
    edited = ApprovalOutcome(
        approved=True, decided_by="alice", edited_arguments={"command": "git push origin main"}
    )
    p = _escalating_pipeline(ledger, lambda req: edited)
    enforce_call(
        p, action(command="git push --force origin main"), lambda a: received.append(a) or "ok"
    )
    assert received == [{"command": "git push origin main"}]

    # an approver cannot turn the call into something the policy hard-blocks
    bad = ApprovalOutcome(
        approved=True, decided_by="mallory", edited_arguments={"command": "rm -rf /"}
    )
    p2 = _escalating_pipeline(ledger, lambda req: bad)
    with pytest.raises(ActionBlocked):
        enforce_call(
            p2, action(command="git push --force origin main"), lambda a: received.append(a)
        )
    assert len(received) == 1
