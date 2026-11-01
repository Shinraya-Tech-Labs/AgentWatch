"""Terminal animation and presentation helpers for the AgentWatch CLI.

This module provides clean, cross-platform Rich terminal presentations
and menus for the interactive command center.
"""

from __future__ import annotations

import random
import sys
import time
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*!?"


def cinematic_logo_reveal(ascii_art: list[str]) -> None:
    """A clean, fast animated reveal for the ASCII logo."""
    if not sys.stdout.isatty():
        for line in ascii_art:
            console.print(f"[bold cyan]{line}[/bold cyan]")
        return

    try:
        frames = 3
        for frame in range(1, frames + 1):
            for line in ascii_art:
                visible = line[: int((frame / frames) * len(line))]
                edge = (
                    f"[bold green]{random.choice(CHARS)}[/bold green]"  # noqa: S311
                    if frame < frames
                    else ""
                )
                console.print(f"[bold cyan]{visible}[/bold cyan]{edge}")
            if frame < frames:
                time.sleep(0.02)
                sys.stdout.write(f"\033[{len(ascii_art)}A")
                sys.stdout.flush()
    except Exception:
        for line in ascii_art:
            console.print(f"[bold cyan]{line}[/bold cyan]")


def matrix_type_print(text: str, color: str = "96m", delay: float = 0.005) -> None:
    """Print text with a Matrix-style character decryption effect."""
    if not sys.stdout.isatty():
        console.print(text)
        return

    current_text = ""
    for char in text:
        if char.strip():
            sys.stdout.write(
                f"\r\033[{color}{current_text}\033[0m\033[92m{random.choice(CHARS)}\033[0m"  # nosec # noqa: S311
            )
            sys.stdout.flush()
            time.sleep(delay)
        current_text += char
    sys.stdout.write(f"\r\033[{color}{text}\033[0m\n")
    sys.stdout.flush()


def animate_table_rows(table: Table, rows: list[list[Any]], delay: float = 0.02) -> None:
    """Animate adding rows to a rich Table."""
    for row in rows:
        table.add_row(*row)
    console.print(table)


def glitch_ascii_art(ascii_art: list[str]) -> None:
    """Legacy glitch function, kept for backward compat."""
    cinematic_logo_reveal(ascii_art)


def print_systematic_menu() -> None:
    """Prints a beautiful, clean, responsive systematic command menu with numbered shortcuts."""
    table = Table(show_edge=False, show_header=True, box=box.ROUNDED, padding=(0, 2))
    table.add_column("#", style="bold yellow", justify="center", width=5)
    table.add_column("Command / Action", style="bold cyan", width=22)
    table.add_column("Description", style="dim white")

    commands = [
        ("1", "watch", "Watch agent execution (Claude Code / OpenCode / AutoGPT)"),
        ("2", "runs", "List reconstructed agent execution runs & trees"),
        ("3", "events", "Inspect normalized event stream for a run"),
        ("4", "doctor", "System health check & installation diagnostics"),
        ("5", "check-env", "Verify environment variables & service dependencies"),
        ("6", "mcp", "Start AgentWatch MCP server (Claude Desktop / Cursor)"),
        ("7", "status", "Check v3 store & model routing status"),
        ("8", "safety check", "Pre-execution threat check & command risk score"),
        ("0", "exit", "Exit interactive shell"),
    ]

    for num, cmd, desc in commands:
        table.add_row(f"[{num}]", cmd, desc)

    panel = Panel(
        table,
        title="[bold green]⚡ PANOPTIS INTERACTIVE COMMAND CENTER ⚡[/bold green]",
        subtitle="[bold dim]Type a number [yellow][1-8][/yellow] or enter any command (e.g. 'watch', 'runs', 'help')[/bold dim]",
        border_style="cyan",
        box=box.ROUNDED,
    )
    console.print(Align.center(panel))
