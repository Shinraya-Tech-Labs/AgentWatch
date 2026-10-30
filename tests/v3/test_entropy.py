"""Token entropy monitor: entropy math, sliding window, anomalies, missing logprobs."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from panoptis import instrument as aw
from panoptis.analysis.base import AnalysisInput
from panoptis.analysis.entropy import (
    AnomalyType,
    TokenEntropyMonitor,
    shannon_entropy,
    token_entropies,
)
from panoptis.analysis.registry import builtin_analyzers
from panoptis.events.model import ArtifactRef, ComputationalEvent, EventKind, TemporalInfo
from panoptis.events.normalizers.llm_api import token_logprobs
from panoptis.sensors.llm_clients import instrument_openai

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def lp(*probs: float) -> list[float]:
    return [math.log(p) for p in probs]


def token(*probs: float) -> dict:
    return {
        "token": "x",
        "logprob": math.log(probs[0]),
        "top_logprobs": [{"token": "x", "logprob": v} for v in lp(*probs)],
    }


UNIFORM = token(0.25, 0.25, 0.25, 0.25)  # 2 bits
CERTAIN = token(0.999, 0.0005, 0.0003, 0.0002)  # about 0.012 bits


def call(
    i: int, tokens: list[dict] | None = None, completion: str | None = None
) -> ComputationalEvent:
    attrs = json.dumps({"logprobs": tokens}) if tokens is not None else "{}"
    outs = (ArtifactRef(f"art-{completion}", "completion"),) if completion else ()
    return ComputationalEvent(
        event_id=f"e{i:02d}",
        tenant_id="t",
        interp_id="i",
        normalizer="openai@1",
        derived_from=(),
        kind=EventKind.MODEL_INVOCATION,
        operation="chat",
        time=TemporalInfo(T0 + timedelta(seconds=i)),
        outputs=outs,
        attributes_json=attrs,
    )


def analyze(events: list[ComputationalEvent], **cfg) -> list[dict]:
    data = AnalysisInput("t", "i", events, {e.event_id: "r1" for e in events}, {"r1": {}}, [])
    return TokenEntropyMonitor(**cfg).analyze(data)


# -- entropy math ----------------------------------------------------------
def test_entropy_known_distributions():
    assert shannon_entropy(lp(0.5, 0.5)) == pytest.approx(1.0)
    assert shannon_entropy(lp(0.25, 0.25, 0.25, 0.25)) == pytest.approx(2.0)
    assert shannon_entropy(lp(1.0)) == pytest.approx(0.0)
    assert shannon_entropy(lp(0.5, 0.25, 0.25)) == pytest.approx(1.5)


def test_entropy_renormalizes_truncated_top_k():
    # top-2 of a larger vocabulary: probabilities sum to 0.5, shape is still 50/50
    assert shannon_entropy(lp(0.25, 0.25)) == pytest.approx(1.0)


def test_token_entropies_skips_tokens_without_top_logprobs():
    toks = [
        UNIFORM,
        {"token": "y", "logprob": -0.1},
        {"token": "z", "logprob": -0.1, "top_logprobs": []},
    ]
    assert token_entropies(toks) == [pytest.approx(2.0)]
    assert token_entropies(None) == [] and token_entropies([]) == []


# -- entropy collapse ------------------------------------------------------
def test_collapse_fires_once_when_window_drops_below_threshold():
    out = analyze([call(0, [UNIFORM] * 4), call(1, [CERTAIN] * 10)], window=4, threshold=0.1)
    assert [r["anomaly_type"] for r in out] == ["ENTROPY_COLLAPSE"]
    r = out[0]
    assert r["events"] == ["e01"] and r["turn_index"] == 1
    assert r["measures"]["window_mean_bits"] < 0.1
    assert r["record_type"] == "token_entropy_anomaly" and r["scope"] == "r1"
    assert r["analyzer"] == "token_entropy@1" and r["maturity"] == "EXPERIMENTAL"


def test_window_slides_across_turns_and_needs_a_full_window():
    # 3 low tokens are not enough for window=4; the 4th (in the next turn) completes it
    out = analyze([call(0, [CERTAIN] * 3), call(1, [CERTAIN] * 2)], window=4, threshold=0.1)
    assert len(out) == 1 and out[0]["events"] == ["e01"] and out[0]["measures"]["token_index"] == 0
    assert analyze([call(0, [CERTAIN] * 3)], window=4, threshold=0.1) == []


def test_high_entropy_old_tokens_slide_out_of_the_window():
    out = analyze([call(0, [UNIFORM] * 3 + [CERTAIN] * 4)], window=4, threshold=0.1)
    assert len(out) == 1 and out[0]["measures"]["token_index"] == 6  # first all-certain window


def test_collapse_rearms_after_recovery():
    toks = [CERTAIN] * 4 + [UNIFORM] * 4 + [CERTAIN] * 4
    out = analyze([call(0, toks)], window=4, threshold=0.1)
    assert [r["measures"]["token_index"] for r in out] == [3, 11]


def test_healthy_generation_raises_nothing():
    assert analyze([call(i, [UNIFORM] * 8, f"c{i}") for i in range(3)], window=4) == []


# -- stagnation loop -------------------------------------------------------
def test_stagnation_on_identical_completions_without_logprobs():
    evs = [call(0, None, "a"), call(1, None, "b"), call(2, None, "b"), call(3, None, "b")]
    out = analyze(evs)
    assert [r["anomaly_type"] for r in out] == ["STAGNATION_LOOP"]
    assert out[0]["events"] == ["e01", "e02", "e03"] and out[0]["measures"]["repeats"] == 3


def test_two_repeats_are_below_the_default_and_alternation_is_not_a_loop():
    assert analyze([call(0, None, "a"), call(1, None, "a"), call(2, None, "b")]) == []
    assert analyze([call(i, None, "ab"[i % 2]) for i in range(6)]) == []
    assert len(analyze([call(0, None, "a"), call(1, None, "a")], min_repeats=2)) == 1


def test_streak_at_the_end_and_two_separate_streaks():
    evs = [call(i, None, c) for i, c in enumerate("aaabbb")]
    assert [r["events"] for r in analyze(evs)] == [["e00", "e01", "e02"], ["e03", "e04", "e05"]]


# -- missing logprobs ------------------------------------------------------
def test_missing_logprobs_and_non_model_events_are_quiet():
    other = ComputationalEvent(
        "tool1", "t", "i", "n@1", (), EventKind.TOOL_INVOCATION, "calc", TemporalInfo(T0)
    )
    evs = [call(0), call(1, []), call(2, [{"token": "a", "logprob": -1.0}]), other]
    assert analyze(evs) == []
    assert analyze([]) == []


def test_records_are_deterministic_and_cover_both_types():
    evs = [call(0, [CERTAIN] * 6, "z"), call(1, None, "z"), call(2, None, "z")]
    assert analyze(evs, window=4) == analyze(list(reversed(evs)), window=4)
    assert {r["anomaly_type"] for r in analyze(evs, window=4)} == {a.value for a in AnomalyType}


@pytest.mark.parametrize("cfg", [{"window": 0}, {"threshold": -0.1}, {"min_repeats": 1}])
def test_bad_config_is_rejected(cfg):
    with pytest.raises(ValueError):
        TokenEntropyMonitor(**cfg)


def test_registered_as_builtin_analyzer():
    assert any(isinstance(a, TokenEntropyMonitor) for a in builtin_analyzers())


# -- normalizer passthrough ------------------------------------------------
def test_token_logprobs_helper_shapes():
    assert token_logprobs({}) == [] and token_logprobs({"logprobs": None}) == []
    assert token_logprobs({"logprobs": {"content": [{"token": "a"}, "junk"]}}) == []
    item = {
        "token": "a",
        "logprob": -0.5,
        "bytes": [97],
        "top_logprobs": [{"token": "a", "logprob": -0.5, "bytes": [97]}, {"token": "b"}],
    }
    assert token_logprobs({"logprobs": {"content": [item]}}) == [
        {"logprob": -0.5, "top_logprobs": [{"logprob": -0.5}]}
    ]
    # token text is not stored (redaction), only the numbers
    assert "token" not in token_logprobs({"logprobs": {"content": [item]}})[0]


class _Completions:
    def __init__(self, tokens: list[dict] | None) -> None:
        self.tokens = tokens

    def create(self, **kw):
        choice: dict = {"message": {"content": "same"}}
        if self.tokens is not None:
            choice["logprobs"] = {"content": self.tokens}
        return {"id": "r", "model": kw["model"], "choices": [choice]}


def _run_calls(engine, sink, tokens: list[dict] | None, n: int = 1) -> str:
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions(tokens)))
    instrument_openai(client, sink)
    with aw.run("r"):
        for _ in range(n):
            client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
            )
    engine.ingest(sink.drafts)
    engine.process()
    return engine.interp_id_for("default")


def test_normalizer_keeps_optional_logprobs(engine, sink):
    iid = _run_calls(engine, sink, [token(0.5, 0.5)])
    (ev,) = [e for e in engine.store.events(iid) if e["kind"] == "MODEL_INVOCATION"]
    assert token_entropies(ev["attributes"]["logprobs"]) == [pytest.approx(1.0)]


def test_normalizer_is_unchanged_without_logprobs(engine, sink):
    iid = _run_calls(engine, sink, None)
    (ev,) = [e for e in engine.store.events(iid) if e["kind"] == "MODEL_INVOCATION"]
    assert "logprobs" not in ev["attributes"]
    assert engine.store.derived(iid, "token_entropy_anomaly") == []


def test_engine_stores_both_anomaly_types(engine, sink):
    iid = _run_calls(engine, sink, [CERTAIN] * 40, n=3)
    found = {r["anomaly_type"] for r in engine.store.derived(iid, "token_entropy_anomaly")}
    assert found == {"ENTROPY_COLLAPSE", "STAGNATION_LOOP"}
