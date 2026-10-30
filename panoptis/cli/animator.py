"""Terminal animation helpers for the AgentWatch CLI.

This module is an internal presentation layer: a small collection of Rich/ANSI
animation utilities used by the CLI commands to render cinematic output (the
matrix-style "decryption" text effect, the animated logo reveal, and the
systematic command menu). It exposes **no user-facing command** of its own; the
functions here are called by the commands defined in :mod:`panoptis.cli.main`.

All functions write directly to stdout or the shared Rich ``console`` and are
side-effecting (they sleep and animate in place); none returns a value.
"""

from __future__ import annotations

import random
import sys
import time
from typing import Any

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*!?"


def cinematic_logo_reveal(ascii_art: list[str]) -> None:
    """A highly animated movie-style reveal for the ASCII logo."""

    # Make space for the logo
    sys.stdout.write("\n" * len(ascii_art))

    # 1. Glitch Slide-in effect
    frames = 25
    for frame in range(frames):
        sys.stdout.write(f"\033[{len(ascii_art)}A")  # Move up
        for i, line in enumerate(ascii_art):
            reveal_len = int((frame / frames) * len(line))
            visible = line[:reveal_len]

            # The leading edge has intense matrix characters
            edge = ""
            if reveal_len < len(line):
                edge = f"\033[92m{random.choice(CHARS)}\033[0m"  # nosec # noqa: S311

            sys.stdout.write(f"\r\033[K\033[96m{visible}\033[0m{edge}\n")
        sys.stdout.flush()
        time.sleep(0.03)

    # 2. Cinematic Flash (White -> Cyan)
    colors = ["\033[97m", "\033[1;96m", "\033[96m"]
    for c in colors:
        sys.stdout.write(f"\033[{len(ascii_art)}A")
        for line in ascii_art:
            sys.stdout.write(f"\r\033[K{c}{line}\033[0m\n")
        sys.stdout.flush()
        time.sleep(0.08)


def matrix_type_print(text: str, color: str = "96m", delay: float = 0.01) -> None:
    """Print text with a Matrix-style character decryption effect."""
    sys.stdout.write("\r\033[K")
    current_text = ""
    for char in text:
        if char.strip():
            # Show random character briefly
            sys.stdout.write(
                f"\r\033[{color}{current_text}\033[0m\033[92m{random.choice(CHARS)}\033[0m"  # nosec # noqa: S311
            )
            sys.stdout.flush()
            time.sleep(0.005)
        current_text += char
        sys.stdout.write(f"\r\033[{color}{current_text}\033[0m")
        sys.stdout.flush()
        time.sleep(delay)
    print()


def animate_table_rows(table: Table, rows: list[list[Any]], delay: float = 0.05) -> None:
    """Animate adding rows to a rich Table."""
    with Live(table, console=console, refresh_per_second=20) as live:
        for row in rows:
            time.sleep(delay)
            table.add_row(*row)
            live.update(table)


def glitch_ascii_art(ascii_art: list[str]) -> None:
    """Legacy glitch function, kept for backward compat."""
    cinematic_logo_reveal(ascii_art)


def print_systematic_menu() -> None:
    """Prints a beautiful, animated systematic command menu."""
    from rich import box

    commands = [
        (
            "[bold green][+][/bold green] [bold]check-env[/bold]",
            "Run full system diagnostic & dependency check",
        ),
        (
            "[bold cyan][>][/bold cyan] [bold]server start[/bold]",
            "Boot the local AgentWatch API server",
        ),
        (
            "[bold magenta][~][/bold magenta] [bold]server status[/bold]",
            "Open the real-time live performance dashboard",
        ),
        (
            "[bold yellow][*][/bold yellow] [bold]session watch[/bold]",
            "Watch a Claude Code execution with full safety",
        ),
        (
            "[bold blue][<][/bold blue] [bold]session replay[/bold]",
            "Replay a captured session step-by-step",
        ),
        (
            "[bold white][=][/bold white] [bold]session list[/bold]",
            "List recent agent sessions from the API",
        ),
        (
            "[bold red][!][/bold red] [bold]safety check[/bold]",
            "Score the risk level of a shell command",
        ),
    ]

    console.print()
    time.sleep(0.2)

    # Animate the table appearing row by row inside the panel
    with Live(console=console, refresh_per_second=20) as live:
        for i in range(len(commands) + 1):
            temp_table = Table(show_edge=False, show_header=False, box=None, padding=(1, 4))
            temp_table.add_column("Command", style="bold cyan")
            temp_table.add_column("Description", style="dim white")

            for j in range(i):
                cmd, desc = commands[j]
                temp_table.add_row(cmd, desc)

            panel = Panel(
                temp_table,
                title="[bold green]S Y S T E M   C O M M A N D S[/bold green]",
                subtitle="[dim]Initializing Modules...[/dim]",
                border_style="cyan",
                box=box.DOUBLE,
                expand=False,
            )
            live.update(Align.center(panel))
            time.sleep(0.12)

        # Completion cinematic flash
        time.sleep(0.1)
        flash_panel = Panel(
            temp_table,
            title="[bold white]S Y S T E M   C O M M A N D S[/bold white]",
            subtitle="[bold green]AWAITING INPUT[/bold green]",
            border_style="bold green",
            box=box.DOUBLE,
            expand=False,
        )
        live.update(Align.center(flash_panel))
        time.sleep(0.15)

        # Settle back to stable state
        final_panel = Panel(
            temp_table,
            title="[bold green]S Y S T E M   C O M M A N D S[/bold green]",
            subtitle="[bold green]AWAITING INPUT[/bold green]",
            border_style="bold cyan",
            box=box.DOUBLE,
            expand=False,
        )
        live.update(Align.center(final_panel))

    console.print()
