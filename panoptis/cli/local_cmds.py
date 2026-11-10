"""``panoptis up`` (local API + dashboard) and ``panoptis demo`` (live and offline)."""

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
    help="Watch PANOPTIS stop an agent that read a poisoned GitHub issue.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def up(
    port: int = typer.Option(8000, "--port", "-p", help="Port for the API and dashboard."),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Interface to bind. Local mode only answers loopback."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
    no_demo: bool = typer.Option(False, "--no-demo", help="Do not seed the labelled demo tenant."),
) -> None:
    """[bold]Start PANOPTIS on this machine[/bold]: API and dashboard on localhost, SQLite, no Docker."""
    from panoptis import local

    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            "[red]panoptis up is local mode and binds loopback only.[/red] "
            "For a shared deployment use Docker Compose (see the README)."
        )
        raise typer.Exit(2)
    if not _port_free(host, port):
        console.print(
            f"[red]Port {port} is in use.[/red] Is PANOPTIS already running? "
            f"Open http://{host}:{port}/ or pick another port with --port."
        )
        raise typer.Exit(1)
    for name in ("DATABASE_URL", "AGENTWATCH_CLOUD_MODE"):
        if os.environ.get(name):
            console.print(f"[yellow]{name} is set; local mode ignores it.[/yellow]")
            os.environ.pop(name, None)

    profile = local.load_or_create_profile()
    local.apply_local_env(profile)
    # a polling dashboard makes thousands of requests an hour; the server default is 100
    os.environ.setdefault("RATE_LIMIT_PER_USER", "50000")

    seeded = 0
    if not no_demo:
        try:
            seeded = local.seed_demo_tenant()
        except Exception as exc:  # noqa: BLE001 - the demo tenant is optional
            console.print(f"[yellow]demo tenant not seeded: {escape(str(exc))}[/yellow]")
    dash = local.dashboard_dir()
    url = f"http://{'127.0.0.1' if host == 'localhost' else host}:{port}"
    lines = [
        "[bold]PANOPTIS is running locally[/bold]",
        f"[dim]Dashboard[/dim]   {url}/" if dash else "[yellow]Dashboard not built[/yellow]",
        f"[dim]API docs[/dim]    {url}/docs",
        f"[dim]Store[/dim]       {escape(str(local.panoptis_home()))} (SQLite)",
        f"[dim]Demo tenant[/dim] {'seeded now' if seeded else 'ready'} (sample data, labelled)"
        if not no_demo
        else "[dim]Demo tenant[/dim] skipped",
        "",
        "Protected agents on this machine report here with no extra setup.",
        "Try: [bold]panoptis demo live[/bold] (needs OPENAI_API_KEY) or "
        "[bold]panoptis demo live --offline[/bold]",
        "Stop with Ctrl+C.",
    ]
    if not dash:
        lines.insert(
            2,
            "[dim]  build it: cd frontend && npm ci && npm run build:static[/dim]",
        )
    console.print(Panel("\n".join(lines), border_style="cyan", padding=(1, 2)))
    if dash and not no_browser:
        local.open_browser_when_ready(f"{url}/", f"{url}/health")

    import uvicorn

    from panoptis.api.limits import ws_max_bytes

    uvicorn.run(
        "panoptis.api.server:app",
        host=host,
        port=port,
        log_level="warning",
        ws_max_size=ws_max_bytes(),
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
        "http://127.0.0.1:8000", "--api", help="Where `panoptis up` runs, for the links."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the outcome as JSON."),
) -> None:
    """A real OpenAI agent reads a poisoned GitHub issue; PANOPTIS blocks or holds what it tries.

    The key is read from [bold]OPENAI_API_KEY[/bold] only. Run [bold]panoptis up[/bold] in another
    terminal first to watch the calls arrive on the dashboard and approve the held one.
    """
    import httpx

    from panoptis import demo_live as d

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
                f"{escape(api)}[/yellow]. Start [bold]panoptis up[/bold] in another terminal to "
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
    api: str = typer.Option("http://127.0.0.1:8000", "--api"),
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
