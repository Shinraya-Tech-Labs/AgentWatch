"""LangChain / LangGraph callback sensor tests.

Callbacks are driven with duck-typed fakes: no live LangChain and no model calls.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace

import pytest

from panoptis.sensors.base import ListSink
from panoptis.sensors.langchain import (
    LANGCHAIN_AVAILABLE,
    AgentWatchLangChainSensor,
    require_langchain,
)


def make():
    sink = ListSink()
    return AgentWatchLangChainSensor(sink), sink


def kinds(sink):
    return [d.source_kind for d in sink.drafts]


def payload(draft):
    return draft.payload


def rid():
    return uuid.uuid4()


def test_llm_success_maps_prompt_output_and_ids():
    s, sink = make()
    r = rid()
    s.on_llm_start({"name": "fake"}, ["hi"], run_id=r, tags=["t"], metadata={"m": 1})
    gen = SimpleNamespace(text="hello", message=None)
    s.on_llm_end(SimpleNamespace(generations=[[gen]], llm_output=None), run_id=r)
    assert kinds(sink) == ["langchain.llm_start", "langchain.llm_end"]
    start, end = sink.drafts
    assert start.declared_ids["run_id"] == str(r)
    assert "parent_run_id" not in start.declared_ids
    assert start.declared_ids["root_run_id"] == str(r)
    assert payload(start)["prompts"] == ["hi"]
    assert payload(start)["metadata"] == {"m": 1}
    assert payload(end)["generations"] == ["hello"]
    assert payload(end)["duration_ms"] >= 0


def test_token_usage_from_llm_output():
    s, sink = make()
    r = rid()
    s.on_llm_start({}, ["p"], run_id=r)
    resp = SimpleNamespace(
        generations=[[SimpleNamespace(text="x")]],
        llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 5}},
    )
    s.on_llm_end(resp, run_id=r)
    assert payload(sink.drafts[1])["token_usage"] == {"input_tokens": 3, "output_tokens": 5}


def test_token_usage_from_message_usage_metadata():
    s, sink = make()
    msg = SimpleNamespace(content="x", usage_metadata={"input_tokens": 7, "output_tokens": 2})
    gen = SimpleNamespace(text="", message=msg)
    s.on_llm_end(SimpleNamespace(generations=[[gen]], llm_output=None), run_id=rid())
    p = payload(sink.drafts[0])
    assert p["token_usage"] == {"input_tokens": 7, "output_tokens": 2}
    assert p["generations"] == ["x"]


def test_no_usage_means_no_token_usage_key():
    s, sink = make()
    s.on_llm_end(SimpleNamespace(generations=[], llm_output=None), run_id=rid())
    assert "token_usage" not in payload(sink.drafts[0])


def test_chat_model_start_keeps_message_type_and_content():
    s, sink = make()
    msgs = [[SimpleNamespace(type="human", content="q")]]
    s.on_chat_model_start({}, msgs, run_id=rid(), invocation_params={"temperature": 0.1})
    p = payload(sink.drafts[0])
    assert p["messages"] == [[{"type": "human", "content": "q"}]]
    assert p["invocation_params"] == {"temperature": 0.1}


def test_tool_success_with_tool_call_id_and_message_output():
    s, sink = make()
    r = rid()
    s.on_tool_start({"name": "calc"}, "1+1", run_id=r, inputs={"a": 1}, tool_call_id="call_9")
    s.on_tool_end(SimpleNamespace(content="2"), run_id=r)
    start, end = sink.drafts
    assert start.declared_ids["tool_call_id"] == "call_9"
    assert payload(start)["input_str"] == "1+1"
    assert payload(end)["output"] == "2"


def test_chain_start_end():
    s, sink = make()
    r = rid()
    s.on_chain_start({"id": ["x"]}, {"q": 1}, run_id=r, name="graph")
    s.on_chain_end({"a": 2}, run_id=r)
    assert payload(sink.drafts[0])["name"] == "graph"
    assert payload(sink.drafts[1])["outputs"] == {"a": 2}


@pytest.mark.parametrize(
    ("start", "err", "kind", "args"),
    [
        ("on_llm_start", "on_llm_error", "langchain.llm_error", ({}, ["p"])),
        ("on_chain_start", "on_chain_error", "langchain.chain_error", ({}, {})),
        ("on_tool_start", "on_tool_error", "langchain.tool_error", ({}, "in")),
        ("on_retriever_start", "on_retriever_error", "langchain.retriever_error", ({}, "q")),
    ],
)
def test_error_callbacks_record_type_message_and_release_state(start, err, kind, args):
    s, sink = make()
    r = rid()
    getattr(s, start)(*args, run_id=r)
    getattr(s, err)(ValueError("boom"), run_id=r)
    assert sink.drafts[-1].source_kind == kind
    assert payload(sink.drafts[-1])["error"] == {"type": "ValueError", "message": "boom"}
    assert "duration_ms" in payload(sink.drafts[-1])
    assert s._root_of == {} and s._started == {}


def test_langgraph_nesting_root_and_parent_ids():
    s, sink = make()
    root, node, llm, tool = rid(), rid(), rid(), rid()
    s.on_chain_start({}, {}, run_id=root)
    s.on_chain_start({}, {}, run_id=node, parent_run_id=root)
    s.on_llm_start({}, ["p"], run_id=llm, parent_run_id=node)
    s.on_llm_end(SimpleNamespace(generations=[]), run_id=llm, parent_run_id=node)
    s.on_tool_start({}, "x", run_id=tool, parent_run_id=node)
    s.on_tool_end("ok", run_id=tool, parent_run_id=node)
    s.on_chain_end({}, run_id=node, parent_run_id=root)
    s.on_chain_end({}, run_id=root)
    for d in sink.drafts:
        assert d.declared_ids["root_run_id"] == str(root)
    assert sink.drafts[2].declared_ids["parent_run_id"] == str(node)
    assert s._root_of == {} and s._started == {}


def test_unknown_parent_omits_root_and_still_cleans_up():
    s, sink = make()
    r = rid()
    s.on_llm_start({}, ["p"], run_id=r, parent_run_id=rid())
    assert "root_run_id" not in sink.drafts[0].declared_ids
    s.on_llm_end(SimpleNamespace(generations=[]), run_id=r)
    assert s._started == {}


def test_agent_action_does_not_leak_state():
    s, sink = make()
    r = rid()
    s.on_chain_start({}, {}, run_id=r)
    s.on_agent_action(SimpleNamespace(tool="t", tool_input={"a": 1}, log="l"), run_id=r)
    s.on_agent_finish(SimpleNamespace(return_values={"o": 1}, log="l"), run_id=r)
    s.on_chain_end({}, run_id=r)
    assert s._root_of == {} and s._started == {}
    assert kinds(sink)[1:3] == ["langchain.agent_action", "langchain.agent_finish"]


def test_retriever_documents():
    s, sink = make()
    r = rid()
    s.on_retriever_start({}, "q", run_id=r)
    doc = SimpleNamespace(page_content="text", metadata={"src": "a"})
    s.on_retriever_end([doc], run_id=r)
    assert payload(sink.drafts[1])["documents"] == [
        {"page_content": "text", "metadata": {"src": "a"}}
    ]


def test_unserializable_objects_are_not_retained():
    s, sink = make()
    s.on_chain_start({}, {"obj": object()}, run_id=rid())
    assert isinstance(payload(sink.drafts[0])["inputs"], (str, dict))


class _BoomSink:
    def emit(self, draft):
        raise RuntimeError("sink down")

    def flush(self):
        return None


def test_failing_sink_never_raises_into_chain():
    s = AgentWatchLangChainSensor(_BoomSink())
    r = rid()
    s.on_chain_start({}, {}, run_id=r)
    s.on_chain_end({}, run_id=r)
    assert s.ctx.errors >= 2


def test_malformed_events_never_raise():
    s, _ = make()
    s.on_llm_end(None, run_id=None)
    s.on_chat_model_start({}, 5, run_id=rid())
    s.on_retriever_end(5, run_id=rid())
    s.on_tool_end(object(), run_id="not-a-uuid")
    s.on_llm_start(None, None, run_id=None)

    class Hostile:
        @property
        def generations(self):
            raise RuntimeError("nope")

    s.on_llm_end(Hostile(), run_id=rid())
    assert s.ctx.errors >= 1


def test_concurrent_runs_are_safe_and_clean_up():
    s, sink = make()
    root = rid()
    s.on_chain_start({}, {}, run_id=root)

    def work():
        for _ in range(50):
            r = rid()
            s.on_tool_start({}, "x", run_id=r, parent_run_id=root)
            s.on_tool_end("y", run_id=r, parent_run_id=root)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s.on_chain_end({}, run_id=root)
    assert len(sink.drafts) == 2 + 8 * 50 * 2
    assert s._root_of == {} and s._started == {}
    seqs = [d.source_seq for d in sink.drafts]
    assert len(set(seqs)) == len(seqs)


def test_require_langchain_raises_clear_error_when_missing(monkeypatch):
    import panoptis.sensors.langchain as mod

    monkeypatch.setattr(mod, "LANGCHAIN_AVAILABLE", False)
    with pytest.raises(ImportError, match=r"panoptis\[langchain\]"):
        mod.require_langchain()
    monkeypatch.setattr(mod, "LANGCHAIN_AVAILABLE", True)
    mod.require_langchain()


def test_module_imports_and_works_without_langchain_core():
    code = (
        "import sys\n"
        "sys.modules['langchain_core'] = None\n"
        "sys.modules['langchain_core.callbacks'] = None\n"
        "import panoptis.sensors.langchain as m\n"
        "assert m.LANGCHAIN_AVAILABLE is False\n"
        "from panoptis.sensors.base import ListSink\n"
        "sink = ListSink(); s = m.AgentWatchLangChainSensor(sink)\n"
        "s.on_chain_start({}, {}, run_id='r1'); s.on_chain_end({}, run_id='r1')\n"
        "assert len(sink.drafts) == 2\n"
        "try:\n"
        "    m.require_langchain()\n"
        "except ImportError:\n"
        "    print('ok')\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_availability_flag_matches_require():
    if LANGCHAIN_AVAILABLE:
        require_langchain()
    else:
        with pytest.raises(ImportError):
            require_langchain()
