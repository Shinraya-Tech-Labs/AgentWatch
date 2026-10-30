"""Replay at explicit levels. Each result states what was reproduced, how, and how
confidently — replay is never claimed to be deterministic when it is not.

* L0 EVENT REPLAY       — reconstruct the recorded timeline (no execution).
* L1 DEPENDENCY REPLAY  — re-derive the run's structure from raw evidence alone and check
                          it matches the stored interpretation; report missing dependencies.
* L2 MOCK REPLAY        — re-execute the recorded command; every instrumented call is served
                          from its captured result; program logic runs live.
* L3 PARTIAL RE-EXECUTION — like L2, but selected operations (or capture misses) run live.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from panoptis.compare.runs import compare
from panoptis.evidence.canonical import canonical_json, sha256_hex
from panoptis.lab.observe import observe
from panoptis.query.workspace import Workspace

LEVELS = ("L0", "L1", "L2", "L3")


class ReplayUnavailableError(RuntimeError):
    pass


def captures_for(ws: Workspace, run_id: str) -> dict[str, dict[str, Any]]:
    caps: dict[str, dict[str, Any]] = {}
    for e in ws.events(run_id):
        key = e["attributes"].get("call_key")
        if not key:
            continue
        out = next(
            (
                o
                for o in e["outputs"]
                if o["role"] in ("result", "completion", "documents", "output")
            ),
            None,
        )
        value = None
        if out is not None:
            art = ws.store.artifact(ws.tenant_id, out["artifact_id"])
            value = art["content"] if art else None
        inp = e["inputs"][0] if e["inputs"] else None
        in_art = ws.store.artifact(ws.tenant_id, inp["artifact_id"]) if inp else None
        caps[key] = {
            "input_sha": sha256_hex(canonical_json(in_art["content"])) if in_art else None,
            "value": value,
            "status": e["status"],
            "error": e.get("error"),
            "faithful": e["status"] != "OK"
            or (out is not None and "non_json_values" not in e["attributes"]),
            "event_id": e["event_id"],
        }
    return caps


def _command(run: dict[str, Any]) -> list[str]:
    cmd = (run.get("attributes") or {}).get("command")
    if not cmd:
        raise ReplayUnavailableError(
            "run has no recorded command; L2/L3 replay needs a run captured with `panoptis observe`"
        )
    return [str(c) for c in cmd]


def replay(
    ws: Workspace,
    run_ref: str,
    level: str = "L1",
    *,
    live: list[str] | None = None,
    substitutions: dict[str, Any] | None = None,
    branch_id: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    run = ws.resolve_run(run_ref)
    rid = run["run_id"]
    if level == "L0":
        result = _l0(ws, run)
    elif level == "L1":
        result = _l1(ws, run)
    else:
        result = _execute(ws, run, level, live or [], substitutions or {}, branch_id)
    result["source_run"] = rid
    if record:
        result["record_id"] = ws.store.put_experiment(ws.tenant_id, "replay", result, subject=rid)
    return result


def _l0(ws: Workspace, run: dict[str, Any]) -> dict[str, Any]:
    events = ws.events(run["run_id"])
    return {
        "replay_level": "L0",
        "description": "timeline reconstructed from stored evidence; nothing was executed",
        "timeline": [
            {
                "event_id": e["event_id"],
                "t": e["time"]["start"],
                "kind": e["kind"],
                "operation": e["operation"],
                "status": e["status"],
            }
            for e in events
        ],
        "reproduction_confidence": {
            "value": 1.0,
            "basis": "recorded events are displayed as observed",
            "calibrated": False,
        },
        "missing_dependencies": run.get("unresolved_links", []),
        "mocked_components": [],
        "live_components": [],
    }


def _l1(ws: Workspace, run: dict[str, Any]) -> dict[str, Any]:
    stored = ws.events(run["run_id"])
    obs_ids = sorted({o for e in stored for o in e["derived_from"]})
    observations = ws.store.observations(ws.tenant_id, obs_ids=obs_ids)
    built = ws.engine.build(ws.tenant_id, ws.interp_id, observations)
    rebuilt_ids = {
        e.event_id for e in built["events"] if built["run_of"].get(e.event_id) == run["run_id"]
    }
    stored_ids = {e["event_id"] for e in stored}
    stored_rel = {r["rel_id"] for r in ws.relations(run["run_id"]) if r["view"] == "EXECUTION"}
    rebuilt_rel = {
        r["rel_id"]
        for r in built["relations"]
        if r["view"] == "EXECUTION" and r.get("run_id") == run["run_id"]
    }
    consistent = rebuilt_ids == stored_ids and rebuilt_rel == stored_rel
    completeness = float(run.get("completeness") or 0.0)
    return {
        "replay_level": "L1",
        "description": "run structure re-derived from this run's raw observations only",
        "consistent_with_stored_interpretation": consistent,
        "events": {
            "stored": len(stored_ids),
            "rebuilt": len(rebuilt_ids),
            "missing": sorted(stored_ids - rebuilt_ids),
            "extra": sorted(rebuilt_ids - stored_ids),
        },
        "execution_relations": {"stored": len(stored_rel), "rebuilt": len(rebuilt_rel)},
        "reproduction_confidence": {
            "value": round(completeness if consistent else completeness * 0.5, 4),
            "basis": "declared-link completeness of the run"
            + ("" if consistent else ", halved: rebuild differs from stored interpretation"),
            "calibrated": False,
        },
        "missing_dependencies": run.get("unresolved_links", []),
        "mocked_components": [],
        "live_components": [],
    }


def _execute(
    ws: Workspace,
    run: dict[str, Any],
    level: str,
    live: list[str],
    substitutions: dict[str, Any],
    branch_id: str | None,
) -> dict[str, Any]:
    command = _command(run)
    caps = captures_for(ws, run["run_id"])
    if not caps:
        raise ReplayUnavailableError("run has no instrumented calls with call keys to mock")
    with tempfile.TemporaryDirectory(prefix="panoptis-replay-") as tmp:
        spec_path = Path(tmp) / "spec.json"
        report_path = Path(tmp) / "report.json"
        spec = {
            "level": level,
            "captures": {
                k: {kk: vv for kk, vv in v.items() if kk != "event_id"} for k, v in caps.items()
            },
            "substitutions": substitutions,
            "live": live,
            "report_file": str(report_path),
        }
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        env = {"AGENTWATCH_REPLAY_SPEC": str(spec_path), "AGENTWATCH_REPLAY_OF": run["run_id"]}
        if branch_id:
            env["AGENTWATCH_BRANCH_ID"] = branch_id
        cwd_attr = (run.get("attributes") or {}).get("cwd")
        res = observe(ws.engine, command, tenant_id=ws.tenant_id, env=env)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    fresh = Workspace(ws.engine, ws.tenant_id)
    comparison = compare(fresh, run["run_id"], res.run_id) if res.run_id else None
    reproduced = _reproduction(comparison)
    uninstrumented = "program logic outside instrumented calls (re-executed live)"
    return {
        "replay_level": level,
        "description": "recorded command re-executed with instrumented calls served from captures"
        + (" and selected components live" if level == "L3" else ""),
        "replay_run": res.run_id,
        "exit_code": res.exit_code,
        "stderr_tail": res.stderr[-2000:],
        "controller": report,
        "mocked_components": sorted(set(report.get("served", []))),
        "live_components": [uninstrumented, *sorted(set(report.get("ran_live", [])))],
        "substituted": report.get("substituted", []),
        "missing_dependencies": report.get("missing", []),
        "stale_captures": report.get("stale_captures", []),
        "working_directory_at_capture": cwd_attr,
        "comparison": comparison,
        "reproduction_confidence": reproduced,
        "determinism_note": "Mocked calls return captured values; any live component (including program logic, clocks, randomness, network) may behave differently than in the original run.",
    }


def _reproduction(comparison: dict[str, Any] | None) -> dict[str, Any]:
    if comparison is None:
        return {"value": 0.0, "basis": "replay produced no observable run", "calibrated": False}
    sim = comparison["structural"]["alignment_similarity"]
    div = comparison["earliest_divergence"]
    return {
        "value": round(sim if div else 1.0, 4),
        "basis": "signature alignment similarity between original and replay"
        + (
            "; first divergence: " + "; ".join(div["reasons"])
            if div
            else "; no divergence observed"
        ),
        "calibrated": False,
    }


def replay_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, default=str)
