"""CLI internal utilities module."""

from __future__ import annotations

import sys

from panoptis.cli._utils.run_cmd import CommandError, run, run_validated_command

__all__ = ["run", "CommandError", "run_validated_command", "ensure_utf8_stdio"]


def ensure_utf8_stdio() -> None:
    """Switch stdout/stderr to UTF-8 so emoji and box-drawing survive cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() not in (
            "utf-8",
            "utf8",
        ):
            stream.reconfigure(encoding="utf-8", errors="replace")
