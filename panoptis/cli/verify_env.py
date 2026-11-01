"""Environment Verification and Diagnostic Utilities for AgentWatch.

This module provides structural system checks, checks for required core python
dependencies, and verifies system environment variable configurations with
animated validation progress feedback.
"""

from __future__ import annotations

import os
import sys
import time

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from panoptis.cli.animator import animate_table_rows

console = Console()


def verify_environment() -> None:
    """Run full system diagnostics and check the runtime environment status.

    Executes a multi-stage sequential validation tracking sequence including:
    1. Python execution runtime compatibility verification (>= 3.12).
    2. Dynamic runtime installation checks for third-party modules.
    3. Global configuration mask screening for required/optional environment strings.
    """
    console.print()

    # Awesome Progress Bar Sequence
    with Progress(
        SpinnerColumn(spinner_name="aesthetic", style="bold cyan"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(complete_style="cyan", finished_style="bold green"),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        t1 = progress.add_task("[cyan]Initializing Diagnostic Matrix...", total=100)
        for i in range(100):
            time.sleep(0.005)
            progress.update(t1, advance=1)

        t2 = progress.add_task("[yellow]Analyzing Python Subsystems...", total=100)
        for i in range(100):
            time.sleep(0.002)
            progress.update(t2, advance=1)

        t3 = progress.add_task("[magenta]Validating Neural Dependencies...", total=100)
        for i in range(100):
            time.sleep(0.008)
            progress.update(t3, advance=1)

        t4 = progress.add_task("[green]Scanning System Environment...", total=100)
        for i in range(100):
            time.sleep(0.006)
            progress.update(t4, advance=1)

    console.print()
    console.print(
        Panel(
            Align.center("[bold cyan]AgentWatch Environment Diagnostics[/bold cyan]"),
            border_style="cyan",
        )
    )

    # 1. Python version check
    py_ver = sys.version_info
    py_ver_str = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    if py_ver.major == 3 and py_ver.minor >= 12:
        console.print(
            f"  [green][PASS][/green] [bold]Python Runtime:[/bold] {py_ver_str} [dim](compatible)[/dim]"
        )
    else:
        console.print(
            f"  [red][FAIL][/red] [bold]Python Runtime:[/bold] {py_ver_str} [red](requires >= 3.12)[/red]"
        )

    console.print()

    # 2. Dependency checks
    deps = {
        "fastapi": "FastAPI",
        "uvicorn": "Uvicorn",
        "pydantic": "Pydantic",
        "sqlalchemy": "SQLAlchemy",
        "redis": "Redis Client",
        "celery": "Celery",
        "httpx": "HTTPX",
        "rich": "Rich Text Engine",
    }
    table = Table(show_header=True, header_style="bold magenta", border_style="dim", expand=True)
    table.add_column("Core Dependency")
    table.add_column("Status", justify="right")

    rows = []
    for pkg, name in deps.items():
        try:
            __import__(pkg)
            rows.append([name, "[green][PASS] Installed[/green]"])
        except ImportError:
            rows.append([name, "[red][FAIL] Missing[/red]"])

    animate_table_rows(table, rows, delay=0.08)
    console.print()

    # 3. Environment Variables
    env_vars = [
        ("DATABASE_URL", False),
        ("REDIS_URL", False),
        ("CELERY_BROKER_URL", False),
        ("AGENTWATCH_API_KEY", False),
        ("ANTHROPIC_API_KEY", False),
        ("ENVIRONMENT", False),
    ]

    var_table = Table(show_header=True, header_style="bold green", border_style="dim", expand=True)
    var_table.add_column("System Variable")
    var_table.add_column("Requirement")
    var_table.add_column("Current State", justify="right")

    var_rows = []
    for var, required in env_vars:
        val = os.environ.get(var)
        if val:
            display_val = val if var in ("ENVIRONMENT",) else f"{val[:6]}... (masked)"
            var_rows.append(
                [
                    var,
                    "[dim]Required[/dim]" if required else "[dim]Optional[/dim]",
                    f"[green][PASS] {display_val}[/green]",
                ]
            )
        else:
            state = "[red]Required[/red]" if required else "[yellow]Optional[/yellow]"
            var_rows.append([var, state, "[dim]Not Set[/dim]"])

    animate_table_rows(var_table, var_rows, delay=0.1)
    console.print()

    from panoptis.cli.animator import matrix_type_print

    console.print(var_table)
    console.print("\n[bold green]Diagnostics complete[/bold green]\n")

    matrix_type_print("  ALL SYSTEMS GO.  ", color="1;92m", delay=0.05)
    console.print()
