"""Evidence for classifying the modules no runtime entry point reaches (TO-034).

    python scripts/classify_modules.py > /tmp/rows.md

For each tests-only or unreachable module: how many test files import it, whether its
package exports it, and which maintained docs describe it. The human classification in
docs/ARCHITECTURE_MAP.md is made from this evidence.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("module_map", ROOT / "scripts" / "module_map.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)

GENERATED_DOCS = {"COMPLETE_PROJECT_DOCUMENTATION.md", "REPO_AUDIT.md"}


def main() -> int:
    mods = mm.modules()
    graph = {m: mm.imports_of(m, p, mods) for m, p in mods.items()}
    runtime = mm.reach([e for e in mm.ENTRY_POINTS if e in mods], graph)
    docs = [
        p
        for p in [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
        if p.name not in GENERATED_DOCS
    ]
    doc_text = {p: p.read_text(encoding="utf-8", errors="replace") for p in docs}
    tests = {
        p: p.read_text(encoding="utf-8", errors="replace") for p in (ROOT / "tests").rglob("*.py")
    }
    for m in sorted(mods):
        if m in runtime:
            continue
        rel = mods[m].relative_to(ROOT).as_posix()
        dotted = re.escape(m)
        pat = re.compile(rf"{dotted}\b|{re.escape(rel)}|{re.escape(rel.split('/', 1)[1])}")
        n_tests = sum(1 for t in tests.values() if re.search(rf"\b{dotted}\b", t))
        in_docs = sorted(
            {p.relative_to(ROOT).as_posix() for p, t in doc_text.items() if pat.search(t)}
        )
        parent = m.rsplit(".", 1)[0]
        exported = False
        if parent in mods and mods[parent].name == "__init__.py":
            leaf = m.rsplit(".", 1)[1]
            exported = bool(
                re.search(
                    rf"from\s+(\.|{re.escape(parent)}\.){leaf}\s+import|import\s+{re.escape(m)}\b",
                    mods[parent].read_text(encoding="utf-8"),
                )
            )
        lines = sum(1 for _ in mods[m].open(encoding="utf-8"))
        print(
            f"| `{m}` | {lines} | {n_tests} | {'yes' if exported else 'no'} | {', '.join(in_docs[:3]) or '—'} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
