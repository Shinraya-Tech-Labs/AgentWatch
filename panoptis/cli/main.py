"""
AgentWatch CLI
Rich terminal interface for session inspection, replay, safety review, and management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import sys
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from panoptis.cli.mcp import app as mcp_app
from panoptis.cli.v3 import evidence_app
from panoptis.cli.v3 import register as _register_v3

if TYPE_CHECKING:
    # httpx is imported lazily inside commands (optional dependency); this
    # type-only import keeps the annotation without a hard runtime import.
    import httpx

    from panoptis.core.policy_loader import CombinedVerdict
    from panoptis.core.schema import RiskLevel, ToolCallData
    from panoptis.cost.reporting import CostReport
    from panoptis.eval.runner import EvalReport

app = typer.Typer(
    name="panoptis",
    help="AgentWatch — Reliability, Safety, and Observability Layer for AI Agents",
    add_completion=True,
    rich_markup_mode="rich",
)
session_app = typer.Typer(
    name="session", help="Manage and inspect agent sessions", no_args_is_help=True
)
app.add_typer(session_app, hidden=True)
app.add_typer(mcp_app, name="mcp")

console = Console()

server_app = typer.Typer(
    name="server", help="Manage the AgentWatch API server", no_args_is_help=True
)
safety_app = typer.Typer(
    name="safety",
    help="AgentWatch Safety & Risk Engine. Analyze shell commands against security policies.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

policies_app = typer.Typer(
    name="policies", help="Inspect the effective safety policy set.", no_args_is_help=True
)
config_app = typer.Typer(
    name="config", help="Manage the local safety policy file.", no_args_is_help=True
)
safety_app.add_typer(policies_app, name="policies")
safety_app.add_typer(config_app, name="config")

cost_app = typer.Typer(
    name="cost",
    help="AgentWatch FinOps. Report token usage and API spend across agents and frameworks.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

eval_app = typer.Typer(
    name="eval",
    help="AgentWatch Evaluation. Run an agent against a dataset and score the batch.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

protect_app = typer.Typer(
    name="protect",
    help="AgentWatch security engine, guardrails, compliance, and evidence.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
protect_app.add_typer(policies_app, name="policies")
protect_app.add_typer(config_app, name="config")

app.add_typer(server_app)
app.add_typer(protect_app, name="protect")
app.add_typer(safety_app, hidden=True)
app.add_typer(cost_app, hidden=True)
app.add_typer(eval_app, hidden=True)

legacy_app = typer.Typer(
    name="legacy",
    help="v0.2 commands kept for compatibility (superseded by v3 commands).",
    no_args_is_help=True,
)
app.add_typer(legacy_app, hidden=True)


_IN_REPL = False


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Show version and exit."),
):
    """AgentWatch CLI with ASCII Animation"""
    global _IN_REPL

    if version:
        import platform
        import sys

        from panoptis import __version__

        console.print(f"AgentWatch CLI version: {__version__}")
        console.print(f"Python: {sys.version.split()[0]}")
        console.print(f"Platform: {platform.platform()}")
        raise typer.Exit()

    if _IN_REPL:
        return

    ascii_art = [
        r"    ___                    __ _       __      __       __  ",
        r"   /   |  ____  ___  ____ / /| |     / /___ _/ /______/ /_ ",
        r"  / /| | / __ `/ _ \/ __ \ __/ | /| / / __ `/ __/ ___/ __ \\",
        r" / ___ |/ /_/ /  __/ / / / /_  |/ |/ / /_/ / /_/ /__/ / / /",
        r"/_/  |_|\__, /\___/_/ /_/\__/  |__/|__/\__,_/\__/\___/_/ /_/",
        r"       /____/                                              ",
    ]

    from panoptis.cli.animator import (
        cinematic_logo_reveal,
        print_systematic_menu,
    )

    if ctx.invoked_subcommand is None:
        import sys

        if not sys.stdin.isatty():
            try:
                app(["--help"])
            except SystemExit:
                pass
            return

        cinematic_logo_reveal(ascii_art)
        print_systematic_menu()
        _IN_REPL = True
        try:
            _start_repl_session()
        finally:
            _IN_REPL = False


def _start_repl_session():
    """Run an interactive REPL shell for the CLI."""
    import shlex

    from rich.panel import Panel
    from rich.prompt import Prompt

    from panoptis.cli.animator import print_systematic_menu

    console.print()
    console.print(
        Panel(
            "[dim]Enter commands directly (e.g. 'safety check ...', 'session list').\n"
            "Type [bold cyan]clear[/bold cyan] to wipe screen, or [bold red]exit[/bold red] to terminate.[/dim]",
            title="[bold cyan]⚡ AGENTWATCH INTERACTIVE TERMINAL ⚡[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )

    while True:
        try:
            cmd_line = Prompt.ask(
                "\n[bold cyan]PANOPTIS[/bold cyan][dim]:[/dim][bold green]CORE[/bold green] [bold white]>[/bold white]"
            )
            cmd_line = cmd_line.strip()
            if not cmd_line:
                continue

            cmd_lower = cmd_line.lower()
            if cmd_lower in ("exit", "quit", "0", "q"):
                console.print("[dim]Terminating Panoptis session. Goodbye![/dim]")
                break

            if cmd_lower in ("clear", "cls"):
                console.clear()
                print_systematic_menu()
                continue

            if cmd_lower in ("help", "menu", "?"):
                print_systematic_menu()
                continue

            # Numbered shortcuts for fast navigation
            shortcuts = {
                "1": ["watch"],
                "2": ["runs"],
                "3": ["events"],
                "4": ["doctor"],
                "5": ["check-env"],
                "6": ["mcp"],
                "7": ["status"],
                "8": ["safety", "check"],
            }
            if cmd_lower in shortcuts:
                args = shortcuts[cmd_lower]
                if cmd_lower == "8":
                    cmd_to_test = Prompt.ask("[bold cyan]Enter shell command to check[/bold cyan]")
                    if cmd_to_test.strip():
                        args.append(cmd_to_test)
                    else:
                        continue
            else:
                args = shlex.split(cmd_line)

            try:
                app(args, standalone_mode=False)
            except SystemExit:
                pass
            except Exception as e:
                console.print(f"[red]Error executing command:[/red] {e}")
        except (KeyboardInterrupt, EOFError):
            console.print("[dim]\nTerminating Panoptis session. Goodbye![/dim]")
            break


# ---------------------------------------------
# Helpers
# ---------------------------------------------


def _status_color(status: str) -> str:
    return {
        "success": "green",
        "running": "blue",
        "failure": "red",
        "blocked": "yellow",
        "rolled_back": "magenta",
        "timeout": "orange1",
        "pending": "dim",
    }.get(status.lower(), "white")


def _risk_color(level: str) -> str:
    return {
        "safe": "green",
        "low": "cyan",
        "medium": "yellow",
        "high": "orange1",
        "critical": "red bold",
    }.get(level.lower(), "white")


def _load_session_file(path: Path):
    """Load a session JSON file from disk."""
    if not path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(1)
    with open(path) as f:
        return json.load(f)


# ─────────────────────────────────────────────
# API auth helpers
# ─────────────────────────────────────────────

# Reusable --api-key option for commands that call protected API endpoints.
# Falls back to the AGENTWATCH_API_KEY environment variable when the flag is
# omitted; an explicit flag takes precedence.
API_KEY_OPTION = typer.Option(
    None,
    "--api-key",
    envvar="AGENTWATCH_API_KEY",
    help="API key for protected endpoints (or set AGENTWATCH_API_KEY).",
)


def _api_headers(api_key: str | None) -> dict[str, str]:
    """Build request headers, sending X-Api-Key only when a key is provided."""
    return {"X-Api-Key": api_key} if api_key else {}


def _handle_http_status_error(exc: httpx.HTTPStatusError, api_url: str) -> NoReturn:
    """Print a consistent message for an HTTP error response, then exit."""
    if exc.response.status_code == 401:
        console.print(
            "[red]Authentication failed (401). Supply a valid key via --api-key "
            f"or the AGENTWATCH_API_KEY environment variable for {api_url}.[/red]"
        )
    else:
        console.print(
            f"[red]API request failed with status {exc.response.status_code}: "
            f"{exc.response.text}[/red]"
        )
    raise typer.Exit(1)


# ─────────────────────────────────────────────
# NEW HELPER: Dry-run printer
# ---------------------------------------------


def _dry_run_print(action: str, detail: str = "") -> None:
    """Print a consistent dry-run preview line to the terminal."""
    detail_str = f"\n  [dim]{detail}[/dim]" if detail else ""
    console.print(f"[bold yellow][DRY-RUN][/bold yellow] Would {action}{detail_str}")


# ---------------------------------------------
# watch command — wrap an agent run
# ---------------------------------------------


@session_app.command(name="watch")
def watch(
    prompt: str | None = typer.Argument(None, help="Prompt to run with the agent"),
    model: str = typer.Option("claude-opus-4-5", "--model", "-m"),
    agent: str = typer.Option(
        "claude", "--agent", "-a", help="Agent to use: claude|ollama|opencode|antigravity"
    ),
    max_turns: int = typer.Option(50, "--max-turns"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save session to file"),
    no_safety: bool = typer.Option(False, "--no-safety", help="Disable safety checks (dangerous)"),
    policy: str = typer.Option(
        "default", "--policy", help="Safety policy: default|strict|permissive"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview what would happen without executing or writing to disk",
    ),
) -> None:
    """[bold]Watch[/bold] an agent execution (Claude Code, ollama, antigravity) with full observability and safety."""
    if not prompt:
        import sys

        if sys.stdin.isatty():
            from rich.prompt import Prompt

            prompt = Prompt.ask("[bold cyan]Enter prompt for the agent[/bold cyan]")
        if not prompt or not prompt.strip():
            console.print("[yellow]No prompt provided. Usage: panoptis watch '<prompt>'[/yellow]")
            return

    if dry_run:
        console.print(
            Panel(
                "[bold yellow]DRY-RUN MODE[/bold yellow] — No agent will be run. "
                "No files will be written.\n"
                f"[dim]Prompt:[/dim]  "
                f"{prompt[:80]}{'...' if len(prompt) > 80 else ''}\n"
                f"[dim]Model:[/dim]   {model}\n"
                f"[dim]Turns:[/dim]   {max_turns}\n"
                f"[dim]Policy:[/dim]  {'DISABLED ⚠️' if no_safety else policy}\n"
                f"[dim]Safety:[/dim]  {'off' if no_safety else 'on'}",
                border_style="yellow",
                title="AgentWatch watch --dry-run",
            )
        )
        if output:
            _dry_run_print(
                "save session to file",
                f"Path: {output.resolve()}",
            )
        else:
            _dry_run_print("run agent (no --output specified, session would not be saved)")
        console.print("\n[yellow]Dry-run complete. Nothing was executed or written.[/yellow]")
        raise typer.Exit(0)

    async def _run() -> None:
        from panoptis.adapters.claude_code import ClaudeCodeAdapter
        from panoptis.core.safety import (
            DEFAULT_POLICY,
            SafetyEngine,
            SafetyPolicy,
            cli_approval_handler,
        )
        from panoptis.replay.engine import ReplayEngine

        console.print(
            Panel(
                f"[bold cyan]AgentWatch[/bold cyan] — watching {agent}\n"
                f"[dim]Prompt:[/dim] {prompt[:80]}{'...' if len(prompt) > 80 else ''}",
                border_style="cyan",
            )
        )

        if no_safety:
            console.print("[yellow]⚠️  Safety checks disabled![/yellow]")
            safety = SafetyEngine(
                policy=SafetyPolicy(
                    policy_id="disabled",
                    name="Disabled",
                    block_on_critical=False,
                    block_on_high=False,
                )
            )
        else:
            p = DEFAULT_POLICY
            if policy == "strict":
                p = SafetyPolicy(
                    policy_id="strict",
                    name="Strict",
                    block_on_high=True,
                    block_on_critical=True,
                    require_approval_on_medium=True,
                )
            safety = SafetyEngine(policy=p)

            def cli_renderer(event, safety):
                tool = event.tool_call
                print("\n" + "=" * 60)
                print("⚠️  AGENTWATCH SAFETY CHECK")
                print("=" * 60)
                print(f"Agent:      {event.agent_name or event.agent_id}")
                print(f"Action:     {tool.tool_name if tool else 'unknown'}")
                if tool and tool.raw_command:
                    print(f"Command:    {tool.raw_command}")
                if tool and tool.affected_resources:
                    print(f"Resources:  {', '.join(tool.affected_resources)}")
                print(f"Risk Level: {safety.risk_level.value.upper()}")
                print(f"Risk Score: {safety.risk_score:.2f}")
                print("Reasons:")
                for r in safety.reasons:
                    print(f"  • {r}")
                print("=" * 60)

            async def wrapped_handler(event, check_safety):
                return await cli_approval_handler(event, check_safety, renderer=cli_renderer)

            safety.set_approval_callback(wrapped_handler)

        # Resolve which adapter to use
        from panoptis.adapters.generic import GenericCLIAdapter

        agent_map = {
            "claude": (ClaudeCodeAdapter, None),
            "ollama": (GenericCLIAdapter, "ollama run llama3"),
            "opencode": (GenericCLIAdapter, "opencode run"),
            "antigravity": (GenericCLIAdapter, "antigravity"),
        }

        if agent not in agent_map:
            print(f"Unsupported agent: {agent}. Choose from {list(agent_map.keys())}")
            return

        adapter_cls, cmd_override = agent_map[agent]

        if adapter_cls == ClaudeCodeAdapter:
            adapter = ClaudeCodeAdapter(safety_engine=safety)
        else:
            adapter = adapter_cls(agent_cmd=cmd_override, safety_engine=safety)

        from panoptis.core.event_bus import get_event_bus

        bus = get_event_bus()

        async def on_event(event) -> None:
            _print_live_event(event)

        bus.subscribe_fn(on_event, handler_id="cli.watch.live")

        try:
            if isinstance(adapter, ClaudeCodeAdapter):
                session = await adapter.run(prompt, model=model, max_turns=max_turns)
            else:
                session = await adapter.run(prompt)
        finally:
            bus.unsubscribe("cli.watch.live")

        _print_session_summary(session, adapter.events)

        if output:
            from panoptis.replay.engine import ReplayEngine

            re = ReplayEngine()
            rs = re.load_from_events(session, adapter.events)
            saved_path = re.save_to_file(rs, output)
            console.print(f"\n[green]Session saved to {saved_path}[/green]")

    asyncio.run(_run())


# Register watch directly on the root app
app.command(name="watch")(watch)


@app.command(name="help", hidden=True)
def help_command(
    command: str | None = typer.Argument(None, help="The command to show help for."),
) -> None:
    """Show help for AgentWatch / Panoptis or a specific command."""
    if command:
        try:
            app([command, "--help"])
        except SystemExit:
            pass
    else:
        try:
            app(["--help"])
        except SystemExit:
            pass


@app.command(name="tui")
def tui_command() -> None:
    """Launch the interactive terminal command center."""
    from panoptis.cli.animator import print_systematic_menu

    print_systematic_menu()
    _start_repl_session()


# ---------------------------------------------
# replay command
# ---------------------------------------------


@session_app.command(name="replay")
def replay(
    session_file: Path = typer.Argument(..., help="Path to session JSON file"),
    speed: str = typer.Option("instant", "--speed", "-s", help="instant|fast|normal|slow"),
    from_step: int = typer.Option(0, "--from", help="Start from step N"),
    to_step: int | None = typer.Option(None, "--to", help="End at step N"),
    show_all: bool = typer.Option(False, "--all", help="Show all events including metadata"),
    failure_only: bool = typer.Option(False, "--failures", "-f", help="Show only failure points"),
) -> None:
    """[bold]Replay[/bold] a captured session step-by-step."""

    async def _run() -> None:
        from panoptis.core.schema import AgentEvent, AgentSession
        from panoptis.replay.engine import ReplayEngine, ReplaySpeed

        data = _load_session_file(session_file)
        session = AgentSession(**data["session"])
        events = [AgentEvent(**e) for e in data["events"]]

        engine = ReplayEngine()
        rs = engine.load_from_events(session, events)

        console.print(
            Panel(
                f"[bold]Replaying Session[/bold]\n"
                f"[dim]ID:[/dim]     {session.session_id}\n"
                f"[dim]Agent:[/dim]  {session.agent_name or session.agent_id}\n"
                f"[dim]Steps:[/dim]  {rs.total_steps}\n"
                f"[dim]Status:[/dim] "
                f"[{_status_color(session.status.value)}]{session.status.value}"
                f"[/{_status_color(session.status.value)}]",
                border_style="blue",
            )
        )

        if rs.failure_analysis:
            fa = rs.failure_analysis
            if (
                fa.primary_cause.value != "unknown" or fa.anomaly_flags
                if hasattr(fa, "anomaly_flags")
                else False
            ):
                console.print("\n[bold red]Failure Analysis:[/bold red]")
                console.print(f"  Cause: [yellow]{fa.primary_cause.value}[/yellow]")
                console.print(f"  {fa.summary}")
                if fa.recommendations:
                    console.print("\n[bold]Recommendations:[/bold]")
                    for rec in fa.recommendations:
                        console.print(f"  → {rec}")

        console.print()

        speed_map = {
            "instant": ReplaySpeed.INSTANT,
            "fast": ReplaySpeed.FAST,
            "normal": ReplaySpeed.NORMAL,
            "slow": ReplaySpeed.SLOW,
        }
        replay_speed = speed_map.get(speed, ReplaySpeed.INSTANT)

        async for step in engine.replay_async(
            rs, speed=replay_speed, start_step=from_step, end_step=to_step
        ):
            if failure_only and not step.is_failure_point:
                continue
            _print_replay_step(step, show_all=show_all)

        console.print("\n[green]✓ Replay complete[/green]")

    asyncio.run(_run())


# ---------------------------------------------
# sessions command
# ---------------------------------------------


@session_app.command(name="list")
def sessions(
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    limit: int = typer.Option(20, "--limit", "-n"),
    framework: str | None = typer.Option(None, "--framework"),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """[bold]List[/bold] recent agent sessions from the AgentWatch API."""

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/sessions",
                    params={"limit": limit, "framework": framework},
                    headers=_api_headers(api_key),
                    timeout=10.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        data = resp.json()
        _print_sessions_table(data["sessions"])

    asyncio.run(_run())


# ─────────────────────────────────────────────
# export command
# ─────────────────────────────────────────────


class ExportFormat(str, Enum):
    json = "json"
    md = "md"
    jsonl = "jsonl"
    csv = "csv"


@session_app.command(name="export")
def export(
    session_id: str = typer.Argument(..., help="ID of the session to export"),
    format: ExportFormat = typer.Option(
        ExportFormat.json, "--format", help="Export format: json, md, jsonl or csv"
    ),
    output: Path | None = typer.Option(None, "--output", "-o", help="Custom output file path"),
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """[bold]Export[/bold] a session replay to a portable JSON, Markdown, JSONL or CSV file."""

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/sessions/{session_id}/replay",
                    headers=_api_headers(api_key),
                    timeout=10.0,
                )
                if resp.status_code == 404:
                    console.print(f"[red]Session {session_id} not found.[/red]")
                    raise typer.Exit(1)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        data = resp.json()

        if format == ExportFormat.json:
            out_path = output or Path(f"panoptis-session-{session_id}.json")
            with open(out_path, "w") as f:
                json.dump(data, f, indent=2)
            console.print(f"[green]{out_path.name} created successfully[/green]")

        elif format in (ExportFormat.jsonl, ExportFormat.csv):
            from panoptis.telemetry.exporter import export_to_csv, export_to_jsonl

            out_path = output or Path(f"panoptis-session-{session_id}.{format.value}")
            session_info = data.get("session") or {}
            events = [
                step["event"]
                for step in data.get("steps") or []
                if isinstance(step, dict) and isinstance(step.get("event"), dict)
            ]
            try:
                if format == ExportFormat.jsonl:
                    count = export_to_jsonl(events, out_path)
                else:
                    count = export_to_csv(
                        events, out_path, session_id=session_info.get("session_id") or session_id
                    )
            except OSError as exc:
                console.print(f"[red]Cannot write to {out_path}: {exc}[/red]")
                raise typer.Exit(1)
            console.print(f"[green]{out_path.name} created successfully ({count} events)[/green]")

        elif format == ExportFormat.md:
            out_path = output or Path(f"panoptis-session-{session_id}.md")

            session = data.get("session", {})
            steps = data.get("steps", [])

            lines = [
                f"# AgentWatch Session: {session.get('session_id', session_id)}",
                "",
                "## Session Overview",
                f"- **Status**: {session.get('status', 'unknown')}",
                f"- **Framework**: {session.get('framework', 'unknown')}",
                f"- **Started at**: {session.get('started_at', 'unknown')}",
                f"- **Ended at**: {session.get('ended_at', 'unknown')}",
                f"- **Total Steps**: {len(steps)}",
            ]

            if session.get("goal"):
                lines.extend(["", "### Goal", session.get("goal")])

            fa = data.get("failure_analysis")
            if fa:
                lines.extend(
                    [
                        "",
                        "## Failure Analysis",
                        f"- **Primary Cause**: {fa.get('primary_cause', 'unknown')}",
                    ]
                )
                if fa.get("recommendations"):
                    lines.append("- **Recommendations**:")
                    for rec in fa.get("recommendations", []):
                        lines.append(f"  - {rec}")

            lines.extend(["", "## Execution Timeline"])
            for step in steps:
                event = step.get("event", {})
                idx = step.get("index", 0)
                etype = event.get("event_type", "unknown")
                status = event.get("status", "")

                status_str = f" ({status})" if status else ""
                lines.extend(
                    [
                        "",
                        f"### Step {idx}",
                        f"- **Type**: {etype}{status_str}",
                    ]
                )

                tool_call = event.get("tool_call")
                if tool_call:
                    lines.extend(
                        [
                            f"- **Tool**: {tool_call.get('tool_name', 'unknown')}",
                            "",
                            "**Command**:",
                            "```",
                            tool_call.get("raw_command", ""),
                            "```",
                        ]
                    )

                tool_result = event.get("tool_result")
                if tool_result:
                    if tool_result.get("output"):
                        lines.extend(
                            [
                                "",
                                "**Output**:",
                                "```",
                                tool_result.get("output", ""),
                                "```",
                            ]
                        )
                    if tool_result.get("error"):
                        lines.extend(
                            [
                                "",
                                "**Error**:",
                                "```",
                                tool_result.get("error", ""),
                                "```",
                            ]
                        )

                safety = event.get("safety")
                if safety and safety.get("blocked"):
                    lines.extend(
                        [
                            "",
                            "**Safety Block**:",
                            f"- **Risk Level**: {safety.get('risk_level', 'unknown').upper()}",
                            "- **Reasons**:",
                        ]
                    )
                    for r in safety.get("reasons", []):
                        lines.append(f"  - {r}")

            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            console.print(f"[green]{out_path.name} created successfully[/green]")

    asyncio.run(_run())


# ─────────────────────────────────────────────
# share command — upload a sanitized trace, get a public link
# ─────────────────────────────────────────────


@app.command(hidden=True)
def share(
    session_file: Path = typer.Argument(..., help="Path to the trace/session JSON file to share"),
    share_url: str | None = typer.Option(
        None,
        "--share-url",
        envvar="AGENTWATCH_SHARE_URL",
        help="Base URL of the share service (or set AGENTWATCH_SHARE_URL).",
    ),
    expires_days: int = typer.Option(
        7, "--expires-days", min=1, help="Days before the shared link expires."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Sanitize and preview the payload locally without uploading.",
    ),
) -> None:
    """[bold]Share[/bold] a trace via a temporary public link (PII/secrets redacted)."""
    from panoptis.platform.sharing import DEFAULT_SHARE_URL, sanitize_trace

    trace = _load_session_file(session_file)
    sanitized = sanitize_trace(trace)

    if dry_run:
        console.print(
            Panel(
                "[bold yellow]DRY-RUN MODE[/bold yellow] — Nothing will be uploaded.\n"
                "[dim]Below is the exact sanitized payload that would be shared.[/dim]",
                border_style="yellow",
                title="AgentWatch share --dry-run",
            )
        )
        console.print_json(data=sanitized)
        console.print("\n[yellow]Dry-run complete. No data left this machine.[/yellow]")
        raise typer.Exit(0)

    base = (share_url or DEFAULT_SHARE_URL).rstrip("/")

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{base}/api/v1/share",
                    json={"trace": sanitized, "expires_days": expires_days},
                    timeout=30.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                console.print(
                    f"[red]Share upload failed with status {exc.response.status_code}: "
                    f"{exc.response.text}[/red]"
                )
                raise typer.Exit(1)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to reach share service at {base}: {exc}[/red]")
                raise typer.Exit(1)

        try:
            data = resp.json()
        except Exception:
            console.print("[red]Share service returned an invalid response (not JSON).[/red]")
            raise typer.Exit(1)

        url = data.get("url")
        if not url:
            token = data.get("id") or data.get("token")
            url = f"{base}/t/{token}" if token else None
        if not url:
            console.print("[red]Share service did not return a link.[/red]")
            raise typer.Exit(1)

        from rich.markup import escape

        safe_url = escape(str(url))
        expires_at = data.get("expires_at")
        safe_expires = escape(str(expires_at)) if expires_at else ""

        console.print(
            Panel(
                f"[bold green]Trace shared![/bold green]\n"
                f"[dim]PII and secrets were redacted before upload.[/dim]\n\n"
                f"Link: [link]{safe_url}[/link]"
                + (f"\n[dim]Expires:[/dim] {safe_expires}" if safe_expires else ""),
                border_style="green",
                title="AgentWatch share",
            )
        )

    asyncio.run(_run())


# ─────────────────────────────────────────────
# confidence command
# ---------------------------------------------


@session_app.command(name="score")
def confidence(
    session_file: Path = typer.Argument(..., help="Path to session JSON file"),
) -> None:
    """[bold]Score[/bold] execution confidence and detect anomalies for a session."""

    from panoptis.core.schema import AgentEvent, AgentSession
    from panoptis.scoring.confidence import ConfidenceScorer

    data = _load_session_file(session_file)
    session = AgentSession(**data["session"])
    events = [AgentEvent(**e) for e in data["events"]]

    scorer = ConfidenceScorer()
    result = scorer.score(events, goal=session.goal)

    score_color = (
        "green"
        if result.overall_score >= 0.7
        else "yellow"
        if result.overall_score >= 0.4
        else "red"
    )

    console.print(
        Panel(
            f"[bold]Confidence Analysis[/bold]\nSession: {session.session_id[:16]}...",
            border_style="blue",
        )
    )

    table = Table(box=box.ROUNDED)
    table.add_column("Metric", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("Rating")

    def _rate(s: float) -> str:
        if s >= 0.8:
            return "[green]● Good[/green]"
        elif s >= 0.5:
            return "[yellow]◐ Fair[/yellow]"
        return "[red]○ Poor[/red]"

    table.add_row(
        "Overall",
        f"[{score_color}]{result.overall_score:.3f}[/{score_color}]",
        _rate(result.overall_score),
    )
    table.add_row("Goal Alignment", f"{result.goal_alignment:.3f}", _rate(result.goal_alignment))
    table.add_row(
        "Consistency",
        f"{result.consistency_score:.3f}",
        _rate(result.consistency_score),
    )

    console.print(table)
    console.print()

    if result.anomaly_flags:
        console.print("[bold red]Anomalies Detected:[/bold red]")
        for flag in result.anomaly_flags:
            console.print(f"  [yellow]⚠[/yellow]  {flag}")
        console.print()

    console.print("[bold]Components:[/bold]")
    for k, v in result.component_scores.items():
        bar_len = int(v * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        color = "green" if v >= 0.7 else "yellow" if v >= 0.4 else "red"
        console.print(f"  {k:<25} [{color}]{bar}[/{color}] {v:.3f}")

    console.print(f"\n[dim]{result.explanation}[/dim]")


# ---------------------------------------------
# safety command
# ---------------------------------------------


@safety_app.command(name="check")
def safety(
    command: str = typer.Argument(..., help="Command to risk-score"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    [bold]Score[/bold] the risk level of a shell command.

    [b]Example Usage:[/b]
    [dim]python -m panoptis.cli.main safety check "rm -rf /var/log"[/dim]
    """
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn

    from panoptis.cli.animator import matrix_type_print
    from panoptis.core.policy_loader import PolicyOutcome
    from panoptis.core.safety import RiskScorer
    from panoptis.core.schema import ToolCallData

    scorer = RiskScorer()
    tool = ToolCallData(tool_name="bash", raw_command=command, arguments={"command": command})

    # Live animated analysis phase
    with Progress(
        SpinnerColumn(spinner_name="dots2", style="cyan"),
        TextColumn("[cyan]{task.description}[/cyan]"),
        transient=True,
    ) as progress:
        task = progress.add_task("Analyzing command vectors...", total=None)
        time.sleep(0.4)
        progress.update(task, description="Cross-referencing security policies...")
        time.sleep(0.3)
        progress.update(task, description="Evaluating what-if scenario impact...")
        time.sleep(0.3)

    level, score, reasons, policies = scorer.score(tool)

    # Overlay any local .agentwatch-safety.yml policy on top of the builtin verdict.
    verdict = _local_policy_verdict(tool, level)
    if verdict is not None and verdict.outcome is not PolicyOutcome.UNCHANGED:
        level = verdict.effective_risk

    color = _risk_color(level.value)

    matrix_type_print("THREAT ANALYSIS COMPLETE", color="1;96m", delay=0.02)

    # Constructing What-If scenario based on risk level
    if score >= 0.8:
        what_if = "If executed, this command could permanently destroy critical data, compromise host integrity, or create severe security vulnerabilities. Recovery would require full system restoration."
    elif score >= 0.5:
        what_if = "If executed, this command may alter important system configurations, expose sensitive network ports, or unexpectedly modify local files."
    elif score >= 0.2:
        what_if = "If executed, this command could result in minor data modifications or unintended side-effects. Generally recoverable."
    else:
        what_if = "This command appears strictly safe. Executing it will likely result in read-only operations or isolated, non-destructive outputs."

    details = [
        f"[dim]Target:[/dim] [bold white]{command}[/bold white]",
        f"[dim]Risk:[/dim]   [{color}][bold]{level.value.upper()}[/bold][/{color}] (Confidence: {score:.2f})",
        "",
    ]

    if reasons:
        details.append(f"[bold {color}]VIOLATIONS DETECTED:[/bold {color}]")
        for r, p in zip(reasons, policies):
            details.append(f"  [{color}][>][/{color}] [bold]{p}[/bold]: {r}")
    else:
        details.append("[green][+] No heuristic violations detected.[/green]")

    if verdict is not None and verdict.matched_label is not None:
        label = escape(verdict.matched_label)
        note = escape(verdict.note) if verdict.note else None
        if verdict.outcome is PolicyOutcome.UNCHANGED:
            if note:
                details.append(f"[dim]Local policy ({label}): {note}[/dim]")
        else:
            details.append(
                f"[bold magenta]LOCAL POLICY:[/bold magenta] "
                f"{verdict.outcome.value.upper()} (rule: {label})"
            )
            if note:
                details.append(f"[dim]{note}[/dim]")

    details.append("")
    details.append("[bold cyan]WHAT-IF SIMULATION:[/bold cyan]")
    details.append(f"[italic]{what_if}[/italic]")

    console.print(
        Panel(
            "\n".join(details),
            border_style=color,
            title=f"[{color}]Security Report[/{color}]",
            padding=(1, 2),
        )
    )


protect_app.command(name="check")(safety)


def _local_policy_verdict(tool: ToolCallData, risk_level: RiskLevel) -> CombinedVerdict | None:
    """Evaluate a command against the local policy file, if one is present."""
    from panoptis.core.policy_loader import combine, discover_policy_file, load_policy_engine
    from panoptis.core.schema import AgentEvent, EventType

    path = discover_policy_file()
    if path is None:
        return None
    try:
        engine = load_policy_engine(path)
    except (ValueError, OSError) as exc:
        console.print(f"[yellow]Ignoring invalid policy file {path}: {escape(str(exc))}[/yellow]")
        return None
    event = AgentEvent(
        session_id="cli", agent_id="cli", event_type=EventType.TOOL_CALL, tool_call=tool
    )
    return combine(risk_level, engine.evaluate(event))


@policies_app.command(name="list")
def safety_policies_list() -> None:
    """
    [bold]List[/bold] the effective safety policy set: builtin patterns + local rules.
    """
    from rich import box

    from panoptis.core.policy_loader import discover_policy_file, load_policy_engine
    from panoptis.core.safety import BUILTIN_RISK_PATTERNS

    builtin = Table(
        title="[bold green]B U I L T I N   P A T T E R N S[/bold green]",
        box=box.DOUBLE_EDGE,
        border_style="bold cyan",
    )
    builtin.add_column("Policy ID", style="bold cyan")
    builtin.add_column("Risk", style="yellow")
    builtin.add_column("Blocks", justify="center")
    builtin.add_column("Reason", style="dim white")
    for pat in BUILTIN_RISK_PATTERNS:
        builtin.add_row(
            pat.policy_id,
            pat.risk_level.value.upper(),
            "yes" if pat.block_by_default else "-",
            pat.reason,
        )
    console.print(builtin)

    path = discover_policy_file()
    if path is None:
        console.print(
            "[dim]No local policy file found (checked ./.agentwatch-safety.yml and "
            "~/.agentwatch/.agentwatch-safety.yml). Run `panoptis safety config generate`.[/dim]"
        )
        return
    try:
        engine = load_policy_engine(path)
    except (ValueError, OSError) as exc:
        console.print(f"[red]Invalid policy file {path}: {escape(str(exc))}[/red]")
        raise typer.Exit(1)

    custom = Table(
        title=f"[bold green]L O C A L   R U L E S[/bold green]  [dim]({escape(str(path))})[/dim]",
        box=box.DOUBLE_EDGE,
        border_style="bold magenta",
    )
    custom.add_column("Label", style="bold magenta")
    custom.add_column("Action", style="yellow")
    custom.add_column("Condition", style="dim white")
    for rule in engine.rules:
        custom.add_row(escape(rule.label or "-"), rule.action.value, escape(rule.condition))
    console.print(custom)
    if not engine.rules:
        console.print("[dim]The policy file has no rules.[/dim]")


@config_app.command(name="generate")
def safety_config_generate(
    path: Path = typer.Option(
        Path(".agentwatch-safety.yml"), "--path", help="Where to write the policy file."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """
    [bold]Generate[/bold] a starter .agentwatch-safety.yml policy file.
    """
    from panoptis.core.policy_loader import DEFAULT_POLICY_YAML

    if path.exists() and not force:
        console.print(
            f"[yellow]{escape(str(path))} already exists. Use --force to overwrite.[/yellow]"
        )
        raise typer.Exit(1)
    path.write_text(DEFAULT_POLICY_YAML, encoding="utf-8")
    console.print(
        f"[green]Wrote safety policy scaffold to[/green] [bold]{escape(str(path))}[/bold]"
    )
    console.print(
        "[dim]Edit the rules, then run `panoptis safety policies list` to review them.[/dim]"
    )


# ---------------------------------------------
# cost report command
# ---------------------------------------------


@cost_app.command(name="report")
def cost_report(
    days: int = typer.Option(30, "--days", help="Reporting window in days (must be >= 1)."),
    group_by: str = typer.Option(
        "framework", "--group-by", help="Group by: framework, agent, or status."
    ),
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    api_key: str | None = API_KEY_OPTION,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """
    [bold]Report[/bold] token usage, USD cost, and cost-per-successful-goal.

    Aggregates recent sessions from the AgentWatch API over the last [b]--days[/b],
    grouped by [b]--group-by[/b].

    [b]Example Usage:[/b]
    [dim]python -m panoptis.cli.main cost report --days 30 --group-by framework[/dim]
    """
    from panoptis.cost.reporting import VALID_GROUP_BY, build_cost_report, parse_sessions

    if group_by not in VALID_GROUP_BY:
        console.print(
            f"[red]Invalid --group-by {group_by!r}. Choose one of: {', '.join(VALID_GROUP_BY)}.[/red]"
        )
        raise typer.Exit(2)
    if days < 1:
        console.print("[red]--days must be >= 1.[/red]")
        raise typer.Exit(2)

    limit = 200  # /api/v1/sessions caps its page size at 200

    async def _fetch() -> list[dict[str, object]]:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/sessions",
                    params={"since_hours": days * 24, "limit": limit},
                    headers=_api_headers(api_key),
                    timeout=15.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        payload = resp.json()
        items = payload.get("sessions", [])
        return list(items) if isinstance(items, list) else []

    raw_sessions = asyncio.run(_fetch())
    if len(raw_sessions) >= limit:
        console.print(
            f"[yellow]Note: the API returned its maximum of {limit} sessions, so this "
            f"report may be incomplete. Narrow the window with a smaller --days.[/yellow]"
        )
    sessions, skipped = parse_sessions(raw_sessions)
    if skipped:
        console.print(f"[yellow]Skipped {skipped} session record(s) that failed to parse.[/yellow]")
    report = build_cost_report(sessions, group_by=group_by, days=days)

    if as_json:
        console.print_json(data=report.to_dict())
        return

    _print_cost_report_table(report)


def _print_cost_report_table(report: CostReport) -> None:
    table = Table(
        title=(
            f"[bold green]C O S T   R E P O R T[/bold green]  "
            f"[dim](last {report.days}d · by {report.group_by})[/dim]"
        ),
        box=box.DOUBLE_EDGE,
        border_style="bold cyan",
    )
    table.add_column(report.group_by.capitalize(), style="bold cyan")
    table.add_column("Sessions", justify="right", style="dim white")
    table.add_column("Tokens", justify="right", style="green")
    table.add_column("USD", justify="right", style="yellow")
    table.add_column("Successful", justify="right", style="cyan")
    table.add_column("USD / success", justify="right", style="bold yellow")

    for row in report.rows:
        cps = row.cost_per_successful_goal
        table.add_row(
            row.group,
            str(row.sessions),
            f"{row.total_tokens:,}",
            f"${row.total_usd:,.4f}",
            str(row.successful),
            "—" if cps is None else f"${cps:,.4f}",
        )

    if report.rows:
        table.add_section()
        table.add_row(
            "[bold]TOTAL[/bold]",
            str(report.total_sessions),
            f"[bold]{report.total_tokens:,}[/bold]",
            f"[bold]${report.total_usd:,.4f}[/bold]",
            "",
            "",
        )

    console.print(table)
    if not report.rows:
        console.print("[dim]No sessions found in the selected window.[/dim]")


# ---------------------------------------------
# eval run command
# ---------------------------------------------


@eval_app.command(name="run")
def eval_run(
    dataset: Path = typer.Argument(
        ..., help="Path to the dataset JSON: a list of {id, prompt, expected?}."
    ),
    agent: Path = typer.Option(
        ..., "--agent", help="Path to an agent script exposing run(prompt) -> list[AgentEvent]."
    ),
    threshold: float = typer.Option(
        0.5, "--threshold", min=0.0, max=1.0, help="Minimum confidence for a case to pass."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """
    [bold]Run[/bold] a batch evaluation of an agent against a dataset of prompts.

    [b]Example Usage:[/b]
    [dim]python -m panoptis.cli.main eval run dataset.json --agent my_agent.py[/dim]
    """
    from panoptis.eval.runner import run_eval

    try:
        report = run_eval(dataset, agent, threshold=threshold)
    except (FileNotFoundError, ImportError, AttributeError, ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        console.print_json(data=report.to_dict())
        return

    _print_eval_report(report)


def _print_eval_report(report: EvalReport) -> None:
    from rich import box

    table = Table(
        title=(
            f"[bold green]E V A L U A T I O N[/bold green]  "
            f"[dim](pass threshold {report.threshold:.2f})[/dim]"
        ),
        box=box.DOUBLE_EDGE,
        border_style="bold cyan",
    )
    table.add_column("Case", style="bold cyan")
    table.add_column("Result", justify="center")
    table.add_column("Confidence", justify="right", style="green")
    table.add_column("Halluc. risk", justify="right", style="yellow")
    table.add_column("Note", style="dim white")

    for r in report.results:
        if r.error is not None:
            result_cell = "[red]ERROR[/red]"
            note = r.error
        elif r.passed:
            result_cell = "[green]PASS[/green]"
            note = "hallucinated" if r.hallucinated else ""
        else:
            result_cell = "[red]FAIL[/red]"
            note = "hallucinated" if r.hallucinated else ""
        table.add_row(
            r.id,
            result_cell,
            f"{r.confidence:.2f}",
            f"{r.hallucination_risk:.2f}",
            note,
        )

    table.add_section()
    table.add_row(
        f"[bold]{report.passed}/{report.total} passed[/bold]",
        f"[bold]{report.pass_rate * 100:.0f}%[/bold]",
        f"[bold]{report.avg_confidence:.2f}[/bold]",
        f"[bold]{report.avg_hallucination_risk:.2f}[/bold]",
        f"{report.num_hallucinated} hallucinated, {report.num_errored} errored",
    )
    console.print(table)


# ---------------------------------------------
# serve command
# ---------------------------------------------


@server_app.command(name="start")
def serve(
    host: str = typer.Option("0.0." + "0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview what would happen without starting the server",
    ),
) -> None:
    """[bold]Start[/bold] the AgentWatch API server."""

    if dry_run:
        console.print(
            Panel(
                "[bold yellow]DRY-RUN MODE[/bold yellow] — Server will NOT be started.\n"
                f"[dim]Would bind to:[/dim]  http://{host}:{port}\n"
                f"[dim]Dashboard:[/dim]     http://localhost:3000\n"
                f"[dim]Hot-reload:[/dim]    {'enabled' if reload else 'disabled'}\n"
                f"[dim]App module:[/dim]    panoptis.api.server:app",
                border_style="yellow",
                title="AgentWatch serve --dry-run",
            )
        )
        _dry_run_print(
            "start uvicorn server",
            f"uvicorn panoptis.api.server:app --host {host} --port {port}"
            + (" --reload" if reload else ""),
        )
        console.print("\n[yellow]Dry-run complete. Server was not started.[/yellow]")
        raise typer.Exit(0)

    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn[/red]")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold cyan]AgentWatch API Server[/bold cyan]\n"
            f"[dim]Listening on[/dim] http://{host}:{port}\n"
            f"[dim]Dashboard[/dim]  http://localhost:3000",
            border_style="cyan",
        )
    )
    uvicorn.run(
        "panoptis.api.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ---------------------------------------------
# top command
# ---------------------------------------------


@server_app.command(name="top")
def top(
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    refresh_rate: float = typer.Option(1.0, "--refresh", min=0.1, help="Refresh rate in seconds"),
    api_key: str | None = API_KEY_OPTION,
    cooldown: int = typer.Option(
        60,
        "--cooldown",
        min=0,
        help="Seconds the circuit breaker stays OPEN before probing recovery (0 = manual resume only)",
    ),
    max_failures: int = typer.Option(
        3, "--max-failures", min=1, help="Consecutive failures that trip the circuit breaker"
    ),
) -> None:
    """[bold]Live Process Monitor[/bold] showing executing agent loops, active tools, and token burn rate."""
    from panoptis.circuit_breaker import CircuitThresholds

    thresholds = CircuitThresholds(
        max_consecutive_errors=max_failures, cooldown_seconds=float(cooldown)
    )
    breaker_info = (
        f"breaker: cooldown {thresholds.cooldown_seconds:g}s, "
        f"max failures {thresholds.max_consecutive_errors}"
    )

    async def _run() -> None:
        try:
            import asyncio

            import httpx
            from rich.live import Live
            from rich.panel import Panel
            from rich.table import Table
        except ImportError:
            console.print("[red]Missing dependencies. Run: pip install httpx rich[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/dashboard/top",
                    headers=_api_headers(api_key),
                    timeout=10.0,
                )
                resp.raise_for_status()
            except Exception as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        def generate_dashboard(data, error_msg=None):
            if error_msg:
                return Panel(
                    f"[red]{error_msg}[/red]",
                    title="AgentWatch Top Error",
                    border_style="red",
                )

            table = Table(show_header=True, header_style="bold magenta", expand=True)
            table.add_column("Session ID", style="cyan")
            table.add_column("Agent ID / Name", style="blue")
            table.add_column("Current Tool", style="yellow")
            table.add_column("Burn Rate (tok/s)", justify="right", style="green")
            table.add_column("Total Tokens", justify="right", style="green")

            top_sessions = data.get("top_sessions", [])
            if not top_sessions:
                return Panel(
                    "No active agent sessions running.",
                    title="[cyan]AgentWatch Top[/cyan]",
                    subtitle=breaker_info,
                    border_style="cyan",
                )

            for s in top_sessions:
                table.add_row(
                    str(s.get("session_id")),
                    f"{s.get('agent_id')} / {s.get('agent_name')}",
                    str(s.get("current_tool")),
                    str(s.get("token_burn_rate_per_sec")),
                    str(s.get("total_tokens")),
                )

            return Panel(
                table,
                title="[cyan]AgentWatch Top - Active Agent Loops[/cyan]",
                subtitle=breaker_info,
                border_style="cyan",
            )

        async def poll_loop(live_display: Any) -> None:
            async with httpx.AsyncClient() as client:
                while True:
                    try:
                        resp = await client.get(
                            f"{api_url}/api/v1/dashboard/top",
                            headers=_api_headers(api_key),
                            timeout=5.0,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        live_display.update(generate_dashboard(data))
                    except Exception as exc:
                        live_display.update(generate_dashboard({}, error_msg=str(exc)))

                    await asyncio.sleep(refresh_rate)

        with Live(
            generate_dashboard({"top_sessions": []}),
            refresh_per_second=1.0 / refresh_rate,
        ) as live:
            await poll_loop(live)

    import asyncio

    asyncio.run(_run())


# ---------------------------------------------
# status command
# ---------------------------------------------


@server_app.command(name="status")
def status(
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    refresh_rate: float = typer.Option(
        1.0, "--refresh", min=0.1, help="Refresh rate in seconds (must be >= 0.1)"
    ),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """[bold]Show[/bold] a real-time live dashboard of AgentWatch runtime status."""

    async def _run() -> None:
        try:
            import httpx
            from rich.align import Align
            from rich.layout import Layout
            from rich.live import Live
        except ImportError:
            console.print("[red]Missing dependencies. Run: pip install httpx rich[/red]")
            raise typer.Exit(1)

        # Initial connection check
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/dashboard/summary",
                    headers=_api_headers(api_key),
                    timeout=10.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        def generate_dashboard(data, error_msg=None):
            if error_msg:
                return Panel(
                    f"[red]{error_msg}[/red]",
                    title="AgentWatch Error",
                    border_style="red",
                )

            # Create sub-panels
            active = data.get("active_sessions", 0)
            failed = data.get("failed_sessions", 0)
            blocked = data.get("blocked_sessions", 0)

            activity = Table.grid(padding=(0, 2))
            activity.add_row("Active Sessions:", f"[green]{active}[/green]")
            activity.add_row("Failed Sessions:", f"[red]{failed}[/red]")
            activity.add_row("Blocked Sessions:", f"[yellow]{blocked}[/yellow]")
            p1 = Panel(activity, title="[cyan]Agent Activity[/cyan]", border_style="cyan")

            tokens = data.get("total_tokens", 0)
            cost = data.get("estimated_cost_usd", 0.0)

            resources = Table.grid(padding=(0, 2))
            resources.add_row("Total Tokens:", f"[bold]{tokens:,}[/bold]")
            resources.add_row("Est. Cost:", f"[green]${cost:.4f}[/green]")
            p2 = Panel(
                resources,
                title="[magenta]Resource Utilization[/magenta]",
                border_style="magenta",
            )

            safety_stats = data.get("safety_stats", {})
            eb_stats = data.get("event_bus_stats", {})

            pipeline = Table.grid(padding=(0, 2))
            pipeline.add_row("Blocked Ops:", f"[red]{safety_stats.get('blocked', 0)}[/red]")
            pipeline.add_row("Event T-Put:", f"{eb_stats.get('total_published', 0):,} processed")
            pipeline.add_row("Subscribers:", f"{eb_stats.get('active_subscribers', 0)}")
            p3 = Panel(
                pipeline,
                title="[yellow]Safety & Event Pipeline[/yellow]",
                border_style="yellow",
            )

            layout = Layout()
            layout.split_column(
                Layout(
                    Panel(
                        Align(
                            "[bold cyan]AgentWatch Live Runtime Dashboard[/bold cyan]\n[dim]Press Ctrl+C to exit[/dim]",
                            align="center",
                        )
                    ),
                    size=4,
                ),
                Layout(name="body"),
            )
            layout["body"].split_row(Layout(p1), Layout(p2), Layout(p3))
            return layout

        async with httpx.AsyncClient() as client:
            with Live(
                generate_dashboard({}),
                refresh_per_second=1 / refresh_rate,
                console=console,
            ) as live:
                while True:
                    try:
                        resp = await client.get(
                            f"{api_url}/api/v1/dashboard/summary",
                            headers=_api_headers(api_key),
                            timeout=2.0,
                        )
                        resp.raise_for_status()
                        live.update(generate_dashboard(resp.json()))
                    except Exception as exc:
                        live.update(generate_dashboard({}, str(exc)))
                    await asyncio.sleep(refresh_rate)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("[dim]Exited status dashboard.[/dim]")


# ─────────────────────────────────────────────
# compare command
# ─────────────────────────────────────────────


@legacy_app.command(name="compare")
def compare(
    session_id_1: str = typer.Argument(..., help="ID of the first session to compare"),
    session_id_2: str = typer.Argument(..., help="ID of the second session to compare"),
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """[bold]Compare[/bold] confidence and quality metrics across two sessions."""

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                headers = _api_headers(api_key)
                conf1_resp = await client.get(
                    f"{api_url}/api/v1/sessions/{session_id_1}/confidence",
                    headers=headers,
                    timeout=10.0,
                )
                conf2_resp = await client.get(
                    f"{api_url}/api/v1/sessions/{session_id_2}/confidence",
                    headers=headers,
                    timeout=10.0,
                )

                if conf1_resp.status_code == 404:
                    console.print(
                        f"[red]Session {session_id_1} not found or has no confidence data.[/red]"
                    )
                    raise typer.Exit(1)
                if conf2_resp.status_code == 404:
                    console.print(
                        f"[red]Session {session_id_2} not found or has no confidence data.[/red]"
                    )
                    raise typer.Exit(1)

                conf1_resp.raise_for_status()
                conf2_resp.raise_for_status()

                conf1 = conf1_resp.json()
                conf2 = conf2_resp.json()

                rep1_resp = await client.get(
                    f"{api_url}/api/v1/sessions/{session_id_1}/replay",
                    headers=headers,
                    timeout=10.0,
                )
                rep2_resp = await client.get(
                    f"{api_url}/api/v1/sessions/{session_id_2}/replay",
                    headers=headers,
                    timeout=10.0,
                )

                if rep1_resp.status_code == 404:
                    console.print(f"[red]Session {session_id_1} replay not found.[/red]")
                    raise typer.Exit(1)
                if rep2_resp.status_code == 404:
                    console.print(f"[red]Session {session_id_2} replay not found.[/red]")
                    raise typer.Exit(1)

                rep1_resp.raise_for_status()
                rep2_resp.raise_for_status()

                rep1 = rep1_resp.json()
                rep2 = rep2_resp.json()

            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        def _compute_metrics(conf, rep):
            """
            Compute comparison metrics from API responses.

            Required confidence fields:
            - overall_score

            Optional fields:
            - goal_alignment
            - hallucination_risk
            - anomaly_flags

            Missing optional fields are rendered as N/A to maintain
            compatibility with older AgentWatch API versions.
            """
            overall = conf.get("overall_score")
            alignment = conf.get("goal_alignment")

            steps = rep.get("steps", [])
            failed_steps = 0
            safety_blocks = 0

            from pydantic import ValidationError

            from panoptis.core.schema import AgentEvent
            from panoptis.reasoning.hallucination import (
                HallucinationClassifier,
                HallucinationRisk,
            )

            # Preferred: if confidence_response has hallucination_risk, use it.
            # Otherwise, derive it from official HallucinationClassifier.
            hrisk = conf.get("hallucination_risk")
            compute_hrisk = hrisk is None

            if compute_hrisk:
                classifier = HallucinationClassifier()
                highest_risk = HallucinationRisk.LOW

            for step in steps:
                ev_data = step.get("event", {})

                # Check for failures and safety blocks
                etype = ev_data.get("event_type", "").lower()
                status = ev_data.get("status", "").lower()
                if etype == "tool_error" or status == "failure":
                    failed_steps += 1

                safety = ev_data.get("safety")
                if etype == "safety_block" or (safety and safety.get("blocked")):
                    safety_blocks += 1

                # Classify hallucination risk using the official source of truth
                if compute_hrisk:
                    try:
                        ev = AgentEvent(**ev_data)
                        classifier.observe(ev)
                        f = classifier.classify(ev)
                        if f.risk == HallucinationRisk.HIGH:
                            highest_risk = HallucinationRisk.HIGH
                        elif (
                            f.risk == HallucinationRisk.MEDIUM
                            and highest_risk == HallucinationRisk.LOW
                        ):
                            highest_risk = HallucinationRisk.MEDIUM
                    except (ValidationError, TypeError, ValueError):
                        continue

            if compute_hrisk:
                hrisk = highest_risk.value.upper()

            return {
                "overall": overall,
                "hrisk": hrisk,
                "alignment": alignment,
                "failed": failed_steps,
                "blocks": safety_blocks,
            }

        m1 = _compute_metrics(conf1, rep1)
        m2 = _compute_metrics(conf2, rep2)

        def format_score(val):
            return f"{val:.2f}" if val is not None else "N/A"

        console.print("\n[bold]AgentWatch Session Comparison[/bold]")
        console.print("────────────────────────────────────\n")

        table = Table(box=None, show_header=True, header_style="")
        table.add_column("", style="bold", width=20)
        table.add_column("Session A", justify="center", width=12)
        table.add_column("Session B", justify="center", width=12)

        table.add_row(
            "Overall Confidence",
            format_score(m1["overall"]),
            format_score(m2["overall"]),
        )
        table.add_row("Hallucination Risk", m1["hrisk"], m2["hrisk"])
        table.add_row(
            "Goal Alignment",
            format_score(m1["alignment"]),
            format_score(m2["alignment"]),
        )
        table.add_row("Failed Steps", str(m1["failed"]), str(m2["failed"]))
        table.add_row("Safety Blocks", str(m1["blocks"]), str(m2["blocks"]))

        console.print(table)
        console.print("\n[bold]Improvement Summary[/bold]")
        console.print("────────────────────────────────────")

        if m1["overall"] is not None and m2["overall"] is not None:
            diff = m2["overall"] - m1["overall"]
            if diff > 0:
                if m1["overall"] > 0:
                    pct = diff / m1["overall"] * 100
                    conf_sum = f"[green]+{pct:.0f}%[/green]"
                else:
                    conf_sum = "N/A"
            elif diff < 0:
                if m1["overall"] > 0:
                    pct = -diff / m1["overall"] * 100
                    conf_sum = f"[red]-{pct:.0f}%[/red]"
                else:
                    conf_sum = "N/A"
            else:
                conf_sum = "Unchanged"
                conf_sum = "Unchanged"
        else:
            conf_sum = "N/A"

        console.print(f"Confidence Increase: {conf_sum}")

        if m1["hrisk"] == "HIGH" and m2["hrisk"] == "LOW":
            hr_sum = "[green]Improved[/green]"
        elif m1["hrisk"] == "LOW" and m2["hrisk"] == "HIGH":
            hr_sum = "[red]Regressed[/red]"
        elif m1["hrisk"] == "N/A" or m2["hrisk"] == "N/A":
            hr_sum = "N/A"
        else:
            hr_sum = "Unchanged"
        console.print(f"Hallucination Risk: {hr_sum}")

        if m1["alignment"] is not None and m2["alignment"] is not None:
            diff_align = m2["alignment"] - m1["alignment"]
            if diff_align > 0:
                al_sum = "[green]Improved[/green]"
            elif diff_align < 0:
                al_sum = "[red]Regressed[/red]"
            else:
                al_sum = "Unchanged"
        else:
            al_sum = "N/A"
        console.print(f"Goal Alignment: {al_sum}")

        console.print("\nHigher confidence session: ", end="")
        if m1["overall"] is not None and m2["overall"] is not None:
            if m2["overall"] > m1["overall"]:
                console.print("[bold green]Session B[/bold green]")
            elif m1["overall"] > m2["overall"]:
                console.print("[bold green]Session A[/bold green]")
            else:
                console.print("[bold yellow]Tie[/bold yellow]")
        else:
            console.print("N/A")
        console.print()

    asyncio.run(_run())


# ─────────────────────────────────────────────
# verify-env command
# ---------------------------------------------


@app.command(name="check-env", hidden=True)
def verify_env() -> None:
    """[bold]Verify[/bold] local developer environment variables and dependencies."""
    from panoptis.cli.verify_env import verify_environment

    verify_environment()


# ─────────────────────────────────────────────
# redteam command
# ─────────────────────────────────────────────


@safety_app.command(name="redteam")
def redteam(
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON"),
) -> None:
    """[bold]Red-team[/bold] the safety engine with simulated attacks and score resilience."""
    from panoptis.security.redteam import RedTeamHarness

    report = RedTeamHarness().run()

    if json_output:
        console.print_json(data=report.to_dict())
        raise typer.Exit(0)

    score = report.resilience_score
    score_color = "green" if score >= 0.8 else "yellow" if score >= 0.5 else "red"
    console.print(
        Panel(
            f"[bold]Red-Team Resilience[/bold]\n"
            f"Score:    [{score_color}]{score:.0%}[/{score_color}]\n"
            f"Defended: {report.defended_count}/{report.total} attacks",
            border_style=score_color,
            title="AgentWatch redteam",
        )
    )

    table = Table(box=box.ROUNDED)
    table.add_column("Scenario", style="bold")
    table.add_column("Category")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for r in report.results:
        result = "[green]✓ defended[/green]" if r.defended else "[red]✗ bypassed[/red]"
        table.add_row(r.scenario.id, r.scenario.category.value, result, r.detail)
    console.print(table)

    if report.bypassed:
        console.print(
            f"\n[red]⚠ {len(report.bypassed)} attack(s) bypassed defenses[/red] "
            "— review the safety detectors for these vectors."
        )


protect_app.command(name="redteam")(redteam)


# ─────────────────────────────────────────────
# upgrade command — CLI-to-Web monetization handoff
# ─────────────────────────────────────────────


def _license_public_key() -> str | None:
    """Resolve the PEM public key used to verify entitlements, if configured.

    Read from ``AGENTWATCH_LICENSE_PUBLIC_KEY`` (inline PEM) or, failing that,
    ``AGENTWATCH_LICENSE_PUBLIC_KEY_FILE`` (path to a PEM file). Returns ``None``
    when no key is configured, in which case the CLI behaves as free tier.
    """
    import os
    import sys

    inline = os.environ.get("AGENTWATCH_LICENSE_PUBLIC_KEY")
    if inline:
        return inline

    key_file = os.environ.get("AGENTWATCH_LICENSE_PUBLIC_KEY_FILE")
    if key_file:
        try:
            return Path(key_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"Warning: License public key file not found: {key_file}", file=sys.stderr)
            return None
        except PermissionError:
            print(
                f"Error: Permission denied reading license public key file: {key_file}",
                file=sys.stderr,
            )
            return None
        except UnicodeDecodeError:
            print(
                f"Error: Failed to decode license public key file {key_file}: invalid encoding.",
                file=sys.stderr,
            )
            return None
        except OSError as exc:
            print(
                f"Error: OS error reading license public key file {key_file}: {exc}",
                file=sys.stderr,
            )
            return None
    return None


def _active_entitlement():
    """Return the verified active entitlement, or ``None`` for free tier."""
    from panoptis.security.entitlement_store import load_entitlement

    public_key = _license_public_key()
    if public_key is None:
        return None
    return load_entitlement(public_key)


def _ensure_premium(feature: str):
    """Gate a premium feature: return the entitlement or prompt to upgrade.

    Raises ``typer.Exit`` (code 1) with an upgrade prompt when the current
    install is not entitled to ``feature``.
    """
    entitlement = _active_entitlement()
    if entitlement is not None and entitlement.grants(feature):
        return entitlement
    console.print(
        f"[yellow]'{feature}' is a premium feature.[/yellow] "
        "Run [bold cyan]panoptis upgrade[/bold cyan] to unlock it."
    )
    raise typer.Exit(1)


@app.command(hidden=True)
def upgrade(
    activate: str | None = typer.Option(
        None,
        "--activate",
        help="Store the entitlement token returned by the checkout page.",
    ),
    show_status: bool = typer.Option(
        False, "--status", help="Show the current entitlement status and exit."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the checkout URL instead of opening a browser."
    ),
    base_url: str | None = typer.Option(
        None, "--checkout-url", help="Override the checkout portal base URL."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview the handoff without opening a browser."
    ),
) -> None:
    """[bold]Upgrade[/bold] to AgentWatch Premium via the secure web checkout."""
    from panoptis.security.checkout import DEFAULT_CHECKOUT_URL, checkout_url, new_session

    if show_status:
        entitlement = _active_entitlement()
        if entitlement is None:
            console.print("[dim]Tier:[/dim] [yellow]Free[/yellow] — no active entitlement.")
        else:
            console.print(
                Panel(
                    f"[dim]Tier:[/dim]    [green]{entitlement.tier}[/green]\n"
                    f"[dim]Account:[/dim] {entitlement.subject}\n"
                    f"[dim]Expires:[/dim] {entitlement.expires_at:%Y-%m-%d}",
                    title="AgentWatch Premium",
                    border_style="green",
                )
            )
        raise typer.Exit(0)

    if activate is not None:
        public_key = _license_public_key()
        if public_key is None:
            console.print(
                "[red]No license public key configured.[/red] Set "
                "AGENTWATCH_LICENSE_PUBLIC_KEY before activating."
            )
            raise typer.Exit(1)
        from panoptis.security.entitlement_store import store_entitlement_token
        from panoptis.security.license import LicenseError, verify_entitlement

        try:
            entitlement = verify_entitlement(activate, public_key)
        except LicenseError as exc:
            console.print(f"[red]Entitlement rejected:[/red] {exc}")
            raise typer.Exit(1)
        path = store_entitlement_token(activate)
        console.print(f"[green]Premium activated[/green] ({entitlement.tier}) — stored at {path}.")
        raise typer.Exit(0)

    session = new_session()
    url = checkout_url(session, base=base_url or DEFAULT_CHECKOUT_URL)

    if dry_run:
        _dry_run_print("open browser to checkout", f"URL: {url}")
        console.print("\n[yellow]Dry-run complete. No browser was opened.[/yellow]")
        raise typer.Exit(0)

    console.print(
        Panel(
            "[bold cyan]AgentWatch Premium[/bold cyan]\n"
            "Complete checkout in your browser, then run\n"
            "[bold]panoptis upgrade --activate <token>[/bold] with the token shown there.",
            border_style="cyan",
        )
    )

    if no_browser:
        console.print(f"\nCheckout URL: [link]{url}[/link]")
    else:
        import webbrowser

        if webbrowser.open(url):
            console.print(f"\n[green]Opened checkout in your browser.[/green]\n[dim]{url}[/dim]")
        else:
            console.print(f"\nCould not open a browser. Visit: [link]{url}[/link]")


# ─────────────────────────────────────────────
# Print helpers
# ---------------------------------------------


def _print_live_event(event) -> None:
    from panoptis.core.schema import EventType

    icon_map = {
        EventType.TOOL_CALL: "[cyan][+][/cyan]",
        EventType.TOOL_RESULT: "[green][=][/green]",
        EventType.TOOL_ERROR: "[red][!][/red]",
        EventType.SAFETY_BLOCK: "[bold red][x][/bold red]",
        EventType.SAFETY_CHECK: "[yellow][*][/yellow]",
        EventType.PLANNER_OUTPUT: "[magenta][~][/magenta]",
        EventType.AGENT_START: "[green][>][/green]",
        EventType.AGENT_END: "[dim][<][/dim]",
        EventType.SESSION_START: "[bold cyan][^][/bold cyan]",
        EventType.SESSION_END: "[bold cyan][$][/bold cyan]",
        EventType.CHECKPOINT_CREATE: "[blue][@][/blue]",
        EventType.ROLLBACK_TRIGGER: "[orange1][&][/orange1]",
        EventType.MEMORY_READ: "[dim][r][/dim]",
        EventType.MEMORY_WRITE: "[dim][w][/dim]",
    }

    icon = icon_map.get(event.event_type, "•")
    ts = event.timestamp.strftime("%H:%M:%S")

    if event.event_type == EventType.TOOL_CALL and event.tool_call:
        name = event.tool_call.tool_name
        cmd = (event.tool_call.raw_command or "")[:60]
        risk_str = ""
        if event.safety:
            rc = _risk_color(event.safety.risk_level.value)
            risk_str = f" [{rc}][{event.safety.risk_level.value}][/{rc}]"
        status_str = ""
        if event.is_blocked:
            status_str = " [red][BLOCKED][/red]"
        console.print(f"[dim]{ts}[/dim] {icon} [bold]{name}[/bold]{risk_str}{status_str}")
        if cmd:
            console.print(f"         [dim]{cmd}[/dim]")
        if event.is_blocked:
            from panoptis.reasoning.auditor import ReasoningAuditor

            signals = ReasoningAuditor.detect_risk_signals(event)
            if signals:
                console.print(f"         [red]⚠ risk signals: {', '.join(signals)}[/red]")

    elif event.event_type == EventType.SAFETY_BLOCK:
        console.print(f"[dim]{ts}[/dim] {icon} [bold red]SAFETY BLOCK[/bold red]")
        if event.safety and event.safety.reasons:
            for r in event.safety.reasons[:2]:
                console.print(f"         [red]→ {r}[/red]")

    elif event.event_type in (EventType.TOOL_RESULT, EventType.TOOL_ERROR):
        res = event.tool_result
        if res:
            text = (res.error or res.output or "").strip()
            colour = "red" if res.error else "green"
            shown = text.splitlines()[:8]
            if shown:
                console.print(f"[dim]{ts}[/dim] {icon} [{colour}]{escape(shown[0])}[/{colour}]")
                for line in shown[1:]:
                    console.print(f"         [{colour}]{escape(line)}[/{colour}]")

    elif event.event_type in (
        EventType.SESSION_START,
        EventType.SESSION_END,
        EventType.AGENT_START,
        EventType.AGENT_END,
    ):
        sc = _status_color(event.status.value)
        console.print(f"[dim]{ts}[/dim] {icon} [{sc}]{event.event_type.value}[/{sc}]")


def _print_replay_step(step, show_all: bool = False) -> None:
    event = step.event
    ts = event.timestamp.strftime("%H:%M:%S.%f")[:-3]

    annotations = " ".join(step.annotations)
    border = "red" if step.is_failure_point else "blue"

    info_lines = [
        f"[bold]Step {step.index:04d}[/bold]  "
        f"[{_status_color(event.status.value)}]{event.event_type.value}"
        f"[/{_status_color(event.status.value)}]  [dim]{ts}[/dim]"
    ]

    if event.tool_call:
        info_lines.append(f"Tool: [bold]{event.tool_call.tool_name}[/bold]")
        if event.tool_call.raw_command:
            info_lines.append(f"Cmd:  [dim]{event.tool_call.raw_command[:80]}[/dim]")
    if event.tool_result and event.tool_result.error:
        info_lines.append(f"[red]Error: {event.tool_result.error[:100]}[/red]")
    if event.safety and event.safety.risk_level.value not in ("safe", "low"):
        rc = _risk_color(event.safety.risk_level.value)
        info_lines.append(
            f"Risk: [{rc}]{event.safety.risk_level.value.upper()}[/{rc}]"
            f" ({event.safety.risk_score:.2f})"
        )
    if annotations:
        info_lines.append(f"[bold]{annotations}[/bold]")

    console.print(Panel("\n".join(info_lines), border_style=border, padding=(0, 1)))


def _print_session_summary(session, events) -> None:
    from panoptis.cli.animator import matrix_type_print
    from panoptis.scoring.confidence import ConfidenceScorer

    scorer = ConfidenceScorer()
    result = scorer.score(events, goal=session.goal)

    sc = _status_color(session.status.value)
    cc = (
        "green"
        if result.overall_score >= 0.7
        else "yellow"
        if result.overall_score >= 0.4
        else "red"
    )

    matrix_type_print(f"SESSION COMPLETE: {session.session_id}", color="1;96m", delay=0.03)

    console.print(
        Panel(
            f"Status:     [{sc}]{session.status.value}[/{sc}]\n"
            f"Events:     {session.total_events}\n"
            f"Tokens:     {session.total_tokens:,}\n"
            f"Cost (est): ${session.estimated_cost_usd:.4f}\n"
            f"Confidence: [{cc}]{result.overall_score:.3f}[/{cc}]\n"
            + (
                f"Anomalies:  {', '.join(result.anomaly_flags)}"
                if result.anomaly_flags
                else "Anomalies:  none"
            ),
            border_style=sc,
            title="AgentWatch Summary",
        )
    )


def _print_sessions_table(sessions: list) -> None:
    from rich import box

    from panoptis.cli.animator import animate_table_rows

    table = Table(
        title="[bold green]R E C E N T   S E S S I O N S[/bold green]",
        box=box.DOUBLE_EDGE,
        border_style="bold cyan",
    )
    table.add_column("ID", style="bold green", width=16)
    table.add_column("Agent", style="bold cyan")
    table.add_column("Framework", style="dim white")
    table.add_column("Status")
    table.add_column("Events", justify="right", style="cyan")
    table.add_column("Tokens", justify="right", style="green")
    table.add_column("Started", style="dim")

    rows = []
    for s in sessions:
        sid = s["session_id"][:12] + "..."
        sc = _status_color(s.get("status", ""))
        started = s.get("started_at", "")[:16] if s.get("started_at") else "-"
        rows.append(
            [
                sid,
                s.get("agent_name") or s.get("agent_id", "?")[:16],
                s.get("framework", "-"),
                f"[{sc}]{s.get('status', '-')}[/{sc}]",
                str(s.get("total_events", 0)),
                f"{s.get('total_tokens', 0):,}",
                started,
            ]
        )

    animate_table_rows(table, rows, delay=0.08)


# ─────────────────────────────────────────────
# session command group
# ─────────────────────────────────────────────


@session_app.command("rollback")
def session_rollback(
    session_id: str = typer.Argument(..., help="Session ID to rollback"),
    to_step: int = typer.Option(..., "--to-step", min=0, help="Step number to rollback to"),
) -> None:
    """[bold]Rollback[/bold] a session to a specific step."""

    async def _run() -> None:
        from panoptis.rollback.engine import RollbackEngine, RollbackStatus

        engine = RollbackEngine()

        console.print(
            f"[dim]Rolling back session[/dim] "
            f"[bold]{session_id}[/bold] "
            f"[dim]to step[/dim] "
            f"[bold]{to_step}[/bold]..."
        )

        result = await engine.rollback_session(session_id, to_step=to_step)

        if result.status == RollbackStatus.COMPLETED:
            console.print("\n[green]✓ Rollback complete[/green]")
            if result.rolled_back_files:
                console.print(f"  Restored {len(result.rolled_back_files)} files.")
            if result.rolled_back_git_ref:
                console.print(f"  Rolled back git to {result.rolled_back_git_ref[:8]}.")
        else:
            console.print(f"\n[red]✗ Rollback failed: {result.error}[/red]")
            raise typer.Exit(1)

    asyncio.run(_run())


# ─────────────────────────────────────────────
# session prune command
# ─────────────────────────────────────────────


def _parse_older_than_to_hours(val: str) -> int:
    import math

    val = val.strip().lower()
    if not val:
        raise typer.BadParameter("Empty duration")

    if val.endswith("d"):
        num = val[:-1]
        try:
            days = float(num)
            if days <= 0:
                raise ValueError()
            hours = math.ceil(days * 24)
            if hours < 1:
                raise ValueError()
            return hours
        except ValueError:
            raise typer.BadParameter(
                f"Invalid format '{val}'. Expected positive number followed by 'd' (e.g. 30d)"
            )

    elif val.endswith("h"):
        num = val[:-1]
        try:
            hours = float(num)
            if hours <= 0:
                raise ValueError()
            hours_i = math.ceil(hours)
            if hours_i < 1:
                raise ValueError()
            return hours_i
        except ValueError:
            raise typer.BadParameter(
                f"Invalid format '{val}'. Expected positive number followed by 'h' (e.g. 12h)"
            )

    else:
        raise typer.BadParameter(
            f"Invalid duration format '{val}'. Must end with 'd' for days or 'h' for hours (e.g. 30d, 12h)"
        )


@session_app.command("prune")
def session_prune(
    older_than: str = typer.Option(
        ..., "--older-than", help="Age of sessions to prune (e.g. 30d, 12h)"
    ),
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview what would be deleted without taking action"
    ),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """[bold]Prune[/bold] old sessions, traces, and checkpoints to free up disk space."""

    hours = _parse_older_than_to_hours(older_than)

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        console.print(
            Panel(
                f"[bold cyan]Session Prune[/bold cyan]\n"
                f"[dim]Threshold:[/dim] older than {older_than}\n"
                f"[dim]Dry-run:[/dim]   {'Yes' if dry_run else 'No'}",
                border_style="cyan",
            )
        )

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.request(
                    "DELETE",
                    f"{api_url}/api/v1/sessions/prune",
                    params={"older_than_hours": hours, "dry_run": dry_run},
                    headers=_api_headers(api_key),
                    timeout=30.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        data = resp.json()

        table = Table(box=box.ROUNDED)
        table.add_column("Resource Type")
        table.add_column("Deleted Count", justify="right")

        table.add_row("Database Sessions", str(data.get("pruned_db_sessions", 0)))
        table.add_row("Trace Files (.json)", str(data.get("pruned_trace_files", 0)))
        table.add_row(
            "Checkpoints (Snapshots + Metadata)",
            str(data.get("pruned_checkpoint_files", 0)),
        )

        console.print(table)

        if dry_run:
            console.print(
                "\n[yellow]Dry-run complete. No files or database records were actually deleted.[/yellow]"
            )
        else:
            console.print("\n[green]Prune complete.[/green]")

    asyncio.run(_run())


# ─────────────────────────────────────────────
# compliance commands
# ---------------------------------------------


compliance_app = typer.Typer(
    name="compliance",
    help="Compliance audit log export and reporting.",
    no_args_is_help=True,
)
app.add_typer(compliance_app, hidden=True)


@compliance_app.command(name="export-csv")
def compliance_export_csv(
    output: Path = typer.Option(
        Path("compliance-audit.csv"), "--output", "-o", help="Output CSV file path"
    ),
    include_allowed: bool = typer.Option(
        False,
        "--include-allowed",
        help="Include allowed actions (denials only by default)",
    ),
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """Export compliance audit log as CSV from the running API server."""

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            console.print("[red]httpx not installed. Run: pip install httpx[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                params: dict[str, Any] = {"format": "csv"}
                if include_allowed:
                    params["include_allowed"] = "true"
                resp = await client.get(
                    f"{api_url}/api/v1/governance/compliance-report",
                    headers=_api_headers(api_key),
                    params=params,
                    timeout=10.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        with open(output, "w", encoding="utf-8", newline="") as f:
            f.write(resp.text)
        console.print(f"[green]{output} created successfully[/green]")

    asyncio.run(_run())


_DEFAULT_AUDIT_LOG_PATH = Path(os.getenv("AGENTWATCH_AUDIT_LOG_PATH", "data/audit-log.jsonl"))


@compliance_app.command(name="export-local")
def compliance_export_local(
    output: Path = typer.Option(
        Path("compliance-audit.csv"), "--output", "-o", help="Output CSV file path"
    ),
    include_allowed: bool = typer.Option(
        False,
        "--include-allowed",
        help="Include allowed actions (denials only by default)",
    ),
    audit_log: Path = typer.Option(
        _DEFAULT_AUDIT_LOG_PATH,
        "--audit-log",
        help="Path to the persisted audit log JSONL file",
    ),
) -> None:
    """Export compliance audit log from a local JSONL file as CSV."""
    from panoptis.governance.compliance_reporter import ComplianceReporter
    from panoptis.governance.engine import GovernanceEngine

    engine = GovernanceEngine(audit_log_path=audit_log)
    loaded = len(engine._audit_log)
    if loaded == 0:
        console.print(
            f"[yellow]No audit entries found in {audit_log}. "
            "The server persists audit data when AGENTWATCH_AUDIT_LOG_PATH is set.[/yellow]"
        )
        return

    reporter = ComplianceReporter(engine)
    csv_content = reporter.generate_csv(include_allowed=include_allowed)

    with open(output, "w", encoding="utf-8", newline="") as f:
        f.write(csv_content)
    # Count actual exported rows (subtract 1 for header)
    exported = max(0, csv_content.count("\n") - 1)
    console.print(f"[green]{output} created successfully ({exported} entries exported)[/green]")


# ─────────────────────────────────────────────
# Version
# ---------------------------------------------


@app.command()
def version() -> None:
    """Show AgentWatch version and diagnostics."""
    from panoptis import __version__

    table = Table(title="AgentWatch Diagnostics", box=box.ROUNDED)
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    table.add_row("Version", __version__)
    table.add_row("Python", sys.version.split()[0])
    table.add_row("Platform", platform.platform())
    table.add_row("Executable", sys.executable)

    try:
        table.add_row("Package Location", str(Path(__file__).resolve().parent.parent.parent))
    except Exception:
        table.add_row("Package Location", "N/A")

    console.print(table)


# ─────────────────────────────────────────────
# Entrypoint
# ---------------------------------------------


def main() -> None:
    for stream in (
        sys.stdout,
        sys.stderr,
    ):  # box-drawing and arrows must not crash legacy Windows consoles
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() not in (
            "utf-8",
            "utf8",
        ):
            stream.reconfigure(encoding="utf-8", errors="replace")
    app()


@app.command(name="doctor")
def doctor(
    rollback_derived: bool = typer.Option(
        False,
        "--rollback-derived",
        help="Safely roll back derived interpretation tables without touching raw evidence.",
    ),
    upgrade_schema: bool = typer.Option(
        False, "--upgrade-schema", help="Apply pending schema upgrades to target version."
    ),
    downgrade_schema: int | None = typer.Option(
        None, "--downgrade-schema", help="Roll back schema to a specific prior version (e.g., 1)."
    ),
    seal_pending: bool = typer.Option(
        False,
        "--seal-pending",
        help="Seal all pending unsealed observations into hash-chained segments.",
    ),
    test_alert: bool = typer.Option(
        False,
        "--test-alert",
        help="Send a diagnostic test alert to configured Slack/PagerDuty channels.",
    ),
) -> None:
    """[bold]Doctor[/bold]: Check AgentWatch installation health and manage schema migrations."""
    import asyncio
    import os
    import shutil
    import subprocess  # nosec B404

    from panoptis.storage.store import Store

    store = Store()

    if rollback_derived:
        from panoptis.storage.migration import rollback_derived_schema

        deleted = rollback_derived_schema(store.engine)
        console.print("[green]Derived interpretation schema rolled back successfully:[/green]")
        for tbl, count in deleted.items():
            console.print(f"  - {tbl}: {count} rows cleared")
        return

    if upgrade_schema:
        from panoptis.storage.migration import upgrade_schema as apply_upgrade

        res = apply_upgrade(store.engine)
        console.print(
            f"[green]Schema migration result:[/green] {res['status']} (v{res['to_version']})"
        )
        for act in res.get("actions", []):
            console.print(f"  - {act}")
        return

    if downgrade_schema is not None:
        from panoptis.storage.migration import downgrade_schema as apply_downgrade

        res = apply_downgrade(store.engine, target_version=downgrade_schema)
        console.print(
            f"[yellow]Schema downgrade result:[/yellow] {res['status']} (v{res['to_version']})"
        )
        for act in res.get("actions", []):
            console.print(f"  - {act}")
        return

    if seal_pending:
        sealed = store.seal_all()
        console.print(f"[green]Successfully sealed {len(sealed)} segment(s).[/green]")
        for seg in sealed:
            console.print(f"  - Segment {seg.segment_id} (seq {seg.seq}): {seg.n_obs} observations")
        return

    if test_alert:
        from panoptis.alerting.engine import AlertingConfig, AlertingEngine

        config = AlertingConfig(
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            pagerduty_webhook_url=os.getenv("PAGERDUTY_WEBHOOK_URL"),
            pagerduty_routing_key=os.getenv("PAGERDUTY_ROUTING_KEY"),
            webhook_signing_secret=os.getenv("AGENTWATCH_WEBHOOK_SIGNING_SECRET"),
        )
        engine = AlertingEngine(config)
        console.print("[cyan]Sending diagnostic test alerts...[/cyan]")
        results = asyncio.run(engine.send_test_alert())
        console.print(
            f"[green]Alert test dispatch complete:[/green] Slack: {results.get('slack')}, PagerDuty: {results.get('pagerduty')}"
        )
        return

    table = Table(title="Health Diagnostics")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="green")

    db_path = Path("agentwatch.db")
    if db_path.exists():
        table.add_row("Database", "OK")
    else:
        table.add_row("Database", "[yellow]Not initialized[/yellow]")

    if "AGENTWATCH_API_KEY" in os.environ:
        table.add_row("API Key", "Configured")
    else:
        table.add_row("API Key", "[red]Missing[/red]")

    try:
        docker_path = shutil.which("docker")
        if docker_path:
            res = subprocess.run([docker_path, "info"], capture_output=True, check=False)  # noqa: S603 # nosec B603
            if res.returncode == 0:
                table.add_row("Docker", "Running")
            else:
                table.add_row("Docker", "[red]Not running[/red]")
        else:
            table.add_row("Docker", "[red]Not installed[/red]")
    except Exception:
        table.add_row("Docker", "[red]Not installed[/red]")

    try:
        from panoptis.storage.migration import get_migration_status

        mig = get_migration_status(store.engine)
        mig_status = (
            f"[green]Up to date (v{mig.installed_version})[/green]"
            if mig.is_up_to_date
            else f"[yellow]v{mig.installed_version} (target v{mig.target_version})[/yellow]"
        )
        table.add_row("Schema Migration", mig_status)
    except Exception as exc:
        logging.getLogger(__name__).debug("Could not inspect schema migration status: %s", exc)

    from panoptis.cli.v3 import v3_health

    colors = {"OK": "green", "WARN": "yellow", "STALE": "yellow", "FAILED": "red", "ERROR": "red"}
    for component, state, detail in v3_health():
        table.add_row(component, f"[{colors.get(state, 'white')}]{state}[/] {detail}")

    console.print(table)


# ─────────────────────────────────────────────
# export-csv command
# ─────────────────────────────────────────────


@session_app.command(name="export-csv")
def export_csv(
    session_id: str = typer.Argument(..., help="ID of the session to export"),
    out: str = typer.Option("report.csv", "--out", help="Custom output file path"),
    api_url: str = typer.Option("http://localhost:8000", "--api"),
    api_key: str | None = API_KEY_OPTION,
) -> None:
    """[bold]Export-csv[/bold] a session replay to a CSV file."""

    async def _run() -> None:
        try:
            import csv

            import httpx
        except ImportError:
            console.print("[red]httpx not installed.[/red]")
            raise typer.Exit(1)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/sessions/{session_id}/replay",
                    headers=_api_headers(api_key),
                    timeout=10.0,
                )
                if resp.status_code == 404:
                    console.print(f"[red]Session {session_id} not found.[/red]")
                    raise typer.Exit(1)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_http_status_error(exc, api_url)
            except httpx.HTTPError as exc:
                console.print(f"[red]Failed to connect to API at {api_url}: {exc}[/red]")
                raise typer.Exit(1)

        try:
            data = resp.json()
            steps = data.get("steps", [])
        except (ValueError, AttributeError) as exc:
            console.print(f"[red]Unexpected API response: {exc}[/red]")
            raise typer.Exit(1)

        try:
            with open(out, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["step", "action", "status"])
                for i, step in enumerate(steps):
                    writer.writerow([i, step.get("action", ""), step.get("status", "")])
            console.print(f"[green]{out} created successfully[/green]")
        except PermissionError:
            console.print(
                f"[red]Cannot write to {out}. Please ensure the file is closed and try again.[/red]"
            )
            raise typer.Exit(1)

    asyncio.run(_run())


# v3 commands (observe, inspect, provenance, compare, …) — registered last so they are the
# canonical names; the v0.2 'compare' lives under 'panoptis legacy compare'.
_register_v3(app)

protect_app.add_typer(compliance_app, name="compliance")
protect_app.add_typer(evidence_app, name="evidence")


if __name__ == "__main__":
    main()
