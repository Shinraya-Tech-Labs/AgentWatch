"""Reproducible protection evaluation and overhead benchmark (TO-036, TO-037).

    python -m benchmarks.protect_eval.run [--out benchmarks/results/protect_eval] [--iterations 300]

Writes ``results.json`` (raw per-case outcomes and every latency sample's summary) and
``REPORT.md`` (methodology, environment, results by threat category and integration,
false positives, overhead, limitations). Nothing is executed: evaluation tools are no-op
stubs; all fixtures are synthetic (see corpus.py). Deterministic: no randomness is used,
so the seed is recorded only for completeness.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from importlib import metadata
from pathlib import Path
from typing import Any

from benchmarks.protect_eval import corpus as C  # noqa: N812
from panoptis.protect import (
    ActionBlocked,
    ApprovalBroker,
    PolicyStore,
    ProtectionPipeline,
    ToolAction,
    protect_tool,
)
from panoptis.protect.enforce import enforce_call
from panoptis.protect.ledger import ProtectLedger
from panoptis.protect.loops import LoopDetector
from panoptis.protect.model import Verdict
from panoptis.protect.output import OutputInspector

SEED = 0


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[k]


def lat(xs: list[float]) -> dict[str, float]:
    return {
        "n": len(xs),
        "p50_ms": round(pct(xs, 50), 3),
        "p95_ms": round(pct(xs, 95), 3),
        "p99_ms": round(pct(xs, 99), 3),
        "mean_ms": round(statistics.fmean(xs), 3) if xs else float("nan"),
    }


def act(
    tool: str,
    args: dict[str, Any],
    agent: str = "eval-agent",
    session: str = "eval",
    objective: str | None = None,
) -> ToolAction:
    return ToolAction(
        agent_id=agent, session_id=session, tool=tool, arguments=dict(args), objective=objective
    )


# ── integrations ───────────────────────────────────────────────────────────────────


def via_pipeline(p: ProtectionPipeline, case: dict[str, Any]) -> tuple[str, float, Any]:
    t0 = time.perf_counter()
    d = p.evaluate(
        act(case["tool"], case["arguments"], session=f"s-{case['id']}"), wait_for_approval=False
    )
    return d.verdict.value, (time.perf_counter() - t0) * 1000, d


def via_protect_tool(p: ProtectionPipeline, case: dict[str, Any]) -> tuple[str, float, int]:
    runs: list[int] = []

    def tool(**kwargs: Any) -> str:
        runs.append(1)
        return "ok"

    tool.__name__ = case["tool"]
    wrapped = protect_tool(tool, pipeline=p, agent_id="eval-agent", session_id=f"pt-{case['id']}")
    t0 = time.perf_counter()
    try:
        wrapped(**case["arguments"])
        verdict = "allow"
    except ActionBlocked as b:
        verdict = b.decision.verdict.value
    return verdict, (time.perf_counter() - t0) * 1000, len(runs)


def via_mcp_gateway(p: ProtectionPipeline, case: dict[str, Any]) -> tuple[str, float, int]:
    from panoptis.protect.mcp_gateway import GatewayIdentity, MCPGateway

    forwarded: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

    async def go() -> float:
        gw: MCPGateway

        async def up(m: dict[str, Any]) -> None:
            if m.get("method") == "tools/call":
                forwarded.append(m)
                asyncio.get_running_loop().call_soon(
                    lambda: asyncio.ensure_future(
                        gw.from_upstream(
                            {
                                "jsonrpc": "2.0",
                                "id": m["id"],
                                "result": {"content": [{"type": "text", "text": "ok"}]},
                            }
                        )
                    )
                )

        async def cl(m: dict[str, Any]) -> None:
            replies.append(m)

        gw = MCPGateway(
            p,
            send_client=cl,
            send_upstream=up,
            identity=GatewayIdentity(agent_id="eval-agent", session_id=f"mcp-{case['id']}"),
        )
        t0 = time.perf_counter()
        await gw.from_client(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": case["tool"], "arguments": case["arguments"]},
            }
        )
        while not replies:
            await asyncio.sleep(0)
        return (time.perf_counter() - t0) * 1000

    ms = asyncio.run(go())
    res = replies[0].get("result") or {}
    meta = (res.get("_meta") or {}).get("agentwatch") or {}
    verdict = meta.get("verdict", "allow" if forwarded else "error")
    return verdict, ms, len(forwarded)


CLAUDE_TOOLS = {
    "bash": ("Bash", lambda a: a),
    "read_file": ("Read", lambda a: {"file_path": a["path"]}),
    "write_file": ("Write", lambda a: {"file_path": a["path"], "content": a.get("content", "")}),
    "fetch_url": ("WebFetch", lambda a: {"url": a["url"], "prompt": "summarize"}),
}


def via_claude_hook(p: ProtectionPipeline, case: dict[str, Any]) -> tuple[str, float, None] | None:
    from panoptis.protect import claude_hook

    if case["tool"] not in CLAUDE_TOOLS:
        return None
    name, conv = CLAUDE_TOOLS[case["tool"]]
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": f"cc-{case['id']}",
        "tool_use_id": case["id"],
        "tool_name": name,
        "tool_input": conv(case["arguments"]),
    }
    t0 = time.perf_counter()
    out = claude_hook.handle(payload, p, env={})
    ms = (time.perf_counter() - t0) * 1000
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision")
    verdict = {"deny": "block", "ask": "escalate_hitl", "allow": "mutate_and_allow"}.get(
        decision or "", "allow"
    )
    return verdict, ms, None


def stopped(v: str) -> bool:
    return v in ("block", "escalate_hitl")


# ── evaluations ────────────────────────────────────────────────────────────────────


def eval_calls(cases: list[dict[str, Any]], integrations: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, fn in integrations.items():
        rows = []
        for case in cases:
            r = fn(case)
            if r is None:
                continue
            verdict, ms, extra = r
            ok = (
                (case["expect"] == "allow" and verdict == "allow")
                or (case["expect"] == "stop" and stopped(verdict))
                or (
                    case["expect"] == "redact"
                    and verdict in ("mutate_and_allow", "block", "escalate_hitl")
                )
            )
            rows.append(
                {
                    "id": case["id"],
                    "label": case["label"],
                    "expect": case["expect"],
                    "verdict": verdict,
                    "correct": ok,
                    "ms": round(ms, 3),
                    "executed": extra,
                }
            )
        out[name] = summarize(rows)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attacks = [r for r in rows if r["label"] == "attack"]
    benign = [r for r in rows if r["label"] == "benign"]
    detected = [r for r in attacks if r["verdict"] != "allow"]
    return {
        "attacks": len(attacks),
        "benign": len(benign),
        "detection_rate": round(len(detected) / len(attacks), 4) if attacks else None,
        "block_rate": round(sum(r["verdict"] == "block" for r in attacks) / len(attacks), 4)
        if attacks
        else None,
        "escalate_rate": round(
            sum(r["verdict"] == "escalate_hitl" for r in attacks) / len(attacks), 4
        )
        if attacks
        else None,
        "missed": [r["id"] for r in attacks if r["verdict"] == "allow"],
        "false_positives": [r["id"] for r in benign if r["verdict"] != "allow"],
        "false_positive_rate": round(sum(r["verdict"] != "allow" for r in benign) / len(benign), 4)
        if benign
        else None,
        "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else None,
        "latency": lat([r["ms"] for r in rows]),
        "rows": rows,
    }


def eval_outputs(cases: list[dict[str, Any]]) -> dict[str, Any]:
    insp = OutputInspector()
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        r = insp.inspect(c["tool"], c["output"])
        ms = (time.perf_counter() - t0) * 1000
        text = (
            r.sanitized_output
            if isinstance(r.sanitized_output, str)
            else json.dumps(r.sanitized_output)
        )
        if c["label"] == "attack":
            removed = (
                "BANANA" not in text
                and "hunter2hunter2" not in text
                and C.FAKE_AWS_KEY not in text
                and "SECRETDATA1234" not in text
            )
            kept = all(k in text for k in c.get("keep", []))
            # detected = the result the agent receives was changed (quarantined, redacted
            # or withheld); secret redaction alone leaves the inspection "safe"
            changed = text != c["output"] or not r.safe
            rows.append(
                {
                    "id": c["id"],
                    "label": "attack",
                    "verdict": "sanitize" if changed else "allow",
                    "injection_removed": removed,
                    "benign_content_kept": kept,
                    "correct": removed and kept,
                    "ms": round(ms, 3),
                }
            )
        else:
            unchanged = text == c["output"]
            rows.append(
                {
                    "id": c["id"],
                    "label": "benign",
                    "verdict": "allow" if (r.safe and unchanged) else "sanitize",
                    "unchanged": unchanged,
                    "correct": unchanged,
                    "ms": round(ms, 3),
                }
            )
    s = summarize(rows)
    att = [r for r in rows if r["label"] == "attack"]
    s["sanitization_correct"] = round(sum(r["correct"] for r in att) / len(att), 4)
    s["benign_preserved"] = round(
        sum(r["correct"] for r in rows if r["label"] == "benign") / max(1, len(rows) - len(att)), 4
    )
    return s


def eval_rbac(cases: list[dict[str, Any]]) -> dict[str, Any]:
    store = PolicyStore()
    store.load_document(C.RBAC_POLICY)
    p = ProtectionPipeline(policy=store)
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        d = p.evaluate(
            act(c["tool"], c["arguments"], agent=c["agent"], session=f"rb-{c['id']}"),
            wait_for_approval=False,
        )
        ms = (time.perf_counter() - t0) * 1000
        v = d.verdict.value
        rows.append(
            {
                "id": c["id"],
                "label": c["label"],
                "expect": c["expect"],
                "verdict": v,
                "correct": (v == "allow") == (c["expect"] == "allow"),
                "ms": round(ms, 3),
            }
        )
    return summarize(rows)


def eval_loops(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for c in cases:
        d = LoopDetector()
        flagged: list[str] = []
        t0 = time.perf_counter()
        for i, (tool, args, out, ok) in enumerate(c["calls"]):
            cid = f"{c['id']}-{i}"
            findings = d.check("s", cid, tool, args)
            flagged += [f.code for f in findings if f.recommends is not Verdict.ALLOW]
            d.record_result(cid, success=ok, output=out, session_id="s")
        ms = (time.perf_counter() - t0) * 1000 / len(c["calls"])
        rows.append(
            {
                "id": c["id"],
                "label": c["label"],
                "verdict": "escalate_hitl" if flagged else "allow",
                "codes": sorted(set(flagged)),
                "correct": bool(flagged) == (c["label"] == "attack"),
                "ms": round(ms, 4),
            }
        )
    return summarize(rows)


def eval_intent(cases: list[dict[str, Any]]) -> dict[str, Any]:
    p = ProtectionPipeline()
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        d = p.evaluate(
            act(c["tool"], c["arguments"], session=f"in-{c['id']}", objective=c["objective"]),
            wait_for_approval=False,
        )
        ms = (time.perf_counter() - t0) * 1000
        intent = [
            f.code
            for f in d.findings
            if f.stage.value == "intent" and f.recommends is not Verdict.ALLOW
        ]
        # judged on the intent stage: other stages (e.g. a recursive delete escalating in
        # threat analysis) are reported in overall_verdict, not counted as intent errors
        v = "escalate_hitl" if intent else "allow"
        rows.append(
            {
                "id": c["id"],
                "label": c["label"],
                "verdict": v,
                "overall_verdict": d.verdict.value,
                "intent_findings": intent,
                "correct": (v != "allow") == (c["label"] == "attack"),
                "ms": round(ms, 3),
            }
        )
    return summarize(rows)


# ── overhead ───────────────────────────────────────────────────────────────────────


def overhead(iterations: int) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp())
    results: dict[str, Any] = {}

    def tool(args: dict[str, Any]) -> str:
        return "ok"

    def bench(
        label: str,
        p: ProtectionPipeline,
        tool_name: str,
        args: dict[str, Any],
        fn: Any = tool,
        n: int = iterations,
    ) -> None:
        xs = []
        for i in range(n):
            a = ToolAction(
                agent_id="bench",
                session_id=f"{label}-{i % 50}",
                tool=tool_name,
                arguments=dict(args),
            )
            t0 = time.perf_counter()
            try:
                enforce_call(p, a, fn)
            except ActionBlocked:
                pass
            xs.append((time.perf_counter() - t0) * 1000)
        results[label] = lat(xs)

    baseline = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        tool({"path": "README.md"})
        baseline.append((time.perf_counter() - t0) * 1000)
    results["baseline_direct_call"] = lat(baseline)
    for ledger_label, ledger in (
        ("no_ledger", None),
        ("sqlite_ledger", ProtectLedger(f"sqlite:///{(tmp / 'bench.db').as_posix()}")),
    ):
        p = ProtectionPipeline(ledger=ledger)
        bench(f"allow/{ledger_label}", p, "read_file", {"path": "README.md"})
        bench(f"block/{ledger_label}", p, "bash", {"command": "rm -rf /"})
        bench(
            f"mutate/{ledger_label}",
            p,
            "write_file",
            {"path": "n.txt", "content": f"k {C.FAKE_AWS_KEY}"},
        )
        bench(
            f"output_sanitize/{ledger_label}",
            p,
            "read_doc",
            {"name": "v2"},
            fn=lambda a: f"Notes\n{C.INJECTION}\nEnd",
        )
        pe = ProtectionPipeline(
            ledger=ledger, approvals=ApprovalBroker(ledger, approver=lambda r: True, timeout_s=5)
        )
        bench(
            f"escalate_auto_approved/{ledger_label}",
            pe,
            "bash",
            {"command": "git push --force origin main"},
            n=max(20, iterations // 5),
        )
    # throughput under concurrency (allow path, no ledger)
    p = ProtectionPipeline()
    for threads in (1, 4, 8):
        n_each = max(50, iterations // threads)
        start = threading.Barrier(threads + 1)

        refused: list[int] = []

        def worker(
            k: int, threads: int = threads, n_each: int = n_each, refused: list[int] = refused
        ) -> None:
            start.wait()
            for i in range(n_each):
                # distinct sessions per run: repeating a session's calls would (correctly)
                # trigger the loop detector
                a = ToolAction(
                    agent_id="bench",
                    session_id=f"thr{threads}-{k}-{i % 20}",
                    tool="read_file",
                    arguments={"path": f"f{i}"},
                )
                try:
                    enforce_call(p, a, tool)
                except ActionBlocked:
                    refused.append(i)

        ts = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
        for t in ts:
            t.start()
        start.wait()
        t0 = time.perf_counter()
        for t in ts:
            t.join()
        el = time.perf_counter() - t0
        results[f"throughput/{threads}_threads"] = {
            "refused": len(refused),
            "calls": threads * n_each,
            "seconds": round(el, 3),
            "calls_per_s": round(threads * n_each / el, 1),
        }
    # memory
    gc.collect()
    tracemalloc.start()
    p = ProtectionPipeline()
    for i in range(1000):
        enforce_call(
            p,
            ToolAction(
                agent_id="bench",
                session_id=f"mem-{i % 100}",
                tool="read_file",
                arguments={"path": f"f{i}"},
            ),
            tool,
        )
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    results["memory/1000_calls_100_sessions"] = {
        "current_kib": round(cur / 1024, 1),
        "peak_kib": round(peak / 1024, 1),
    }
    return results


# ── environment and report ─────────────────────────────────────────────────────────


def environment() -> dict[str, Any]:
    def v(pkg: str) -> str | None:
        try:
            return metadata.version(pkg)
        except metadata.PackageNotFoundError:
            return None

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH, read-only
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()  # noqa: S603, S607
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607 - read-only
                capture_output=True,
                text=True,
                check=False,
                cwd=Path(__file__).resolve().parents[2],
            ).stdout.strip()
        )
    except OSError:
        commit, dirty = None, False
    return {
        "commit": commit,
        "uncommitted_changes": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "packages": {
            k: v(k) for k in ("panoptis", "fastapi", "starlette", "sqlalchemy", "pydantic", "mcp")
        },
        "corpus_version": C.VERSION,
        "seed": SEED,
        "command": " ".join(sys.argv),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmarks/results/protect_eval")
    ap.add_argument("--iterations", type=int, default=300)
    ns = ap.parse_args(argv)
    os.environ.setdefault("AGENTWATCH_KILL_FILE", str(Path(tempfile.mkdtemp()) / "eval.kill"))
    os.environ.setdefault("AGENTWATCH_FORWARD_EVENTS", "0")
    corp = C.corpus()

    def fresh() -> ProtectionPipeline:
        return ProtectionPipeline()

    integrations = {
        "pipeline": lambda c: via_pipeline(fresh(), c),
        "protect_tool": lambda c: via_protect_tool(fresh(), c),
        "mcp_gateway": lambda c: via_mcp_gateway(fresh(), c),
        "claude_code_hook": lambda c: via_claude_hook(fresh(), c),
    }
    results = {
        "environment": environment(),
        "categories": {
            "dangerous_arguments": eval_calls(corp["dangerous_arguments"], integrations),
            "secrets": eval_calls(corp["secrets"], {"pipeline": integrations["pipeline"]}),
            "prompt_injection_outputs": eval_outputs(corp["prompt_injection_outputs"]),
            "policy_violations": eval_rbac(corp["policy_violations"]),
            "loops": eval_loops(corp["loops"]),
            "intent": eval_intent(corp["intent"]),
        },
        "overhead": overhead(ns.iterations),
    }
    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out / "corpus_v1.json").write_text(json.dumps(corp, indent=2, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text(report(results), encoding="utf-8")
    cats = results["categories"]
    da = cats["dangerous_arguments"]["pipeline"]
    print(
        json.dumps(
            {
                "dangerous_arguments": {
                    k: da[k] for k in ("detection_rate", "false_positive_rate")
                },
                "out": str(out),
            }
        )
    )
    return 0


def report(r: dict[str, Any]) -> str:
    env = r["environment"]
    c = r["categories"]
    lines = [
        "# Protection evaluation and overhead report",
        "",
        "Generated by `python -m benchmarks.protect_eval.run` (TO-036, TO-037). **Synthetic corpus only**: the cases are written for this project, harmless, and labelled as defensive fixtures (`benchmarks/protect_eval/corpus.py`, version "
        + env["corpus_version"]
        + "). No external dataset was used, so these numbers say how the implemented rules handle these cases; they are not a measure of real-world attack coverage and are not comparable with other products' published numbers.",
        "",
        "## Environment",
        "",
        f"- commit `{env['commit']}`{' plus uncommitted changes' if env.get('uncommitted_changes') else ''}; Python {env['python']}; {env['platform']}; {env['machine']} ({env['processor']}), {env['cpu_count']} CPUs",
        "- packages: " + ", ".join(f"{k} {v}" for k, v in env["packages"].items()),
        f"- seed {env['seed']} (no randomness is used); command: `{env['command']}`",
        "",
        "## Metrics",
        "",
        "- **detection rate**: attacks the decision did not allow (blocked, escalated to a human, or mutated); **block rate** / **escalate rate**: of attacks.",
        "- **false-positive rate**: benign controls that were not allowed (or, for outputs, not returned unchanged).",
        "- **missed**: attack case ids that were allowed. **sanitization correct**: the injected/secret content removed *and* the legitimate content kept.",
        "- **latency**: wall time of the decision (or inspection), p50/p95/p99 over the cases; overhead below is measured separately with repetitions.",
        "",
        "## Results by threat category",
        "",
        "| Category | Integration | Attacks | Benign | Detection | Block | Escalate | FP rate | Missed | False positives | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def row(cat: str, integ: str, s: dict[str, Any]) -> str:
        return f"| {cat} | {integ} | {s['attacks']} | {s['benign']} | {s['detection_rate']} | {s['block_rate']} | {s['escalate_rate']} | {s['false_positive_rate']} | {', '.join(s['missed']) or '—'} | {', '.join(s['false_positives']) or '—'} | {s['latency']['p50_ms']} | {s['latency']['p95_ms']} |"

    for integ, s in c["dangerous_arguments"].items():
        lines.append(row("dangerous tool arguments", integ, s))
    lines.append(row("secret exposure", "pipeline", c["secrets"]["pipeline"]))
    lines.append(
        row("prompt injection (tool output)", "output inspector", c["prompt_injection_outputs"])
    )
    lines.append(row("policy violations (RBAC)", "pipeline", c["policy_violations"]))
    lines.append(row("loops", "loop detector", c["loops"]))
    lines.append(row("intent mismatch", "pipeline", c["intent"]))
    pio = c["prompt_injection_outputs"]
    lines += [
        "",
        f"Output sanitization: attack outputs correctly sanitized (injection/secret removed, legitimate lines kept): **{pio['sanitization_correct']}**; benign outputs returned byte-for-byte unchanged: **{pio['benign_preserved']}**.",
        "",
        "Integrations: the same decision engine runs behind every integration, so their detection should match; the Claude Code hook row covers only the cases expressible as Claude tools (Bash, Read, Write, WebFetch), and the `protect_tool` row also confirms that no blocked tool body ran (`executed` in `results.json`).",
        "",
        "## Runtime overhead",
        "",
        "Each path measured through `enforce_call` (decide, run a no-op tool, inspect output, record), repeated; `sqlite_ledger` records every decision and execution in a local SQLite evidence store.",
        "",
        "| Path | n | p50 ms | p95 ms | p99 ms | mean ms |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in r["overhead"].items():
        if "p50_ms" in v:
            lines.append(
                f"| {k} | {v['n']} | {v['p50_ms']} | {v['p95_ms']} | {v['p99_ms']} | {v['mean_ms']} |"
            )
    lines += ["", "| Concurrency / memory | result |", "|---|---|"]
    for k, v in r["overhead"].items():
        if "p50_ms" not in v:
            lines.append(f"| {k} | {json.dumps(v)} |")
    lines += [
        "",
        "## Reliability (covered by tests, not timed here)",
        "",
        "Timeouts, cancellation, upstream failures and degraded dependencies are exercised by the test suite: gateway upstream timeout/disconnect/error (`tests/protect/test_mcp_gateway.py`), approval timeout/cancellation/emergency stop (`tests/protect/test_approvals_lifecycle.py`, `test_emergency_controls.py`), fail-closed enforcement errors (`tests/protect/test_enforce.py`), store failure in the API (`tests/protect/test_protect_api.py`), unreachable API in the CLI (`tests/protect/test_cli_deployment.py`).",
        "",
        "## Limitations",
        "",
        "- Synthetic, project-authored cases: a high score shows the rules do what they are written to do on these inputs, not that unseen attacks are caught. Paraphrased injections and novel command forms can evade pattern rules.",
        "- Latency is single-process, on the machine above; the SQLite ledger numbers depend on the disk. Performance targets should be set from product requirements, not from this run.",
        "- No competitor comparison is made.",
        "",
        "Raw per-case outcomes: `results.json`; the exact corpus: `corpus_v1.json`.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
