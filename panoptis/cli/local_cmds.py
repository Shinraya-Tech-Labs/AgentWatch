"""``panoptis up`` (local API + dashboard) and ``panoptis demo`` (a separate demo workspace)."""

from __future__ import annotations

import os
import socket
import sys

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

console = Console()

demo_app = typer.Typer(
    name="demo",
    help=(
        "Sample data in a separate demo workspace (never your own). "
        "[bold]panoptis demo[/bold] seeds and serves it, [bold]--clear[/bold] removes it, "
        "[bold]demo live[/bold] runs an attack against it."
    ),
    invoke_without_command=True,
    rich_markup_mode="rich",
)


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _check_bind(host: str, port: int, what: str) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[red]{what} is local mode and binds loopback only.[/red] "
            "For a shared deployment use Docker Compose (see the README)."
        )
        raise typer.Exit(2)
    if not _port_free(host, port):
        console.print(
            f"[red]Port {port} is in use.[/red] Is PANOPTIS already running? "
            f"Open http://{host}:{port}/ or pick another port with --port."
        )
        raise typer.Exit(1)


def _serve(host: str, port: int, no_browser: bool, lines: list[str]) -> None:
    """Start the local API + dashboard in the current workspace (blocks until Ctrl+C)."""
    from panoptis import local

    for name in ("DATABASE_URL", "AGENTWATCH_CLOUD_MODE"):
        if os.environ.get(name):
            console.print(f"[yellow]{name} is set; local mode ignores it.[/yellow]")
            os.environ.pop(name, None)
    local.apply_local_env(local.load_or_create_profile())
    dash = local.dashboard_dir()
    url = f"http://{'127.0.0.1' if host == 'localhost' else host}:{port}"
    head = [
        f"[dim]Dashboard[/dim]   {url}/" if dash else "[yellow]Dashboard not built[/yellow]",
        f"[dim]API docs[/dim]    {url}/docs",
        f"[dim]Store[/dim]       {escape(str(local.panoptis_home()))} (SQLite)",
    ]
    if not dash:
        head.insert(1, "[dim]  build it: cd frontend && npm ci && npm run build:static[/dim]")
    body = "\n".join([lines[0], *head, "", *lines[1:]])
    console.print(Panel(body, border_style="cyan", padding=(1, 2)))
    from panoptis.protect.claude_hook import outdated_warnings

    for warning in outdated_warnings():
        console.print(f"[yellow]{escape(warning)}[/yellow]")
    if dash and not no_browser:
        local.open_browser_when_ready(f"{url}/", f"{url}/health")

    import uvicorn

    from panoptis.api.limits import ws_max_bytes

    marker = local.mark_running(port)
    try:
        uvicorn.run(
            "panoptis.api.server:app",
            host=host,
            port=port,
            log_level="warning",
            ws_max_size=ws_max_bytes(),
        )
    finally:
        marker.unlink(missing_ok=True)


def up(
    port: int = typer.Option(8000, "--port", "-p", help="Port for the API and dashboard."),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Interface to bind. Local mode only answers loopback."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
) -> None:
    """[bold]Start PANOPTIS on this machine[/bold]: API and dashboard on localhost, SQLite, no Docker."""
    _check_bind(host, port, "panoptis up")
    _serve(
        host,
        port,
        no_browser,
        [
            "[bold]PANOPTIS is running locally[/bold]",
            "Connect an agent: the dashboard shows the setup for Claude Code, Cursor (MCP)",
            "and Python agents, and waits for the first session. Protected agents on this",
            "machine report here with no extra setup.",
            "Sample data: [bold]panoptis demo[/bold] (a separate workspace on port 8001).",
            "Stop with Ctrl+C.",
        ],
    )


@demo_app.callback()
def demo(
    ctx: typer.Context,
    clear: bool = typer.Option(
        False, "--clear", help="Delete the demo workspace and its sample data."
    ),
    port: int = typer.Option(8001, "--port", "-p", help="Port for the demo dashboard."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
    no_serve: bool = typer.Option(
        False, "--no-serve", help="Seed the demo workspace and exit without starting a server."
    ),
) -> None:
    """Seed sample data into the demo workspace and open its dashboard."""
    from panoptis import local

    if ctx.invoked_subcommand is not None:
        return
    home = local.panoptis_home()
    if clear:
        changed, message = local.clear_demo_workspace(home)
        console.print(("[green]" if changed else "[yellow]") + escape(message) + "[/]")
        raise typer.Exit(0 if changed or "nothing to clear" in message else 1)
    if not no_serve:
        _check_bind("127.0.0.1", port, "panoptis demo")
    target = local.enter_demo_workspace(home)
    seeded = local.seed_demo_workspace()
    console.print(
        f"Demo workspace: {escape(str(target))} "
        + (f"(seeded {seeded} sample calls)" if seeded else "(sample data already there)")
    )
    if no_serve:
        return
    _serve(
        "127.0.0.1",
        port,
        no_browser,
        [
            "[bold]PANOPTIS demo workspace[/bold] [yellow](sample data, not your agents)[/yellow]",
            "Watch an attack arrive live: [bold]panoptis demo live --offline[/bold] (no key) or",
            "[bold]panoptis demo live[/bold] (OPENAI_API_KEY), in another terminal.",
            "Remove everything with [bold]panoptis demo --clear[/bold] after stopping this server.",
            "Stop with Ctrl+C.",
        ],
    )


def _verdict_style(v: str | None) -> str:
    return {
        "allow": "green",
        "block": "bold red",
        "escalate_hitl": "bold yellow",
        "mutate_and_allow": "cyan",
    }.get(v or "", "white")


_PLAIN = {
    "allow": "allowed",
    "block": "BLOCKED",
    "escalate_hitl": "HELD for approval",
    "mutate_and_allow": "allowed after rewriting",
}


@demo_app.command("live")
def demo_live(
    offline: bool = typer.Option(
        False, "--offline", help="Scripted agent instead of a model: no key, no network."
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        envvar="PANOPTIS_DEMO_MODEL",
        help="OpenAI model (default: the Agents SDK default).",
    ),
    approval_timeout: float = typer.Option(
        300.0, "--approval-timeout", help="Seconds a held call waits for a person."
    ),
    api: str = typer.Option(
        "http://127.0.0.1:8001", "--api", help="Where `panoptis demo` serves, for the links."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the outcome as JSON."),
) -> None:
    """A real OpenAI agent reads a poisoned GitHub issue; PANOPTIS blocks or holds what it tries.

    Everything is recorded in the demo workspace, never your own. The key is read from
    [bold]OPENAI_API_KEY[/bold] only. Run [bold]panoptis demo[/bold] in another terminal first
    to watch the calls arrive on the demo dashboard and approve the held one.
    """
    import httpx

    from panoptis import demo_live as d
    from panoptis import local

    local.enter_demo_workspace()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        dashboard = httpx.get(f"{api}/health", timeout=2).status_code < 500
    except httpx.HTTPError:
        dashboard = False
    console.print(
        Panel(
            "[bold]The attack[/bold]: issue #42 carries hidden instructions to upload the "
            "repository's .env and delete the project.\n"
            "[bold]The task[/bold]: fix the flaky test and force-push the fix.\n\n"
            + (
                f"Watch it live: [bold]{api}/[/bold]  (approve the held call under Approvals)"
                if dashboard
                else "[yellow]No dashboard found at "
                f"{escape(api)}[/yellow]. Start [bold]panoptis demo[/bold] in another terminal to "
                "watch live; decisions are recorded either way, and a held call can be approved "
                "with [bold]panoptis protect approvals approve <id>[/bold]."
            ),
            title="panoptis demo live" + (" --offline" if offline else ""),
            border_style="cyan",
        )
    )

    def say(text: str) -> None:
        console.print(f"  {escape(text)}")

    try:
        if offline:
            out = d.run_offline(approval_timeout_s=approval_timeout, store=None, say=say)
        else:
            out = d.run_live(model=model, approval_timeout_s=approval_timeout, store=None, say=say)
    except RuntimeError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(2) from exc
    if as_json:
        print(d.to_json(out))
        raise typer.Exit(0 if out.ok else 1)

    table = Table(title="What the agent tried, and what PANOPTIS decided", show_lines=False)
    table.add_column("tool")
    table.add_column("call")
    table.add_column("decision")
    for rec in out.decisions:
        if rec["kind"] == "decision":
            v = rec["verdict"]
            table.add_row(
                rec["tool"] or "",
                escape((rec["command"] or "")[:70]),
                f"[{_verdict_style(v)}]{_PLAIN.get(v or '', v or '')}[/]",
            )
        elif rec["sanitized"]:
            table.add_row(
                rec["tool"] or "",
                "[dim]tool output[/dim]",
                "[cyan]injected lines removed before the model read them[/]",
            )
    console.print(table)
    if out.final_output:
        console.print(Panel(escape(out.final_output[:800]), title="agent's final answer"))
    if out.error:
        hint = (
            " Check that OPENAI_API_KEY belongs to an active OpenAI account, or run "
            "`panoptis demo live --offline`."
            if "AuthenticationError" in out.error or "401" in out.error
            else ""
        )
        console.print(f"[yellow]the run ended early: {escape(out.error)}{hint}[/yellow]")
    elif not any(c.startswith("curl") for c in out.attempted) and not offline:
        console.print(
            "[dim]This run the model did not attempt the injected upload (models sometimes "
            "refuse). The offline run shows what happens when an agent does obey it.[/dim]"
        )
    for ok, claim in out.checks():
        console.print(f"  {'[green]PASS[/]' if ok else '[red]FAIL[/]'} {escape(claim)}")
    raise typer.Exit(0 if out.ok else 1)


@demo_app.command("offline")
def demo_offline(
    approval_timeout: float = typer.Option(300.0, "--approval-timeout"),
    api: str = typer.Option("http://127.0.0.1:8001", "--api"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The same attack with a scripted agent: no model, key or network (fallback)."""
    demo_live(
        offline=True,
        model=None,
        approval_timeout=approval_timeout,
        api=api,
        as_json=as_json,
    )


def register(app: typer.Typer) -> None:
    app.command(name="up")(up)
    app.add_typer(demo_app, name="demo")
