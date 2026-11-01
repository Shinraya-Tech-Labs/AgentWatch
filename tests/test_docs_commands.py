"""Every `panoptis ...` command shown in the user-facing docs must exist, with its options.

Guards against docs that tell users to run commands that were renamed or moved (for example
`panoptis watch`, which lives at `panoptis session watch`).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import typer.main

from panoptis.cli.main import app

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "README.md",
    "docs/getting-started.md",
    "docs/v3/USER_GUIDE.md",
    "docs/v3/API.md",
    "benchmarks/awbench/REAL_MODELS.md",
    "examples/research_system.py",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs/adapters").glob("*.md")),
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs/cli").glob("*.md")),
]
# prose that names something that is deliberately NOT a command
ALLOWED = {"panoptis cli", "panoptis import"}
COMMAND = re.compile(r"(?:^|`|\s)(panoptis\s+[a-z][^`#|\n]*)")


def _commands(path: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
        for m in COMMAND.finditer(line):
            text = m.group(1).split("  #")[0].strip()
            if not any(text.startswith(a) for a in ALLOWED):
                out.append((n, text))
    return out


def _problems(text: str) -> list[str]:
    try:
        parts = shlex.split(text.replace("<", "X").replace(">", "X"))
    except ValueError:
        return []
    cmd = typer.main.get_command(app)
    args, path = parts[1:], ["panoptis"]
    while args and hasattr(cmd, "commands") and args[0] in cmd.commands:
        cmd = cmd.commands[args[0]]
        path.append(args[0])
        args = args[1:]
    if hasattr(cmd, "commands"):
        return (
            [f"unknown command {' '.join(path)} {args[0]}"]
            if args and not args[0].startswith("-")
            else []
        )
    if path[-1] == "observe":  # everything after the program belongs to the program
        args = [a for a in args if a in ("--store", "--system")]
    opts = {"--help"} | {
        o for p in cmd.params for o in [*p.opts, *getattr(p, "secondary_opts", [])]
    }
    return [
        f"{' '.join(path)} has no option {a}"
        for a in args
        if a.startswith("--") and a.split("=")[0] not in opts
    ]


@pytest.mark.parametrize("doc", [d for d in DOCS if (ROOT / d).exists()])
def test_documented_commands_exist(doc: str) -> None:
    bad = [f"{doc}:{n}: {p}  ({text})" for n, text in _commands(doc) for p in _problems(text)]
    assert not bad, "\n".join(bad)
