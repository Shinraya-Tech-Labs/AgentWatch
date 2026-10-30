"""Context guard: system prompt eviction and context-window headroom (issue #747)."""

from __future__ import annotations

import hashlib

import pytest

from panoptis.events.model import Diagnostic
from panoptis.monitoring.context_guard import (
    CONTEXT_LIMIT_UNKNOWN,
    CONTEXT_USAGE_UNAVAILABLE,
    CONTEXT_WINDOW_EXHAUSTED,
    SYSTEM_PROMPT_EVICTED,
    ContextGuard,
    PromptAnchor,
    extract_system_segments,
    reported_prompt_tokens,
    reserved_completion_tokens,
)

SYSTEM = "You are a billing assistant. Never issue refunds above 100 USD without approval."
LEAK_PROBE = "refunds above 100 USD"
LIMIT = 1000


def openai_request(system: str | None = SYSTEM, user: str = "Hi", **extra: object) -> dict:
    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return {"model": "gpt-x", "messages": messages, **extra}


def anthropic_request(system: object = SYSTEM, user: str = "Hi", **extra: object) -> dict:
    req: dict = {"model": "claude-x", "messages": [{"role": "user", "content": user}], **extra}
    if system is not None:
        req["system"] = system
    return req


def guard(**kw: object) -> ContextGuard:
    return ContextGuard(default_context_limit=LIMIT, **kw)  # type: ignore[arg-type]


# --- instruction integrity ---------------------------------------------------------------


class TestPromptIntegrity:
    def test_first_request_registers_the_anchor_and_is_intact(self):
        g = guard()
        r = g.check("run-1", openai_request(), prompt_tokens=100)
        assert r.anchor_registered is True
        assert r.prompt_intact is True
        assert r.eviction_reason is None
        assert r.ok

    def test_unchanged_prompt_stays_intact(self):
        g = guard()
        g.check("run-1", openai_request())
        r = g.check("run-1", openai_request(user="a different question"), prompt_tokens=100)
        assert r.prompt_intact is True
        assert r.anchor_registered is False
        assert SYSTEM_PROMPT_EVICTED not in r.codes

    def test_missing_system_message_is_reported(self):
        g = guard()
        g.check("run-1", openai_request())
        r = g.check("run-1", openai_request(system=None), prompt_tokens=100)
        assert r.prompt_intact is False
        assert r.eviction_reason == "missing"
        assert SYSTEM_PROMPT_EVICTED in r.codes
        assert not r.ok

    def test_truncated_system_message_is_reported(self):
        g = guard()
        g.check("run-1", openai_request())
        r = g.check("run-1", openai_request(system=SYSTEM[:30]), prompt_tokens=100)
        assert r.prompt_intact is False
        assert r.eviction_reason == "truncated"
        assert SYSTEM_PROMPT_EVICTED in r.codes

    def test_altered_system_message_of_same_length_is_reported(self):
        g = guard()
        g.check("run-1", openai_request())
        r = g.check("run-1", openai_request(system=SYSTEM.replace("100", "999")))
        assert r.eviction_reason == "altered"
        assert SYSTEM_PROMPT_EVICTED in r.codes

    def test_extra_instructions_added_later_do_not_count_as_eviction(self):
        g = guard()
        g.check("run-1", openai_request())
        req = openai_request()
        req["messages"].insert(1, {"role": "system", "content": "Answer in French."})
        r = g.check("run-1", req)
        assert r.prompt_intact is True

    def test_one_of_two_anchored_segments_lost(self):
        g = guard()
        first = openai_request()
        first["messages"].insert(1, {"role": "system", "content": "Always cite the ticket id."})
        g.check("run-1", first)
        r = g.check("run-1", openai_request())
        assert r.prompt_intact is False
        assert r.eviction_reason == "truncated"
        (finding,) = [f for f in r.findings if f.code == SYSTEM_PROMPT_EVICTED]
        assert "1 of 2" in finding.message

    def test_runs_are_isolated(self):
        g = guard()
        g.check("run-a", openai_request(system="Instructions A. Keep to topic A only."))
        g.check("run-b", openai_request(system="Instructions B. Keep to topic B only."))
        ok_b = g.check("run-b", openai_request(system="Instructions B. Keep to topic B only."))
        assert ok_b.prompt_intact is True
        # run-a sending run-b's prompt is an eviction for run-a
        r = g.check("run-a", openai_request(system="Instructions B. Keep to topic B only."))
        assert r.prompt_intact is False

    def test_registering_again_replaces_the_anchor(self):
        g = guard()
        g.check("run-1", openai_request())
        g.register_anchor("run-1", "New system prompt that is allowed to differ.")
        r = g.check("run-1", openai_request(system="New system prompt that is allowed to differ."))
        assert r.prompt_intact is True

    def test_no_system_prompt_at_all_means_nothing_to_protect(self):
        g = guard()
        r = g.check("run-1", openai_request(system=None))
        assert r.prompt_intact is None
        assert r.anchor_registered is False
        assert g.anchor_for("run-1") is None

    def test_blank_system_message_is_not_an_anchor(self):
        g = guard()
        r = g.check("run-1", openai_request(system="   "))
        assert r.anchor_registered is False

    def test_register_anchor_rejects_empty(self):
        with pytest.raises(ValueError):
            guard().register_anchor("run-1", openai_request(system=None))
        with pytest.raises(ValueError):
            guard().register_anchor("run-1", "  ")

    def test_forget_drops_the_anchor(self):
        g = guard()
        g.check("run-1", openai_request())
        g.forget("run-1")
        g.forget("never-seen")
        r = g.check("run-1", openai_request(system=None))
        assert r.prompt_intact is None

    def test_anchor_store_is_bounded_and_evicts_the_oldest_run(self):
        g = guard(max_runs=2)
        for run in ("r1", "r2", "r3"):
            g.check(run, openai_request(system=f"Prompt for {run}, long enough."))
        assert g.anchor_for("r1") is None
        assert g.anchor_for("r2") is not None
        assert g.anchor_for("r3") is not None

    def test_checking_a_run_keeps_it_recent(self):
        g = guard(max_runs=2)
        g.check("r1", openai_request(system="Prompt for r1, long enough."))
        g.check("r2", openai_request(system="Prompt for r2, long enough."))
        g.check("r1", openai_request(system="Prompt for r1, long enough."))
        g.check("r3", openai_request(system="Prompt for r3, long enough."))
        assert g.anchor_for("r1") is not None
        assert g.anchor_for("r2") is None

    def test_tuple_run_keys_work(self):
        g = guard()
        key = ("native.run", "abc")
        g.check(key, openai_request())
        assert g.check(key, openai_request(system=None)).eviction_reason == "missing"


class TestNoPromptLeak:
    def test_findings_and_anchor_do_not_contain_prompt_text(self):
        g = guard()
        g.check("run-1", openai_request())
        r = g.check("run-1", openai_request(system=SYSTEM[:30]), prompt_tokens=100)
        text = " ".join(f.message for f in r.findings) + repr(r) + repr(g.anchor_for("run-1"))
        assert LEAK_PROBE not in text
        assert "billing assistant" not in text
        assert SYSTEM[:30] not in text

    def test_anchor_holds_only_digests_and_a_length(self):
        g = guard()
        g.check("run-1", openai_request())
        anchor = g.anchor_for("run-1")
        assert isinstance(anchor, PromptAnchor)
        assert anchor.segment_digests == (hashlib.sha256(SYSTEM.encode()).hexdigest(),)
        assert anchor.char_count == len(SYSTEM)
        assert len(anchor.fingerprint) == 64
        assert anchor.short == anchor.fingerprint[:12]

    def test_finding_names_the_fingerprint_prefix(self):
        g = guard()
        g.check("run-1", openai_request())
        anchor = g.anchor_for("run-1")
        r = g.check("run-1", openai_request(system=None))
        assert f"sha256:{anchor.short}" in r.findings[0].message


# --- request shapes ------------------------------------------------------------------------


class TestExtraction:
    def test_openai_system_and_developer_roles(self):
        req = {
            "messages": [
                {"role": "developer", "content": "dev rules"},
                {"role": "user", "content": "q"},
                {"role": "system", "content": [{"type": "text", "text": "part one"}]},
            ]
        }
        assert extract_system_segments(req) == ["dev rules", "part one"]

    def test_anthropic_string_and_block_list(self):
        assert extract_system_segments(anthropic_request("be brief")) == ["be brief"]
        blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        assert extract_system_segments(anthropic_request(blocks)) == ["a", "b"]
        assert extract_system_segments(anthropic_request(["plain", "strings"])) == [
            "plain",
            "strings",
        ]

    def test_responses_api_instructions_and_input(self):
        req = {"instructions": "top level", "input": [{"role": "system", "content": "in input"}]}
        assert extract_system_segments(req) == ["top level", "in input"]

    def test_user_text_is_never_treated_as_instructions(self):
        assert extract_system_segments({"messages": [{"role": "user", "content": "hi"}]}) == []

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"messages": None},
            {"messages": "not a list"},
            {"messages": [None, 5, "x", {"role": "system"}, {"role": "system", "content": 7}]},
            {"system": 42},
            {"system": [None, {"text": 3}, {"no_text": True}]},
            {"instructions": 5},
        ],
    )
    def test_malformed_requests_do_not_raise(self, bad):
        assert extract_system_segments(bad) == []
        guard().check("run-1", bad)

    def test_anthropic_request_is_checked_like_openai(self):
        g = guard()
        g.check("run-1", anthropic_request())
        assert g.check("run-1", anthropic_request()).prompt_intact is True
        assert g.check("run-1", anthropic_request(None)).eviction_reason == "missing"


# --- capacity ------------------------------------------------------------------------------


class TestHeadroom:
    def test_plenty_of_room(self):
        r = guard().check("r", openai_request(), prompt_tokens=500, reserved_completion=100)
        assert r.headroom_tokens == 400
        assert r.headroom_percent == 40.0
        assert CONTEXT_WINDOW_EXHAUSTED not in r.codes

    def test_exactly_ten_percent_is_not_reported(self):
        # 1000 - (800 + 100) = 100 tokens = exactly 10%
        r = guard().check("r", openai_request(), prompt_tokens=800, reserved_completion=100)
        assert r.headroom_tokens == 100
        assert CONTEXT_WINDOW_EXHAUSTED not in r.codes
        assert r.ok

    def test_one_token_under_ten_percent_is_reported(self):
        r = guard().check("r", openai_request(), prompt_tokens=800, reserved_completion=101)
        assert r.headroom_tokens == 99
        assert CONTEXT_WINDOW_EXHAUSTED in r.codes
        assert not r.ok

    def test_zero_headroom_and_overflow_are_reported(self):
        at_limit = guard().check("r", openai_request(), prompt_tokens=900, reserved_completion=100)
        assert at_limit.headroom_tokens == 0
        assert CONTEXT_WINDOW_EXHAUSTED in at_limit.codes
        over = guard().check("r", openai_request(), prompt_tokens=1200, reserved_completion=100)
        assert over.headroom_tokens == -300
        assert CONTEXT_WINDOW_EXHAUSTED in over.codes

    def test_uses_integer_math_at_the_boundary_for_odd_limits(self):
        g = ContextGuard(default_context_limit=1005)
        # 10% of 1005 is 100.5, so 100 left is under the minimum and 101 is not
        assert CONTEXT_WINDOW_EXHAUSTED in g.check("r", {}, prompt_tokens=905).codes
        assert CONTEXT_WINDOW_EXHAUSTED not in g.check("r", {}, prompt_tokens=904).codes

    def test_reserved_completion_comes_from_the_request(self):
        r = guard().check("r", openai_request(max_tokens=250), prompt_tokens=700)
        assert r.reserved_completion == 250
        assert r.reserved_completion_reported is True
        assert r.headroom_tokens == 50
        assert CONTEXT_WINDOW_EXHAUSTED in r.codes

    def test_explicit_reserved_completion_wins(self):
        r = guard().check(
            "r", openai_request(max_tokens=250), prompt_tokens=700, reserved_completion=0
        )
        assert r.reserved_completion == 0
        assert r.headroom_tokens == 300

    def test_unreported_reservation_counts_as_zero_and_says_so(self):
        r = guard().check("r", openai_request(), prompt_tokens=700)
        assert r.reserved_completion == 0
        assert r.reserved_completion_reported is False
        assert r.headroom_tokens == 300

    def test_custom_minimum_percent(self):
        g = ContextGuard(default_context_limit=1000, min_headroom_percent=25)
        assert CONTEXT_WINDOW_EXHAUSTED in g.check("r", {}, prompt_tokens=800).codes
        assert CONTEXT_WINDOW_EXHAUSTED not in g.check("r", {}, prompt_tokens=750).codes

    def test_per_model_limits(self):
        g = ContextGuard(context_limits={"small": 100, "big": 100000}, default_context_limit=None)
        small = g.check("r", {}, model="small", prompt_tokens=95)
        big = g.check("r", {}, model="big", prompt_tokens=95)
        assert CONTEXT_WINDOW_EXHAUSTED in small.codes
        assert big.headroom_tokens == 100000 - 95
        assert small.context_limit == 100

    def test_explicit_limit_overrides_configuration(self):
        r = guard().check("r", {}, prompt_tokens=55, context_limit=60)
        assert r.context_limit == 60
        assert CONTEXT_WINDOW_EXHAUSTED in r.codes

    def test_usage_block_supplies_prompt_tokens(self):
        r = guard().check("r", openai_request(), usage={"prompt_tokens": 950})
        assert r.prompt_tokens == 950
        assert CONTEXT_WINDOW_EXHAUSTED in r.codes

    def test_explicit_prompt_tokens_win_over_usage(self):
        r = guard().check("r", {}, usage={"prompt_tokens": 950}, prompt_tokens=10)
        assert r.prompt_tokens == 10


class TestMissingCounts:
    def test_missing_prompt_tokens_is_reported_not_guessed(self):
        r = guard().check("r", openai_request(max_tokens=100))
        assert r.prompt_tokens is None
        assert r.headroom_tokens is None
        assert r.headroom_percent is None
        assert CONTEXT_USAGE_UNAVAILABLE in r.codes
        assert CONTEXT_WINDOW_EXHAUSTED not in r.codes
        assert r.ok

    def test_missing_limit_is_reported(self):
        r = ContextGuard().check("r", openai_request(), prompt_tokens=100)
        assert CONTEXT_LIMIT_UNKNOWN in r.codes
        assert r.headroom_tokens is None
        assert CONTEXT_WINDOW_EXHAUSTED not in r.codes

    def test_unknown_model_without_default_has_no_limit(self):
        g = ContextGuard(context_limits={"known": 1000})
        assert CONTEXT_LIMIT_UNKNOWN in g.check("r", {}, model="other", prompt_tokens=1).codes

    def test_unavailable_usage_does_not_hide_a_prompt_eviction(self):
        g = guard()
        g.check("r", openai_request())
        r = g.check("r", openai_request(system=None))
        assert {SYSTEM_PROMPT_EVICTED, CONTEXT_USAGE_UNAVAILABLE} <= r.codes

    @pytest.mark.parametrize("bad", [-1, True, 1.5, "100", None])
    def test_invalid_token_counts_count_as_missing(self, bad):
        r = guard().check("r", {}, prompt_tokens=bad)
        assert CONTEXT_USAGE_UNAVAILABLE in r.codes

    def test_zero_is_a_real_count(self):
        r = guard().check("r", {}, prompt_tokens=0)
        assert r.headroom_tokens == LIMIT
        assert CONTEXT_USAGE_UNAVAILABLE not in r.codes


class TestUsageHelpers:
    def test_openai_usage(self):
        assert reported_prompt_tokens({"prompt_tokens": 12, "completion_tokens": 3}) == 12

    def test_anthropic_usage_includes_cached_tokens(self):
        usage = {
            "input_tokens": 10,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
            "output_tokens": 5,
        }
        assert reported_prompt_tokens(usage) == 60

    @pytest.mark.parametrize("usage", [None, {}, {"output_tokens": 4}, "x", {"prompt_tokens": -3}])
    def test_usage_without_prompt_tokens(self, usage):
        assert reported_prompt_tokens(usage) is None

    def test_reserved_completion_key_order(self):
        assert reserved_completion_tokens({"max_completion_tokens": 7, "max_tokens": 9}) == 7
        assert reserved_completion_tokens({"max_output_tokens": 5}) == 5
        assert reserved_completion_tokens({"max_tokens": 0}) == 0
        assert reserved_completion_tokens({"max_tokens": "9"}) is None
        assert reserved_completion_tokens({}) is None


# --- result and configuration --------------------------------------------------------------


class TestResultAndConfig:
    def test_findings_are_diagnostics_with_levels(self):
        g = guard()
        g.check("r", openai_request())
        r = g.check("r", openai_request(system=None), prompt_tokens=950)
        assert all(isinstance(f, Diagnostic) for f in r.findings)
        by_code = {f.code: f for f in r.findings}
        assert by_code[SYSTEM_PROMPT_EVICTED].level == "warning"
        assert by_code[CONTEXT_WINDOW_EXHAUSTED].level == "warning"
        assert "950" in by_code[CONTEXT_WINDOW_EXHAUSTED].message

    def test_info_findings_do_not_make_the_result_not_ok(self):
        assert ContextGuard().check("r", openai_request()).ok is True

    @pytest.mark.parametrize("percent", [-1, 101, True])
    def test_invalid_percent(self, percent):
        with pytest.raises(ValueError):
            ContextGuard(min_headroom_percent=percent)

    @pytest.mark.parametrize("runs", [0, -5, True])
    def test_invalid_max_runs(self, runs):
        with pytest.raises(ValueError):
            ContextGuard(max_runs=runs)

    @pytest.mark.parametrize("limit", [0, -10, 1.5, True])
    def test_invalid_default_limit(self, limit):
        with pytest.raises(ValueError):
            ContextGuard(default_context_limit=limit)

    def test_invalid_model_limit(self):
        with pytest.raises(ValueError):
            ContextGuard(context_limits={"m": 0})

    def test_zero_percent_only_flags_a_full_or_overflowing_window(self):
        g = ContextGuard(default_context_limit=1000, min_headroom_percent=0)
        assert CONTEXT_WINDOW_EXHAUSTED not in g.check("r", {}, prompt_tokens=1000).codes
        assert CONTEXT_WINDOW_EXHAUSTED in g.check("r", {}, prompt_tokens=1001).codes

    def test_eviction_and_exhaustion_together(self):
        g = guard()
        g.check("r", openai_request())
        r = g.check("r", openai_request(system=SYSTEM[:10]), prompt_tokens=990)
        assert {SYSTEM_PROMPT_EVICTED, CONTEXT_WINDOW_EXHAUSTED} <= r.codes
        assert not r.ok
