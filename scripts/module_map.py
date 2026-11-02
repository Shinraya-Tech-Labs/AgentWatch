"""Map AgentWatch's modules to their entry points (TO-034).

    python scripts/module_map.py [--json out.json]

Builds the import graph of ``panoptis`` and ``panopti`` (every import, including the
lazy ones inside functions, plus dotted module names written as strings, which is how
plugins and lazy loaders refer to modules) and reports, for every module:

* whether a runtime entry point reaches it (console scripts, the API server, the Celery
  tasks, the MCP server, the package roots and their exports);
* whether only tests reach it;
* whether nothing does (a candidate for review, not automatically dead: a module may be a
  documented library API that nothing in the repository happens to call).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("panoptis", "panopti")
ENTRY_POINTS = (
    "panoptis.__main__",  # python -m panoptis
    "panopti.__main__",  # python -m panopti
    "panoptis",  # package root and its exports
    "panopti",
    "panoptis.cli.main",  # console script `panoptis`
    "panopti.cli",  # console script `panopti`
    "panoptis.api.server",  # FastAPI app (Dockerfile.api, uvicorn)
    "panoptis.tasks",  # Celery worker (Dockerfile.worker)
    "panoptis.protocol.mcp_server",  # MCP server (panoptis mcp)
    "panoptis.protect",  # the enforcement SDK
    "panoptis.protect.mcp_gateway",  # python -m panoptis.protect.mcp_gateway
    "panoptis.protect.claude_hook_entry",  # Claude Code hook command
    "panoptis.evidence.verifier",  # standalone verifier
    "panoptis.instrument",  # instrumentation SDK
)
_DOTTED = re.compile(r"""["']((?:panoptis|panopti)(?:\.[A-Za-z_][A-Za-z0-9_]*)+)["']""")


def modules() -> dict[str, Path]:
    out = {}
    for pkg in PACKAGES:
        for p in (ROOT / pkg).rglob("*.py"):
            rel = p.relative_to(ROOT).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            out[".".join(parts)] = p
    return out


def _resolve(name: str, mods: dict[str, Path]) -> str | None:
    while name:
        if name in mods:
            return name
        name = name.rsplit(".", 1)[0] if "." in name else ""
    return None


def imports_of(mod: str, path: Path, mods: dict[str, Path]) -> set[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = pkg.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        for n in names:
            r = _resolve(n, mods)
            if r and r != mod:
                found.add(r)
    for m in _DOTTED.findall(src):
        r = _resolve(m, mods)
        if r and r != mod:
            found.add(r)
    # a package's submodule implies its parent package is imported
    if "." in mod:
        found.add(mod.rsplit(".", 1)[0])
    return found


def reach(starts: list[str], graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        stack.extend(graph.get(m, ()))
    return seen


def test_imports(mods: dict[str, Path]) -> set[str]:
    found: set[str] = set()
    for p in (ROOT / "tests").rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if r := _resolve(a.name, mods):
                        found.add(r)
            elif isinstance(node, ast.ImportFrom) and node.module:
                for n in [node.module] + [f"{node.module}.{a.name}" for a in node.names]:
                    if r := _resolve(n, mods):
                        found.add(r)
        for m in _DOTTED.findall(src):
            if r := _resolve(m, mods):
                found.add(r)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ns = ap.parse_args()
    mods = modules()
    graph = {m: imports_of(m, p, mods) for m, p in mods.items()}
    runtime = reach([e for e in ENTRY_POINTS if e in mods], graph)
    tested = reach(sorted(test_imports(mods)), graph)
    report = []
    for m, p in sorted(mods.items()):
        lines = sum(1 for _ in p.open(encoding="utf-8"))
        status = "runtime" if m in runtime else ("tests-only" if m in tested else "unreachable")
        report.append({"module": m, "lines": lines, "status": status})
    counts: dict[str, list[int]] = {}
    for r in report:
        c = counts.setdefault(r["status"], [0, 0])
        c[0] += 1
        c[1] += r["lines"]
    for status, (n, lines) in sorted(counts.items()):
        print(f"{status:12} {n:4} modules {lines:7} lines")
    for r in report:
        if r["status"] != "runtime":
            print(f"  {r['status']:12} {r['lines']:5}  {r['module']}")
    if ns.json:
        Path(ns.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
