"""Real-model integration tests (opt-in; they call provider APIs and cost money).

    OPENAI_API_KEY=... ANTHROPIC_API_KEY=... pytest tests/v3/test_real_models.py -m external

Without credentials every test is skipped with the reason SKIPPED_EXTERNAL_CREDENTIAL; a skip
is never reported as a pass. Models can be overridden with AGENTWATCH_TEST_OPENAI_MODEL and
AGENTWATCH_TEST_ANTHROPIC_MODEL.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest

from panoptis import instrument as aw
from panoptis.provenance.lineage import lineage
from panoptis.query.workspace import Workspace

pytestmark = pytest.mark.external

OPENAI_MODEL = os.environ.get("AGENTWATCH_TEST_OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.environ.get("AGENTWATCH_TEST_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
DOCS = [
    {
        "id": "D1",
        "text": "Solar panels convert sunlight into electricity using photovoltaic cells.",
    },
    {"id": "D2", "text": "Home batteries store surplus solar energy for use after sunset."},
]


def needs(var: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not os.environ.get(var), reason=f"SKIPPED_EXTERNAL_CREDENTIAL: {var} is not set"
    )


def credential_usable(call: Callable[[], object], provider: str) -> None:
    """Skip (never pass) when the provider rejects the credential: a set but deactivated or
    invalid key is as unusable as a missing one. Any other error fails the test."""
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - classified below
        status = getattr(exc, "status_code", None)
        if status in (401, 403, 429):
            code = (
                (getattr(exc, "body", None) or {}).get("code")
                if isinstance(getattr(exc, "body", None), dict)
                else None
            )
            pytest.skip(
                f"SKIPPED_EXTERNAL_CREDENTIAL: {provider} rejected the credential "
                f"(HTTP {status}{f', {code}' if code else ''})"
            )
        raise


def _is_quota_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return any(
        tok in msg for tok in ("insufficient_quota", "credit_balance", "quota", "rate_limit")
    )


def rag_run(engine, sink, answer: Callable[[str], str]) -> tuple[Workspace, str]:
    @aw.retriever("kb")
    def search(q: str) -> list[dict[str, str]]:
        return DOCS

    with aw.run("real-model-rag"):
        with aw.span("OPERATION", "answer_question", actor="agent:assistant"):
            docs = search("home solar")
            prompt = (
                "Answer in two sentences using only this context.\n"
                + "\n".join(f"- {d['text']}" for d in docs)
                + "\nQuestion: how can a home use solar power at night?"
            )
            text = answer(prompt)
            with aw.span("STATE_MUTATION", "write_report", facets=["artifact_creation"]) as s:
                s.input(text)  # the very object the model call returned: a declared reference
                s.output("# Report\n" + text, role="artifact", label="report.md")
    engine.ingest(sink.drafts)
    ws = Workspace(engine)
    return ws, ws.resolve_run("latest")["run_id"]


def check(ws: Workspace, run_id: str, provider_kind: str = "MODEL_INVOCATION") -> None:
    models = ws.events(run_id, kind=provider_kind)
    assert models, "the provider call was not normalized to a model invocation"
    assert any((m["resources"].get("tokens_in") or 0) > 0 for m in models), "no token usage"
    report = next(e for e in ws.events(run_id) if "artifact_creation" in e["facets"])
    res = lineage(ws, f"inst:{report['event_id']}/o0", run_id=run_id)
    text = "\n".join(str(x) for x in [res["root"]])
    assert "RETRIEVAL" in text or any("RETRIEVAL" in str(n) for n in res["metrics"]["origins"]), (
        "the retrieved documents are not in the report's lineage"
    )
    info = next(iter(ws.derived("information_evidence", run_id)))
    assert (info["high_fidelity_share"] or 0) > 0


@needs("OPENAI_API_KEY")
def test_openai_client_rag_lineage(engine, sink):
    openai = pytest.importorskip("openai")
    from panoptis.sensors.llm_clients import instrument_openai

    client = openai.OpenAI()
    credential_usable(lambda: client.models.list(), "OpenAI")
    instrument_openai(client, sink)

    def answer(prompt: str) -> str:
        r = client.chat.completions.create(
            model=OPENAI_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0
        )
        return r.choices[0].message.content or ""

    try:
        ws, rid = rag_run(engine, sink, answer)
    except Exception as exc:  # noqa: BLE001
        if _is_quota_error(exc):
            pytest.skip(f"SKIPPED_EXTERNAL_CREDENTIAL: OpenAI quota exhausted ({exc})")
        raise
    check(ws, rid)


@needs("ANTHROPIC_API_KEY")
def test_anthropic_client_rag_lineage(engine, sink):
    anthropic = pytest.importorskip("anthropic")
    from panoptis.sensors.llm_clients import instrument_anthropic

    client = anthropic.Anthropic()
    credential_usable(lambda: client.models.list(limit=1), "Anthropic")
    instrument_anthropic(client, sink)

    def answer(prompt: str) -> str:
        r = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=200,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")

    try:
        ws, rid = rag_run(engine, sink, answer)
    except Exception as exc:  # noqa: BLE001
        if _is_quota_error(exc):
            pytest.skip(f"SKIPPED_EXTERNAL_CREDENTIAL: Anthropic quota exhausted ({exc})")
        raise
    check(ws, rid)
