"""TO-020: semantic loop and stuck-agent detection.

All tools are synthetic and harmless (status reads, list/read, a test runner stub). The
matrix: identical repeats, A/B and A/B/C oscillation, edited failing retries, productive
polling (must not be flagged), stuck polling, legitimate repeats, different meaningful
arguments, session isolation, memory bounds, and the circuit-breaker integration.
"""

from __future__ import annotations

import itertools
import time
from typing import Any

import pytest

from panoptis.protect import ActionBlocked, LoopConfig, LoopDetector, ToolAction
from panoptis.protect.enforce import enforce_call
from panoptis.protect.loops import levenshtein_similarity, token_jaccard
from panoptis.protect.model import Verdict

_ids = itertools.count()


def call(
    d: LoopDetector,
    tool: str,
    args: dict[str, Any],
    *,
    ok: bool = True,
    out: Any = None,
    session: str = "s",
) -> list[str]:
    cid = f"c{next(_ids)}"
    findings = d.check(session, cid, tool, args)
    d.record_result(cid, success=ok, output=out, session_id=session)
    return [f.code for f in findings]


# ── exact repeats ────────────────────────────────────────────────────────────────


def test_identical_call_with_identical_output_is_flagged_on_the_third_repeat():
    d = LoopDetector()
    codes = [call(d, "list_branches", {"repo": "."}, out="clean") for _ in range(3)]
    assert codes[:2] == [[], []]
    assert "DUPLICATE_CALL_LOOP" in codes[2]


def test_identical_failing_call_is_flagged_on_the_third_repeat():
    d = LoopDetector()
    codes = [
        call(d, "read_file", {"path": "missing.txt"}, ok=False, out="not found") for _ in range(3)
    ]
    assert "DUPLICATE_CALL_LOOP" in codes[2]


def test_duplicate_finding_carries_evidence_and_the_configured_verdict():
    d = LoopDetector(LoopConfig(verdict=Verdict.BLOCK))
    for _ in range(2):
        call(d, "list_branches", {}, out="clean")
    f = d.check("s", "last", "list_branches", {})[0]
    assert f.code == "DUPLICATE_CALL_LOOP" and f.recommends is Verdict.BLOCK
    assert f.evidence["occurrences"] == 3 and len(f.evidence["calls"]) == 2
    assert f.evidence["distinct_recent_outputs"] == 1


def test_threshold_is_configurable():
    d = LoopDetector(LoopConfig(duplicate_threshold=5))
    codes = [call(d, "list_branches", {}, out="clean") for _ in range(5)]
    assert codes[:4] == [[]] * 4 and "DUPLICATE_CALL_LOOP" in codes[4]


# ── polling: productive vs stuck ───────────────────────────────────────────────────


def test_productive_polling_with_changing_state_is_never_flagged():
    d = LoopDetector()
    states = [f"running {p}%" for p in range(0, 100, 10)] + ["done"]
    codes = [call(d, "get_build", {"id": 7}, out=s) for s in states]
    assert codes == [[]] * len(states)


def test_polling_resumes_being_fine_once_the_state_changes():
    d = LoopDetector()
    codes = [
        call(d, "get_build", {"id": 7}, out=s) for s in ["queued", "queued", "running", "done"]
    ]
    assert "DUPLICATE_CALL_LOOP" in codes[2]  # two identical results before it
    assert codes[3] == []  # the state changed: progress


def test_stuck_polling_is_flagged():
    d = LoopDetector()
    codes = [call(d, "get_build", {"id": 7}, out="queued") for _ in range(4)]
    assert "DUPLICATE_CALL_LOOP" in codes[2] and "DUPLICATE_CALL_LOOP" in codes[3]


def test_status_tools_count_as_polling():
    d = LoopDetector()
    assert [call(d, "git_status", {}, out="clean") for _ in range(3)] == [[], [], []]


def test_polling_tools_tolerate_more_repeats_but_stuck_polling_is_still_caught():
    d = LoopDetector()
    codes = [call(d, "poll_job", {"id": 7}, out="queued") for _ in range(10)]
    assert codes[:8] == [[]] * 8  # 3 x 3 = 9 identical calls before a polling tool loops
    assert "DUPLICATE_CALL_LOOP" in codes[8]


# ── legitimate repeats and different arguments ─────────────────────────────────────


def test_same_test_run_after_different_edits_is_not_a_loop():
    d = LoopDetector()
    codes = []
    for i in range(5):
        codes.append(call(d, "edit_file", {"path": "a.py", "content": f"v{i}"}, out="ok"))
        codes.append(call(d, "run_tests", {"path": "tests"}, ok=False, out="1 failed"))
    assert all("DUPLICATE_CALL_LOOP" not in c for c in codes)
    # the run keeps failing, though: after 4 consecutive failures that is reported separately
    assert codes[-1] == ["REPEATED_FAILURE"]


def test_identical_edits_and_identical_test_runs_are_a_loop():
    d = LoopDetector()
    codes = []
    for _ in range(3):
        codes.append(call(d, "edit_file", {"path": "a.py", "content": "same"}, out="ok"))
        codes.append(call(d, "run_tests", {"path": "tests"}, ok=False, out="1 failed"))
    assert "DUPLICATE_CALL_LOOP" in codes[4]  # the third identical edit
    assert "DUPLICATE_CALL_LOOP" in codes[5]  # the third identical test run


def test_different_meaningful_arguments_are_not_duplicates():
    d = LoopDetector()
    codes = [call(d, "read_file", {"path": f"src/mod{i}.py"}, out=f"source {i}") for i in range(10)]
    assert codes == [[]] * 10


def test_similar_arguments_that_succeed_are_not_thrashing():
    d = LoopDetector()
    codes = [
        call(d, "bash", {"command": f"pytest tests/test_a.py -k case{i}"}, out=f"{i} passed")
        for i in range(6)
    ]
    assert codes == [[]] * 6


# ── oscillation ────────────────────────────────────────────────────────────────────


def test_a_b_oscillation_without_new_results_is_flagged():
    d = LoopDetector()
    codes = []
    for _ in range(2):
        codes.append(call(d, "list_dir", {"path": "."}, out="a.txt b.txt"))
        codes.append(call(d, "read_file", {"path": "a.txt"}, out="hello"))
    assert codes[:3] == [[], [], []]
    assert "OSCILLATION_LOOP" in codes[3]


def test_a_b_c_oscillation_is_flagged():
    d = LoopDetector()
    codes = []
    for _ in range(2):
        for tool, out in (("list_dir", "x"), ("read_file", "y"), ("grep", "z")):
            codes.append(call(d, tool, {}, out=out))
    assert codes[:5] == [[]] * 5 and "OSCILLATION_LOOP" in codes[5]


def test_alternation_with_changing_results_is_not_oscillation():
    d = LoopDetector()
    codes = []
    for i in range(3):
        codes.append(call(d, "list_queue", {}, out=f"{10 - i} items"))
        codes.append(call(d, "read_head", {}, out=f"item {i}"))
    assert all("OSCILLATION_LOOP" not in c for c in codes)


# ── edited failing retries ─────────────────────────────────────────────────────────


def test_edited_failing_retries_with_the_same_error_are_thrashing():
    d = LoopDetector()
    cmds = [
        "pytest tests/test_a.py -k foo",
        "pytest tests/test_a.py -k foo -x",
        "pytest tests/test_a.py -k foo -vx",
        "pytest tests/test_a.py -k foo -q",
    ]
    codes = [call(d, "bash", {"command": c}, ok=False, out="1 failed: test_foo") for c in cmds]
    assert codes[:3] == [[], [], []]
    assert "SEMANTIC_THRASHING_LOOP" in codes[3]
    f = next(
        x
        for x in d.check("s", "next", "bash", {"command": "pytest tests/test_a.py -k foo -s"})
        if x.code == "SEMANTIC_THRASHING_LOOP"
    )
    assert f.evidence["threshold"] == 0.80
    assert all(
        max(s["levenshtein"] or 0, s["jaccard"]) > 0.80 for s in f.evidence["similar_failures"]
    )


def test_edited_retries_whose_error_changes_each_time_are_not_thrashing():
    d = LoopDetector()
    for i in range(4):
        codes = call(
            d, "bash", {"command": f"make build TARGET=x{i}"}, ok=False, out=f"error at step {i}"
        )
    assert "SEMANTIC_THRASHING_LOOP" not in codes


def test_dissimilar_failing_commands_are_not_thrashing():
    d = LoopDetector()
    cmds = ["make build", "npm ci", "cargo test --all", "go vet ./..."]
    codes = [call(d, "bash", {"command": c}, ok=False, out="failed") for c in cmds]
    assert all("SEMANTIC_THRASHING_LOOP" not in c for c in codes)


def test_similarity_measures():
    assert levenshtein_similarity("abc", "abc") == 1.0
    assert levenshtein_similarity("kitten", "sitting") == pytest.approx(1 - 3 / 7)
    assert levenshtein_similarity("kitten", "sitting", at_least=0.9) == 0.0  # below: not computed
    assert levenshtein_similarity(
        "pytest -k foo", "pytest -k foo -x", at_least=0.8
    ) == pytest.approx(1 - 3 / 16)
    assert token_jaccard("git push origin main", "git push origin main --force") == pytest.approx(
        4 / 5
    )


def test_similarity_on_long_commands_is_bounded_in_time():
    d = LoopDetector()
    base = "echo " + "x" * 5000
    for i in range(9):
        call(d, "bash", {"command": base + str(i)}, ok=False, out="e")
    t0 = time.perf_counter()
    d.check("s", "timed", "bash", {"command": base + "z"})
    assert time.perf_counter() - t0 < 0.5


# ── repeated failure, no progress ─────────────────────────────────────────────────


def test_repeated_failure_of_one_tool_with_any_arguments():
    d = LoopDetector()
    tools = ["make build", "npm ci", "cargo test", "go vet ./...", "tox -e py312"]
    codes = [call(d, "bash", {"command": c}, ok=False, out=f"err {i}") for i, c in enumerate(tools)]
    assert "REPEATED_FAILURE" in codes[4]


def test_no_progress_is_informational():
    d = LoopDetector()
    for _ in range(4):
        call(d, "a", {}, out="same")
        call(d, "b", {"k": 1}, out="same")
    findings = d.check("s", "np", "c", {})
    codes = {f.code: f for f in findings}
    assert "NO_PROGRESS" in codes and codes["NO_PROGRESS"].recommends is Verdict.ALLOW


# ── isolation and bounds ───────────────────────────────────────────────────────────


def test_sessions_are_isolated():
    d = LoopDetector()
    for i in range(6):
        assert call(d, "list_branches", {}, out="clean", session=f"s{i % 3}") == []
    assert "DUPLICATE_CALL_LOOP" in call(d, "list_branches", {}, out="clean", session="s0")


def test_call_ids_are_scoped_to_their_session():
    d = LoopDetector()
    d.check("a", "call_0", "get_x", {})
    d.check("b", "call_0", "get_x", {})
    d.record_result("call_0", success=False, output="e", session_id="b")
    assert [r.outcome for r in d.window("a")] == [None]
    assert [r.outcome for r in d.window("b")] == ["failure"]
    with pytest.raises(ValueError, match="several sessions"):
        d.record_result("call_0", success=True)


def test_window_is_bounded_and_old_calls_age_out():
    d = LoopDetector(LoopConfig(window=4))
    call(d, "list_branches", {}, out="clean")
    call(d, "list_branches", {}, out="clean")
    for i in range(4):
        call(d, "read_file", {"path": f"f{i}"}, out=str(i))
    assert len(d.window("s")) == 4
    assert call(d, "list_branches", {}, out="clean") == []  # the earlier repeats left the window
    assert d.stats()["indexed_calls"] == 4


def test_memory_is_bounded_across_many_sessions():
    d = LoopDetector(LoopConfig(max_sessions=100))
    for i in range(5000):
        call(d, "t", {"i": i}, out=i, session=f"s{i}")
    st = d.stats()
    assert st == {"sessions": 100, "records": 100, "indexed_calls": 100}
    assert d.window("s0") == [] and len(d.window("s4999")) == 1


def test_recently_used_sessions_survive_eviction():
    d = LoopDetector(LoopConfig(max_sessions=2))
    call(d, "list_branches", {}, out="clean", session="busy")
    call(d, "t", {}, session="x")
    call(d, "list_branches", {}, out="clean", session="busy")  # touch
    call(d, "t", {}, session="y")  # evicts x, not busy
    assert "DUPLICATE_CALL_LOOP" in call(d, "list_branches", {}, out="clean", session="busy")


@pytest.mark.parametrize(
    "bad",
    [{"window": 1}, {"duplicate_threshold": 1}, {"similarity_threshold": 1.5}, {"max_sessions": 0}],
)
def test_invalid_configuration_is_rejected(bad):
    with pytest.raises(ValueError):
        LoopConfig(**bad)


# ── pipeline and circuit breaker ─────────────────────────────────────────────────


def _act(**args: Any) -> ToolAction:
    return ToolAction(agent_id="coder", session_id="loop-s", tool="read_file", arguments=args)


def test_pipeline_escalates_a_loop_and_without_an_approver_blocks_it(make_pipeline):
    p = make_pipeline(approver=False)
    runs = []
    for _ in range(2):
        enforce_call(p, _act(path="notes.txt"), lambda a: runs.append(1) or "same")
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, _act(path="notes.txt"), lambda a: runs.append(1) or "same")
    assert len(runs) == 2
    assert any(f.code == "DUPLICATE_CALL_LOOP" for f in exc.value.decision.findings)


def test_approved_loop_call_runs(make_pipeline):
    p = make_pipeline(approver=lambda req: True)
    runs = []
    for _ in range(3):
        enforce_call(p, _act(path="notes.txt"), lambda a: runs.append(1) or "same")
    assert len(runs) == 3


def test_repeated_loops_open_the_session_circuit_breaker(make_pipeline):
    p = make_pipeline(approver=False)
    runs = []
    for _ in range(4):
        try:
            enforce_call(p, _act(path="notes.txt"), lambda a: runs.append(1) or "same")
        except ActionBlocked:
            pass
    # two confirmed loop decisions (trip_breaker_after_loops=2) open the breaker
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, _act(path="other.txt"), lambda a: runs.append(1) or "new")
    assert any(f.code == "CIRCUIT_OPEN" for f in exc.value.decision.findings)
    assert len(runs) == 2
    # another session of the same agent is unaffected
    other = ToolAction(
        agent_id="coder", session_id="loop-other", tool="read_file", arguments={"path": "x"}
    )
    assert enforce_call(p, other, lambda a: "fine") == "fine"


def test_loop_findings_are_recorded_in_the_decision_ledger(make_pipeline, ledger):
    p = make_pipeline(approver=False)
    for _ in range(3):
        try:
            enforce_call(p, _act(path="notes.txt"), lambda a: "same")
        except ActionBlocked:
            pass
    from panoptis.protect.ledger import KIND_DECISION

    docs = ledger.query((KIND_DECISION,), newest_first=False)
    loops = [
        d for d in docs if any(f["code"] == "DUPLICATE_CALL_LOOP" for f in d["payload"]["findings"])
    ]
    assert len(loops) == 1 and loops[0]["declared"]["verdict"] == "block"
