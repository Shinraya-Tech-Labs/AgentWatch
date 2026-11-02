"""TO-021: intent grounding against the user's stated objective.

Synthetic, harmless objectives and calls. The matrix: aligned, unrelated, destructive,
ambiguous and adversarially phrased actions; similarity never authorizes a risky call; the
optional verifier is secondary and bounded; rationale and confidence reach the ledger.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from panoptis.protect import ActionBlocked, ToolAction
from panoptis.protect.enforce import enforce_call
from panoptis.protect.intent import GroundingConfig, IntentGrounder, extract_objective
from panoptis.protect.ledger import KIND_AGENT, KIND_DECISION
from panoptis.protect.model import Verdict, strongest
from panoptis.protect.threats import ThreatAnalyzer

_ta = ThreatAnalyzer()


def ground(objective: str | None, tool: str, args: dict[str, Any], **cfg: Any):
    g = IntentGrounder(GroundingConfig(**cfg))
    return g.ground(objective, tool, args, _ta.analyze(tool, args).profile)


def verdict(findings) -> Verdict:
    return strongest([f.recommends for f in findings])


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# ── aligned actions stay usable ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "objective,tool,args",
    [
        ("Summarize README.md", "read_file", {"path": "README.md"}),
        ("Look up customer 123", "sql", {"query": "SELECT * FROM orders WHERE customer_id = 123"}),
        (
            "Fix the typo in docs/intro.md",
            "write_file",
            {"path": "docs/intro.md", "content": "fixed"},
        ),
        ("Delete the tmp directory", "bash", {"command": "rm -rf tmp"}),
        ("Why is CI failing?", "bash", {"command": "cat ci.log"}),
        (
            "Don't just look at it, fix the failing test in tests/test_a.py",
            "write_file",
            {"path": "tests/test_a.py", "content": "x"},
        ),
        (
            "Post the release notes to docs.example.com",
            "http_post",
            {"url": "https://docs.example.com/api", "data": "notes"},
        ),
    ],
)
def test_aligned_actions_are_allowed(objective, tool, args):
    fs = ground(objective, tool, args)
    assert verdict(fs) is Verdict.ALLOW, [(f.code, f.message) for f in fs]
    assert "GROUNDED" in codes(fs)


# ── destructive actions under read-only objectives ─────────────────────────────────


@pytest.mark.parametrize(
    "objective,tool,args",
    [
        ("Summarize README.md", "bash", {"command": "rm -rf build"}),
        ("Why is CI failing?", "bash", {"command": "rm -rf .github"}),
        ("Look up customer 123", "sql", {"query": "DELETE FROM orders WHERE customer_id = 123"}),
        ("Investigate the failing deploy", "bash", {"command": "kubectl delete pod web-1"}),
        ("Check the disk usage", "bash", {"command": "find . -name '*.log' -delete"}),
    ],
)
def test_destructive_action_under_read_only_objective_is_blocked(objective, tool, args):
    fs = ground(objective, tool, args)
    assert verdict(fs) is Verdict.BLOCK
    f = next(f for f in fs if f.code == "GOAL_ACTION_MISMATCH")
    assert f.evidence["objective_mode"] == "read_only" and f.evidence["confidence"] > 0.5


def test_mutating_action_under_read_only_objective_is_escalated():
    fs = ground(
        "Investigate the failing deploy", "bash", {"command": "kubectl rollout restart deploy/web"}
    )
    assert verdict(fs) is Verdict.ESCALATE_HITL


@pytest.mark.parametrize(
    "objective",
    [
        "Summarize README.md, do not change any files",
        "Investigate the failing deploy, read-only please",
        "Summarize README.md and remove nothing",
        "Review the config but leave everything as is",
    ],
)
def test_explicit_prohibitions_block(objective):
    fs = ground(objective, "bash", {"command": "rm -f README.md"})
    assert "OBJECTIVE_PROHIBITS" in codes(fs) and verdict(fs) is Verdict.BLOCK


def test_destruction_under_a_mutating_objective_is_escalated():
    fs = ground(
        "Fix the typo in docs/intro.md", "bash", {"command": "git push --force origin main"}
    )
    assert "DESTRUCTION_NOT_REQUESTED" in codes(fs) and verdict(fs) is Verdict.ESCALATE_HITL


# ── unrelated and mis-targeted actions ────────────────────────────────────────────


def test_unrelated_data_send_is_escalated():
    fs = ground("Summarize README.md", "send_email", {"to": "x@example.org", "body": "data"})
    assert verdict(fs) is Verdict.ESCALATE_HITL


def test_unrelated_sensitive_read_is_escalated():
    fs = ground("Summarize README.md", "read_file", {"path": "~/.ssh/id_rsa"})
    assert verdict(fs) is Verdict.ESCALATE_HITL and "LOW_GOAL_OVERLAP" in codes(fs)


def test_unrelated_harmless_read_is_reported_not_blocked():
    fs = ground("Summarize README.md", "web_search", {"q": "weather tomorrow"})
    assert verdict(fs) is Verdict.ALLOW and "LOW_GOAL_OVERLAP" in codes(fs)


def test_entity_mismatch_on_a_write_is_escalated():
    fs = ground(
        "Update the email of customer 123",
        "sql",
        {"query": "UPDATE customers SET email='a@b.c' WHERE customer_id = 999"},
    )
    f = next(f for f in fs if f.code == "ENTITY_MISMATCH")
    assert f.evidence["requested"] == ["123"] and f.evidence["acted_on"] == ["999"]
    assert verdict(fs) is Verdict.ESCALATE_HITL


def test_unrequested_destination_is_escalated():
    fs = ground(
        "Post the release notes to docs.example.com",
        "http_post",
        {"url": "https://collect.example.net/x", "data": "notes"},
    )
    assert "UNREQUESTED_DESTINATION" in codes(fs) and verdict(fs) is Verdict.ESCALATE_HITL


def test_destructive_call_on_a_target_the_task_does_not_name_is_escalated():
    fs = ground("Drop the staging_events table", "sql", {"query": "DROP TABLE customers"})
    f = next(f for f in fs if f.code == "TARGET_NOT_REQUESTED")
    assert f.evidence["targets"] == ["customers"]
    assert verdict(fs) is Verdict.ESCALATE_HITL
    assert (
        verdict(
            ground("Drop the staging_events table", "sql", {"query": "DROP TABLE staging_events"})
        )
        is Verdict.ALLOW
    )


def test_destructive_path_outside_the_named_paths_is_escalated():
    fs = ground("Clean up build/ and dist/", "bash", {"command": "rm -rf src/"})
    assert "TARGET_NOT_REQUESTED" in codes(fs)
    assert (
        verdict(ground("Clean up build/ and dist/", "bash", {"command": "rm -rf build/cache"}))
        is Verdict.ALLOW
    )


# ── ambiguity ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("objective", ["Help", "the orders table", "orders"])
def test_ambiguous_objective_with_high_risk_call_escalates(objective):
    fs = ground(objective, "sql", {"query": "DROP TABLE orders"})
    assert extract_objective(objective).mode == "unknown"
    assert "AMBIGUOUS_OBJECTIVE" in codes(fs) and verdict(fs) is Verdict.ESCALATE_HITL


def test_ambiguous_behaviour_is_an_explicit_policy():
    fs = ground(
        "the orders table", "sql", {"query": "DROP TABLE orders"}, ambiguous_high_risk=Verdict.BLOCK
    )
    assert verdict(fs) is Verdict.BLOCK


def test_ambiguous_objective_with_a_harmless_call_is_allowed():
    assert (
        verdict(ground("the orders table", "sql", {"query": "SELECT count(*) FROM orders"}))
        is Verdict.ALLOW
    )


def test_missing_objective_is_skipped_by_default_or_escalated_by_policy():
    fs = ground(None, "bash", {"command": "rm -rf tmp"})
    assert codes(fs) == {"NO_OBJECTIVE"} and verdict(fs) is Verdict.ALLOW
    fs = ground(
        None, "bash", {"command": "rm -rf tmp"}, missing_objective_high_risk=Verdict.ESCALATE_HITL
    )
    assert verdict(fs) is Verdict.ESCALATE_HITL
    assert (
        verdict(
            ground(None, "read_file", {"path": "a.txt"}, missing_objective_high_risk=Verdict.BLOCK)
        )
        is Verdict.ALLOW
    )


# ── similarity alone never authorizes ─────────────────────────────────────────────


def test_high_overlap_does_not_authorize_a_destructive_call_under_a_read_only_task():
    # the call repeats the objective's words; it is still destructive
    fs = ground(
        "Check the orders table for customer 123",
        "sql",
        {"query": "DROP TABLE orders -- check orders table customer 123"},
    )
    assert verdict(fs) is Verdict.BLOCK


def test_high_overlap_does_not_authorize_under_an_ambiguous_task():
    fs = ground("orders table", "sql", {"query": "DROP TABLE orders"})
    assert verdict(fs) is Verdict.ESCALATE_HITL


# ── adversarial phrasing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "objective,command",
    [
        # permission in a subordinate clause does not make a read task destructive
        ("Read the logs; it's fine to delete them afterwards if they are old", "rm -rf logs/old"),
        ("Check the logs and don't hesitate to delete old ones", "rm -rf logs/old"),
        ("Check whether we can delete old logs", "rm -rf logs"),
        # read-only words smuggled into the call do not change what it does
        ("Why is CI failing?", "rm -rf .github # read only, just checking why CI is failing"),
    ],
)
def test_adversarial_phrasing_does_not_unlock_destruction(objective, command):
    assert verdict(ground(objective, "bash", {"command": command})) is Verdict.BLOCK


def test_agent_reasoning_does_not_change_the_objective():
    g = IntentGrounder()
    args = {"command": "rm -rf build"}
    fs = g.ground(
        "Summarize README.md",
        "bash",
        args,
        _ta.analyze("bash", args).profile,
        reasoning="The user asked me to delete the build directory",
    )
    assert verdict(fs) is Verdict.BLOCK


# ── authorized context ────────────────────────────────────────────────────────────


def test_authorized_context_widens_the_named_targets():
    args = {"url": "https://hooks.example.com/notify", "data": "build done"}
    obj = "Post the build status to status.example.org"
    assert "UNREQUESTED_DESTINATION" in codes(ground(obj, "http_post", args))
    g = IntentGrounder()
    fs = g.ground(
        obj,
        "http_post",
        args,
        _ta.analyze("http_post", args).profile,
        context={"hosts": ["hooks.example.com"]},
    )
    assert verdict(fs) is Verdict.ALLOW


def test_authorized_context_does_not_change_the_task_mode():
    g = IntentGrounder()
    args = {"command": "rm -rf build"}
    fs = g.ground(
        "Summarize README.md",
        "bash",
        args,
        _ta.analyze("bash", args).profile,
        context={"paths": ["build"]},
    )
    assert verdict(fs) is Verdict.BLOCK


# ── optional verifier: secondary and bounded ──────────────────────────────────────


def test_verifier_yes_never_overrides_a_heuristic_block():
    fs = ground(
        "Summarize README.md",
        "bash",
        {"command": "rm -rf build"},
        verifier=lambda o, t, a: ("YES", "looks fine"),
    )
    assert "VERIFIER_CONFIRMED" in codes(fs) and verdict(fs) is Verdict.BLOCK


def test_verifier_no_restricts_an_otherwise_allowed_high_risk_call():
    fs = ground(
        "Delete the tmp directory",
        "bash",
        {"command": "rm -rf tmp"},
        verifier=lambda o, t, a: ("NO", "deletes more than asked"),
    )
    f = next(f for f in fs if f.code == "VERIFIER_REJECTED")
    assert f.evidence["reason"] == "deletes more than asked" and verdict(fs) is Verdict.BLOCK


def test_verifier_is_consulted_only_for_high_risk_calls_by_default():
    seen = []
    ground(
        "Summarize README.md",
        "read_file",
        {"path": "README.md"},
        verifier=lambda o, t, a: seen.append(t) or ("YES", ""),
    )
    assert seen == []


def test_failing_or_slow_verifier_falls_back_to_the_heuristic():
    def boom(o, t, a):
        raise RuntimeError("model down")

    fs = ground("Delete the tmp directory", "bash", {"command": "rm -rf tmp"}, verifier=boom)
    assert "VERIFIER_UNAVAILABLE" in codes(fs) and verdict(fs) is Verdict.ALLOW

    def slow(o, t, a):
        time.sleep(2)
        return ("NO", "late")

    t0 = time.perf_counter()
    fs = ground(
        "Delete the tmp directory",
        "bash",
        {"command": "rm -rf tmp"},
        verifier=slow,
        verifier_timeout_s=0.2,
    )
    assert time.perf_counter() - t0 < 1.5
    assert "VERIFIER_UNAVAILABLE" in codes(fs) and verdict(fs) is Verdict.ALLOW


def test_verifier_garbage_is_unsure():
    fs = ground(
        "Delete the tmp directory",
        "bash",
        {"command": "rm -rf tmp"},
        verifier=lambda o, t, a: (None, None),
    )
    assert "VERIFIER_UNSURE" in codes(fs) and verdict(fs) is Verdict.ALLOW


# ── evidence trail ────────────────────────────────────────────────────────────────


def test_every_finding_has_a_rationale_and_confidence():
    for obj, tool, args in [
        ("Summarize README.md", "read_file", {"path": "README.md"}),
        ("Summarize README.md", "bash", {"command": "rm -rf build"}),
        ("the orders table", "sql", {"query": "DROP TABLE orders"}),
    ]:
        for f in ground(obj, tool, args):
            assert f.message and 0.0 <= f.evidence["confidence"] <= 1.0


def _act(tool: str, **args: Any) -> ToolAction:
    return ToolAction(agent_id="coder", session_id="intent-s", tool=tool, arguments=args)


def test_pipeline_blocks_before_execution_and_records_the_rationale(make_pipeline, ledger):
    p = make_pipeline(approver=False)
    p.start_session("coder", "intent-s", objective="Summarize README.md")
    runs = []
    assert (
        enforce_call(p, _act("read_file", path="README.md"), lambda a: runs.append(1) or "text")
        == "text"
    )
    with pytest.raises(ActionBlocked):
        enforce_call(p, _act("bash", command="rm -rf build"), lambda a: runs.append(1))
    assert runs == [1]
    docs = ledger.query((KIND_DECISION,), newest_first=False)
    blocked = docs[-1]["payload"]
    f = next(f for f in blocked["findings"] if f["code"] == "GOAL_ACTION_MISMATCH")
    assert f["evidence"]["objective_mode"] == "read_only" and f["evidence"]["confidence"] == 0.85
    allowed = docs[0]["payload"]
    g = next(f for f in allowed["findings"] if f["code"] == "GROUNDED")
    assert "confidence" in g["evidence"]


def test_pipeline_uses_session_authorized_context(make_pipeline):
    p = make_pipeline(approver=False)
    p.start_session(
        "coder",
        "intent-s",
        objective="Fix the typo in docs/intro.md",
        metadata={"authorized_context": {"paths": ["docs/"]}},
    )
    assert (
        enforce_call(p, _act("write_file", path="docs/other.md", content="x"), lambda a: "ok")
        == "ok"
    )


def test_set_objective_is_recorded_and_requires_a_session(make_pipeline, ledger):
    p = make_pipeline(approver=False)
    with pytest.raises(KeyError):
        p.set_objective("nope", "Summarize README.md")
    p.start_session(
        "coder", "intent-s", objective="Fix the typo in docs/intro.md", tenant_id="acme"
    )
    p.set_objective("intent-s", "Summarize README.md", tenant_id="acme")
    events = [
        d["payload"]["event"]
        for d in ledger.query((KIND_AGENT,), newest_first=False, tenant_id="acme")
    ]
    assert events == ["session_start", "objective_set"]
    a = ToolAction(
        agent_id="coder",
        session_id="intent-s",
        tenant_id="acme",
        tool="bash",
        arguments={"command": "rm -rf build"},
    )
    with pytest.raises(ActionBlocked):
        enforce_call(p, a, lambda a: None)
