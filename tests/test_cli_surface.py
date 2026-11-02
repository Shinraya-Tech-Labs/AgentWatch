"""Characterisation test for the CLI surface.

This exists to make the CLI refactor in #600 reviewable.

`panoptis/cli/main.py` is being split into per-family modules. The point of that change is that it
is *behaviour-preserving*: same commands, same flags, same defaults, same help. But "I didn't change
anything" is not a claim a reviewer can check by reading a 2,500-line diff that moves code between
files.

So this records the whole CLI surface into a golden file. Rename a command, drop one, change an
option's default, make an argument required, alter a help string — any of it turns this red with a
diff. Move code between modules all you like; the moment the interface shifts, you know.

**On why it inspects the Click objects rather than snapshotting `--help`.** The `--help` route is the
obvious one and it does not work. That output goes through Rich, which wraps text at points that
differ between Windows and Linux even with `COLUMNS` pinned, draws panels with box characters the
Windows console encoding cannot represent, and prints the program name as `panoptis.EXE` on Windows.
None of that is the CLI's contract, and a golden file full of it fails on whichever platform did not
generate it. A test that goes red for cosmetic reasons is one people learn to ignore.

Reading the command objects sidesteps the rendering entirely, and records *more* than `--help` shows:
parameter types, defaults, and whether each is required.

To update the golden file after a *deliberate* CLI change:

    UPDATE_CLI_SURFACE=1 pytest tests/test_cli_surface.py

and commit the diff, so the change to the public interface is explicit in review.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path, PurePath

import pytest
import typer.main

from panoptis.cli.main import app

GOLDEN = Path(__file__).parent / "cli_surface.txt"


def _params(cmd: object) -> list[str]:
    """One stable line per parameter: name, kind, type, required, default, flags."""
    lines: list[str] = []
    for param in getattr(cmd, "params", []):
        kind = type(param).__name__
        type_name = getattr(getattr(param, "type", None), "name", "?")
        # click renamed param type names ("str" → "text", "int" → "integer"); normalize so the golden
        # file records the CLI surface rather than the installed click version.
        type_name = {"text": "str", "integer": "int", "integer range": "int range"}.get(
            type_name, type_name
        )
        default = param.default() if callable(param.default) else param.default
        # repr() of a pathlib.Path embeds the concrete class — PosixPath on Linux, WindowsPath on
        # Windows — so a Path default would make the golden file platform-specific even though the
        # value is identical. Record it as a plain forward-slash string instead.
        if isinstance(default, PurePath):
            default = default.as_posix()
        flags = ",".join(sorted(param.opts)) if param.opts else "-"
        help_text = " ".join((getattr(param, "help", "") or "").split())
        lines.append(
            f"    param {param.name} kind={kind} type={type_name} "
            f"required={bool(param.required)} default={default!r} flags={flags} "
            f"help={help_text!r}"
        )
    return sorted(lines)


def _capture() -> str:
    """Walk the command tree and record every command's public surface."""
    root = typer.main.get_command(app)
    out: list[str] = []

    def walk(cmd: object, path: list[str]) -> None:
        out.append(f"command: panoptis {' '.join(path)}".rstrip())
        out.append(f"    help: {(getattr(cmd, 'help', '') or '').strip()}")
        out.append(f"    short_help: {(getattr(cmd, 'short_help', '') or '').strip()}")
        out.extend(_params(cmd))
        out.append("")

        # Read `.commands` rather than testing `isinstance(cmd, click.Group)`. Typer vendors its own
        # Click, so a TyperGroup descends from `typer._click.core.Command` and is *not* an instance
        # of the installed `click.Group` — that isinstance check silently returns False, the walk
        # never descends, and the test passes while protecting exactly one command. A green test
        # that locks nothing is the worst outcome available here.
        for name in sorted(getattr(cmd, "commands", {})):
            walk(cmd.commands[name], [*path, name])

    walk(root, [])
    return "\n".join(out) + "\n"


def test_cli_surface_is_unchanged() -> None:
    """The CLI surface must match the golden file exactly."""
    actual = _capture()

    if os.environ.get("UPDATE_CLI_SURFACE"):
        GOLDEN.write_text(actual, encoding="utf-8", newline="\n")
        pytest.skip(f"golden file rewritten: {GOLDEN}")

    if not GOLDEN.exists():  # pragma: no cover
        GOLDEN.write_text(actual, encoding="utf-8", newline="\n")
        pytest.fail(f"golden file did not exist; wrote {GOLDEN}. Review and commit it.")

    expected = GOLDEN.read_text(encoding="utf-8")

    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile="cli_surface.txt (committed)",
                tofile="actual CLI",
                lineterm="",
            )
        )
        pytest.fail(
            "The CLI surface changed.\n\n"
            "If that was deliberate, re-record it with:\n"
            "    UPDATE_CLI_SURFACE=1 pytest tests/test_cli_surface.py\n"
            "and commit the diff, so the change to the public interface is explicit in review.\n\n"
            "If it was not deliberate — which is the case during a refactor meant to preserve "
            "behaviour — this is the bug.\n\n"
            f"{diff}"
        )


def test_every_command_has_a_callback() -> None:
    """Every leaf command resolves to something callable.

    Distinct from the snapshot. During a refactor a command can end up registered but pointing at a
    callback that never imported — the surface would still look correct while the command was dead.
    This asserts there is something behind each one.
    """
    root = typer.main.get_command(app)
    broken: list[str] = []

    def walk(cmd: object, path: list[str]) -> None:
        subcommands = getattr(cmd, "commands", {})
        if not subcommands and not callable(getattr(cmd, "callback", None)):
            broken.append("panoptis " + " ".join(path))
        for name in sorted(subcommands):
            walk(subcommands[name], [*path, name])

    walk(root, [])
    assert not broken, "commands with no callable behind them:\n" + "\n".join(broken)
