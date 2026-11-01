"""The API, the CLI and the read model must show the same canonical data (release gate).

Each API surface is compared with what Workspace / the store / the CLI returns for the same
store, so the three interfaces cannot drift into separate interpretations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from panoptis.api import v3 as v3api
from panoptis.cli.main import app as cli_app
from panoptis.lab.branch import branch_history
from panoptis.lab.observe import observe
from panoptis.provenance.lineage import dependents, lineage
from panoptis.query.workspace import Workspace
from panoptis.runtime.engine import Engine

EXAMPLE = str(Path(__file__).resolve().parents[2] / "examples" / "research_system.py")


@pytest.fixture
def setup(store, monkeypatch):
    from panoptis.api import server
    from panoptis.api.middleware.rate_limiter import InMemoryBackend
    from panoptis.api.server import app

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    monkeypatch.setenv("AGENTWATCH_ALLOW_REEXECUTION", "1")
    monkeypatch.setenv("AGENTWATCH_STORE", store.url)
    monkeypatch.setenv("COLUMNS", "250")
    engine = Engine(store)
    normal = observe(engine, ["python", EXAMPLE]).run_id
    flaky = observe(engine, ["python", EXAMPLE, "--variant", "flaky_tool"]).run_id
    v3api.set_engine(engine)
    yield TestClient(app), engine, normal, flaky
    v3api.set_engine(None)


def cli_json(*args: str):
    r = CliRunner().invoke(cli_app, [*args, "--json"])
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


def canon(x):
    return json.loads(json.dumps(x, default=str, sort_keys=True))


def test_runs_events_entities_match(setup):
    c, engine, normal, _ = setup
    ws = Workspace(engine)
    assert canon(c.get("/api/v3/runs").json()["runs"]) == canon(ws.runs(limit=100))
    assert canon(c.get(f"/api/v3/runs/{normal}/events").json()["events"]) == canon(
        ws.events(normal)
    )
    assert canon(c.get("/api/v3/entities").json()) == canon(ws.entities())
    assert canon(cli_json("events", normal[:8])) == canon(ws.events(normal))


def test_graphs_match_for_every_view(setup):
    c, engine, normal, _ = setup
    ws = Workspace(engine)
    for view in ("all", "execution", "information"):
        api = c.get(f"/api/v3/runs/{normal}/graph?view={view}").json()
        rels = ws.relations(normal)
        if view != "all":
            rels = [r for r in rels if r["view"] == view.upper()]
        assert canon(api["relations"]) == canon(rels)
        cli = json.loads(
            CliRunner()
            .invoke(cli_app, ["graph", normal[:8], "--view", view, "--format", "json"])
            .output
        )
        assert canon(cli["relations"]) == canon(rels)
        assert {n["node"] for n in api["nodes"]} == {n["node"] for n in cli["nodes"]}


def test_provenance_dependents_and_ambiguity_match(setup):
    c, engine, normal, _ = setup
    ws = Workspace(engine)
    api = c.get(f"/api/v3/provenance/report.md?run={normal}").json()
    direct = lineage(ws, "report.md", run_id=normal)
    assert canon(api["root"]) == canon(direct["root"])
    assert canon(api["metrics"]) == canon(direct["metrics"])
    assert canon(cli_json("provenance", "report.md", "--run", normal[:8])["root"]) == canon(
        direct["root"]
    )
    tool = ws.events(normal, kind="TOOL_INVOCATION")[0]
    api_dep = c.get(f"/api/v3/dependents/event:{tool['event_id']}").json()
    assert api_dep["count"] == dependents(ws, f"event:{tool['event_id']}")["count"]
    evidence = c.get(f"/api/v3/runs/{normal}/provenance-evidence").json()
    assert canon(evidence["ambiguous"]) == canon(ws.derived("ambiguous_provenance", normal))
    assert evidence["summary"] == canon(next(iter(ws.derived("information_evidence", normal))))
    inst = c.get(f"/api/v3/instances/{direct['node']}").json()
    assert inst["instance"]["node"] == direct["node"]


def test_motifs_compare_causal_match(setup):
    c, engine, normal, flaky = setup
    ws = Workspace(engine)
    assert canon(c.get(f"/api/v3/runs/{flaky}/motifs").json()) == canon(
        ws.derived("motif_instance", flaky)
    )
    from panoptis.compare.runs import compare

    api_cmp = c.get(f"/api/v3/compare?a={normal}&b={flaky}").json()
    direct = compare(ws, normal, flaky)
    assert canon(api_cmp["earliest_divergence"]) == canon(direct["earliest_divergence"])
    tool = ws.events(flaky, kind="TOOL_INVOCATION")[0]
    from panoptis.causality.cones import causes, effects

    api_causes = c.get(f"/api/v3/causes/{tool['event_id']}").json()
    assert canon(api_causes["dependencies"]) == canon(causes(ws, tool["event_id"])["dependencies"])
    api_effects = c.get(f"/api/v3/effects/{tool['event_id']}").json()
    assert canon(api_effects["dependents"]) == canon(effects(ws, tool["event_id"])["dependents"])


def test_replay_experiments_and_erasure_match(setup):
    c, engine, normal, _ = setup
    ws = Workspace(engine)
    rep = c.post("/api/v3/replay", json={"run": normal, "level": "L1"}).json()
    assert rep["replay_level"] == "L1" and "reproduction_confidence" in rep
    tool = ws.events(normal, kind="TOOL_INVOCATION")[0]
    br = c.post(
        "/api/v3/branches",
        json={"run": normal, "at": tool["event_id"], "substitute": 7, "level": "L2"},
    ).json()
    bid = br["branch"]["branch_id"]
    assert c.get(f"/api/v3/experiments/{bid}").json()["record_type"] == "branch"
    listed = c.get(f"/api/v3/experiments?record_type=branch&subject={normal}").json()
    assert bid in {e.get("branch_id") or e.get("record_id") for e in listed}
    assert canon(c.get(f"/api/v3/runs/{normal}/branches").json()) == canon(
        branch_history(Workspace(engine), normal)
    )
    assert c.get("/api/v3/experiments/does-not-exist").status_code == 404
    erased = c.post("/api/v3/evidence/erase", json={"subject": "nobody", "reason": "parity test"})
    assert erased.status_code == 200, erased.text
    log = c.get("/api/v3/evidence/erasures").json()
    assert canon(log) == canon(engine.store.erasures("default"))
    assert [e["subject"] for e in log] == ["nobody"]
