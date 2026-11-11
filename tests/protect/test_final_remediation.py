"""Regressions for INDEPENDENT_TECHNICAL_EVALUATION.md findings 4, 5 and 8."""

from __future__ import annotations

import json
import math
import os
import struct
import uuid

import pytest
from fastapi.testclient import TestClient

from panoptis.api import server
from panoptis.evidence import jcs
from panoptis.governance.gdpr import (
    CrossSessionErasureService,
    ErasureRequest,
    ErasureScope,
    PartialErasureError,
)
from panoptis.memory.governance import drop_memory_entries, register_memory_store
from panoptis.protect import ActionBlocked, ProtectionConfig, ProtectionPipeline, ToolAction
from panoptis.protect.enforce import enforce_call
from panoptis.protect.ltl_trajectory import LTLTrajectoryVerifier
from panoptis.protect.stego_neutralizer import StegoNeutralizer

REVERSE_SHELL = "import socket, subprocess\ns = socket.socket()\nsubprocess.Popen(['/bin/sh'])\n"
SESSION = {"session_id": "s", "tenant_id": "t", "agent_id": "a"}


def _write(ltl, path, content="hello", **session):
    ltl.record_step(
        ToolAction(
            tool="write_file", arguments={"path": path, "content": content}, **(session or SESSION)
        )
    )


def _codes(ltl, tool="bash", arguments=None):
    action = ToolAction(tool=tool, arguments=arguments or {"command": "ls"}, **SESSION)
    return {f.code for f in ltl.verify_action(action)}


# ── trajectory memory ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_steps_per_session": 1},
        {"max_steps_per_session": 2},
        {"max_steps_per_session": 0},
        {"max_sessions": 0},
        {"max_files_per_session": 0},
        {"max_tracked_files": 0},
    ],
)
def test_limits_that_cannot_hold_the_invariants_are_refused(kwargs):
    with pytest.raises(ValueError):
        LTLTrajectoryVerifier(**kwargs)


@pytest.mark.parametrize("limit", [3, 4, 5, 10, 101])
def test_a_session_never_exceeds_its_step_limit(limit):
    ltl = LTLTrajectoryVerifier(max_steps_per_session=limit)
    ltl.record_step(ToolAction(tool="read_file", arguments={"path": ".env"}, **SESSION))
    ltl.record_step(ToolAction(tool="get_issue", arguments={"id": 1}, **SESSION))
    for i in range(limit * 4):
        ltl.record_step(ToolAction(tool="list_dir", arguments={"path": str(i)}, **SESSION))
        assert len(ltl.traces["t/s"]) <= limit
    assert any(s.reads_sensitive for s in ltl.traces["t/s"])
    assert any(s.untrusted_ingest for s in ltl.traces["t/s"])


def test_unique_path_flooding_is_bounded_and_escalates_execution():
    ltl = LTLTrajectoryVerifier(max_files_per_session=50)
    for i in range(5_000):
        _write(ltl, f"gen/{i}.txt")
    files = ltl.modified_files["t/s"]
    assert len(files) <= 51  # the cap plus the single overflow entry
    assert "LTL_FILE_TRACKING_OVERFLOW" in _codes(ltl)


def test_a_session_under_its_cap_is_not_escalated():
    ltl = LTLTrajectoryVerifier(max_files_per_session=50)
    for i in range(50):
        _write(ltl, f"gen/{i}.txt")
    assert "LTL_FILE_TRACKING_OVERFLOW" not in _codes(ltl)


def test_danger_written_past_the_cap_is_still_seen():
    ltl = LTLTrajectoryVerifier(max_files_per_session=2)
    _write(ltl, "a")
    _write(ltl, "b")
    _write(ltl, "hidden.py", REVERSE_SHELL)
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in _codes(ltl)


def test_the_tenant_path_cap_bounds_many_sessions():
    ltl = LTLTrajectoryVerifier(max_files_per_session=100, max_tracked_files=10)
    for s in range(30):
        _write(ltl, f"f{s}", session_id=f"s{s}", tenant_id="t", agent_id="a")
    # the cap plus one overflow entry for each of the 20 sessions past it
    assert ltl.capacity("t")["tracked_files"] == 10 + 20


def test_an_overflowed_session_cannot_be_evicted():
    ltl = LTLTrajectoryVerifier(max_sessions=3, max_files_per_session=1)
    _write(ltl, "a")
    _write(ltl, "b")
    for i in range(10):
        _write(ltl, "f", session_id=f"other-{i}", tenant_id="t", agent_id="a")
    assert "t/s" not in ltl.traces  # ordinary history can go
    assert "t/s" in ltl.modified_files  # security state cannot
    assert "LTL_FILE_TRACKING_OVERFLOW" in _codes(ltl)


def test_the_tenant_cap_flags_only_the_session_that_reached_it():
    ltl = LTLTrajectoryVerifier(max_files_per_session=100, max_tracked_files=4)
    for s in ("s0", "s1"):
        for f in ("a", "b"):
            _write(ltl, f, session_id=s, tenant_id="t", agent_id="a")
    assert ltl.capacity("t")["tracked_files"] == 4

    _write(ltl, "c", session_id="s2", tenant_id="t", agent_id="a")

    assert len(ltl.modified_files["t/s2"]) == 1 < ltl.max_files_per_session
    assert list(ltl.modified_files["t/s2"]) == ["<untracked paths>"]
    run = {"tool": "bash", "arguments": {"command": "ls"}, "tenant_id": "t", "agent_id": "a"}
    late = ltl.verify_action(ToolAction(session_id="s2", **run))
    early = ltl.verify_action(ToolAction(session_id="s0", **run))
    assert "LTL_FILE_TRACKING_OVERFLOW" in {f.code for f in late}
    assert "LTL_FILE_TRACKING_OVERFLOW" not in {f.code for f in early}


def test_a_full_tenant_budget_does_not_hide_danger_from_the_session_past_it():
    ltl = LTLTrajectoryVerifier(max_files_per_session=100, max_tracked_files=1)
    _write(ltl, "a", session_id="first", tenant_id="t", agent_id="a")
    _write(ltl, "n.py", "s = socket.socket()")
    _write(ltl, "e.py", "subprocess.Popen('sh')")
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in _codes(ltl)


def test_dangerous_halves_in_separate_overflow_writes_are_combined():
    ltl = LTLTrajectoryVerifier(max_files_per_session=1)
    _write(ltl, "first")
    _write(ltl, "net.py", "s = socket.socket()")
    _write(ltl, "spawn.py", "subprocess.Popen(['/bin/sh'])")
    _write(ltl, "readme.md", "nothing to see here")
    assert list(ltl.modified_files["t/s"]) == ["first", "<untracked paths>"]
    assert {"LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED", "LTL_FILE_TRACKING_OVERFLOW"} <= _codes(ltl)


def test_an_indicator_split_across_two_overflow_writes_is_seen():
    ltl = LTLTrajectoryVerifier(max_files_per_session=1)
    _write(ltl, "first")
    _write(ltl, "one.py", "import socket; s = socket.so")
    _write(ltl, "two.py", "cket(); subprocess.Popen('sh')")
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in _codes(ltl)


def test_an_overflow_write_does_not_clear_danger_already_tracked():
    ltl = LTLTrajectoryVerifier(max_files_per_session=1)
    _write(ltl, "stager.py", REVERSE_SHELL)
    _write(ltl, "other.py", "harmless")
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in _codes(ltl)


def _flood(ltl, sessions=10):
    for i in range(sessions):
        _write(ltl, "f", session_id=f"other-{i}", tenant_id="t", agent_id="a")


def test_a_session_that_read_something_sensitive_survives_eviction():
    ltl = LTLTrajectoryVerifier(max_sessions=3)
    ltl.record_step(ToolAction(tool="read_file", arguments={"path": ".env"}, **SESSION))
    _flood(ltl)
    assert "t/s" not in ltl.traces  # ordinary history can go; the rule below needs none of it
    assert "LTL_MULTI_HOP_EXFILTRATION_DETECTED" in _codes(
        ltl, "http_post", {"url": "https://collector.example/x"}
    )


def test_a_session_that_ingested_untrusted_input_survives_eviction():
    ltl = LTLTrajectoryVerifier(max_sessions=3)
    ltl.record_step(ToolAction(tool="fetch_url", arguments={"url": "https://x.example"}, **SESSION))
    _flood(ltl)
    assert "LTL_UNTRUSTED_INPUT_TO_SHELL_CHAIN" in _codes(ltl)


def test_a_session_that_assembled_danger_survives_eviction():
    ltl = LTLTrajectoryVerifier(max_sessions=3)
    _write(ltl, "stager.py", REVERSE_SHELL)
    _flood(ltl)
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in _codes(ltl)


def test_half_of_a_dangerous_pair_survives_eviction():
    ltl = LTLTrajectoryVerifier(max_sessions=3)
    _write(ltl, "net.py", "s = socket.socket()")
    _flood(ltl)
    _write(ltl, "spawn.py", "subprocess.Popen(['/bin/sh'])")
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in _codes(ltl)


def test_overwriting_the_only_indicator_makes_a_session_evictable_again():
    ltl = LTLTrajectoryVerifier(max_sessions=3)
    _write(ltl, "net.py", "s = socket.socket()")
    _write(ltl, "net.py", "nothing here\n")
    _flood(ltl)
    assert "t/s" not in ltl.traces
    assert "t/s" not in ltl.modified_files


def test_when_only_sessions_holding_history_remain_the_oldest_is_sealed_and_refused():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2)
    for i in range(2):
        _write(ltl, "n.py", "s = socket.socket()", session_id=f"h{i}", tenant_id="t", agent_id="a")
    _write(ltl, "f", session_id="new", tenant_id="t", agent_id="a")

    def run(session):
        action = ToolAction(
            tool="bash",
            arguments={"command": "ls"},
            session_id=session,
            tenant_id="t",
            agent_id="a",
        )
        return {f.code for f in ltl.verify_action(action)}

    assert "t/new" in ltl.modified_files
    assert "t/h0" not in ltl.modified_files
    assert "LTL_STATE_OVERFLOW" in run("h0")
    assert "LTL_STATE_OVERFLOW" not in run("h1")
    assert "LTL_STATE_OVERFLOW" not in run("new")


def test_a_step_keeps_at_most_the_argument_cap():
    ltl = LTLTrajectoryVerifier()
    arguments = {f"arg{i}": "x" for i in range(500)}
    arguments["k" * 5_000] = "v" * 5_000
    ltl.record_step(ToolAction(tool="noop", arguments=arguments, **SESSION))
    kept = ltl.traces["t/s"][0].arguments
    assert len(kept) == 32
    assert max(len(k) for k in kept) <= 64
    assert max(len(v) for v in kept.values()) <= 256


def test_a_sensitive_path_past_the_argument_cap_is_still_detected():
    ltl = LTLTrajectoryVerifier()
    arguments = {f"pad{i}": "x" for i in range(100)} | {"path": "/home/u/.env"}
    ltl.record_step(ToolAction(tool="read_file", arguments=arguments, **SESSION))
    assert "path" not in ltl.traces["t/s"][0].arguments
    assert ltl.traces["t/s"][0].reads_sensitive
    assert "LTL_MULTI_HOP_EXFILTRATION_DETECTED" in _codes(
        ltl, "http_post", {"url": "https://collector.example/x"}
    )


def test_sealing_returns_path_budget():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2, max_tracked_files=5)
    for i in range(50):
        _write(ltl, "f", session_id=f"s{i}", tenant_id="t", agent_id="a")
    assert ltl.capacity("t")["tracked_files"] <= 2
    assert not any("<untracked paths>" in files for files in ltl.modified_files.values())


def test_very_long_paths_and_contents_are_not_retained():
    ltl = LTLTrajectoryVerifier()
    _write(ltl, "p" * 100_000, "c" * 100_000 + " subprocess\n")
    assert max(len(k) for k in ltl.modified_files["t/s"]) <= 256
    assert sum(len(v) for v in ltl.traces["t/s"][0].arguments.values()) <= 1_000


def test_overflow_blocks_execution_without_an_approval_channel():
    p = ProtectionPipeline(config=ProtectionConfig(enable_ltl_trajectory=True))
    p.ltl.max_files_per_session = 2

    def call(tool, arguments):
        action = ToolAction(tool=tool, arguments=arguments, **SESSION)
        return enforce_call(p, action, lambda a: "ok")

    for i in range(3):
        call("write_file", {"path": f"f{i}", "content": "import subprocess\n"})
    with pytest.raises(ActionBlocked):
        call("bash", {"command": "ls"})


# ── RFC 8785 ─────────────────────────────────────────────────────────────

# RFC 8785 Appendix B: the IEEE 754 bit pattern and its required serialization
RFC_NUMBERS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


@pytest.mark.parametrize(("bits", "expected"), RFC_NUMBERS)
def test_rfc8785_number_vectors(bits, expected):
    assert jcs.dumps(struct.unpack(">d", bytes.fromhex(bits))[0]) == expected


def test_rfc8785_key_order_is_by_utf16_code_units():
    # RFC 8785 section 3.2.3: U+1F600 sorts before U+FB33 in UTF-16, after it in code points
    data = {"דּ": 1, "\U0001f600": 2, "a": 3, "\r": 4, "1": 5, "\u0080": 6, "ö": 7}
    keys = list(json.loads(jcs.dumps(data)))
    assert keys == ["\r", "1", "a", "\u0080", "ö", "\U0001f600", "דּ"]


def test_rfc8785_string_escapes_and_structure():
    assert jcs.dumps({"b": [True, None, "€$\u000f\nA'B\"\\"], "a": 1.0}) == (
        '{"a":1,"b":[true,null,"€$\\u000f\\nA\'B\\"\\\\"]}'
    )


@pytest.mark.parametrize(
    "bad", [math.nan, math.inf, -math.inf, 2**53, "\ud800", {1: "x"}, object()]
)
def test_values_rfc8785_cannot_represent_are_refused(bad):
    with pytest.raises((ValueError, TypeError)):
        jcs.dumps(bad)


def test_distinct_floats_stay_distinct():
    a, b = 1.00000000001, 1.00000000002
    n = StegoNeutralizer()
    assert n.to_canonical_json_string(a) != n.to_canonical_json_string(b)
    assert float(n.to_canonical_json_string(a)) == a
    assert n.canonicalize_json_value({"x": a}) != n.canonicalize_json_value({"x": b})


def test_neutralizing_a_payload_never_changes_a_number():
    args = {"note": "text  \t", "ratio": 0.12345678901234, "timeout": 3.0, "n": 2**60}
    cleaned = StegoNeutralizer().neutralize(args).cleaned_payload
    assert cleaned["note"] == "text"
    assert cleaned["ratio"] == 0.12345678901234
    assert isinstance(cleaned["timeout"], float)
    assert cleaned["n"] == 2**60


def test_numbers_reach_the_tool_unchanged_when_other_arguments_are_cleaned():
    p = ProtectionPipeline(config=ProtectionConfig(enable_stego_neutralizer=True))
    seen = {}
    args = {"content": "x \t", "ratio": 1.00000000001}
    enforce_call(
        p, ToolAction(tool="tune", arguments=args, **SESSION), lambda a: seen.update(a) or "ok"
    )
    assert seen == {"content": "x", "ratio": 1.00000000001}


# ── erasure ──────────────────────────────────────────────────────────────


class _Target:
    def __init__(self, name, error=None):
        self.name, self.error = name, error

    async def count_matching(self, identifier, scope):
        return 1

    async def erase_matching(self, identifier, scope):
        if self.error:
            raise self.error
        return 1


def _service(*targets, not_reached=()):
    return CrossSessionErasureService(
        targets=targets, signing_secret=b"s" * 32, not_reached=not_reached
    )


async def test_a_receipt_names_what_it_reached_and_what_it_did_not():
    receipt = await _service(_Target("sql"), not_reached=["remote store"]).erase(
        ErasureRequest(identifier="u", scope=ErasureScope.USER_ID)
    )
    assert receipt.targets_reached == ["sql"]
    assert receipt.not_reached == ["remote store"]


async def test_a_failed_target_is_not_listed_as_reached():
    receipt = await _service(_Target("ok"), _Target("down", OSError("unavailable"))).erase(
        ErasureRequest(identifier="u", scope=ErasureScope.USER_ID)
    )
    assert receipt.targets_reached == ["ok"]
    assert receipt.failed_targets == ["down"]


async def test_changing_the_coverage_statement_changes_the_signature():
    request = ErasureRequest(identifier="u", scope=ErasureScope.USER_ID)
    one = await _service(_Target("t"), not_reached=["a"]).erase(request)
    two = await _service(_Target("t"), not_reached=["b"]).erase(request)
    assert one.audit_signature != two.audit_signature


async def test_partial_erasure_reports_what_it_did_delete():
    receipt = await _service(_Target("mem", PartialErasureError("1 failed", erased=2))).erase(
        ErasureRequest(identifier="u", scope=ErasureScope.USER_ID)
    )
    assert receipt.items_erased == 2
    assert receipt.failed_targets == ["mem"]


class _Entry:
    def __init__(self, entry_id):
        self.entry_id, self.agent_id, self.session_id = entry_id, "bot", "s"
        self.metadata = {"user_id": "u-1"}


class _ReadOnlyStore:
    """A store the registry can read but not delete from, such as a remote mirror."""

    def all_entries(self):
        return [_Entry("e1")]

    def remove(self, entry_id):
        return False


class _NoRemoveStore:
    def all_entries(self):
        return [_Entry("e2")]


class _WorkingStore:
    def __init__(self):
        self.entries = [_Entry("e3")]

    def all_entries(self):
        return list(self.entries)

    def remove(self, entry_id):
        self.entries = [e for e in self.entries if e.entry_id != entry_id]
        return True


@pytest.mark.parametrize("broken", [_ReadOnlyStore, _NoRemoveStore])
async def test_a_store_that_cannot_delete_is_a_failure_and_others_still_run(broken):
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    bad, good = broken(), _WorkingStore()
    register_memory_store(bad, tenant_id=tenant)
    register_memory_store(good, tenant_id=tenant)
    with pytest.raises(PartialErasureError) as partial:
        await drop_memory_entries("u-1", tenant_id=tenant)
    assert partial.value.erased == 1
    assert good.entries == []


@pytest.fixture
def api(monkeypatch, tmp_path):
    import weakref

    from panoptis.memory import governance

    # a fresh registry: stores registered by earlier tests are only dropped when the garbage
    # collector gets to them, which Python 3.14's incremental collector does later
    monkeypatch.setattr(governance, "_STORES", weakref.WeakKeyDictionary())
    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(server, "_db_session_factory", None)
    server.reset_rate_limiter_for_tests()
    with TestClient(server.app) as client:
        yield client


def _erase(client, identifier="u-1", scope="user_id", key="key-acme"):
    return client.post(
        "/api/v1/gdpr/erase",
        json={"identifier": identifier, "scope": scope},
        headers={"X-Api-Key": key},
    )


def test_the_api_receipt_never_claims_targets_it_did_not_reach(api):
    body = _erase(api).json()
    assert body["targets_reached"] == ["runtime_state", "trace_cache"]
    joined = " ".join(body["not_reached"])
    for gap in ("other processes", "evidence ledger", "backups", "none configured", "no store"):
        assert gap in joined


def test_the_api_reports_an_unavailable_memory_store_as_failed(api):
    class Down:
        def all_entries(self):
            raise ConnectionError("memory backend unavailable")

    store = Down()
    register_memory_store(store, tenant_id="acme")
    response = _erase(api)
    assert response.status_code == 207
    assert "memory" in response.json()["failed_targets"]
    assert "memory" not in response.json()["targets_reached"]


def test_the_persisted_memory_table_is_erased_within_the_tenant(monkeypatch, tmp_path):
    from sqlalchemy import select

    from panoptis.core.models import MemoryEntryRecord as Entry

    monkeypatch.setenv("AGENTWATCH_ERASURE_SECRET", "test-erasure-secret")
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    server.reset_rate_limiter_for_tests()

    with TestClient(server.app) as client:
        factory = server._db_session_factory

        async def seed():
            async with factory() as db:
                for entry_id, tenant in (("m-acme", "acme"), ("m-globex", "globex")):
                    db.add(
                        Entry(
                            entry_id=entry_id,
                            tenant_id=tenant,
                            agent_id="bot",
                            memory_type="episodic",
                            content="a fact",
                            entry_metadata={"user_id": "u-1"},
                        )
                    )
                await db.commit()

        async def remaining():
            async with factory() as db:
                return sorted((await db.execute(select(Entry.entry_id))).scalars())

        client.portal.call(seed)
        body = _erase(client).json()
        assert "persisted_memory" in body["targets_reached"]
        assert body["items_erased"] >= 1
        assert client.portal.call(remaining) == ["m-globex"]


def test_snapshot_sees_a_same_size_write_with_its_timestamp_restored(tmp_path):
    from panoptis.protect.wasi_sandbox import WorkspaceSnapshot

    target = tmp_path / "config.txt"
    target.write_text("v1", encoding="utf-8")
    before = target.stat()
    snapshot = WorkspaceSnapshot(tmp_path)
    target.write_text("v2", encoding="utf-8")
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    diff = snapshot.compute_diff()
    snapshot.cleanup()

    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert diff.modified == ["config.txt"]
