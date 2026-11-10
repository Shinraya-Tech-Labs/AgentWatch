"""
PANOPTIS init command - Project initialization
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


def init_cmd(
    project_path: str = ".",
    force: bool = False,
    dry_run: bool = False,
):
    """
    Initialize PANOPTIS in a project.
    """
    base_path = Path(project_path).resolve()
    # Validate path exists and is a directory
    if not base_path.exists():
        console.print(f"[red]❌ Error: Path '{base_path}' does not exist[/red]")
        raise typer.Exit(1)

    if not base_path.is_dir():
        console.print(f"[red]❌ Error: '{base_path}' is not a directory[/red]")
        raise typer.Exit(1)
    console.print(Panel.fit("[bold blue]🚀 PANOPTIS Init[/bold blue]", border_style="blue"))

    console.print(f"📁 Project: {base_path}")

    if dry_run:
        console.print("[yellow]🔍 DRY RUN - No files will be created[/yellow]")

    failed_steps = []

    # Step 1: Create panoptis.toml
    config_path = base_path / "panoptis.toml"
    if config_path.exists() and not force:
        console.print("[yellow]⚠️ panoptis.toml already exists (use --force to overwrite)[/yellow]")
        failed_steps.append("panoptis.toml already exists")
    else:
        if not dry_run:
            with open(config_path, "w") as f:
                f.write(CONFIG_TEMPLATE)
            console.print("✅ Created panoptis.toml")

    # Step 2: Update .gitignore
    gitignore_path = base_path / ".gitignore"
    if not dry_run:
        try:
            with open(gitignore_path, "a") as f:
                f.write("\n# PANOPTIS\npanoptis_data/\n*.awlog\n")
            console.print("✅ Updated .gitignore")
        except FileNotFoundError:
            with open(gitignore_path, "w") as f:
                f.write("# PANOPTIS\npanoptis_data/\n*.awlog\n")
            console.print("✅ Created .gitignore")

    # Step 3: Check .env for API keys
    env_path = base_path / ".env"
    if env_path.exists():
        with open(env_path) as f:
            content = f.read()
            if "OPENAI_API_KEY" in content:
                console.print("✅ OPENAI_API_KEY found")
            else:
                console.print("[yellow]⚠️ OPENAI_API_KEY not found in .env[/yellow]")
                failed_steps.append("OPENAI_API_KEY not found")
    else:
        console.print("[yellow]⚠️ .env file not found[/yellow]")
        failed_steps.append(".env not found")

    # Step 4: Create data directory
    data_dir = base_path / "panoptis_data"
    if data_dir.exists() and not force:
        console.print("[yellow]⚠️ panoptis_data already exists[/yellow]")
    else:
        if not dry_run:
            data_dir.mkdir(exist_ok=True)
            console.print("✅ Created panoptis_data directory")

    if failed_steps:
        console.print("[bold yellow]⚠️ PANOPTIS initialized with warnings![/bold yellow]")
        console.print(f"[yellow]Failed steps: {', '.join(failed_steps)}[/yellow]")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            "[bold green]✅ PANOPTIS initialized successfully![/bold green]", border_style="green"
        )
    )
    console.print("📝 Next steps:")
    console.print("  1. Review panoptis.toml")
    console.print("  2. Set API keys in .env")
    console.print("  3. Run panoptis watch")


# Template for panoptis.toml
CONFIG_TEMPLATE = """
[panoptis]
name = "my-agent"
version = "0.1.0"

[observability]
enabled = true
provider = "otel"
endpoint = "http://localhost:4318"

[security]
enable_redaction = true
enable_safety = true

[llm]
default_provider = "openai"
default_model = "gpt-4o-mini"

[storage]
type = "sqlite"
path = "panoptis_data/agentwatch.db"
"""
