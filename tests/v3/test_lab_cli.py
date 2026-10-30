"""End-to-end: observe a real program, replay at each level, branch, counterfactual, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from panoptis.cli.main import app
from panoptis.lab.branch import branch_history, counterfactual, create_branch, execute_branch
from panoptis.lab.observe import observe
from panoptis.lab.replay import replay
from panoptis.query.workspace import Workspace, run_order_key

EXAMPLE = str(Path(__file__).resolve().parents[2] / "examples" / "research_system.py")
OFF_TOPIC = [
    {
        "id": "D5",
        "text": "Medieval castles were built with thick stone walls and moats to defend against attackers.",
    }
]


@pytest.fixture
def observed(engine):
    normal = observe(engine, ["python", EXAMPLE])
    flaky = observe(engine, ["python", EXAMPLE, "--variant", "flaky_tool"])
    assert normal.exit_code == 0 and normal.run_id, normal.stderr
    assert flaky.exit_code == 0 and flaky.run_id, flaky.stderr
    return engine, normal.run_id, flaky.run_id


def test_observe_records_command_and_structure(observed):
    engine, normal, _ = observed
    ws = Workspace(engine)
    run = ws.resolve_run(normal)
    assert run["attributes"]["command"][-1].endswith("research_system.py")
    assert run["completeness"] == 1.0 and run["status"] == "OK"
    kinds = {e["kind"] for e in ws.events(normal)}
    assert {
        "RETRIEVAL",
        "MODEL_INVOCATION",
        "TOOL_INVOCATION",
        "MEMORY_ACCESS",
        "DELEGATION",
        "MESSAGE",
        "STATE_MUTATION",
    } <= kinds


def test_replay_levels(observed):
    engine, normal, flaky = observed
    ws = Workspace(engine)
    l0 = replay(ws, normal, "L0")
    assert l0["timeline"] and l0["mocked_components"] == []
    l1 = replay(ws, normal, "L1")
    assert (
        l1["consistent_with_stored_interpretation"]
        and l1["reproduction_confidence"]["value"] == 1.0
    )
    l2 = replay(Workspace(engine), flaky, "L2")
    assert l2["exit_code"] == 0 and l2["reproduction_confidence"]["value"] == 1.0
    assert (
        "TOOL_INVOCATION|calculator|1" in l2["mocked_components"]
    )  # the recorded failure is replayed
    assert l2["comparison"]["resources"]["retries"]["b"] == 2
    assert not l2["reproduction_confidence"]["calibrated"]
    recorded = Workspace(engine).store.experiments("default", "replay", subject=flaky)
    assert recorded


def test_branch_substitution_changes_downstream_and_history(observed):
    engine, normal, _ = observed
    ws = Workspace(engine)
    retrieval = ws.events(normal, kind="RETRIEVAL")[0]
    br = create_branch(ws, normal, retrieval["event_id"], OFF_TOPIC, level="L3")
    res = execute_branch(ws, br["branch_id"])
    assert res["label"] == "SIMULATED"
    assert res["final_outputs_changed"] is True
    assert res["divergence"]["b_event"]["label"] == "RETRIEVAL corpus-index"
    assert any("summarizer" in k for k in res["live_components"])  # changed input → ran live
    hist = branch_history(Workspace(engine), normal)
    assert hist["branches"][0]["result"]["branch_run"] == res["branch_run"]


def test_branch_requires_instrumented_call(observed):
    engine, normal, _ = observed
    ws = Workspace(engine)
    plan = next(e for e in ws.events(normal) if e["operation"] == "plan")
    with pytest.raises(ValueError):
        create_branch(ws, normal, plan["event_id"], "x")


def test_counterfactual_labels(observed):
    engine, normal, _ = observed
    ws = Workspace(engine)
    retrieval = ws.events(normal, kind="RETRIEVAL")[0]
    sim = counterfactual(ws, normal, retrieval["event_id"], OFF_TOPIC)
    assert sim["estimate"]["label"] == "SIMULATED" and sim["maturity"] == "EXPERIMENTAL"
    assert "reproduction_confidence" in sim["estimate"]["uncertainty"]
    est = counterfactual(Workspace(engine), normal, retrieval["event_id"], OFF_TOPIC, execute=False)
    assert (
        est["estimate"]["label"] == "UNKNOWN"
    )  # fewer than 3 historical runs with that alternative


def test_cli_end_to_end(observed, monkeypatch, store):
    engine, normal, flaky = observed
    monkeypatch.setenv("AGENTWATCH_STORE", store.url)
    monkeypatch.setenv("COLUMNS", "250")  # rich tables must not be cropped in the test terminal
    runner = CliRunner()

    def run(*args: str) -> str:
        r = runner.invoke(app, list(args))
        assert r.exit_code == 0, r.output
        return r.output

    assert "research_system" in run("runs")
    out = run("inspect", flaky[:8])
    assert "retry_loop" in out and "TOOL_INVOCATION calculator" in out
    assert "RETRIEVAL corpus-index" in run("provenance", "report.md", "--run", normal[:8])
    assert "Earliest divergence" in run("compare", normal[:8], flaky[:8])
    assert "M001" in run("motifs", flaky[:8])
    data = json.loads(run("graph", normal[:8], "--format", "json", "--view", "information"))
    assert data["relations"] and all(r["view"] == "INFORMATION" for r in data["relations"])
    assert "digraph" in run("graph", normal[:8], "--format", "dot")
    tool = Workspace(engine).events(flaky, kind="TOOL_INVOCATION")[0]
    out = run("causes", tool["event_id"][:10])
    assert "dependencies" in out and "interventions" in out
    assert "reproduction confidence" in run("replay", normal[:8], "--level", "L1")
    assert json.loads(run("evidence", "verify"))["ok"] is True
    assert "Earliest divergence" in run(
        "query", f"why did {flaky[:8]} take longer than {normal[:8]}?"
    )
    assert "capabilities" in run("status")
    bad = runner.invoke(app, ["inspect", "nonexistent-run"])
    assert bad.exit_code == 1 and "no run matches" in bad.output


def test_cli_ingest_legacy_jsonl(tmp_path, monkeypatch, store):
    monkeypatch.setenv("AGENTWATCH_STORE", store.url)
    p = tmp_path / "legacy.jsonl"
    p.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {
                    "event_id": "e1",
                    "session_id": "s1",
                    "agent_id": "a",
                    "event_type": "session.start",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
                {
                    "event_id": "e2",
                    "session_id": "s1",
                    "agent_id": "a",
                    "event_type": "tool.call",
                    "tool_call": {"tool_name": "ls", "tool_id": "t1"},
                },
                {"event_id": "e3", "session_id": "s1", "agent_id": "a", "event_type": "bogus"},
            ]
        ),
        encoding="utf-8",
    )
    r = CliRunner().invoke(app, ["ingest", str(p), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["format"] == "legacy-jsonl"
    assert out["translation"]["statuses"] == {"ACCEPTED_WITH_LOSS": 2, "REJECTED": 1}
    assert out["translation"]["lost_fields"]["parent"] == 2


def test_cli_full_surface_on_persisted_data(tmp_path, monkeypatch):
    """Every major command against a store written by `panoptis observe` (release gate)."""
    from panoptis.storage.store import Store

    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    monkeypatch.setenv("AGENTWATCH_STORE", url)
    monkeypatch.setenv("AGENTWATCH_ENCRYPT_PAYLOADS", "1")
    monkeypatch.setenv("COLUMNS", "250")
    runner = CliRunner()

    def run(*args: str) -> str:
        r = runner.invoke(app, list(args))
        assert r.exit_code == 0, (args, r.output)
        return r.output

    run("observe", "python", EXAMPLE)
    run("observe", "python", EXAMPLE, "--variant", "flaky_tool")
    ws = Workspace(Store(url))
    normal, flaky = (r["run_id"] for r in sorted(ws.runs(), key=run_order_key))
    assert normal[:8] in run("runs")
    assert "execution" in run("inspect", normal[:8]) and "information lineage" in run(
        "inspect", normal[:8]
    )
    events = json.loads(run("events", normal[:8], "--json"))
    assert events
    tool = next(e for e in events if e["kind"] == "TOOL_INVOCATION")
    assert tool["event_id"][:8] in run("show", tool["event_id"][:10])
    for view in ("execution", "information", "causal", "all"):
        for fmt in ("text", "dot", "json"):
            run("graph", normal[:8], "--view", view, "--format", fmt)
    assert "ambiguous candidates" in run("provenance", "report.md", "--run", normal[:8])
    assert json.loads(run("provenance", "report.md", "--run", normal[:8], "--json"))["root"]
    run("dependents", tool["event_id"][:10])
    assert "Earliest divergence" in run("compare", normal[:8], flaky[:8])
    run("motifs", flaky[:8])
    run("motifs")
    run("causes", tool["event_id"][:10])
    run("effects", tool["event_id"][:10])
    for level in ("L0", "L1", "L2"):
        assert "reproduction confidence" in run("replay", normal[:8], "--level", level)
    out = run("branch", normal[:8], "--at", tool["event_id"][:10], "--substitute", "42")
    assert "branch" in out.lower()
    assert ":= 42" in run("branches", normal[:8])
    run("genome")
    run("states")
    run("forecast", normal[:8])
    assert "store" in run("status")
    assert json.loads(run("evidence", "verify"))["ok"] is True
    doctor = run("doctor")
    assert "v3 store" in doctor and "v3 evidence chain" in doctor
    refused = runner.invoke(app, ["evidence", "erase-subject", "nobody", "--reason", "test"])
    assert refused.exit_code == 1 and "--yes" in refused.output
    erased = json.loads(run("evidence", "erase-subject", "nobody", "--reason", "test", "--yes"))
    assert "subject" in json.dumps(erased)


def test_observe_rejects_unsafe_arguments_without_a_traceback(tmp_path, monkeypatch):
    """Regression: an argument the command validator refuses (e.g. containing ';') crashed
    `observe` with a traceback instead of a clean error."""
    monkeypatch.setenv("AGENTWATCH_STORE", f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    r = CliRunner().invoke(app, ["observe", "python", "-c", "import sys; print(1)"])
    assert r.exit_code == 1
    assert "refused to run the command" in r.output
    assert "Traceback" not in r.output
