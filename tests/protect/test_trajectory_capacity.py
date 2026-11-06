"""Bounded trajectory state: session exhaustion, tenant isolation, capacity recovery, split writes.

The guarantee under test: limits may make a verdict stricter, never weaker, and a tenant's limits
are its own. ``scripts/mutation_check_trajectory.py`` proves these tests notice when that breaks.
"""

from __future__ import annotations

import logging
import random
import tracemalloc

import pytest

from panoptis.protect import (
    ActionBlocked,
    ProtectionConfig,
    ProtectionPipeline,
    ToolAction,
    Verdict,
)
from panoptis.protect.enforce import enforce_call
from panoptis.protect.ltl_trajectory import (
    _INDICATORS,
    _MAX_PREFIX_CHARS,
    LTLTrajectoryVerifier,
    _SealedSessions,
)

NET = "s = socket.socket()\n"
SPAWN = "subprocess.Popen(['/bin/sh'])\n"
INERT = "print('hello')\n"
JAILBREAK = "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED"
EXFIL = "LTL_MULTI_HOP_EXFILTRATION_DETECTED"
OVERFLOW = "LTL_STATE_OVERFLOW"
FILE_OVERFLOW = "LTL_FILE_TRACKING_OVERFLOW"


def act(tool, tenant="t", session="s", **arguments):
    return ToolAction(
        tool=tool, arguments=arguments, session_id=session, tenant_id=tenant, agent_id="a"
    )


def write(ltl, path, content, tool="write_file", **ids):
    ltl.record_step(act(tool, path=path, content=content, **ids))


def read_secret(ltl, **ids):
    ltl.record_step(act("read_file", path=".env", **ids))


def flood(ltl, count, tenant="t", prefix="flood", content="import subprocess\n"):
    for i in range(count):
        write(ltl, "f.py", content, tenant=tenant, session=f"{prefix}-{i}")


def saturate(ltl, tenant="t"):
    """Fill a tenant's filter outright: the key is random, so a flood alone is not repeatable."""
    flood(ltl, ltl.max_facts_per_tenant + 1, tenant=tenant, prefix="saturate")
    sealed = ltl._tenants[tenant].sealed
    sealed._bits[:] = b"\xff" * len(sealed._bits)


def codes(ltl, tool="bash", tenant="t", session="s", **arguments):
    arguments = arguments or {"command": "ls"}
    return {f.code for f in ltl.verify_action(act(tool, tenant, session, **arguments))}


def strictest(findings):
    verdicts = {f.recommends for f in findings}
    return 2 if Verdict.BLOCK in verdicts else 1 if verdicts else 0


# ── limits ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_facts_per_tenant": 0},
        {"max_tenants": 0},
        {"sealed_filter_bits": 7},
        {"max_steps_per_session": 100, "max_history_steps": 99},
    ],
)
def test_limits_that_cannot_hold_are_refused(kwargs):
    with pytest.raises(ValueError):
        LTLTrajectoryVerifier(**kwargs)


def test_a_session_with_nothing_relevant_costs_no_security_state():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "keep.py", SPAWN, session="keep")
    flood(ltl, 1_000, content=INERT)
    assert ltl.capacity("t")["sessions_with_facts"] == 1
    assert ltl.capacity("t")["sealed_sessions"] == 0
    assert JAILBREAK not in codes(ltl, session="keep")


def test_history_stays_within_its_session_limit():
    ltl = LTLTrajectoryVerifier(max_sessions=4)
    for i in range(200):
        write(ltl, "f", INERT, tenant="a" if i % 2 else "b", session=f"s{i % 9}")
    assert len(ltl.traces) == 4


def test_history_stays_within_its_step_limit():
    ltl = LTLTrajectoryVerifier(max_steps_per_session=5, max_history_steps=12)
    for i in range(200):
        write(ltl, "f", INERT, tenant="a" if i % 2 else "b", session=f"s{i % 8}")
    assert 0 < sum(map(len, ltl.traces.values())) <= 12
    assert ltl._history_steps == sum(map(len, ltl.traces.values()))


def test_a_tenant_with_no_history_left_costs_nothing():
    ltl = LTLTrajectoryVerifier(max_sessions=2)
    for i in range(100):
        write(ltl, "f", INERT, tenant=f"t{i}")
    assert len(ltl._history) <= 2


def test_memory_stops_growing_under_a_session_flood():
    ltl = LTLTrajectoryVerifier(
        max_sessions=50,
        max_steps_per_session=10,
        max_history_steps=100,
        max_facts_per_tenant=50,
        sealed_filter_bits=1 << 16,
    )
    flood(ltl, 500, prefix="warm")
    tracemalloc.start()
    try:
        flood(ltl, 1_000, prefix="first")
        settled, _ = tracemalloc.get_traced_memory()
        flood(ltl, 4_000, prefix="second")
        grown = tracemalloc.get_traced_memory()[0] - settled
    finally:
        tracemalloc.stop()
    assert grown < 32 * 1024  # an unbounded table would add megabytes here


# ── session exhaustion ─────────────────────────────────────────────────────────────────────


def test_a_flood_of_indicator_sessions_stays_inside_the_table():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=8)
    flood(ltl, 500)
    capacity = ltl.capacity("t")
    assert capacity["sessions_with_facts"] == 8
    assert capacity["sealed_sessions"] == 492
    assert capacity["tracked_files"] == 8


def test_new_sessions_keep_working_after_a_flood():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=8)
    flood(ltl, 500)
    write(ltl, "n.py", NET, session="new")
    assert OVERFLOW not in codes(ltl, session="new")
    write(ltl, "e.py", SPAWN, session="new")
    assert JAILBREAK in codes(ltl, session="new")  # exact tracking, not a sealed guess


def test_the_least_recently_active_session_is_sealed_first():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2)
    write(ltl, "a.py", NET, session="recorded")
    write(ltl, "a.py", NET, session="verified")
    write(ltl, "a.py", NET, session="idle")  # seals "recorded", the oldest
    assert OVERFLOW in codes(ltl, session="recorded")
    write(ltl, "b.py", INERT, session="verified")
    write(ltl, "a.py", NET, session="new")  # "idle" is older than "verified" was touched
    assert OVERFLOW not in codes(ltl, session="verified")
    assert OVERFLOW in codes(ltl, session="idle")


def test_a_verify_counts_as_activity():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2)
    write(ltl, "a.py", NET, session="old")
    write(ltl, "a.py", NET, session="younger")
    codes(ltl, session="old")
    write(ltl, "a.py", NET, session="new")
    assert OVERFLOW not in codes(ltl, session="old")
    assert OVERFLOW in codes(ltl, session="younger")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("bash", {"command": "ls"}),
        ("run_python", {"script": "1"}),
        ("anything", {"command": "ls"}),
        ("http_post", {"url": "https://collector.example/x"}),
        ("curl", {"url": "https://collector.example/x"}),
        ("write_file", {"path": "/home/u/.bashrc", "content": "x"}),
    ],
)
def test_a_sealed_session_is_refused_whatever_a_rule_could_catch(tool, arguments):
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", NET)
    flood(ltl, 1)
    findings = ltl.verify_action(act(tool, **arguments))
    assert OVERFLOW in {f.code for f in findings}
    assert strictest(findings) == 2
    assert next(f for f in findings if f.code == OVERFLOW).evidence["sealed_sessions"] == 1


def test_a_sealed_session_can_still_do_what_no_rule_covers():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", NET)
    flood(ltl, 1)
    assert ltl.verify_action(act("read_file", path="notes.txt")) == []
    assert ltl.verify_action(act("write_file", path="notes.txt", content="x")) == []


def test_a_sealed_session_gives_no_verdict_weaker_than_it_had():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    read_secret(ltl)
    write(ltl, "a.py", NET + SPAWN)
    before = codes(ltl, "http_post", url="https://collector.example/x")
    flood(ltl, 1)
    after = codes(ltl, "http_post", url="https://collector.example/x")
    assert EXFIL in before
    assert EXFIL in after
    assert OVERFLOW in after
    assert JAILBREAK in codes(ltl)


def test_a_sealed_session_takes_no_more_capacity():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", NET)
    flood(ltl, 1)
    write(ltl, "b.py", SPAWN)
    read_secret(ltl)
    assert "t/s" not in ltl.modified_files
    assert ltl.capacity("t")["sessions_with_facts"] == 1


def test_sealing_is_never_silent(caplog):
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    with caplog.at_level(logging.WARNING, logger="panoptis.protect.ltl_trajectory"):
        flood(ltl, 2)
    assert "1 session(s) sealed" in caplog.text
    assert "refused shell, network and persistence writes" in caplog.text


def test_sealing_warnings_do_not_flood_the_log(caplog):
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    with caplog.at_level(logging.WARNING, logger="panoptis.protect.ltl_trajectory"):
        flood(ltl, 1_025)
    assert len(caplog.records) == 11  # 1, 2, 4 ... 1024


def test_a_sealed_session_is_recognised_however_many_follow_it():
    sealed = _SealedSessions(1 << 12, b"k" * 16)
    sessions = [f"session-{i}" for i in range(5_000)]
    for session in sessions:
        sealed.add(session)
    assert all(session in sealed for session in sessions)
    assert sealed.count == 5_000


def test_the_filter_keeps_false_positives_rare_at_its_rated_load():
    sealed = _SealedSessions(1 << 21, b"k" * 16)
    for i in range(100_000):
        sealed.add(f"sealed-{i}")
    wrong = sum(f"innocent-{i}" in sealed for i in range(100_000))
    assert wrong / 100_000 < 0.005
    assert sealed.fill() < 0.25


def test_nobody_can_pick_ids_that_collide_in_another_process():
    assert LTLTrajectoryVerifier()._seal_key != LTLTrajectoryVerifier()._seal_key


def test_a_saturated_filter_refuses_new_sessions_rather_than_forgetting():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    saturate(ltl)
    assert ltl.capacity("t")["sealed_filter_fill"] == 1.0
    assert OVERFLOW in codes(ltl, session="brand-new")


def test_a_saturated_filter_leaves_sessions_with_exact_records_alone():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2)
    saturate(ltl)
    write(ltl, "a.py", NET, session="saturate-2")  # the one session still holding a record
    assert "t/saturate-2" in ltl.modified_files
    assert OVERFLOW not in codes(ltl, session="saturate-2")


def test_limits_never_turn_a_block_into_a_pass_through_the_pipeline():
    p = ProtectionPipeline(config=ProtectionConfig(enable_ltl_trajectory=True))
    p.ltl.max_facts_per_tenant = 1

    def call(tool, session, **arguments):
        action = ToolAction(
            tool=tool, arguments=arguments, session_id=session, tenant_id="t", agent_id="a"
        )
        return enforce_call(p, action, lambda a: "ok")

    call("write_file", "victim", path="a.py", content=NET)
    call("write_file", "later", path="a.py", content=NET)
    with pytest.raises(ActionBlocked):
        call("bash", "victim", command="ls")
    assert call("bash", "fresh", command="ls") == "ok"


# ── no verdict is ever weaker ──────────────────────────────────────────────────────────────

_PIECES = ("socket.so", "cket()\n", NET, SPAWN, "eval(", "urllib\n", "/bin/sh", INERT, "o", "s")


def _random_action(rnd):
    ids = {"tenant": rnd.choice(("a", "b")), "session": f"s{rnd.randrange(6)}"}
    path = f"f{rnd.randrange(5)}.py"
    return rnd.choice(
        (
            lambda: act("read_file", path=rnd.choice((".env", "notes.txt")), **ids),
            lambda: act("fetch_url", url="https://x.example", **ids),
            lambda: act("write_file", path=path, content=rnd.choice(_PIECES), **ids),
            lambda: act("append_file", path=path, content=rnd.choice(_PIECES), **ids),
            lambda: act("edit_file", path=path, replacement=rnd.choice(_PIECES), **ids),
            lambda: act("write_file", path="/home/u/.bashrc", content="x", **ids),
            lambda: act("bash", command="ls", **ids),
            lambda: act("bash", command=f"echo socket.socket >> {path}", **ids),
            lambda: act("http_post", url="https://collector.example/x", **ids),
        )
    )()


@pytest.mark.parametrize("max_tenants", [1, 2])
@pytest.mark.parametrize("seed", range(30))
def test_tight_limits_never_give_a_weaker_verdict_than_no_limits(seed, max_tenants):
    rnd = random.Random(seed)  # noqa: S311 - repeatable test data
    bounded = LTLTrajectoryVerifier(
        max_sessions=2,
        max_steps_per_session=3,
        max_history_steps=4,
        max_files_per_session=2,
        max_tracked_files=3,
        max_facts_per_tenant=2,
        max_tenants=max_tenants,
        sealed_filter_bits=1 << 12,
    )
    unbounded = LTLTrajectoryVerifier(
        max_sessions=10_000, max_facts_per_tenant=10_000, max_tracked_files=100_000
    )
    for _ in range(600):
        action = _random_action(rnd)
        limited, exact = bounded.verify_action(action), unbounded.verify_action(action)
        assert strictest(limited) >= strictest(exact), (action, limited, exact)
        if strictest(limited) < 2:  # a refused action never runs, so it is never recorded
            bounded.record_step(action)
            unbounded.record_step(action)


# ── multi-step trajectories ────────────────────────────────────────────────────────────────


def test_losing_history_loses_nothing_the_rules_need():
    ltl = LTLTrajectoryVerifier(max_sessions=1)
    read_secret(ltl)
    ltl.record_step(act("fetch_url", url="https://x.example"))
    write(ltl, "a.py", NET + SPAWN)
    flood(ltl, 3, content=INERT)
    assert "t/s" not in ltl.traces
    assert {EXFIL} <= codes(ltl, "http_post", url="https://collector.example/x")
    assert {JAILBREAK, "LTL_UNTRUSTED_INPUT_TO_SHELL_CHAIN"} <= codes(ltl)


def test_facts_name_the_first_step_that_caused_them():
    ltl = LTLTrajectoryVerifier()
    first = act("read_file", path=".env")
    ltl.record_step(first)
    read_secret(ltl)
    ingest = act("fetch_url", url="https://x.example")
    ltl.record_step(ingest)
    ltl.record_step(act("get_issue", number=1))
    egress = ltl.verify_action(act("http_post", url="https://collector.example/x"))
    shell = ltl.verify_action(act("bash", command="ls"))
    assert egress[0].evidence["antecedent_call_id"] == first.call_id
    assert shell[0].evidence["antecedent_tool"] == "fetch_url"


def test_session_context_comes_from_facts_not_history():
    ltl = LTLTrajectoryVerifier(max_sessions=1)
    read_secret(ltl)
    flood(ltl, 3, content=INERT)
    assert "t/s" not in ltl.traces
    assert ltl.session_context(act("bash")) == {
        "has_read_sensitive": True,
        "has_untrusted_ingest": False,
    }


def test_a_sealed_session_reports_the_worst_context():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", NET)
    flood(ltl, 1)
    assert ltl.session_context(act("bash")) == {
        "has_read_sensitive": True,
        "has_untrusted_ingest": True,
    }


def test_the_pipeline_gives_the_simulator_facts_not_history():
    seen = []

    class Simulator:
        def simulate_action(self, action, context):
            seen.append(context)
            return type("Result", (), {"findings": []})()

    p = ProtectionPipeline(
        config=ProtectionConfig(enable_ltl_trajectory=True, enable_temporal_simulation=True)
    )
    p.temporal_simulator = Simulator()
    p.ltl.max_sessions = 1
    read_secret(p.ltl)
    flood(p.ltl, 3, content=INERT)
    assert "t/s" not in p.ltl.traces
    enforce_call(p, act("notes"), lambda a: "ok")
    assert seen[-1]["has_read_sensitive"] is True


# ── tenant isolation ───────────────────────────────────────────────────────────────────────


def _snapshot(ltl, tenant):
    capacity = ltl.capacity(tenant)
    return {k: capacity[k] for k in capacity if k != "tenants"}


def test_one_tenants_flood_changes_nothing_for_another_tenant():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=4, max_tracked_files=6)
    read_secret(ltl, tenant="b", session="victim")
    write(ltl, "a.py", NET, tenant="b", session="victim")
    write(ltl, "n.py", NET, tenant="b", session="second")
    before = _snapshot(ltl, "b")

    flood(ltl, 2_000, tenant="a")
    for i in range(50):
        write(ltl, f"p{i}.py", NET, tenant="a", session="wide")

    assert _snapshot(ltl, "b") == before
    assert OVERFLOW not in codes(ltl, tenant="b", session="victim")
    assert EXFIL in codes(ltl, "http_post", tenant="b", session="victim", url="https://c.example/x")


def test_one_tenant_cannot_use_up_anothers_path_budget():
    ltl = LTLTrajectoryVerifier(max_files_per_session=100, max_tracked_files=4)
    for i in range(50):
        write(ltl, f"p{i}.py", NET, tenant="a")
    assert FILE_OVERFLOW in codes(ltl, tenant="a")

    write(ltl, "own.py", NET, tenant="b")

    assert "own.py" in ltl.modified_files["b/s"]
    assert FILE_OVERFLOW not in codes(ltl, tenant="b")


def test_one_tenant_cannot_use_up_anothers_fact_table():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=4)
    write(ltl, "a.py", NET, tenant="b", session="first")
    flood(ltl, 100, tenant="a")
    for i in range(3):
        write(ltl, "a.py", NET, tenant="b", session=f"more-{i}")
    assert ltl.capacity("b")["sealed_sessions"] == 0
    assert ltl.capacity("b")["sessions_with_facts"] == 4


def test_sealing_in_one_tenant_does_not_touch_the_same_session_id_in_another():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", NET, tenant="a", session="shared")
    write(ltl, "a.py", NET, tenant="b", session="shared")
    flood(ltl, 1, tenant="a")
    assert OVERFLOW in codes(ltl, tenant="a", session="shared")
    assert OVERFLOW not in codes(ltl, tenant="b", session="shared")


def test_a_saturated_filter_stays_with_its_tenant():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    saturate(ltl, tenant="a")
    assert OVERFLOW in codes(ltl, tenant="a", session="brand-new")
    assert OVERFLOW not in codes(ltl, tenant="b", session="brand-new")


def test_tenant_and_session_names_cannot_run_together():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", NET + SPAWN, tenant="a/b", session="c")
    assert JAILBREAK not in codes(ltl, tenant="a", session="b/c")


def test_history_pressure_falls_on_the_tenant_using_the_most_sessions():
    ltl = LTLTrajectoryVerifier(max_sessions=10)
    for i in range(3):
        write(ltl, "f", INERT, tenant="small", session=f"s{i}")
    flood(ltl, 100, tenant="big", content=INERT)
    assert {f"small/s{i}" for i in range(3)} <= set(ltl.traces)
    assert len(ltl.traces) == 10


def test_history_pressure_falls_on_the_tenant_using_the_most_steps():
    ltl = LTLTrajectoryVerifier(max_steps_per_session=10, max_history_steps=30)
    for _ in range(5):
        write(ltl, "f", INERT, tenant="small")
    for i in range(10):
        for _ in range(10):
            write(ltl, "f", INERT, tenant="big", session=f"s{i}")
    assert len(ltl.traces["small/s"]) == 5
    assert sum(map(len, ltl.traces.values())) <= 30


def test_step_pressure_goes_by_steps_not_by_sessions():
    ltl = LTLTrajectoryVerifier(max_steps_per_session=10, max_history_steps=20)
    for i in range(5):
        write(ltl, "f", INERT, tenant="wide", session=f"s{i}")
    for i in range(2):
        for _ in range(8):
            write(ltl, "f", INERT, tenant="long", session=f"l{i}")
    assert {f"wide/s{i}" for i in range(5)} <= set(ltl.traces)
    assert "long/l0" not in ltl.traces


def test_a_tenant_with_nothing_relevant_takes_no_place():
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    for i in range(50):
        step = act("write_file", tenant=f"benign-{i}", path="a.py", content=INERT)
        assert ltl.verify_action(step) == []
        ltl.record_step(step)
    assert ltl.capacity("benign-0")["tenants"] == 0
    assert "benign-49/s" in ltl.traces


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("read_file", {"path": ".env"}),
        ("fetch_url", {"url": "https://x.example"}),
        ("write_file", {"path": "a.py", "content": SPAWN}),
        ("write_file", {"path": "a.py", "content": "s = socket.so"}),
        ("bash", {"command": "echo subprocess > a.py"}),
    ],
)
def test_a_tenant_with_no_place_is_refused_what_would_create_state(tool, arguments, caplog):
    ltl = LTLTrajectoryVerifier(max_tenants=2)
    write(ltl, "a.py", NET, tenant="one")
    write(ltl, "a.py", NET, tenant="two")
    with caplog.at_level(logging.WARNING, logger="panoptis.protect.ltl_trajectory"):
        findings = ltl.verify_action(act(tool, tenant="three", **arguments))
    assert [f.code for f in findings] == [OVERFLOW]
    assert strictest(findings) == 2
    assert findings[0].evidence["max_tenants"] == 2
    assert "tenant table full" in caplog.text
    assert ltl.capacity("three")["tenants"] == 2


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("bash", {"command": "ls"}),
        ("http_post", {"url": "https://x.example"}),
        ("read_file", {"path": "notes.txt"}),
        ("write_file", {"path": "a.py", "content": INERT}),
        ("write_file", {"path": "/home/u/.bashrc", "content": INERT}),
    ],
)
def test_a_tenant_with_no_place_can_still_do_what_creates_no_state(tool, arguments):
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    step = act(tool, tenant="two", **arguments)
    assert ltl.verify_action(step) == []
    ltl.record_step(step)
    assert ltl.capacity("two")["tenants"] == 1


def test_a_full_table_leaves_tenants_with_a_place_alone():
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    assert OVERFLOW in codes(ltl, "read_file", tenant="two", path=".env")
    read_secret(ltl, tenant="one")
    write(ltl, "b.py", SPAWN, tenant="one")
    assert JAILBREAK in codes(ltl, tenant="one")


def test_a_place_is_reserved_before_the_step_runs():
    ltl = LTLTrajectoryVerifier(max_tenants=2)
    write(ltl, "a.py", NET, tenant="one")
    running = act("read_file", tenant="busy", path=".env")
    assert ltl.verify_action(running) == []

    assert OVERFLOW in codes(ltl, "read_file", tenant="newcomer", path=".env")

    ltl.record_step(running)
    assert EXFIL in codes(ltl, "http_post", tenant="busy", url="https://collector.example/x")


def test_overlapping_steps_in_one_tenant_all_keep_their_facts():
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    first = act("read_file", tenant="busy", session="a", path=".env")
    second = act("fetch_url", tenant="busy", session="b", url="https://x.example")
    ltl.verify_action(first)
    ltl.verify_action(second)
    ltl.record_step(first)  # the first to finish must not give up the place the second needs

    assert OVERFLOW in codes(ltl, "read_file", tenant="newcomer", path=".env")

    ltl.record_step(second)
    assert "LTL_UNTRUSTED_INPUT_TO_SHELL_CHAIN" in codes(ltl, tenant="busy", session="b")
    assert EXFIL in codes(
        ltl, "http_post", tenant="busy", session="a", url="https://collector.example/x"
    )


def test_a_place_lasts_after_the_tenants_facts_are_gone():
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    write(ltl, "a.py", INERT, tenant="one")
    assert ltl.capacity("one")["sessions_with_facts"] == 0
    assert OVERFLOW in codes(ltl, "read_file", tenant="two", path=".env")


def test_a_caller_that_skips_verify_is_warned_when_a_fact_cannot_be_kept(caplog):
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    with caplog.at_level(logging.WARNING, logger="panoptis.protect.ltl_trajectory"):
        read_secret(ltl, tenant="two")
    assert "tenant table full" in caplog.text
    assert ltl.capacity("two")["sessions_with_facts"] == 0


def test_refusals_do_not_flood_the_log(caplog):
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    with caplog.at_level(logging.WARNING, logger="panoptis.protect.ltl_trajectory"):
        for _ in range(1_025):
            codes(ltl, "read_file", tenant="two", path=".env")
    assert len(caplog.records) == 11  # 1, 2, 4 ... 1024


def test_a_tenant_holding_only_sealed_sessions_keeps_its_place():
    ltl = LTLTrajectoryVerifier(max_tenants=1, max_facts_per_tenant=1)
    write(ltl, "a.py", NET, tenant="a", session="sealed")
    write(ltl, "a.py", NET, tenant="a", session="other")
    ltl.release_session("other", tenant_id="a")
    assert OVERFLOW in codes(ltl, "read_file", tenant="new", path=".env")
    assert OVERFLOW in codes(ltl, tenant="a", session="sealed")


def test_a_refused_step_leaves_no_state_to_lose_when_a_place_opens():
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    assert OVERFLOW in codes(ltl, "read_file", tenant="two", path=".env")
    ltl.release_tenant("one")
    assert EXFIL not in codes(ltl, "http_post", tenant="two", url="https://collector.example/x")


# ── capacity recovery ──────────────────────────────────────────────────────────────────────


def test_overwriting_the_last_relevant_file_frees_the_session_automatically():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", NET)
    write(ltl, "a.py", INERT)
    capacity = ltl.capacity("t")
    assert capacity["sessions_with_facts"] == 0
    assert capacity["tracked_files"] == 0


def test_overwriting_one_of_two_files_keeps_the_other():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", NET)
    write(ltl, "b.py", SPAWN)
    write(ltl, "a.py", INERT)
    assert ltl.capacity("t")["tracked_files"] == 1
    assert ltl.capacity("t")["sessions_with_facts"] == 1


def test_overwriting_cannot_free_a_session_that_read_something_sensitive():
    ltl = LTLTrajectoryVerifier()
    read_secret(ltl)
    write(ltl, "a.py", NET)
    write(ltl, "a.py", INERT)
    assert EXFIL in codes(ltl, "http_post", url="https://collector.example/x")


def test_releasing_a_session_gives_back_everything_it_held():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2, max_tracked_files=2)
    write(ltl, "a.py", NET, session="ended")
    write(ltl, "b.py", NET, session="ended")

    assert ltl.release_session("ended", tenant_id="t") is True

    capacity = ltl.capacity("t")
    assert capacity["sessions_with_facts"] == 0
    assert capacity["tracked_files"] == 0
    assert "t/ended" not in ltl.traces
    assert ltl._history_sessions == 0
    assert ltl._history_steps == 0
    write(ltl, "c.py", NET, session="next")
    write(ltl, "d.py", NET, session="next")
    assert FILE_OVERFLOW not in codes(ltl, session="next")
    assert ltl.capacity("t")["sealed_sessions"] == 0


def test_releasing_an_unknown_session_changes_nothing():
    ltl = LTLTrajectoryVerifier()
    assert ltl.release_session("nobody", tenant_id="unknown") is False
    write(ltl, "a.py", NET)
    assert ltl.release_session("nobody", tenant_id="t") is False
    assert ltl.capacity("t")["sessions_with_facts"] == 1


def test_releasing_a_session_does_not_unseal_it():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", NET)
    flood(ltl, 1)
    ltl.release_session("s", tenant_id="t")
    assert OVERFLOW in codes(ltl)


def test_releasing_a_tenant_clears_a_saturated_filter():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    saturate(ltl)
    assert OVERFLOW in codes(ltl, session="brand-new")

    assert ltl.release_tenant("t") is True

    assert OVERFLOW not in codes(ltl, session="brand-new")
    assert ltl.capacity("t")["sealed_sessions"] == 0
    assert ltl._history_sessions == 0
    assert ltl._history_steps == 0


def test_releasing_a_tenant_makes_room_for_one_that_was_refused():
    ltl = LTLTrajectoryVerifier(max_tenants=1)
    write(ltl, "a.py", NET, tenant="one")
    assert OVERFLOW in codes(ltl, "read_file", tenant="two", path=".env")
    assert ltl.release_tenant("one") is True
    assert OVERFLOW not in codes(ltl, "read_file", tenant="two", path=".env")
    assert ltl.release_tenant("nobody") is False


def test_releasing_a_tenant_with_only_history_counts_it_out():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", INERT, tenant="history-only")
    assert ltl.release_tenant("history-only") is True
    assert ltl._history_sessions == 0
    assert ltl._history_steps == 0


def test_a_flood_needs_nobody_to_clean_up_after_it():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=8)
    flood(ltl, 1_000)
    for i in range(8):
        write(ltl, "a.py", NET, session=f"after-{i}")
    assert all(OVERFLOW not in codes(ltl, session=f"after-{i}") for i in range(8))


# ── split indicators ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("indicator", _INDICATORS)
def test_every_indicator_is_seen_wherever_it_is_split(indicator):
    for cut in range(1, len(indicator)):
        ltl = LTLTrajectoryVerifier()
        write(ltl, "x.py", "lead " + indicator[:cut])
        write(ltl, "x.py", indicator[cut:] + "\n", tool="append_file")
        assert indicator in ltl.modified_files["t/s"]["x.py"].indicators


@pytest.mark.parametrize("indicator", _INDICATORS)
def test_every_indicator_is_seen_one_character_at_a_time(indicator):
    ltl = LTLTrajectoryVerifier()
    write(ltl, "x.py", indicator[0])
    for character in indicator[1:]:
        write(ltl, "x.py", character, tool="append_file")
    assert indicator in ltl.modified_files["t/s"]["x.py"].indicators


def test_an_open_prefix_survives_losing_history():
    ltl = LTLTrajectoryVerifier(max_sessions=1)
    write(ltl, "a.py", "s = socket.so")
    flood(ltl, 3, content=INERT)
    assert "t/s" not in ltl.traces
    write(ltl, "a.py", "cket()\n", tool="append_file")
    write(ltl, "b.py", SPAWN)
    assert JAILBREAK in codes(ltl)


def test_an_open_prefix_alone_keeps_a_session_tracked():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", "s = socket.so")
    assert ltl.capacity("t")["sessions_with_facts"] == 1


def test_a_split_cannot_get_past_sealing():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=1)
    write(ltl, "a.py", "s = socket.so")
    flood(ltl, 1)
    write(ltl, "a.py", "cket()\n", tool="append_file")
    write(ltl, "b.py", SPAWN)
    assert {OVERFLOW, JAILBREAK} <= codes(ltl)


def test_an_append_past_the_cap_joins_the_overflow_entry_even_when_it_looks_harmless():
    ltl = LTLTrajectoryVerifier(max_files_per_session=1)
    write(ltl, "first.py", NET)
    write(ltl, "second.py", "s = subproc")
    write(ltl, "second.py", "ess.run()\n", tool="append_file")
    assert "subprocess" in ltl.modified_files["t/s"]["<untracked paths>"].indicators


def test_what_a_file_keeps_is_bounded_however_much_is_written():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", "x" * 100_000 + "socket.socket.so")
    assert len(ltl.modified_files["t/s"]["a.py"].tail) <= _MAX_PREFIX_CHARS
    assert _MAX_PREFIX_CHARS == 12


def test_a_file_ending_mid_word_is_only_kept_when_an_indicator_could_finish():
    ltl = LTLTrajectoryVerifier()
    write(ltl, "a.py", "the end of the line\n")
    write(ltl, "b.py", "value = 1;")
    assert "t/s" not in ltl.modified_files
