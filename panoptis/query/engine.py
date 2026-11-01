"""Query engine: structured queries first; natural language is routed to them.

Structured grammar (one command per query)::

    runs [name=<n>] [status=<s>] [limit=<k>]
    events <run> [kind=<K>] [status=<S>] [actor=<A>] [operation=<O>]
    provenance <node>            dependents <node>
    compare <runA> <runB>        motifs [<run>]
    causes <event>               effects <event>
    genome <scope>               drift <scopeA> <scopeB>

Natural language is matched against a small set of deterministic patterns and compiled
to one of the structured queries above. An optional ``planner`` callable (e.g. an LLM)
may translate free text into a structured query string; its output is parsed and
executed like any other query, and the answer is still composed only from results.
Every answer carries the evidence ids it rests on.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from typing import Any

from panoptis.query.workspace import AmbiguousError, NotFoundError, Workspace

Planner = Callable[[str], str | None]


class QueryError(ValueError):
    pass


def _kv(args: list[str]) -> tuple[list[str], dict[str, str]]:
    pos, kv = [], {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k] = v
        else:
            pos.append(a)
    return pos, kv


def execute(ws: Workspace, query: str) -> dict[str, Any]:
    parts = shlex.split(query)
    if not parts:
        raise QueryError("empty query")
    cmd, (pos, kv) = parts[0].lower(), _kv(parts[1:])
    if cmd == "runs":
        runs = ws.runs()
        if "name" in kv:
            runs = [r for r in runs if r.get("name") == kv["name"]]
        if "status" in kv:
            runs = [r for r in runs if r.get("status") == kv["status"].upper()]
        runs = runs[: int(kv.get("limit", 50))]
        return {"type": "runs", "result": runs, "evidence": [r["run_id"] for r in runs]}
    if cmd == "events":
        if not pos:
            raise QueryError("events needs a run")
        run = ws.resolve_run(pos[0])
        evs = ws.events(run["run_id"])
        for key in ("kind", "status", "actor", "operation"):
            if key in kv:
                want = kv[key].upper() if key in ("kind", "status") else kv[key]
                evs = [e for e in evs if (e.get(key) or "") == want]
        return {
            "type": "events",
            "run_id": run["run_id"],
            "result": evs,
            "evidence": [e["event_id"] for e in evs],
        }
    if cmd in ("provenance", "dependents"):
        from panoptis.provenance.lineage import dependents, lineage

        if not pos:
            raise QueryError(f"{cmd} needs a node reference")
        res = lineage(ws, pos[0]) if cmd == "provenance" else dependents(ws, pos[0])
        return {"type": cmd, "result": res, "evidence": _nodes_in(res)}
    if cmd == "compare":
        from panoptis.compare.runs import compare

        if len(pos) < 2:
            raise QueryError("compare needs two runs")
        res = compare(ws, pos[0], pos[1])
        ev = [res["run_a"]["run_id"], res["run_b"]["run_id"]]
        div = res["earliest_divergence"]
        if div:
            ev += [x["event_id"] for x in (div.get("a_event"), div.get("b_event")) if x]
        return {"type": "compare", "result": res, "evidence": ev}
    if cmd == "motifs":
        recs = ws.derived("motif_instance", ws.resolve_run(pos[0])["run_id"] if pos else None)
        return {
            "type": "motifs",
            "result": recs,
            "evidence": [e for r in recs for e in r["events"]],
        }
    if cmd in ("causes", "effects"):
        from panoptis.causality.cones import causes, effects

        if not pos:
            raise QueryError(f"{cmd} needs an event")
        res = (causes if cmd == "causes" else effects)(ws, pos[0])
        key = "dependencies" if cmd == "causes" else "dependents"
        return {"type": cmd, "result": res, "evidence": [d["node"] for d in res[key]]}
    if cmd == "genome":
        from panoptis.behaviour.profile import genome

        profs = _scope_profiles(ws, pos[0] if pos else "all")
        return {
            "type": "genome",
            "result": genome(profs, pos[0] if pos else "all"),
            "evidence": [p["run_id"] for p in profs],
        }
    if cmd == "drift":
        from panoptis.behaviour.drift import drift

        if len(pos) < 2:
            raise QueryError("drift needs two scopes")
        a, b = _scope_profiles(ws, pos[0]), _scope_profiles(ws, pos[1])
        return {"type": "drift", "result": drift(a, b), "evidence": [p["run_id"] for p in a + b]}
    raise QueryError(f"unknown query command {cmd!r}")


def _nodes_in(obj: Any) -> list[str]:
    found: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            n = o.get("node")
            if isinstance(n, str):
                found.append(n)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return list(dict.fromkeys(found))


def _scope_profiles(ws: Workspace, scope: str) -> list[dict[str, Any]]:
    """Scope syntax: ``all`` | ``name:<run name>`` | ``version:<system_version>`` | ``runs:<id>,<id>`` |
    ``variant:<value>`` (run attribute) | a single run reference."""
    profs = ws.derived("behaviour_profile")
    runs = {r["run_id"]: r for r in ws.runs()}
    if scope == "all":
        return profs
    kind, _, val = scope.partition(":")
    if kind == "name":
        return [p for p in profs if p.get("run_name") == val]
    if kind == "version":
        return [p for p in profs if p.get("system_version") == val]
    if kind == "runs":
        ids = {ws.resolve_run(x)["run_id"] for x in val.split(",") if x}
        return [p for p in profs if p["run_id"] in ids]
    if kind in ("variant", "attr") or "=" in val:
        key, value = val.split("=", 1) if "=" in val else ("variant", val)
        return [p for p in profs if _run_attr(ws, runs.get(p["run_id"]), key) == value]
    rid = ws.resolve_run(scope)["run_id"]
    return [p for p in profs if p["run_id"] == rid]


def _run_attr(ws: Workspace, run: dict[str, Any] | None, key: str) -> Any:
    if run is None:
        return None
    if key in (run.get("attributes") or {}):
        return run["attributes"][key]
    # attributes of a nested aw.run() inside an observed program live on the OPERATION span with facet 'run'
    for e in ws.events(run["run_id"], kind="OPERATION"):
        if "run" in e["facets"] and key in e["attributes"]:
            return e["attributes"][key]
    return None


# ── natural language routing ───────────────────────────────────────────────
NL_PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (
        re.compile(
            r"why did (?:run )?(?P<b>\S+) (?:take longer|use more tokens|cost more|behave differently|fail|differ)\w*.* than (?:run )?(?P<a>\S+?)[?.!]*$",
            re.I,
        ),
        lambda m: f"compare {m['a']} {m['b']}",
    ),
    (
        re.compile(
            r"(?:how|what) (?:did|does) (?:run )?(?P<a>\S+) differ from (?:run )?(?P<b>\S+?)[?.!]*$",
            re.I,
        ),
        lambda m: f"compare {m['b']} {m['a']}",
    ),
    (
        re.compile(r"where did (?:artifact )?(?P<x>\S+?) come from[?.!]*$", re.I),
        lambda m: f"provenance {m['x']}",
    ),
    (
        re.compile(r"(?:which|what) retrievals? influenced (?:artifact )?(?P<x>\S+?)[?.!]*$", re.I),
        lambda m: f"provenance {m['x']}",
    ),
    (
        re.compile(
            r"(?:what|which) (?:later )?(?:outputs|events|artifacts)? ?depends? on (?:memory item |artifact )?(?P<x>\S+?)[?.!]*$",
            re.I,
        ),
        lambda m: f"dependents {m['x']}",
    ),
    (re.compile(r"why did (?:event )?(?P<x>\S+) fail[?.!]*$", re.I), lambda m: f"causes {m['x']}"),
    (
        re.compile(
            r"what (?:happened|changed) (?:because of|after) (?:event )?(?P<x>\S+?)[?.!]*$", re.I
        ),
        lambda m: f"effects {m['x']}",
    ),
    (
        re.compile(
            r"(?:which|what) motifs? (?:are|appear|occur)(?:red)? in (?:run )?(?P<x>\S+?)[?.!]*$",
            re.I,
        ),
        lambda m: f"motifs {m['x']}",
    ),
]


def compile_question(question: str, planner: Planner | None = None) -> tuple[str | None, str]:
    q = question.strip()
    for pat, build in NL_PATTERNS:
        m = pat.search(q)
        if m:
            return build(m), "deterministic_pattern"
    if planner is not None:
        plan = planner(q)
        if plan:
            return plan.strip(), "planner"
    return None, "unmatched"


def ask(ws: Workspace, question: str, planner: Planner | None = None) -> dict[str, Any]:
    structured, how = compile_question(question, planner)
    if structured is None:
        return {
            "question": question,
            "answered": False,
            "compiled_by": how,
            "message": "Could not map the question to a supported query. Try: 'why did <runB> take longer than <runA>', "
            "'where did <artifact> come from', 'why did <event> fail', 'what depends on <artifact>'.",
            "evidence": [],
        }
    try:
        res = execute(ws, structured)
    except (QueryError, NotFoundError, AmbiguousError) as exc:
        return {
            "question": question,
            "answered": False,
            "compiled_by": how,
            "structured_query": structured,
            "message": str(exc),
            "evidence": [],
        }
    return {
        "question": question,
        "answered": True,
        "compiled_by": how,
        "structured_query": structured,
        "answer": explain(res),
        "evidence": res["evidence"],
        "result": res["result"],
    }


def explain(res: dict[str, Any]) -> list[str]:
    """Deterministic rendering of a result into sentences that only restate result fields."""
    t, r = res["type"], res["result"]
    lines: list[str] = []
    if t == "compare":
        b = r["run_b"]
        d = r["earliest_divergence"]
        if d:
            be = d.get("b_event") or {}
            lines.append(
                f"Earliest divergence ({d['type']}): {be.get('label', 'end of run')} [{(be.get('event_id') or '')[:8]}] — "
                + "; ".join(d["reasons"])
                + "."
            )
            cone = d.get("cone") or {}
            if cone.get("latency_share") is not None:
                lines.append(
                    f"{cone['events_in_cone']} of {cone['events_total']} events and {cone['latency_share']:.0%} of leaf latency in run {b['run_id'][:8]} lie in that point's dependency cone (dependency, not causation)."
                )
        else:
            lines.append("No structural or content divergence was found between the two runs.")
        res_ = r["resources"]
        for k in (
            "duration_ms",
            "tokens_in",
            "tokens_out",
            "retries",
            "errors",
            "tool_calls",
            "model_calls",
        ):
            v = res_[k]
            if v["delta"]:
                lines.append(f"{k}: {v['a']} → {v['b']} (delta {v['delta']}).")
        for m, c in r["motifs"].items():
            if c["a"] != c["b"]:
                lines.append(f"motif {m}: {c['a']} → {c['b']} instances.")
        lines.append(
            f"Structural similarity {r['structural']['alignment_similarity']}; same system version: {r['fingerprint']['same_system_version']}."
        )
    elif t == "provenance":
        m = r["metrics"]
        lines.append(
            f"Lineage depth {m['provenance_depth']}, {m['transformations']} transformations, {len(m['origins'])} origins; weakest path confidence {m['weakest_path_confidence']}."
        )
        from panoptis.provenance.lineage import render

        lines.extend(render(r))
    elif t == "dependents":
        lines.append(
            f"{r['count']} dependent nodes; {len(r['affected_outputs'])} final outputs affected."
        )
        lines.extend(
            f"- {d['description'].get('label')} ({d['node'][:20]}) via {d['rel_type']}"
            for d in r["dependents"][:15]
        )
    elif t in ("causes", "effects"):
        key = "dependencies" if t == "causes" else "dependents"
        lines.append(
            f"{len(r[key])} structural {key} (OBSERVATIONAL: possible influence, not established)."
        )
        lines.extend(f"- {d['label']} [{d['node'][6:14]}] via {d['via']}" for d in r[key][:10])
        if t == "causes":
            c = r["correlations"]
            if c.get("status") == "computed":
                for row in c["associations"][:3]:
                    lines.append(
                        f"CORRELATIONAL: failure rate {row['failure_rate_with']} with {row['upstream_signature']} vs {row['failure_rate_without']} without (n={row['n_with']}/{row['n_without']})."
                    )
            else:
                lines.append(f"Correlations: {c.get('status')}.")
        for h in r["hypotheses"]:
            lines.append(
                f"Hypothesis ({h['status']}, evidence class {h['evidence_class']}): {h['statement']}"
            )
        for i in r["interventions"]:
            lines.append(
                f"INTERVENTIONAL: branch {str(i['branch_id'])[:8]} — outcome changed: {i['outcome_changed']} (reproduction confidence {i['reproduction_confidence']})."
            )
    elif t == "motifs":
        if r:
            lines.extend(f"- {m['motif_id']} {m['motif_name']}: {m['explanation']}" for m in r)
        else:
            lines.append("No motifs detected.")
    elif t == "runs":
        lines.extend(
            f"- {x['run_id'][:8]} {x.get('name')} {x.get('status')} {x.get('started_at')}"
            for x in r
        )
    elif t == "events":
        lines.extend(f"- {e['event_id'][:8]} {e['kind']} {e['operation']} {e['status']}" for e in r)
    elif t == "drift":
        lines.append(f"Drift status: {r['status']} (n={r['n_baseline']} vs {r['n_candidate']}).")
        lines.extend(f"- {f} changed (q<{r['alpha']})" for f in r.get("drifted_features", []))
    elif t == "genome":
        lines.append(f"Genome over {r['n_runs']} runs (features v{r['features_version']}).")
    return lines
