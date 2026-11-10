#!/usr/bin/env python3
"""AgentWatch live demo — watch it stop a dangerous agent in seconds.

Run it with no setup at all::

    pip install panoptis
    python examples/demo.py

No Docker, no ``.env``, no API keys. It uses AgentWatch's built-in safety
auditor (zero config) to score each step a simulated agent proposes, and stops
at the destructive one. This is a simulation: no command is ever executed and
no database exists. ``examples/walkthrough/live_demo.py`` runs the real pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from rich.console import Console
from rich.panel import Panel

from panoptis.core.blast_radius import BlastRadiusEstimator
from panoptis.core.safety import RiskScorer
from panoptis.core.schema import AgentEvent, EventType, RiskLevel, ToolCallData

console = Console()

# Each step is (human description, shell command). A command of None is plain
# narration with nothing to run. Step 3 is destructive: the agent "resets" an
# account by wiping a database directory. No command here is ever executed.
AgentStep = tuple[str, str | None]


async def account_reset_agent() -> AsyncIterator[AgentStep]:
    """A tiny simulated agent handling an account-reset request, step by step."""
    yield ("Read the user's account-reset request", None)
    await asyncio.sleep(0.7)
    yield (
        "Look up the user in the database",
        "psql -c \"SELECT id FROM users WHERE email = 'user@example.com'\"",
    )
    await asyncio.sleep(0.7)
    yield (
        "Reset the account by clearing the database directory",
        "sudo rm -rf /var/lib/postgresql/data",
    )
    await asyncio.sleep(0.7)
    # Only reached if the dangerous step above is NOT blocked.
    yield ("Email the user their reset confirmation", "python send_email.py --to user")


def _tool_call(command: str) -> ToolCallData:
    return ToolCallData(tool_name="shell", raw_command=command, arguments={})


def _event(command: str) -> AgentEvent:
    return AgentEvent(
        session_id="demo",
        agent_id="reset-bot",
        event_type=EventType.TOOL_CALL,
        tool_call=_tool_call(command),
    )


def _print_block(step: int, command: str, risk_score: float, reasons: list[str], radius) -> None:
    # De-duplicate reasons while preserving order.
    unique_reasons = list(dict.fromkeys(reasons))
    console.print(f"[bold red]🚫 BLOCKED Step {step}:[/bold red] [red]{command}[/red]")
    console.print(
        f"   [yellow]Risk: CRITICAL[/yellow] "
        f"[dim](score {risk_score:.2f}/1.00)[/dim]  |  "
        f"[yellow]Blast Radius: {radius.score}/100[/yellow]"
    )
    console.print(f"   [white]Reason:[/white] [dim]{'; '.join(unique_reasons)}[/dim]")
    console.print(f"   [dim]{radius.explanation}[/dim]")
    console.print(
        f"[green]✅ Step {step + 1}:[/green] "
        "[dim](never reached — AgentWatch stopped the agent here)[/dim]"
    )


def _print_summary(command: str, reason: str) -> None:
    console.print(
        Panel(
            "AgentWatch scored each step this simulated agent proposed and "
            "[bold]stopped at the destructive command[/bold].\n\n"
            f"[bold]Blocked command:[/bold] [red]{command}[/red]\n"
            f"[bold]Why:[/bold] {reason}\n\n"
            "This was a simulation: no command runs and no database exists. "
            "A real deployment is protected only for calls routed through it.",
            title="[bold green]✔ What just happened[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )


async def main() -> None:
    scorer = RiskScorer()
    blast = BlastRadiusEstimator()

    console.print(
        "\n[bold cyan]🤖 Agent running...[/bold cyan] "
        "[dim](simulated: AgentWatch scores each proposed step)[/dim]\n"
    )

    step = 0
    async for description, command in account_reset_agent():
        step += 1

        # Narration steps have nothing to execute — always safe.
        if command is None:
            console.print(f"[green]✅ Step {step}:[/green] {description}")
            continue

        risk_level, risk_score, reasons, _policies = scorer.score(_tool_call(command))
        radius = blast.estimate(_event(command))

        # AgentWatch's built-in patterns rate this action CRITICAL → block it.
        if risk_level is RiskLevel.CRITICAL:
            _print_block(step, command, risk_score, reasons, radius)
            primary_reason = list(dict.fromkeys(reasons))[0] if reasons else "high-risk operation"
            console.print()
            _print_summary(command, primary_reason)
            return

        console.print(f"[green]✅ Step {step}:[/green] {description}")

    console.print("\n[green]Agent finished — no dangerous actions detected.[/green]")


if __name__ == "__main__":
    asyncio.run(main())
