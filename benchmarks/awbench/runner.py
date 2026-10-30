"""AWBench runner.

    python -m benchmarks.awbench.runner            # quick matrix (seed 0, drift n=10)
    python -m benchmarks.awbench.runner --seeds 3  # more seeds

1. Runs every (architecture, scenario, seed) of the registry as a separate observed
   process (``panoptis observe``) into a fresh store. Ground truth goes to separate files
   that the AgentWatch pipeline never reads.
2. Evaluates each task against ground truth.
3. Writes a machine-generated result file (git SHA, registry hash, environment) and
   compares it with the pre-registered thresholds. Measured values are never edited.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from panoptis.evidence.canonical import canonical_json, sha256_hex
from panoptis.lab.observe import observe
from panoptis.query.workspace import Workspace
from panoptis.runtime.engine import Engine

HERE = Path(__file__).resolve().parent
SYSTEM = HERE / "systems" / "workbench.py"
REGISTRY = HERE / "REGISTRY.yaml"
RESULTS = HERE / "results"


@dataclass
class RunRecord:
    arch: str
    scenario: str
    seed: int
    sensor: str
    run_id: str
    gt: dict[str, Any]
    extra_runs: list[str] = field(default_factory=list)
    held_out: bool = False


def _git(*args: str) -> str | None:
    from panoptis.cli._utils.run_cmd import run_validated_command

    try:
        code, out, _ = run_validated_command(["git", *args], check=False)
    except OSError:
        return None
    return out if code == 0 else None


def _git_dirty() -> bool | None:
    out = _git("status", "--porcelain", "--", "panoptis", "benchmarks/awbench")
    if out is None:
        return None
    return any(
        line and not line[3:].startswith("benchmarks/awbench/results") for line in out.splitlines()
    )


def _git_sha() -> str:
    return _git("rev-parse", "HEAD") or "unknown"


def _credential_problem(spec: str) -> str | None:
    """None when the provider accepts the credential; otherwise why not (never raises)."""
    provider = spec.partition(":")[0]
    var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    if var is None:
        return f"unsupported provider {provider!r}"
    if not os.environ.get(var):
        return f"{var} is not set"
    try:
        if provider == "anthropic":
            import anthropic

            anthropic.Anthropic().models.list(limit=1)
        else:
            import openai

            openai.OpenAI().models.list()
    except Exception as exc:  # noqa: BLE001 - reported
        status = getattr(exc, "status_code", None)
        return f"{provider} rejected the credential (HTTP {status})" if status else str(exc)
    return None


def effective_status(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Evidence status of an architecture, computed rather than asserted: a declared held-out
    architecture becomes FORMER_HELD_OUT as soon as AgentWatch code changed after its first
    scored run (its later results may have been shaped by it)."""
    spec = spec or {}
    declared = spec.get("declared", "DEVELOPMENT")
    first = spec.get("first_scored_run")
    if declared != "HELD_OUT":
        return {"status": declared}
    if not first:
        return {"status": "HELD_OUT", "reason": "first scored run"}
    changed = _git("diff", "--name-only", first["commit"], "HEAD", "--", "panoptis")
    if changed is None:
        return {"status": "UNKNOWN", "reason": "git unavailable"}
    if changed.strip() or _git_dirty():
        return {
            "status": "FORMER_HELD_OUT",
            "reason": f"panoptis/ changed since first scored run {first['commit']}",
            "first_scored_run": first,
        }
    return {"status": "HELD_OUT", "first_scored_run": first}


def execute_matrix(
    engine: Engine, gt_dir: Path, registry: dict[str, Any], seeds: int, drift_n: int
) -> list[RunRecord]:
    records: list[RunRecord] = []

    def run(arch: str, scenario: str, seed: int, sensor: str = "native") -> RunRecord:
        gt_path = gt_dir / f"{arch}-{scenario}-{seed}-{sensor}.json"
        before = (
            {r["run_id"] for r in Workspace(engine, process=False).runs()}
            if sensor.startswith("otel")
            else set()
        )
        res = observe(
            engine,
            [
                "python",
                str(SYSTEM),
                "--arch",
                arch,
                "--scenario",
                scenario,
                "--seed",
                str(seed),
                "--gt",
                str(gt_path),
                "--sensor",
                sensor,
            ],
        )
        if res.exit_code != 0 or not gt_path.exists():
            raise RuntimeError(
                f"system run failed: {arch}/{scenario}/{seed}/{sensor}: {res.stderr[-1500:]}"
            )
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        run_id = res.run_id or ""
        extra: list[str] = []
        if sensor.startswith("otel"):
            new = [
                r["run_id"]
                for r in Workspace(engine).runs()
                if r["run_id"] not in before and r["run_id"] != res.run_id
            ]
            run_id, extra = (new[0] if new else ""), [res.run_id or ""]
        rec = RunRecord(arch, scenario, seed, sensor, run_id, gt, extra)
        records.append(rec)
        return rec

    for arch, scenarios in (registry.get("held_out") or {}).items():
        for seed in range(seeds):
            for scenario in scenarios:
                run(arch, scenario, seed).held_out = True
    for arch, scenarios in registry["scenarios"].items():
        for seed in range(seeds):
            for scenario in scenarios:
                run(arch, scenario, seed)
    for seed in range(seeds):
        run("tool_loop", "normal", seed, "otel")
        run("tool_loop", "normal", seed, "otel_enriched")
    # drift sets: extra seeds of normal vs model_substitution (tool_loop)
    for seed in range(seeds, drift_n):
        run("tool_loop", "normal", seed)
        run("tool_loop", "model_substitution", seed)
    for seed in range(drift_n, 2 * drift_n):
        run("tool_loop", "normal", seed)  # control set
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--drift-n", type=int, default=10)
    ap.add_argument("--out", type=Path, default=RESULTS)
    ap.add_argument(
        "--keep-store",
        type=Path,
        default=None,
        help="write the benchmark store here for inspection",
    )
    ap.add_argument(
        "--real-model",
        default=None,
        metavar="PROVIDER:MODEL",
        help="opt-in: replace the stub models with a real provider (anthropic:<model> or "
        "openai:<model>); results go to results/real/ and never replace the stub results",
    )
    ap.add_argument("--real-model-alt", default=None, metavar="PROVIDER:MODEL")
    ap.add_argument(
        "--archs", default=None, help="comma-separated architectures to run (default: all)"
    )
    ap.add_argument(
        "--first-scored-run",
        action="append",
        default=[],
        metavar="ARCH",
        help="score a never-scored HELD_OUT architecture (otherwise it is skipped, so it cannot "
        "be looked at by accident during development)",
    )
    args = ap.parse_args(argv)

    from benchmarks.awbench import tasks

    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    if args.real_model:
        reason = _credential_problem(args.real_model)
        if reason:
            print(f"SKIPPED_EXTERNAL_CREDENTIAL: {reason}")
            return 0
        os.environ["AWBENCH_MODEL"] = args.real_model
        if args.real_model_alt:
            os.environ["AWBENCH_MODEL_ALT"] = args.real_model_alt
        if args.out == RESULTS:
            args.out = RESULTS / "real"
    if args.archs:
        keep = set(args.archs.split(","))
        registry = {
            **registry,
            "scenarios": {k: v for k, v in registry["scenarios"].items() if k in keep},
            "held_out": {k: v for k, v in (registry.get("held_out") or {}).items() if k in keep},
        }
    status_specs = registry.get("architecture_status") or {}
    held_out = dict(registry.get("held_out") or {})
    for arch in list(held_out):
        st = effective_status(status_specs.get(arch))
        if st.get("reason") == "first scored run" and arch not in args.first_scored_run:
            print(f"skipping never-scored held-out architecture {arch} (--first-scored-run {arch})")
            del held_out[arch]
    registry = {**registry, "held_out": held_out}
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="awbench-") as tmp:
        tmpdir = Path(tmp)
        store_path = args.keep_store or (tmpdir / "awbench.db")
        if args.keep_store and store_path.exists():
            store_path.unlink()
        engine = Engine(f"sqlite:///{store_path.as_posix()}")
        gt_dir = tmpdir / "ground_truth"
        gt_dir.mkdir()
        drift_n = max(args.drift_n, args.seeds)
        records = execute_matrix(engine, gt_dir, registry, args.seeds, drift_n)
        t_runs = time.perf_counter()
        ws = Workspace(engine)
        held = [r for r in records if r.held_out]
        records = [r for r in records if not r.held_out]
        results = tasks.evaluate_all(ws, engine, records, drift_n)
        # each held-out architecture is scored on its own: one that has informed a fix no
        # longer tests that fix, so pooling them would blur held-out and development evidence
        held_results = {
            arch: tasks.evaluate_held_out(
                Workspace(engine), engine, [r for r in held if r.arch == arch]
            )
            for arch in dict.fromkeys(r.arch for r in held)
        }
        per_seed: dict[int, dict[str, Any]] = {}
        if args.seeds > 1:
            matrix_seeds = range(args.seeds)
            for sd in matrix_seeds:
                subset = [r for r in records if r.seed == sd]
                per_seed[sd] = tasks.evaluate_all(
                    Workspace(engine), engine, subset, drift_n, include_drift=False
                )
        engine.store.close()
    finished = time.perf_counter()
    report: dict[str, Any] = {
        "awbench_version": registry["version"],
        "registry_sha256": sha256_hex(REGISTRY.read_text(encoding="utf-8")),
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "config": {
            "seeds": args.seeds,
            "drift_n": args.drift_n,
            "runs": len(records),
            "models": (
                f"REAL provider model {os.environ['AWBENCH_MODEL']} (not deterministic)"
                if os.environ.get("AWBENCH_MODEL")
                else "deterministic stubs (no real LLMs)"
            ),
        },
        "timing_s": {
            "system_runs": round(t_runs - t0, 2),
            "evaluation": round(finished - t_runs, 2),
        },
        "tasks": {},
    }
    for name, measured in results.items():
        spec = registry["tasks"].get(name, {})
        thr = spec.get("thresholds", {})
        passed = {k: _meets(measured["metrics"].get(k), v) for k, v in thr.items()}
        spread = _spread(name, per_seed)
        if spread:
            measured = {**measured, "across_seeds": spread}
        report["tasks"][name] = {
            "hypothesis": spec.get("hypothesis"),
            "capability": spec.get("capability"),
            "thresholds": thr,
            **measured,
            "meets_threshold": passed,
            "all_thresholds_met": all(passed.values()) if passed else None,
        }
    report["held_out"] = {}
    report["held_out_notes"] = registry.get("held_out_notes") or {}
    status_specs = registry.get("architecture_status") or {}
    report["architecture_status"] = {
        arch: effective_status(status_specs.get(arch))
        for arch in [*registry["scenarios"], *(registry.get("held_out") or {})]
    }
    for arch, arch_results in held_results.items():
        report["held_out"][arch] = {}
        for name, measured in arch_results.items():
            spec = registry["tasks"].get(name, {})
            thr = spec.get("thresholds", {})
            passed = {
                k: _meets(measured["metrics"].get(k), v)
                for k, v in thr.items()
                if k in measured["metrics"]
            }
            report["held_out"][arch][name] = {
                **measured,
                "thresholds": thr,
                "meets_threshold": passed,
            }
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    body = json.dumps(report, indent=2, default=str)
    (args.out / f"awbench-{stamp}.json").write_text(body, encoding="utf-8")
    (args.out / "latest.json").write_text(body, encoding="utf-8")
    (args.out / "LATEST.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0


def _spread(task: str, per_seed: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """Per-metric mean/min/max over seeds (each seed evaluated on its own runs only)."""
    if len(per_seed) < 2:
        return None
    out: dict[str, Any] = {}
    metrics = set().union(*(set(v.get(task, {}).get("metrics", {})) for v in per_seed.values()))
    for m in sorted(metrics):
        vals = [v[task]["metrics"].get(m) for v in per_seed.values() if task in v]
        nums = [float(x) for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if nums:
            out[m] = {
                "mean": round(sum(nums) / len(nums), 4),
                "min": round(min(nums), 4),
                "max": round(max(nums), 4),
                "n_seeds": len(nums),
            }
    return out


def _meets(value: Any, threshold: Any) -> bool:
    if value is None:
        return False
    if isinstance(threshold, bool):
        return value is threshold
    if isinstance(threshold, (int, float)) and threshold == 0 and not isinstance(threshold, bool):
        return value <= threshold  # "max" style thresholds are registered as upper bounds of 0
    return value >= threshold


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AWBench results (machine-generated — do not edit)",
        "",
        f"- generated: {report['generated_at']}",
        f"- git: `{report['git_sha']}`{' (uncommitted changes present)' if report.get('git_dirty') else ''} · registry sha256 `{report['registry_sha256'][:12]}`",
        f"- runs: {report['config']['runs']} · seeds: {report['config']['seeds']} · models: {report['config']['models']}",
        f"- python {report['environment']['python']} on {report['environment']['platform']}",
        "",
        "| task | metric | measured (pooled) | across seeds (min–max) | threshold | met |",
        "|---|---|---|---|---|---|",
    ]
    for name, t in report["tasks"].items():
        for k, v in t["metrics"].items():
            thr = t["thresholds"].get(k, "")
            met = t["meets_threshold"].get(k)
            sp = (t.get("across_seeds") or {}).get(k)
            spread = f"{sp['min']}–{sp['max']} (n={sp['n_seeds']})" if sp else ""
            shown = v if not isinstance(v, float) else round(v, 4)
            verdict = "" if met is None else ("yes" if met else "**no**")
            lines.append(f"| {name} | {k} | {shown} | {spread} | {thr} | {verdict} |")
    notes = report.get("held_out_notes", {})
    for arch, arch_results in (report.get("held_out") or {}).items():
        lines += [
            "",
            f"## Held-out architecture: {arch} — status "
            f"{(report.get('architecture_status') or {}).get(arch, {}).get('status', '?')}",
            "",
            notes.get(arch, ""),
            "",
            "| task | metric | measured | threshold | met |",
            "|---|---|---|---|---|",
        ]
        for name, t in arch_results.items():
            for k, v in t["metrics"].items():
                thr = t["thresholds"].get(k, "")
                met = t["meets_threshold"].get(k)
                shown = v if not isinstance(v, float) else round(v, 4)
                verdict = "" if met is None else ("yes" if met else "**no**")
                lines.append(f"| {name} | {k} | {shown} | {thr} | {verdict} |")
    lines += [
        "",
        "Results come from stub systems (seeded document choice, failure counts and latency jitter); they do not measure behaviour with real models. Lab and faithfulness tasks use the lowest seed of each set.",
        "",
    ]
    for name, t in report["tasks"].items():
        if t.get("notes"):
            lines.append(f"- **{name}**: {t['notes']}")
    return "\n".join(lines) + "\n"


def config_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))[:12]


if __name__ == "__main__":
    raise SystemExit(main())
