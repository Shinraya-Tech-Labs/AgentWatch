"""Unit tests for Panopti Forensic Shadow Replay & Perturbation Engine."""

from __future__ import annotations

import pytest

from panopti.replay import (
    DropStepPerturbation,
    MutateResultPerturbation,
    ReplayTape,
    ShadowReplayer,
)


def test_replay_tape_record_and_serialize() -> None:
    tape = ReplayTape()
    call1 = tape.record("read_file", {"path": "/app/config.json"}, {"status": "ok"})
    call2 = tape.record("query_db", {"sql": "SELECT 1"}, [{"id": 1}])

    assert len(tape) == 2
    assert call1.step_index == 0
    assert call2.step_index == 1
    assert call1.fingerprint is not None

    raw_json = tape.to_json()
    restored = ReplayTape.from_json(raw_json)
    assert len(restored) == 2
    assert restored.calls[0].tool_name == "read_file"
    assert restored.calls[1].tool_name == "query_db"


def test_shadow_replayer_congruent_execution() -> None:
    tape = ReplayTape()
    tape.record("fetch_user", {"id": 42}, {"name": "Alice"})
    tape.record("send_email", {"to": "alice@example.com"}, {"sent": True})

    replayer = ShadowReplayer(tape)
    res1, err1 = replayer.step("fetch_user", {"id": 42})
    assert err1 is None
    assert res1 == {"name": "Alice"}

    res2, err2 = replayer.step("send_email", {"to": "alice@example.com"})
    assert err2 is None
    assert res2 == {"sent": True}


def test_shadow_replayer_divergence_detection() -> None:
    tape = ReplayTape()
    tape.record("fetch_user", {"id": 42}, {"name": "Alice"})

    replayer = ShadowReplayer(tape)
    res, err = replayer.step("fetch_user", {"id": 99})  # Divergent argument
    assert err is not None
    assert "Divergence at step 0" in err


def test_shadow_replayer_drop_step_perturbation() -> None:
    tape = ReplayTape()
    tape.record("step_zero", {}, "val0")
    tape.record("step_one", {}, "val1")

    replayer = ShadowReplayer(tape, perturbations=[DropStepPerturbation(target_step=1)])
    res0, err0 = replayer.step("step_zero", {})
    assert err0 is None
    assert res0 == "val0"

    res1, err1 = replayer.step("step_one", {})
    assert err1 is not None
    assert "SimulatedTimeoutError" in err1


def test_shadow_replayer_mutate_result_perturbation() -> None:
    tape = ReplayTape()
    tape.record("compute_score", {"input": 10}, 100)

    replayer = ShadowReplayer(
        tape,
        perturbations=[MutateResultPerturbation(target_step=0, mutator=lambda r: r * 2)],
    )
    res, err = replayer.step("compute_score", {"input": 10})
    assert err is None
    assert res == 200


def test_shadow_replayer_tape_exhausted() -> None:
    tape = ReplayTape()
    tape.record("init", {}, True)

    replayer = ShadowReplayer(tape)
    replayer.step("init", {})
    with pytest.raises(IndexError) as exc_info:
        replayer.step("unexpected_next", {})
    assert "Replay exhausted" in str(exc_info.value)
