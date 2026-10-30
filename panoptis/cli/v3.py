"""AgentWatch v3 CLI commands: a debugging interface over the canonical v3 model.

All commands read through :class:`panoptis.query.workspace.Workspace`, the same read
model used by the API and the frontend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

console = Console(soft_wrap=True)

STORE_OPTION = typer.Option(
    None,
    "--store",
    envvar="AGENTWATCH_STORE",
    help="Store URL (sqlite:///path or postgresql+psycopg://…). Default: ~/.agentwatch/agentwatch.db",
)
JSON_OPTION = typer.Option(False, "--json", help="Machine-readable JSON output.")

evidence_app = typer.Typer(
    name="evidence", help="Verify, seal and manage immutable evidence.", no_args_is_help=True
)


def _ws(store: str | None, process: bool = True) -> Any:
    from panoptis.query.workspace import Workspace
    from panoptis.runtime.engine import Engine

    return Workspace(Engine(store), process=process)


def _out(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def _fail(msg: str, code: int = 1) -> None:
    console.print(f"[red]{msg}[/red]")
    raise typer.Exit(code)


def _guard(fn: Any) -> Any:
    """Turn lookup/validation errors into a clean CLI error instead of a traceback."""
    import functools

    from panoptis.query.workspace import AmbiguousError, NotFoundError

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (NotFoundError, AmbiguousError, LookupError, ValueError) as exc:
            _fail(str(exc))

    return wrapper


def _short(x: str | None, n: int = 8) -> str:
    return (x or "")[:n]


def _event_line(e: dict[str, Any]) -> str:
    status = {
        "OK": "[green]OK[/green]",
        "ERROR": "[red]ERROR[/red]",
        "TIMEOUT": "[red]TIMEOUT[/red]",
    }.get(e["status"], f"[yellow]{e['status']}[/yellow]")
    actor = f" [dim]by {e['actor']}[/dim]" if e.get("actor") else ""
    dur = e["time"].get("duration_ms")
    d = f" [dim]{dur:.1f}ms[/dim]" if dur is not None else ""
    return f"[cyan]{e['kind']}[/cyan] {e['operation']}{actor} {status}{d} [dim]{_short(e['event_id'])}[/dim]"


# ── commands ────────────────────────────────────────────────────────────────
def observe(
    command: list[str] = typer.Argument(..., help="Program to run, e.g. python app.py --flag"),
    store: str | None = STORE_OPTION,
    system: str | None = typer.Option(None, "--system", help="System name recorded on the run."),
) -> None:
    """[bold]Observe[/bold] a program: run it under the v3 sensors and reconstruct the run."""
    from panoptis.cli._utils.run_cmd import CommandError
    from panoptis.lab.observe import observe as run_observe
    from panoptis.runtime.engine import Engine

    engine = Engine(store)
    env = {"AGENTWATCH_SYSTEM": system} if system else None
    try:
        res = run_observe(engine, list(command), env=env)
    except CommandError as exc:
        # arguments are validated before anything runs (cli._utils policy: no shell
        # metacharacters such as ';'); report it instead of a traceback
        _fail(f"refused to run the command: {exc}")
    if res.stdout:
        sys.stdout.write(res.stdout)
    if res.stderr:
        sys.stderr.write(res.stderr[-4000:])
    if not res.run_id:
        _fail(
            f"no observations were recorded (exit code {res.exit_code}). Instrument the program with panoptis.instrument or a sensor.",
            res.exit_code or 1,
        )
    console.print(
        f"\n[bold]observed run[/bold] {res.run_id}  ({res.observations} observations, exit code {res.exit_code})"
    )
    console.print(f"next: [cyan]panoptis inspect {(res.run_id or '')[:8]}[/cyan]")
    if res.exit_code:
        raise typer.Exit(res.exit_code)


@_guard
def ingest(
    path: Path = typer.Argument(..., exists=True, readable=True, help="File to import"),
    fmt: str = typer.Option(
        "auto", "--format", help="auto | ndjson | otlp-json | claude-code | legacy-jsonl"
    ),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Ingest[/bold] observations from a file into the evidence store."""
    from panoptis.runtime.engine import Engine

    engine = Engine(store)
    fmt = _detect_format(path) if fmt == "auto" else fmt
    translations = None
    if fmt == "ndjson":
        from panoptis.sensors.base import read_ndjson

        res = engine.ingest(read_ndjson(path))
    elif fmt == "otlp-json":
        from panoptis.sensors.otel import drafts_from_spans, spans_from_otlp_json

        res = engine.ingest(
            drafts_from_spans(spans_from_otlp_json(json.loads(path.read_text(encoding="utf-8"))))
        )
    elif fmt == "claude-code":
        from panoptis.sensors.claude_code import drafts_from_file

        res = engine.ingest(drafts_from_file(path))
    elif fmt == "legacy-jsonl":
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        translations, res = engine.ingest_legacy(lines)
    else:
        _fail(f"unknown format {fmt!r}")
        return
    report = engine.process()
    out: dict[str, Any] = {"format": fmt, "append": res.to_dict(), "processing": report}
    if translations is not None:
        from collections import Counter

        out["translation"] = {
            "statuses": dict(Counter(t.status.value for t in translations)),
            "lost_fields": dict(Counter(f for t in translations for f in t.lost_fields)),
            "rejected": [t.errors for t in translations if t.errors][:20],
        }
    if as_json:
        _out(out)
        return
    a = res.to_dict()
    console.print(
        f"[bold]{fmt}[/bold]: {a['accepted']} accepted, {a['duplicates']} duplicates, {len(a['rejected'])} rejected, {a['redacted_observations']} redacted"
    )
    if translations is not None:
        console.print(
            f"legacy translation: {out['translation']['statuses']}  lost: {out['translation']['lost_fields']}"
        )
    console.print(
        f"interpretation {report['interp_id']}: {report.get('events')} events, {report.get('runs')} runs, {report.get('diagnostics')} diagnostics"
    )


def _detect_format(path: Path) -> str:
    head = path.read_text(encoding="utf-8", errors="replace")[:4000].lstrip()
    if head.startswith("{") and '"resourceSpans"' in head:
        return "otlp-json"
    first = head.splitlines()[0] if head else ""
    try:
        obj = json.loads(first)
    except json.JSONDecodeError:
        return "ndjson"
    if "sensor" in obj and "source_kind" in obj:
        return "ndjson"
    if "event_type" in obj and "session_id" in obj:
        return "legacy-jsonl"
    return "claude-code"


@_guard
def runs(
    store: str | None = STORE_OPTION,
    limit: int = typer.Option(20, "--limit"),
    as_json: bool = JSON_OPTION,
) -> None:
    """List reconstructed [bold]runs[/bold]."""
    ws = _ws(store)
    rs = ws.runs(limit=limit)
    if as_json:
        _out(rs)
        return
    t = Table(title="runs", show_lines=False)
    for c in (
        "run",
        "name",
        "status",
        "started",
        "events",
        "errors",
        "duration ms",
        "completeness",
    ):
        t.add_column(c)
    for r in rs:
        t.add_row(
            _short(r["run_id"]),
            str(r.get("name")),
            r.get("status") or "",
            (r.get("started_at") or "")[:19],
            str(r["event_count"]),
            str(r["error_events"]),
            f"{r['duration_ms']:.0f}" if r.get("duration_ms") else "-",
            f"{r['completeness']:.2f}",
        )
    console.print(t)


@_guard
def inspect(
    run: str = typer.Argument("latest", help="run id/prefix, name, 'latest' or 'latest~N'"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Inspect[/bold] a run: reconstructed execution tree, graph statistics, motifs."""
    from panoptis.compare.runs import summarize, tree

    ws = _ws(store)
    s = summarize(ws, run)
    r = s["run"]
    if as_json:
        s = {
            **s,
            "tree": [(d, e["event_id"]) for d, e in tree(ws, r["run_id"], s["events"])],
            "motif_instances": ws.derived("motif_instance", r["run_id"]),
        }
        _out(s)
        return
    console.print(
        f"[bold]run[/bold] {r['run_id']}  [bold]{r.get('name')}[/bold]  status {r.get('status')} ({r.get('status_basis')})"
    )
    console.print(
        f"started {r.get('started_at')}  duration {r.get('duration_ms') or 0:.1f} ms  events {r['event_count']}  tokens {r['tokens_in']}/{r['tokens_out']}  cost ${r['cost_usd']}"
    )
    console.print(
        f"structure: depth {s['graph']['max_depth']}, branching {s['graph']['mean_branching']}, multi-parent {s['graph']['multi_parent_events']}, "
        f"declared-link completeness {r['completeness']:.2f} ({r['resolved_links']}/{r['declared_links']})"
    )
    root = Tree("[bold]execution[/bold]")
    stack: list[tuple[int, Any]] = [(-1, root)]
    for depth, e in tree(ws, r["run_id"], s["events"]):
        while stack and stack[-1][0] >= depth:
            stack.pop()
        node = stack[-1][1].add(_event_line(e))
        stack.append((depth, node))
    console.print(root)
    if s["failures"]:
        console.print(
            "[bold red]failures[/bold red]: "
            + "; ".join(
                f"{f['label']} ({(f.get('error') or {}).get('message')})" for f in s["failures"]
            )
        )
    motifs = ws.derived("motif_instance", r["run_id"])
    if motifs:
        console.print("[bold]motifs[/bold] (EXPERIMENTAL):")
        for m in motifs:
            console.print(f"  {m['motif_id']} {m['motif_name']}: {m['explanation']}")
    info = next(iter(ws.derived("information_evidence", r["run_id"])), None)
    if info:
        share = info.get("high_fidelity_share")
        console.print(
            f"information lineage: consumed values {info['consumed_values']}, "
            f"ambiguous {info['ambiguous_values']}, declared (high-fidelity) share "
            f"{'n/a' if share is None else f'{share:.0%}'}"
        )
    if s["missing_facts"]:
        console.print(f"[dim]missing facts (not invented): {s['missing_facts']}[/dim]")


@_guard
def events(
    run: str = typer.Argument("latest"),
    kind: str | None = typer.Option(None, "--kind"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """List a run's normalized [bold]events[/bold]."""
    ws = _ws(store)
    r = ws.resolve_run(run)
    evs = ws.events(r["run_id"], kind=kind.upper() if kind else None)
    if as_json:
        _out(evs)
        return
    t = Table(title=f"events of {_short(r['run_id'])}")
    for c in ("event", "start", "kind", "operation", "actor", "object", "status", "ms"):
        t.add_column(c)
    for e in evs:
        d = e["time"].get("duration_ms")
        t.add_row(
            _short(e["event_id"]),
            (e["time"]["start"] or "?")[11:23],
            e["kind"],
            e["operation"],
            e.get("actor") or "-",
            e.get("object") or "-",
            e["status"],
            f"{d:.1f}" if d is not None else "-",
        )
    console.print(t)


@_guard
def show(
    event: str = typer.Argument(..., help="event id or prefix"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Show[/bold] an event: normalized form, raw evidence, parents and children."""
    ws = _ws(store)
    e = ws.event(event)
    g = ws.graph(e.get("run_id"))
    node = f"event:{e['event_id']}"
    rels_in = [g.relations[r] | {"other": o} for r, o in g.inc.get(node, [])]
    rels_out = [g.relations[r] | {"other": o} for r, o in g.out.get(node, [])]
    data = {
        "event": e,
        "evidence": ws.event_evidence(e["event_id"]),
        "incoming": rels_in,
        "outgoing": rels_out,
    }
    if as_json:
        _out(data)
        return
    console.print(_event_line(e))
    console.print(
        f"normalizer {e['normalizer']}  interpretation {e['interp_id']}  run {e.get('run_id')} ({e.get('run_basis')})"
    )
    console.print(
        f"missing: {e['missing'] or 'none'}   confidence: observation {e['confidence']['observation']}, attribution {e['confidence']['attribution']}"
    )
    for rel in rels_in:
        console.print(
            f"  ← {rel['type']} [{rel['view'].lower()}, {rel['basis'].lower()}] {ws.describe_node(rel['other'])['label']}"
        )
    for rel in rels_out:
        console.print(
            f"  → {rel['type']} [{rel['view'].lower()}, {rel['basis'].lower()}] {ws.describe_node(rel['other'])['label']}"
        )
    console.print("[bold]raw evidence[/bold]")
    for o in data["evidence"]:
        console.print(
            f"  {o['obs_id']} {o['source_kind']} sha256={o['payload_sha256'][:12]} segment={o['segment_id']}"
        )


@_guard
def graph(
    run: str = typer.Argument("latest"),
    view: str = typer.Option("execution", "--view", help="execution | information | causal | all"),
    fmt: str = typer.Option("text", "--format", help="text | dot | json"),
    store: str | None = STORE_OPTION,
) -> None:
    """Print a run's [bold]graph[/bold] (execution, information or causal view)."""
    ws = _ws(store)
    r = ws.resolve_run(run)
    rels = ws.relations(r["run_id"])
    if view == "causal":
        from panoptis.causality.hypotheses import causal_relations

        rels = causal_relations(ws)
    elif view != "all":
        rels = [x for x in rels if x["view"] == view.upper()]
    nodes = sorted({n for x in rels for n in x["tail"] + x["head"]})
    if fmt == "json":
        _out(
            {
                "run_id": r["run_id"],
                "view": view,
                "nodes": [ws.describe_node(n) for n in nodes],
                "relations": rels,
            }
        )
        return
    if fmt == "dot":
        lines = ["digraph panoptis {", "  rankdir=TB; node [shape=box, fontname=monospace];"]
        for n in nodes:
            d = ws.describe_node(n)
            shape = {"event": "box", "artifact": "note", "instance": "note"}.get(
                d["type"], "ellipse"
            )
            lines.append(
                f'  "{n}" [label="{str(d["label"])[:60].replace(chr(34), chr(39))}", shape={shape}];'
            )
        for x in rels:
            a = x.get("attributes") or {}
            style = (
                "dotted"  # a candidate or a similarity: not an information flow
                if a.get("resolution") == "AMBIGUOUS" or a.get("strength") == "NONE"
                else "solid"
                if x["basis"] == "DECLARED"
                else "dashed"
            )
            for t in x["tail"]:
                for h in x["head"]:
                    lines.append(f'  "{t}" -> "{h}" [label="{x["type"]}", style={style}];')
        lines.append("}")
        sys.stdout.write("\n".join(lines) + "\n")
        return
    for x in sorted(rels, key=lambda x: (x["view"], x["type"])):
        tails = ", ".join(str(ws.describe_node(t)["label"])[:40] for t in x["tail"])
        heads = ", ".join(str(ws.describe_node(h)["label"])[:40] for h in x["head"])
        a = x.get("attributes") or {}
        if a.get("evidence_type"):
            conf = f" ({a['evidence_type'].lower()}, {a['strength']}"
            conf += ", AMBIGUOUS)" if a.get("resolution") == "AMBIGUOUS" else ")"
        else:
            conf = "" if x["basis"] == "DECLARED" else f" ({x['basis'].lower()} {x['confidence']})"
        console.print(
            f"[dim]{x['view'][:4]}[/dim] {tails} [cyan]-{x['type']}->[/cyan] {heads}{conf}"
        )


@_guard
def provenance(
    node: str = typer.Argument(
        ...,
        help="artifact label (e.g. report.md), artifact:<content id>, inst:<instance>, or event id",
    ),
    run: str | None = typer.Option(None, "--run"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Provenance[/bold]: where did this information come from?"""
    from panoptis.provenance.lineage import lineage, render

    ws = _ws(store)
    rid = ws.resolve_run(run)["run_id"] if run else None
    res = lineage(ws, node, run_id=rid)
    if as_json:
        _out(res)
        return
    for line in render(res):
        console.print(line, highlight=False, markup=False)
    m = res["metrics"]
    console.print(
        f"\n[dim]depth {m['provenance_depth']} · transformations {m['transformations']} · origins {len(m['origins'])} · "
        f"weakest link confidence {m['weakest_path_confidence']} · links by strength {m['relations_by_strength']} · "
        f"ambiguous candidates {len(m['ambiguous_candidates'])} · metrics EXPERIMENTAL[/dim]"
    )
    if m["ambiguous_candidates"]:
        console.print(
            "[yellow]Some sources are ambiguous: the evidence fits several producers equally, so "
            "they are listed as candidates rather than chosen. Declaring inputs (source=...) "
            "removes the ambiguity.[/yellow]"
        )


@_guard
def dependents(
    node: str = typer.Argument(...),
    run: str | None = typer.Option(None, "--run"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Which later events and outputs depend on this node?"""
    from panoptis.provenance.lineage import dependents as deps

    ws = _ws(store)
    rid = ws.resolve_run(run)["run_id"] if run else None
    res = deps(ws, node, run_id=rid)
    if as_json:
        _out(res)
        return
    console.print(f"{res['count']} dependents of {res['node'][:40]}")
    for d in res["dependents"]:
        console.print(
            f"{'  ' * d['depth']}→ {d['rel_type']} {d['description']['label']}", markup=False
        )


@_guard
def compare(
    run_a: str = typer.Argument(...),
    run_b: str = typer.Argument(...),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Compare[/bold] two runs: earliest divergence, structure, resources, information flow."""
    from panoptis.compare.runs import compare as cmp
    from panoptis.query.engine import explain

    ws = _ws(store)
    res = cmp(ws, run_a, run_b)
    if as_json:
        _out(res)
        return
    console.print(
        f"[bold]A[/bold] {res['run_a']['run_id']} {res['run_a']['name']} {res['run_a']['status']}"
    )
    console.print(
        f"[bold]B[/bold] {res['run_b']['run_id']} {res['run_b']['name']} {res['run_b']['status']}"
    )
    for line in explain({"type": "compare", "result": res}):
        console.print(line, markup=False)
    s = res["structural"]
    for sig in s["only_in_a"][:10]:
        console.print(f"  [red]- {sig['signature']} ×{sig['count']}[/red]")
    for sig in s["only_in_b"][:10]:
        console.print(f"  [green]+ {sig['signature']} ×{sig['count']}[/green]")


@_guard
def motifs(
    run: str | None = typer.Argument(
        None, help="run reference; omit for registry statistics over all runs"
    ),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Behavioural [bold]motifs[/bold] detected in a run (or registry statistics)."""
    from panoptis.behaviour.motifs import motif_stats

    ws = _ws(store)
    if run:
        r = ws.resolve_run(run)
        inst = ws.derived("motif_instance", r["run_id"])
        if as_json:
            _out(inst)
            return
        if not inst:
            console.print("no motifs detected")
        for m in inst:
            console.print(
                f"[bold]{m['motif_id']}[/bold] {m['motif_name']} (confidence {m['confidence']}): {m['explanation']}"
            )
            console.print(f"  events: {', '.join(_short(x) for x in m['events'])}", markup=False)
        return
    stats = motif_stats(ws.derived("motif_instance"), ws.runs())
    if as_json:
        _out(stats)
        return
    t = Table(title="motif registry")
    for c in ("id", "name", "kind", "maturity", "instances", "support", "err with", "err without"):
        t.add_column(c)
    for m in stats:
        t.add_row(
            m["motif_id"],
            m["name"],
            m["kind"],
            m["maturity"],
            str(m["instances"]),
            f"{m['support']:.2f}",
            "-"
            if m["error_rate_with"] is None
            else f"{m['error_rate_with']:.2f} (n={m['n_with']})",
            "-"
            if m["error_rate_without"] is None
            else f"{m['error_rate_without']:.2f} (n={m['n_without']})",
        )
    console.print(t)


@_guard
def genome(
    scope: str = typer.Argument(
        "all", help="all | name:<run name> | version:<v> | runs:<a>,<b> | variant:<v> | <run>"
    ),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Behavioural [bold]genome[/bold] (aggregate profile) for a scope of runs. EXPERIMENTAL."""
    from panoptis.behaviour.profile import genome as build
    from panoptis.query.engine import _scope_profiles

    ws = _ws(store)
    g = build(_scope_profiles(ws, scope), scope)
    if as_json:
        _out(g)
        return
    console.print(
        f"[bold]genome[/bold] {scope}: {g['n_runs']} runs, features v{g['features_version']} (EXPERIMENTAL)"
    )
    t = Table()
    for c in ("feature", "mean", "95% CI", "min", "max"):
        t.add_column(c)
    for f, v in g["features"].items():
        t.add_row(
            f,
            f"{v['mean']:.4g}",
            "-" if not v["ci95"] else f"[{v['ci95'][0]:.4g}, {v['ci95'][1]:.4g}]",
            f"{v['min']:.4g}",
            f"{v['max']:.4g}",
        )
    console.print(t)
    console.print(
        "motif frequency: " + ", ".join(f"{k}={v:.2f}" for k, v in g["motif_frequency"].items())
    )


@_guard
def drift(
    baseline: str = typer.Argument(..., help="scope, e.g. variant:normal"),
    candidate: str = typer.Argument(...),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Behavioural [bold]drift[/bold] between two scopes of runs. EXPERIMENTAL."""
    from panoptis.behaviour.drift import drift as run_drift
    from panoptis.query.engine import _scope_profiles

    ws = _ws(store)
    res = run_drift(_scope_profiles(ws, baseline), _scope_profiles(ws, candidate))
    if as_json:
        _out(res)
        return
    console.print(
        f"[bold]drift[/bold] {baseline} ({res['n_baseline']}) → {candidate} ({res['n_candidate']}): {res['status']}"
    )
    if res.get("message"):
        console.print(f"[yellow]{res['message']}[/yellow]")
    t = Table()
    for c in ("feature", "baseline", "candidate", "rel. change", "q"):
        t.add_column(c)
    for f in res["features"]:
        if f["baseline_mean"] == f["candidate_mean"]:
            continue
        mark = "[bold red]*[/bold red]" if f["feature"] in res.get("drifted_features", []) else ""
        rc = "-" if f["relative_change"] is None else f"{f['relative_change']:+.1%}"
        t.add_row(
            f["feature"] + mark,
            f"{f['baseline_mean']:.4g}",
            f"{f['candidate_mean']:.4g}",
            rc,
            "-" if f["q_value"] is None else f"{f['q_value']:.4f}",
        )
    console.print(t)
    if res.get("distances"):
        console.print(
            "candidate distances: "
            + ", ".join(f"{k}={v}" for k, v in res["distances"].items() if k != "note")
        )


def _causal_print(res: dict[str, Any], key: str) -> None:
    console.print(f"[bold]{res['event']['label']}[/bold] [{_short(res['event']['node'][6:])}]")
    console.print(f"\n[bold]{key}[/bold] — {res['legend'][key]}")
    for d in res[key]:
        console.print(
            f"  {'  ' * (d['depth'] - 1)}{d['label']} [{_short(d['node'][6:])}] via {d['via']} ({(d['basis'] or '').lower()})",
            markup=False,
        )
    if "correlations" in res:
        c = res["correlations"]
        console.print(
            f"\n[bold]correlations[/bold] — {res['legend']['correlations']}: {c.get('status')}"
        )
        for row in c.get("associations", [])[:10]:
            console.print(
                f"  {row['upstream_signature']}: failure {row['failure_rate_with']} with vs {row['failure_rate_without']} without (n={row['n_with']}/{row['n_without']})",
                markup=False,
            )
    console.print(
        f"\n[bold]hypotheses[/bold] — {res['legend'].get('hypotheses', 'claims with computed evidence class')}"
    )
    for h in res["hypotheses"] or []:
        console.print(
            f"  {h['hypothesis_id'][:10]} {h['status']} evidence={h['evidence_class']}: {h['statement']}",
            markup=False,
        )
    if not res["hypotheses"]:
        console.print("  none recorded")
    console.print(f"\n[bold]interventions[/bold] — {res['legend']['interventions']}")
    for i in res["interventions"] or []:
        console.print(
            f"  branch {str(i['branch_id'])[:10]} fork {_short(i['fork_event'])}: outcome changed={i['outcome_changed']}, reproduction {i['reproduction_confidence']}",
            markup=False,
        )
    if not res["interventions"]:
        console.print("  none recorded (try: panoptis branch … / panoptis counterfactual …)")


@_guard
def causes(
    event: str = typer.Argument(...), store: str | None = STORE_OPTION, as_json: bool = JSON_OPTION
) -> None:
    """What may have influenced an event — dependency, correlation, hypothesis, intervention kept separate."""
    from panoptis.causality.cones import causes as run

    res = run(_ws(store), event)
    _out(res) if as_json else _causal_print(res, "dependencies")


@_guard
def effects(
    event: str = typer.Argument(...), store: str | None = STORE_OPTION, as_json: bool = JSON_OPTION
) -> None:
    """What depends on an event, and what changed when it was intervened on."""
    from panoptis.causality.cones import effects as run

    res = run(_ws(store), event)
    _out(res) if as_json else _causal_print(res, "dependents")


@_guard
def replay(
    run: str = typer.Argument("latest"),
    level: str = typer.Option("L1", "--level", help="L0 | L1 | L2 | L3"),
    live: list[str] = typer.Option([], "--live", help="operation to run live at L3 (repeatable)"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Replay[/bold] a run at an explicit level; reports reproduction confidence and what was mocked or live."""
    from panoptis.lab.replay import replay as run_replay

    ws = _ws(store)
    res = run_replay(ws, run, level.upper(), live=live)
    if as_json:
        _out(res)
        return
    rc = res["reproduction_confidence"]
    console.print(
        f"[bold]replay {res['replay_level']}[/bold] of {res['source_run']}: {res['description']}"
    )
    console.print(f"reproduction confidence {rc['value']} ({rc['basis']}, uncalibrated)")
    if res.get("replay_run"):
        console.print(f"replay run {res['replay_run']} exit code {res['exit_code']}")
    console.print(f"mocked: {len(res['mocked_components'])}  live: {res['live_components']}")
    if res["missing_dependencies"]:
        console.print(f"[yellow]missing dependencies: {res['missing_dependencies']}[/yellow]")
    if res.get("stale_captures"):
        console.print(
            f"[yellow]stale captures (input changed, not served): {res['stale_captures']}[/yellow]"
        )
    if res.get("determinism_note"):
        console.print(f"[dim]{res['determinism_note']}[/dim]")


def _parse_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


@_guard
def branch(
    run: str = typer.Argument(...),
    at: str = typer.Option(..., "--at", help="event id/prefix of an instrumented call"),
    substitute: str = typer.Option(
        ..., "--substitute", help="JSON (or plain string) result to substitute"
    ),
    level: str = typer.Option(
        "L3", "--level", help="L2 (mock everything) | L3 (changed inputs run live)"
    ),
    execute: bool = typer.Option(True, "--execute/--no-execute"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Create (and execute) a [bold]branch[/bold]: fork a run at an event with a substituted result."""
    from panoptis.lab.branch import create_branch, execute_branch

    ws = _ws(store)
    br = create_branch(ws, run, at, _parse_value(substitute), level=level.upper())
    res = execute_branch(ws, br["branch_id"]) if execute else None
    if as_json:
        _out({"branch": br, "result": res})
        return
    console.print(
        f"[bold]branch[/bold] {br['branch_id']} of run {_short(br['source_run'])} at {br['event_label']} ({br['call_key']})"
    )
    if res:
        console.print(
            f"branch run {res['branch_run']}: outcome {res['outcome']} changed={res['outcome_changed']} final outputs changed={res['final_outputs_changed']} [SIMULATED]"
        )
        d = res.get("divergence")
        if d:
            console.print(
                f"earliest divergence: {(d.get('b_event') or {}).get('label')} — {'; '.join(d['reasons'])}"
            )
        console.print(
            f"reproduction confidence {res['reproduction_confidence']}; live: {res['live_components']}"
        )


@_guard
def branches(
    run: str = typer.Argument("latest"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Branch history of a run."""
    from panoptis.lab.branch import branch_history

    res = branch_history(_ws(store), run)
    if as_json:
        _out(res)
        return
    console.print(f"run {res['run_id']}")
    for b in res["branches"]:
        r = b.get("result") or {}
        console.print(
            f"  ├─ {b['branch_id'][:10]} at {b['event_label']} := {b['substitution_preview'][:60]} → {r.get('branch_run', 'not executed')} changed={r.get('outcome_changed')}",
            markup=False,
        )


@_guard
def counterfactual(
    run: str = typer.Argument(...),
    at: str = typer.Option(..., "--at"),
    alternative: str = typer.Option(..., "--alternative", help="JSON value"),
    execute: bool = typer.Option(True, "--execute/--estimate-only"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """EXPERIMENTAL: what might have happened if an event had produced a different result?"""
    from panoptis.lab.branch import counterfactual as cf

    res = cf(_ws(store), run, at, _parse_value(alternative), execute=execute)
    if as_json:
        _out(res)
        return
    console.print(f"[bold]{res['question']}[/bold]  [dim](EXPERIMENTAL)[/dim]")
    console.print(f"observed: {res['observed']}")
    console.print(f"method: {res.get('method')}")
    _out(res["estimate"])


@_guard
def states(
    runs_: list[str] = typer.Argument(None, metavar="RUNS", help="runs (default: all)"),
    window: int = typer.Option(4, "--window"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """EXPERIMENTAL latent behavioural states (interpretable baseline)."""
    from panoptis.state.latent import infer_states

    res = infer_states(_ws(store), runs_ or None, window=window)
    if as_json or res.get("status") != "estimated":
        _out(res)
        return
    console.print(f"k={res['k']} silhouette={res['silhouette']} (EXPERIMENTAL)")
    for s in res["states"]:
        console.print(f"  {s['state']} {s['name']}: {s['windows']} windows")
    for rid, seq in list(res["trajectories"].items())[:10]:
        console.print(
            f"  {_short(rid)}: "
            + " → ".join(x["state"] + ("*" if x.get("change_point") else "") for x in seq)
        )


@_guard
def forecast(
    run: str = typer.Argument("latest"),
    evaluate_only: bool = typer.Option(False, "--evaluate"),
    store: str | None = STORE_OPTION,
) -> None:
    """EXPERIMENTAL outcome forecast for a (partial) run, with calibration evidence."""
    from panoptis.forecasting.trajectory import evaluate
    from panoptis.forecasting.trajectory import forecast as fc

    ws = _ws(store)
    _out(evaluate(ws) if evaluate_only else fc(ws, run))


@_guard
def query(
    text: str = typer.Argument(..., help="structured query (e.g. 'compare A B') or a question"),
    store: str | None = STORE_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """[bold]Query[/bold] system behaviour; answers cite the evidence they rest on."""
    from panoptis.query.engine import QueryError, ask, execute, explain

    ws = _ws(store)
    try:
        res = execute(ws, text)
        answer = {
            "answered": True,
            "structured_query": text,
            "compiled_by": "structured",
            "answer": explain(res),
            "evidence": res["evidence"],
            "result": res["result"],
        }
    except QueryError:
        answer = ask(ws, text)
    if as_json:
        _out(answer)
        return
    if not answer["answered"]:
        _fail(answer["message"])
    console.print(f"[dim]query: {answer['structured_query']} ({answer['compiled_by']})[/dim]")
    for line in answer["answer"]:
        console.print(line, markup=False)
    console.print(
        f"[dim]evidence: {', '.join(str(e)[:14] for e in answer['evidence'][:12])}{' …' if len(answer['evidence']) > 12 else ''}[/dim]"
    )


@_guard
def status(store: str | None = STORE_OPTION, as_json: bool = JSON_OPTION) -> None:
    """v3 store status, interpretation and capability maturity."""
    from panoptis.analysis.capabilities import all_capabilities

    ws = _ws(store)
    interp = ws.store.get_interpretation(ws.interp_id)
    data = {
        "store": ws.store.url,
        "observations": ws.store.count_observations(),
        "interpretation": interp,
        "capabilities": [c.to_dict() for c in all_capabilities()],
        "diagnostics": len(ws.diagnostics()),
    }
    if as_json:
        _out(data)
        return
    console.print(
        f"store {data['store']}  observations {data['observations']}  diagnostics {data['diagnostics']}"
    )
    console.print(f"interpretation {ws.interp_id}: {(interp or {}).get('stats')}")
    t = Table(title="capabilities")
    for c in ("capability", "maturity", "evidence"):
        t.add_column(c)
    for c in data["capabilities"]:
        t.add_row(c["name"], c["maturity"], ", ".join(c["evidence"]) or "-")
    console.print(t)


def v3_health(store: str | None = None) -> list[tuple[str, str, str]]:
    """(component, status, detail) rows for ``panoptis doctor``: the v3 evidence store and
    interpretation. Read-only: nothing is processed or written."""
    rows: list[tuple[str, str, str]] = []
    try:
        from panoptis.runtime.engine import Engine
        from panoptis.storage.schema import SCHEMA_VERSION

        engine = Engine(store)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return [("v3 store", "ERROR", f"cannot open: {exc}")]
    st = engine.store
    rows.append(("v3 store", "OK", f"{st.url} (schema v{SCHEMA_VERSION})"))
    n = st.count_observations()
    rows.append(("v3 observations", "OK", str(n)))
    interp = st.get_interpretation(engine.interp_id_for("default"))
    if n == 0:
        rows.append(("v3 interpretation", "OK", "nothing observed yet"))
    elif interp is None or interp.get("status") != "active":
        rows.append(
            (
                "v3 interpretation",
                "STALE",
                "components changed since the last build: run `panoptis reprocess`",
            )
        )
    else:
        rows.append(("v3 interpretation", "OK", f"current ({interp.get('interp_id', '')[:24]})"))
    try:
        report = st.verify()
        rows.append(
            (
                "v3 evidence chain",
                "OK" if report.ok else "FAILED",
                "verified" if report.ok else str(report),
            )
        )
    except Exception as exc:  # noqa: BLE001
        rows.append(("v3 evidence chain", "ERROR", str(exc)))
    try:
        import cryptography  # noqa: F401

        crypto = "available"
    except ImportError:
        crypto = "unavailable (pip install cryptography): payload encryption / erasure disabled"
    rows.append(("v3 crypto-shredding", "OK" if crypto == "available" else "WARN", crypto))
    rows.append(
        (
            "v3 payload encryption",
            "OK",
            "on"
            if getattr(st, "encrypt_payloads", False)
            else "off (AGENTWATCH_ENCRYPT_PAYLOADS=1)",
        )
    )
    st.close()
    return rows


@evidence_app.command("verify")
@_guard
def evidence_verify(
    store: str | None = STORE_OPTION,
    seal: bool = typer.Option(True, "--seal/--no-seal", help="seal pending observations first"),
    tenant: str = typer.Option("default", "--tenant"),
) -> None:
    """Verify the hash chain and Merkle roots of all sealed evidence."""
    from panoptis.storage.store import Store

    st = Store(store)
    if seal:
        st.seal_all(tenant)
    rep = st.verify(tenant)
    _out(rep.to_dict())
    if not rep.ok:
        raise typer.Exit(2)


@evidence_app.command("seal")
@_guard
def evidence_seal(
    store: str | None = STORE_OPTION, tenant: str = typer.Option("default", "--tenant")
) -> None:
    """Seal pending observations into hash-chained segments."""
    from panoptis.storage.store import Store

    segs = Store(store).seal_all(tenant)
    _out([s.to_dict() for s in segs])


@evidence_app.command("purge")
@_guard
def evidence_purge(
    older_than_days: int = typer.Option(..., "--older-than-days"),
    reason: str = typer.Option(..., "--reason"),
    yes: bool = typer.Option(False, "--yes"),
    store: str | None = STORE_OPTION,
    tenant: str = typer.Option("default", "--tenant"),
) -> None:
    """Retention: remove sealed segments older than N days, their payload files and derived copies (chain preserved)."""
    from datetime import UTC, datetime, timedelta

    from panoptis.runtime.engine import Engine

    engine = Engine(store)
    st = engine.store
    segs = st.segments_sealed_before(tenant, datetime.now(UTC) - timedelta(days=older_than_days))
    if not segs:
        console.print("nothing to purge")
        return
    if not yes:
        _fail(f"{len(segs)} segments would be purged; re-run with --yes to confirm")
    report = engine.purge_segments(segs, reason=reason, actor="cli")
    console.print(
        f"purged {len(segs)} segments ({report['observations']} observations, "
        f"{report['blobs_deleted']} payload files); derived data rebuilt; "
        f"chain verification: {st.verify(tenant).ok}"
    )


@evidence_app.command("erase-subject")
@_guard
def evidence_erase_subject(
    subject: str = typer.Argument(..., help="value of the declared subject_id"),
    reason: str = typer.Option(..., "--reason"),
    yes: bool = typer.Option(False, "--yes"),
    store: str | None = STORE_OPTION,
    tenant: str = typer.Option("default", "--tenant"),
) -> None:
    """Crypto-shred a data subject: destroy its key and purge derived copies (irreversible)."""
    from panoptis.runtime.engine import Engine

    if not yes:
        _fail(f"erasing subject {subject!r} is irreversible; re-run with --yes to confirm")
    _out(Engine(store).erase_subject(tenant, subject, reason=reason, actor="cli"))


@evidence_app.command("show")
@_guard
def evidence_show(obs_id: str = typer.Argument(...), store: str | None = STORE_OPTION) -> None:
    """Show a raw observation exactly as stored, with its inclusion proof."""
    from panoptis.storage.store import Store

    st = Store(store)
    obs = st.get_observation(obs_id)
    if obs is None:
        _fail("observation not found")
        return
    _out({"observation": obs.to_dict(), "inclusion_proof": st.inclusion_proof(obs_id)})


@_guard
def reprocess(
    store: str | None = STORE_OPTION, tenant: str = typer.Option("default", "--tenant")
) -> None:
    """Rebuild the current interpretation from raw evidence (evidence is never modified)."""
    from panoptis.runtime.engine import Engine

    _out(Engine(store).process(tenant, force=True))


V3_COMMANDS = [
    observe,
    ingest,
    runs,
    inspect,
    events,
    show,
    graph,
    provenance,
    dependents,
    compare,
    motifs,
    genome,
    drift,
    causes,
    effects,
    replay,
    branch,
    branches,
    counterfactual,
    states,
    forecast,
    query,
    status,
    reprocess,
]


def register(app: typer.Typer) -> None:
    for fn in V3_COMMANDS:
        settings = (
            {"ignore_unknown_options": True, "allow_interspersed_args": False}
            if fn is observe
            else None
        )
        app.command(name=fn.__name__.rstrip("_"), context_settings=settings)(fn)
    app.add_typer(evidence_app)
