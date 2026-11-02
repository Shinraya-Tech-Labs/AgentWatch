"""Check that every third-party import of the shipped packages is a declared dependency.

    python scripts/check_imports.py            # exit 1 on an undeclared import

A module-level import must come from a core dependency. An import that is guarded (inside
a function, or in ``try/except ImportError``) may come from an optional extra instead.
Standard-library modules and the shipped packages themselves are ignored.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("panoptis", "panopti")

# import name -> distribution name, where they differ and the distribution is not installed
KNOWN = {
    "yaml": "pyyaml",
    "jwt": "pyjwt",
    "psycopg": "psycopg",
    "google": "protobuf",
    "opentelemetry": "opentelemetry-sdk",
    "presidio_analyzer": "presidio-analyzer",
    "presidio_anonymizer": "presidio-anonymizer",
    "sentence_transformers": "sentence-transformers",
    "multipart": "python-multipart",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared() -> tuple[set[str], dict[str, set[str]]]:
    doc = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    proj = doc["project"]

    def names(reqs: list[str]) -> set[str]:
        return {_norm(re.split(r"[\s<>=!~\[;]", r, maxsplit=1)[0]) for r in reqs if not r.startswith("panoptis[")}

    extras = {k: names(v) for k, v in proj.get("optional-dependencies", {}).items()}
    return names(proj.get("dependencies", [])), extras


def imports() -> list[tuple[str, str, int, bool]]:
    """(module, file, line, guarded) for every absolute import in the shipped packages."""
    out = []
    for pkg in PACKAGES:
        for path in (ROOT / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            guarded_nodes: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Try, ast.If)):
                    for sub in ast.walk(node):
                        if sub is not node and isinstance(sub, (ast.Import, ast.ImportFrom)):
                            guarded_nodes.add(id(sub))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module]
                else:
                    continue
                for m in mods:
                    out.append((m.split(".")[0], str(path.relative_to(ROOT)), node.lineno, id(node) in guarded_nodes))
    return out


def main() -> int:
    core, extras = declared()
    all_extra = set().union(*extras.values()) if extras else set()
    dist_of = metadata.packages_distributions()
    stdlib = set(sys.stdlib_module_names)
    problems = []
    for mod, file, line, guarded in imports():
        if mod in stdlib or mod in PACKAGES or mod == "__future__":
            continue
        dists = {_norm(d) for d in dist_of.get(mod, [])} or {_norm(KNOWN.get(mod, mod))}
        if dists & core:
            continue
        if guarded and dists & all_extra:
            continue
        where = "an optional extra (guard the import)" if dists & all_extra else "no dependency group"
        problems.append(f"{file}:{line}: '{mod}' ({', '.join(sorted(dists))}) is declared in {where}")
    for p in sorted(set(problems)):
        print(p)
    print(f"{len(set(problems))} undeclared import(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
