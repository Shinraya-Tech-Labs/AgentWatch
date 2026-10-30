"""CMP-005 — tamper-evident audit log for RBAC / policy changes (issue #395)."""

from __future__ import annotations

from panoptis.governance.audit_log import GENESIS_HASH, AuditLog
from panoptis.governance.rbac import RBACEngine, Role, TeamPolicy, User

# ── AuditLog hash chain ────────────────────────────────────────────────────


def test_empty_log_verifies_and_head_is_genesis():
    log = AuditLog()
    assert len(log) == 0
    assert log.head_hash == GENESIS_HASH
    assert log.verify()


def test_append_chains_records():
    log = AuditLog()
    first = log.append("role.change", "u1", actor="admin", details={"to": "admin"})
    second = log.append("policy.set", "team-1")

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.record_hash
    assert log.head_hash == second.record_hash
    assert log.verify()


def test_tampering_with_a_record_breaks_verification():
    log = AuditLog()
    log.append("user.add", "u1", details={"role": "viewer"})
    log.append("role.change", "u1", details={"from": "viewer", "to": "owner"})

    assert log.verify()
    # Mutate a stored record's payload — its hash no longer matches.
    log._records[0].details["role"] = "owner"  # noqa: SLF001 — simulate tampering
    assert not log.verify()


def test_records_returns_detached_copies():
    log = AuditLog()
    log.append("user.add", "u1", details={"role": "viewer"})

    # Mutating a returned record must not corrupt the stored chain.
    log.records()[0].details["role"] = "owner"
    assert log.verify()
    assert log.records()[0].details["role"] == "viewer"


def test_reordering_breaks_verification():
    log = AuditLog()
    log.append("user.add", "u1")
    log.append("user.add", "u2")

    log._records.reverse()  # noqa: SLF001 — simulate tampering by reordering
    assert not log.verify()


# ── RBACEngine integration ─────────────────────────────────────────────────


def test_add_user_is_audited():
    engine = RBACEngine()
    engine.add_user(User("u1", "u1@x.com", Role.VIEWER), actor="owner")

    records = engine.audit.records()
    assert len(records) == 1
    assert records[0].action == "user.add"
    assert records[0].target == "u1"
    assert records[0].actor == "owner"
    assert records[0].details["role"] == "viewer"
    assert engine.audit.verify()


def test_role_change_is_audited_with_before_and_after():
    engine = RBACEngine()
    engine.add_user(User("u1", "u1@x.com", Role.VIEWER))

    assert engine.set_role("u1", Role.ADMIN, actor="owner") is True
    assert engine.get_user("u1").role == Role.ADMIN

    change = engine.audit.records()[-1]
    assert change.action == "role.change"
    assert change.details == {"from": "viewer", "to": "admin"}
    assert engine.audit.verify()


def test_set_role_unknown_user_is_noop():
    engine = RBACEngine()
    assert engine.set_role("ghost", Role.ADMIN) is False
    assert len(engine.audit) == 0


def test_team_policy_change_is_audited():
    engine = RBACEngine()
    engine.set_team_policy(TeamPolicy("team-1", allowed_tools={"bash"}), actor="admin")

    record = engine.audit.records()[-1]
    assert record.action == "policy.set"
    assert record.target == "team-1"
    assert record.details["allowed_tools"] == ["bash"]
    assert engine.audit.verify()


def test_full_change_history_chain_is_intact():
    engine = RBACEngine()
    engine.add_user(User("u1", "u1@x.com", Role.VIEWER), actor="owner")
    engine.set_role("u1", Role.OPERATOR, actor="owner")
    engine.set_team_policy(TeamPolicy("team-1"), actor="admin")

    assert len(engine.audit) == 3
    assert engine.audit.verify()
