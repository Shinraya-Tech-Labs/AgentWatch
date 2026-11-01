"""OpenInference <-> OTel GenAI bridge: normalize_otel_span."""

from __future__ import annotations

import copy

import pytest

from panoptis.telemetry.otel import (
    OPENINFERENCE_SPAN_KIND_MAP,
    normalize_otel_span,
)


def test_empty_dict():
    assert normalize_otel_span({}) == {}


def test_openinference_llm_span():
    out = normalize_otel_span(
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "gpt-4o",
            "llm.provider": "openai",
            "llm.token_count.prompt": 12,
            "llm.token_count.completion": 5,
            "llm.token_count.total": 17,
            "input.value": "hi",
            "output.value": "hello",
        }
    )
    assert out["gen_ai.operation.name"] == "chat"
    assert out["gen_ai.request.model"] == "gpt-4o"
    assert out["gen_ai.provider.name"] == "openai"
    assert out["gen_ai.usage.input_tokens"] == 12
    assert out["gen_ai.usage.output_tokens"] == 5
    assert out["gen_ai.usage.total_tokens"] == 17
    assert out["gen_ai.prompt"] == "hi"
    assert out["gen_ai.completion"] == "hello"
    assert out["panoptis.span_kind"] == "MODEL_INVOCATION"
    assert "extra" not in out  # everything was mapped


def test_pure_genai_span_is_unchanged():
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 3,
        "db.system": "redis",
        "vendor.thing": "x",
    }
    assert normalize_otel_span(attrs) == attrs


def test_openinference_and_genai_give_same_result():
    oi = normalize_otel_span({"llm.model_name": "m", "llm.token_count.prompt": 4})
    genai = normalize_otel_span({"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 4})
    assert oi == genai


@pytest.mark.parametrize("kind", sorted(OPENINFERENCE_SPAN_KIND_MAP))
def test_each_span_kind(kind):
    out = normalize_otel_span({"openinference.span.kind": kind})
    aw_kind, operation = OPENINFERENCE_SPAN_KIND_MAP[kind]
    assert out["panoptis.span_kind"] == aw_kind
    assert out.get("gen_ai.operation.name") == operation
    assert "extra" not in out


def test_span_kind_is_case_insensitive():
    out = normalize_otel_span({"openinference.span.kind": "llm"})
    assert out["gen_ai.operation.name"] == "chat"


def test_unknown_span_kind_goes_to_extra_without_error():
    out = normalize_otel_span({"openinference.span.kind": "GUARDRAIL"})
    assert "panoptis.span_kind" not in out
    assert out["extra"] == {"openinference.span.kind": "GUARDRAIL"}


def test_tool_span_maps_name_and_payload():
    out = normalize_otel_span(
        {
            "openinference.span.kind": "TOOL",
            "tool.name": "get_weather",
            "input.value": '{"city": "Pune"}',
            "output.value": "sunny",
        }
    )
    assert out["gen_ai.operation.name"] == "execute_tool"
    assert out["gen_ai.tool.name"] == "get_weather"
    assert out["gen_ai.tool.call.arguments"] == {"city": "Pune"}
    assert out["gen_ai.tool.call.result"] == "sunny"


def test_agent_name_mapped():
    out = normalize_otel_span({"openinference.span.kind": "AGENT", "agent.name": "planner"})
    assert out["gen_ai.agent.name"] == "planner"
    assert out["gen_ai.operation.name"] == "invoke_agent"


def test_hybrid_official_value_wins_and_openinference_kept_in_extra():
    out = normalize_otel_span(
        {
            "gen_ai.request.model": "official-model",
            "llm.model_name": "oi-model",
            "gen_ai.usage.input_tokens": 10,
            "llm.token_count.prompt": 99,
        }
    )
    assert out["gen_ai.request.model"] == "official-model"
    assert out["gen_ai.usage.input_tokens"] == 10
    assert out["extra"] == {"llm.model_name": "oi-model", "llm.token_count.prompt": 99}


def test_hybrid_agreeing_values_do_not_duplicate_into_extra():
    out = normalize_otel_span({"gen_ai.request.model": "m", "llm.model_name": "m"})
    assert out == {"gen_ai.request.model": "m"}


def test_hybrid_official_operation_beats_span_kind():
    out = normalize_otel_span(
        {"gen_ai.operation.name": "embeddings", "openinference.span.kind": "LLM"}
    )
    assert out["gen_ai.operation.name"] == "embeddings"
    assert out["extra"] == {"openinference.span.kind": "LLM"}


def test_unmapped_openinference_attributes_kept_in_extra():
    out = normalize_otel_span(
        {"llm.invocation_parameters": '{"t": 1}', "session.id": "s1", "retrieval.documents.0": "d"}
    )
    assert out == {
        "extra": {
            "llm.invocation_parameters": '{"t": 1}',
            "session.id": "s1",
            "retrieval.documents.0": "d",
        }
    }


def test_unrelated_vendor_attributes_pass_through():
    out = normalize_otel_span({"vendor.flag": True, "http.method": "GET"})
    assert out == {"vendor.flag": True, "http.method": "GET"}


def test_none_values_count_as_absent():
    out = normalize_otel_span({"gen_ai.request.model": None, "llm.model_name": "m"})
    assert out["gen_ai.request.model"] == "m"
    assert "extra" not in out
    assert normalize_otel_span({"llm.model_name": None}) == {}


def test_zero_token_count_is_a_real_value():
    out = normalize_otel_span({"llm.token_count.prompt": 0, "llm.token_count.total": "0"})
    assert out["gen_ai.usage.input_tokens"] == 0
    assert out["gen_ai.usage.total_tokens"] == 0


def test_token_counts_as_strings_become_ints():
    out = normalize_otel_span({"llm.token_count.completion": "42"})
    assert out["gen_ai.usage.output_tokens"] == 42


def test_non_numeric_token_count_goes_to_extra():
    out = normalize_otel_span({"llm.token_count.prompt": "lots", "llm.token_count.total": True})
    assert "gen_ai.usage.input_tokens" not in out
    assert out["extra"] == {"llm.token_count.prompt": "lots", "llm.token_count.total": True}


def test_flattened_messages():
    out = normalize_otel_span(
        {
            "llm.input_messages.1.message.content": "second",
            "llm.input_messages.0.message.role": "user",
            "llm.input_messages.0.message.content": "first",
            "llm.output_messages.0.message.content": "answer",
        }
    )
    assert out["gen_ai.input.messages"] == [
        {"role": "user", "content": "first"},
        {"content": "second"},
    ]
    assert out["gen_ai.output.messages"] == [{"content": "answer"}]
    assert "extra" not in out


def test_json_string_and_object_messages_are_equivalent():
    msgs = [{"role": "user", "content": "hi"}]
    as_list = normalize_otel_span({"llm.input_messages": msgs})
    as_json = normalize_otel_span({"llm.input_messages": '[{"role": "user", "content": "hi"}]'})
    assert as_list == as_json == {"gen_ai.input.messages": msgs}


def test_invalid_json_messages_are_kept_as_given():
    out = normalize_otel_span({"llm.input_messages": "[not json"})
    assert out["gen_ai.input.messages"] == "[not json"


def test_conflicting_messages_keep_official_and_extra():
    official = [{"role": "user", "content": "a"}]
    out = normalize_otel_span(
        {"gen_ai.input.messages": official, "llm.input_messages.0.message.content": "b"}
    )
    assert out["gen_ai.input.messages"] == official
    assert out["extra"] == {"llm.input_messages": [{"content": "b"}]}


def test_input_is_not_mutated():
    attrs = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "m",
        "gen_ai.request.model": "other",
        "llm.input_messages": [{"role": "user", "content": "hi"}],
        "extra": {"kept": 1},
    }
    before = copy.deepcopy(attrs)
    out = normalize_otel_span(attrs)
    assert attrs == before
    out["extra"]["new"] = 1
    assert attrs == before


def test_idempotent():
    once = normalize_otel_span(
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "a",
            "gen_ai.request.model": "b",
            "session.id": "s",
            "llm.input_messages.0.message.content": "hi",
        }
    )
    assert normalize_otel_span(once) == once


def test_existing_extra_dict_is_merged():
    out = normalize_otel_span({"extra": {"a": 1}, "session.id": "s"})
    assert out["extra"] == {"a": 1, "session.id": "s"}
