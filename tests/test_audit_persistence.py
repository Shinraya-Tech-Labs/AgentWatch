"""Tests for persistent tamper-evident audit logs."""

from __future__ import annotations

from panoptis.governance.audit_log import (
    GENESIS_HASH,
    AuditLog,
    AuditStore,
    InMemoryAuditStore,
    PersistentAuditLog,
    verify_chain,
)


async def test_empty_log_head_is_genesis_and_verifies():
    log = PersistentAuditLog(InMemoryAuditStore())
    assert await log.count() == 0
    assert await log.head_hash() == GENESIS_HASH
    assert await log.verify() is True


async def test_append_persists_and_chains():
    store = InMemoryAuditStore()
    log = PersistentAuditLog(store)

    first = await log.append("role.change", "u1", actor="admin", details={"to": "admin"})
    second = await log.append("policy.set", "team-1")

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.record_hash
    assert await log.head_hash() == second.record_hash
    assert await log.verify() is True


async def test_chain_survives_restart():
    store = InMemoryAuditStore()

    before = PersistentAuditLog(store)
    await before.append("user.add", "u1", details={"role": "viewer"})
    await before.append("role.change", "u1", details={"from": "viewer", "to": "admin"})

    # Restart: a new log object reading the same store.
    after = PersistentAuditLog(store)
    records = await after.records()
    assert [r.action for r in records] == ["user.add", "role.change"]
    assert await after.count() == 2
    assert await after.verify() is True


async def test_append_after_restart_continues_one_chain():
    store = InMemoryAuditStore()
    first_log = PersistentAuditLog(store)
    r0 = await first_log.append("user.add", "u1")

    # New instance reads the prev hash from storage, not genesis.
    second_log = PersistentAuditLog(store)
    r1 = await second_log.append("role.change", "u1")

    assert r1.seq == 1
    assert r1.prev_hash == r0.record_hash
    assert await second_log.verify() is True


async def test_tampering_with_persisted_record_breaks_verification():
    store = InMemoryAuditStore()
    log = PersistentAuditLog(store)
    await log.append("user.add", "u1", details={"role": "viewer"})
    await log.append("role.change", "u1", details={"from": "viewer", "to": "owner"})

    assert await log.verify() is True
    store._records[0].details["role"] = "owner"  # noqa: SLF001 — tamper
    assert await log.verify() is False


async def test_reordering_persisted_records_breaks_verification():
    store = InMemoryAuditStore()
    log = PersistentAuditLog(store)
    await log.append("user.add", "u1")
    await log.append("user.add", "u2")

    store._records.reverse()  # noqa: SLF001 — tamper
    assert await log.verify() is False


async def test_records_are_detached_copies():
    store = InMemoryAuditStore()
    log = PersistentAuditLog(store)
    await log.append("user.add", "u1", details={"role": "viewer"})

    (await log.records())[0].details["role"] = "owner"
    assert await log.verify() is True
    assert (await log.records())[0].details["role"] == "viewer"


async def test_persistent_and_in_memory_produce_identical_chains(monkeypatch):
    # Freeze the timestamp so both logs hash identical inputs.
    import panoptis.governance.audit_log as audit_mod

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            from datetime import datetime as real_datetime

            return real_datetime(2026, 1, 1, tzinfo=tz)

    monkeypatch.setattr(audit_mod, "datetime", _FixedDatetime)

    mem = AuditLog()
    mem.append("user.add", "u1", actor="owner", details={"role": "viewer"})

    persistent = PersistentAuditLog(InMemoryAuditStore())
    await persistent.append("user.add", "u1", actor="owner", details={"role": "viewer"})

    assert mem.records()[0].record_hash == (await persistent.records())[0].record_hash
    assert verify_chain(mem.records())
    assert verify_chain(await persistent.records())


def test_in_memory_store_satisfies_protocol():
    assert isinstance(InMemoryAuditStore(), AuditStore)


def test_sql_store_satisfies_protocol():
    from panoptis.core.models import SqlAlchemyAuditStore

    assert isinstance(SqlAlchemyAuditStore(session=None), AuditStore)  # type: ignore[arg-type]


def test_audit_log_record_model_instantiation():
    from panoptis.core.models import AuditLogRecord

    row = AuditLogRecord(
        seq=0,
        timestamp="2026-01-01T00:00:00+00:00",
        actor="owner",
        action="user.add",
        target="u1",
        details={"role": "viewer"},
        prev_hash=GENESIS_HASH,
        record_hash="a" * 64,
    )
    assert row.seq == 0
    assert row.action == "user.add"
