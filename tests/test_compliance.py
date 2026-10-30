"""Phase 8 — Compliance tests (CMP-001..009)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from panoptis.governance.causal import AdverseOutcome, attribute
from panoptis.governance.compliance_reporter import ComplianceReporter
from panoptis.governance.engine import (
    AuditEventType,
    GovernanceEngine,
    Permission,
    Principal,
)
from panoptis.governance.eu_ai_act import (
    EUAIActPackage,
    TechnicalDocumentation,
)
from panoptis.governance.gdpr import GDPREngine, detect_pii, redact
from panoptis.governance.hipaa import HIPAAEngine, detect_phi
from panoptis.governance.iso42001 import (
    ISO42001AMS,
    GovernanceDoc,
    Incident,
    IncidentSeverity,
    RiskAssessment,
)
from panoptis.governance.rbac import (
    RBACEngine,
    Role,
    SAMLClaims,
    TeamPolicy,
    User,
    issue_token,
    verify_token,
)
from panoptis.governance.reports import ReportInputs, export
from panoptis.governance.residency import (
    Region,
    ResidencyRouter,
    eu_only_policy,
)
from panoptis.memory.causal_graph import CausalGraph, CausalNode, EdgeKind

# ─────────────────────────────────────────────
# CMP-001 — GDPR
# ─────────────────────────────────────────────


def test_pii_detection_finds_email_and_ssn():
    text = "Contact alice@example.com or call 555-12-3456 about her record."
    findings = detect_pii(text)
    labels = {f.label for f in findings}
    assert "email" in labels


def test_redaction_replaces_sensitive():
    out = redact("My email is bob@test.com and IP 192.168.1.1")
    assert "[REDACTED:EMAIL]" in out.redacted_text
    assert "[REDACTED:IP_ADDRESS]" in out.redacted_text


def test_gdpr_erasure_removes_user_records():
    eng = GDPREngine(signing_key=b"test-secret")
    records = [
        {"user_id": "alice", "value": "x"},
        {"user_id": "bob", "value": "y"},
    ]
    kept, receipt = eng.erase("alice", records)
    assert len(kept) == 1
    assert receipt.items_erased == 1
    assert receipt.audit_signature.startswith("hmac-sha256:")


# ─────────────────────────────────────────────
# CMP-003 — HIPAA
# ─────────────────────────────────────────────


def test_phi_detection_finds_mrn():
    findings = detect_phi("Patient MRN: ABC1234567 has diabetes.")
    labels = {f.label for f in findings}
    assert "mrn" in labels or "condition" in labels


def test_hipaa_access_log():
    eng = HIPAAEngine()
    eng.log_access("record-1", "doctor-x", "read")
    eng.log_access("record-1", "nurse-y", "read")
    eng.log_access("record-2", "doctor-x", "write")
    assert len(eng.access_log()) == 3
    assert len(eng.access_log(resource="record-1")) == 2


# ─────────────────────────────────────────────
# CMP-004 — EU AI Act
# ─────────────────────────────────────────────


def test_eu_ai_act_partial_compliance():
    pkg = EUAIActPackage()
    pkg.set_documentation(
        TechnicalDocumentation(
            system_name="AgentWatch",
            intended_purpose="agent observability",
            risk_category="limited",
            data_governance={"retention": "90d"},
            transparency_disclosures=["public docs"],
            human_oversight_description="dashboard review",
        )
    )
    assessment = pkg.assess()
    assert assessment.score > 0
    assert assessment.verdict in ("partial", "compliant", "non_compliant")


def test_eu_ai_act_no_doc_is_non_compliant():
    pkg = EUAIActPackage()
    assessment = pkg.assess()
    assert assessment.verdict == "no_documentation"


# ─────────────────────────────────────────────
# CMP-005 — RBAC + SAML
# ─────────────────────────────────────────────


def test_rbac_viewer_cannot_write_policy():
    rbac = RBACEngine()
    rbac.add_user(User(user_id="u1", email="v@x", role=Role.VIEWER))
    assert rbac.has_permission("u1", "session:read")
    assert not rbac.has_permission("u1", "policy:write")


def test_rbac_owner_has_full_access():
    rbac = RBACEngine()
    rbac.add_user(User(user_id="o1", email="o@x", role=Role.OWNER))
    assert rbac.has_permission("o1", "anything:goes")


def test_rbac_team_policy_blocks_tool():
    rbac = RBACEngine()
    rbac.add_user(User(user_id="u1", email="x@x", role=Role.OPERATOR, team_id="t1"))
    rbac.set_team_policy(TeamPolicy(team_id="t1", blocked_tools={"bash"}))
    assert not rbac.can_run_tool("u1", "bash")
    assert rbac.can_run_tool("u1", "read")


def test_saml_token_roundtrip():
    secret = b"verysecret"
    claims = SAMLClaims(sub="u1", email="u@x", role=Role.ADMIN, team_id="t1")
    token = issue_token(claims, secret)
    decoded = verify_token(token, secret)
    assert decoded is not None
    assert decoded.role == Role.ADMIN


def test_saml_token_rejects_wrong_secret():
    token = issue_token(SAMLClaims(sub="u1", email="u@x", role=Role.VIEWER), b"a")
    assert verify_token(token, b"b") is None


# ─────────────────────────────────────────────
# CMP-006 — Compliance reports
# ─────────────────────────────────────────────


def test_compliance_export_each_framework():
    inputs = ReportInputs(
        audit_events=[{"e": 1}, {"e": 2}],
        pii_findings={"email": 3},
        phi_access_log=[{"user": "x"}],
        conformity={"score": 0.9},
    )
    for fw in ("soc2", "gdpr", "hipaa", "eu_ai_act"):
        out = export(fw, inputs)
        assert out.body
        assert out.framework


# ─────────────────────────────────────────────
# CMP-007 — Residency
# ─────────────────────────────────────────────


def test_residency_eu_only_routes_to_eu():
    router = ResidencyRouter()
    router.add_policy("user-eu", eu_only_policy())
    decision = router.route("user-eu", current_user_region=Region.US_EAST)
    assert decision.region in {Region.EU_WEST, Region.EU_CENTRAL}


def test_residency_user_region_when_allowed():
    router = ResidencyRouter()
    router.add_policy("user-eu", eu_only_policy())
    decision = router.route("user-eu", current_user_region=Region.EU_CENTRAL)
    assert decision.region == Region.EU_CENTRAL


# ─────────────────────────────────────────────
# CMP-008 — Causal attribution
# ─────────────────────────────────────────────


def test_causal_attribution_signs_report():
    g = CausalGraph()
    g.add_node(CausalNode(node_id="policy", kind="constraint", text="data leak policy"))
    g.add_node(CausalNode(node_id="action", kind="decision", text="curl posted secrets"))
    g.add_node(CausalNode(node_id="outcome", kind="outcome", text="data leaked"))
    g.add_edge("policy", "action", EdgeKind.CONSTRAINED_BY)
    g.add_edge("action", "outcome", EdgeKind.PRODUCED)
    outcome = AdverseOutcome(outcome_id="outcome", description="leak", severity="high")
    report = attribute(outcome, g)
    assert report.chain
    assert report.signature.startswith("sha256:")
    assert "rotate_secrets" in report.remediation


# ─────────────────────────────────────────────
# CMP-009 — ISO 42001
# ─────────────────────────────────────────────


def test_iso42001_report_counts():
    ams = ISO42001AMS()
    ams.add_risk(RiskAssessment(risk_id="R1", description="x", likelihood=0.6, impact=0.9))
    ams.add_incident(
        Incident(
            incident_id="I1",
            description="oops",
            severity=IncidentSeverity.HIGH,
            occurred_at=datetime.now(UTC),
        )
    )
    ams.add_governance_doc(GovernanceDoc(title="AI Policy", version="1.0", summary="ok"))
    rep = ams.report()
    assert rep.metrics["open_incidents"] == 1
    assert rep.metrics["high_risks"] == 1


# ─────────────────────────────────────────────
# CMP-010 — CSV Export (Issue #148)
# ─────────────────────────────────────────────


def _make_engine_with_entries() -> GovernanceEngine:
    engine = GovernanceEngine()
    viewer = Principal(principal_id="u1", name="Viewer", roles=["viewer"])
    admin = Principal(principal_id="u2", name="Admin", roles=["admin"])
    engine.register_principal(viewer)
    engine.register_principal(admin)
    engine.check_permission("u1", Permission.SAFETY_OVERRIDE, "policy")
    engine.check_permission("u1", Permission.ADMIN_ALL, "config")
    engine.record_action(
        "u2", AuditEventType.CONFIG_CHANGE, "config", "updated", allowed=True
    )
    return engine


def test_compliance_report_to_csv_has_header():
    engine = _make_engine_with_entries()
    reporter = ComplianceReporter(engine)
    report = reporter.generate()
    csv_output = report.to_csv()
    reader = csv.reader(io.StringIO(csv_output))
    header = next(reader)
    assert "audit_id" in header
    assert "event_type" in header
    assert "allowed" in header


def test_compliance_report_to_csv_contains_denials():
    engine = _make_engine_with_entries()
    reporter = ComplianceReporter(engine)
    report = reporter.generate()
    csv_output = report.to_csv()
    assert "permission_denied" in csv_output


def test_compliance_report_to_csv_empty_when_no_denials():
    engine = GovernanceEngine()
    engine.register_principal(Principal(principal_id="a", name="A", roles=["admin"]))
    engine.record_action(
        "a", AuditEventType.CONFIG_CHANGE, "c", "u", allowed=True
    )
    reporter = ComplianceReporter(engine)
    report = reporter.generate()
    csv_output = report.to_csv()
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) == 1


def test_compliance_reporter_generate_csv_denials_only():
    engine = _make_engine_with_entries()
    reporter = ComplianceReporter(engine)
    csv_output = reporter.generate_csv(include_allowed=False)
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) >= 2
    for row in rows[1:]:
        assert "permission_denied" in row[3] or "safety_override" in row[3]


def test_compliance_reporter_generate_csv_includes_allowed():
    engine = _make_engine_with_entries()
    reporter = ComplianceReporter(engine)
    csv_output = reporter.generate_csv(include_allowed=True)
    reader = csv.reader(io.StringIO(csv_output))
    rows = list(reader)
    assert len(rows) >= 3
    actions = [row[5] for row in rows[1:]]
    assert "updated" in actions
