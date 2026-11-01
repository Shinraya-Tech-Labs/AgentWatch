"""v3 performance measurements (SQLite, single process).

    python -m benchmarks.perf.v3_perf --spans 2000

Measures what exists; it does not tune anything. Results go to benchmarks/perf/results/.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from panoptis import instrument as aw
from panoptis.compare.runs import compare
from panoptis.provenance.lineage import lineage
from panoptis.query.workspace import Workspace, run_order_key
from panoptis.runtime.engine import Engine
from panoptis.sensors.base import ListSink

OUT = Path(__file__).resolve().parent / "results"


def workload(n_spans: int, variant: int = 0) -> None:
    """A synthetic run: batches of retrieval → model → tool under one operation each."""

    @aw.retriever("idx")
    def search(q: str) -> list[dict[str, str]]:
        return [
            {
                "id": f"d{i}",
                "text": f"document {i} about topic {q} with several words of content here",
            }
            for i in range(3)
        ]

    @aw.model("stub/m")
    def gen(p: str) -> str:
        return "answer: " + p[:120]

    @aw.tool("t")
    def tool(x: str) -> str:
        return x.upper()[: 40 + variant]

    per_batch = 4
    with aw.run("perf"):
        for b in range(max(1, n_spans // per_batch)):
            with aw.span("OPERATION", f"step{b % 7}", actor=f"agent:a{b % 3}"):
                docs = search(f"q{b}")
                out = gen(" ".join(d["text"] for d in docs))
                tool(out)


def timed(fn, repeat: int = 1) -> tuple[float, object]:
    best = None
    res = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        res = fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best or 0.0, res


def spread(samples: list[float]) -> dict[str, float]:
    """Median and range of repeated measurements (single runs vary with machine load)."""
    xs = sorted(samples)
    return {
        "median": round(statistics.median(xs), 3),
        "min": round(xs[0], 3),
        "max": round(xs[-1], 3),
        "n": len(xs),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spans", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=OUT, help="results directory")
    args = ap.parse_args(argv)
    out_dir: Path = args.out
    results: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "spans": args.spans,
    }

    # sensor overhead: time per instrumented call vs the same call uninstrumented
    sink = ListSink()
    aw.configure(sink)

    def plain(x: str) -> str:
        return x.upper()

    wrapped = aw.tool("bench")(plain)
    n = 5000
    with aw.run("overhead"):
        t_plain, _ = timed(lambda: [plain("abc") for _ in range(n)])
        t_wrapped, _ = timed(lambda: [wrapped("abc") for _ in range(n)])
        rounds = []
        for _ in range(7):  # repeated rounds: the per-call overhead varies with machine load
            tp, _ = timed(lambda: [plain("abc") for _ in range(n)])
            tw, _ = timed(lambda: [wrapped("abc") for _ in range(n)])
            rounds.append((tw - tp) / n * 1e6)
    results["sensor_overhead_us_per_call"] = round(statistics.median(rounds), 2)
    results["sensor_overhead_us_spread"] = spread(rounds)

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "perf.db"
        engine = Engine(f"sqlite:///{db.as_posix()}")
        sink = ListSink()
        aw.configure(sink)
        workload(args.spans)
        workload(args.spans, variant=1)
        drafts = list(sink.drafts)  # a copy: the sink is cleared for the incremental step below
        t_ingest, _ = timed(lambda: engine.ingest(drafts))
        results["observations"] = len(drafts)
        results["ingest_obs_per_s"] = round(len(drafts) / t_ingest, 1)
        # ingest again into fresh stores (a store is idempotent: re-ingesting only dedupes)
        rates = [len(drafts) / t_ingest]
        for k in range(2):
            fresh = Engine(f"sqlite:///{(Path(tmp) / f'ingest{k}.db').as_posix()}")
            t_k, _ = timed(lambda fresh=fresh: fresh.ingest(drafts))
            rates.append(len(drafts) / t_k)
            fresh.store.close()
        results["ingest_obs_per_s"] = round(statistics.median(rates), 1)
        results["ingest_obs_per_s_spread"] = spread(rates)
        rebuilds = []
        for _ in range(3):
            t_k, report = timed(lambda: engine.process(force=True))
            rebuilds.append(t_k)
        t_process = statistics.median(rebuilds)
        results["rebuild_s"] = round(t_process, 3)
        results["rebuild_s_spread"] = spread(rebuilds)
        results["rebuild_events_per_s"] = round(report["events"] / t_process, 1)  # type: ignore[index]
        results["relations"] = report["relations"]  # type: ignore[index]
        # graph construction alone (the part of a rebuild that builds relations)
        from panoptis.graph.build import build_execution, build_information
        from panoptis.runs.segment import build_source_index, segment

        built = engine.build(
            "default", engine.interp_id_for("default"), engine.store.observations()
        )
        evs = built["events"]
        seg = segment(evs, "default")
        index = build_source_index(evs)
        from panoptis.events.normalize import NormalizeContext

        nctx = NormalizeContext(
            "default",
            engine.interp_id_for("default"),
            engine.store.artifact_key("default"),
            artifacts=dict(built["artifacts"]),
        )

        def graph_only() -> int:
            return len(build_execution(evs, seg.run_of, index)) + len(
                build_information(
                    evs,
                    seg.run_of,
                    nctx.artifacts,
                    lambda v, r: nctx.artifact(v, r).artifact_id,
                    index,
                )
            )

        t_graph_construct, _ = timed(graph_only)
        results["graph_construction_s"] = round(t_graph_construct, 3)
        # one more small run arriving after the big rebuild: incremental path
        sink.drafts.clear()
        workload(40, variant=2)
        engine.ingest(list(sink.drafts))
        t_inc, inc = timed(lambda: engine.process())
        results["incremental_process_s"] = round(t_inc, 3)
        results["incremental_mode"] = inc.get("mode")  # type: ignore[union-attr]
        ws = Workspace(engine)
        a, b = (
            sorted(ws.runs(), key=run_order_key)[0]["run_id"],
            sorted(ws.runs(), key=run_order_key)[1]["run_id"],
        )  # the two large runs
        t_graph, g = timed(lambda: ws._build_graph(a, None), repeat=3)  # uncached build
        results["graph_build_ms"] = round(t_graph * 1000, 1)
        start = f"event:{ws.events(a, kind='RETRIEVAL')[0]['event_id']}"
        t_trav, steps = timed(lambda: g.descendants(start), repeat=3)  # type: ignore[attr-defined]
        results["descendants_ms"] = round(t_trav * 1000, 2)
        results["descendants_reached"] = len(steps)  # type: ignore[arg-type]
        last_tool = ws.events(a, kind="TOOL_INVOCATION")[-1]
        t_prov, _ = timed(
            lambda: lineage(ws, f"event:{last_tool['event_id']}", run_id=a, max_depth=8)
        )
        results["provenance_ms"] = round(t_prov * 1000, 1)
        t_cmp, _ = timed(lambda: compare(ws, a, b))
        results["compare_ms"] = round(t_cmp * 1000, 1)
        engine.store.seal_all()
        t_ver, rep = timed(lambda: engine.store.verify())
        results["verify_s"] = round(t_ver, 3)
        engine.store.close()
        size = os.path.getsize(db) + sum(
            p.stat().st_size for p in Path(tmp).glob("perf.db*") if p != db
        )
        results["store_bytes"] = size
        results["store_bytes_per_observation"] = round(size / len(drafts), 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = out_dir / "latest.json"
    if previous.exists():
        before = json.loads(previous.read_text(encoding="utf-8"))
        results["compared_with"] = before.get("generated_at")
        results["change_vs_previous"] = {
            k: {"before": before[k], "now": v, "ratio": round(v / before[k], 3)}
            for k, v in results.items()
            if isinstance(v, (int, float))
            and isinstance(before.get(k), (int, float))
            and before[k]
            and k != "spans"
        }
    body = json.dumps(results, indent=2)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"perf-{stamp}.json").write_text(body, encoding="utf-8")
    previous.write_text(body, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
