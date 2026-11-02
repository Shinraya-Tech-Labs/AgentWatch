from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()


def test_watch_dry_run():
    result = runner.invoke(
        app,
        ["session", "watch", "hello", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "DRY-RUN MODE" in result.stdout
    assert "Prompt:" in result.stdout
    assert "hello" in result.stdout
    assert "Model:" in result.stdout
    assert "claude-opus-4-5" in result.stdout
    assert "Safety:" in result.stdout
    assert "on" in result.stdout
    assert "Nothing was executed or written." in result.stdout


def test_watch_dry_run_no_safety():
    result = runner.invoke(
        app,
        ["session", "watch", "hello", "--dry-run", "--no-safety"],
    )

    assert result.exit_code == 0
    assert "DRY-RUN MODE" in result.stdout
    assert "DISABLED" in result.stdout
    assert "Safety:" in result.stdout
    assert "off" in result.stdout


def test_watch_dry_run_output_file(tmp_path):
    output = tmp_path / "session.json"

    result = runner.invoke(
        app,
        [
            "session",
            "watch",
            "hello",
            "--dry-run",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert "Path:" in result.stdout
    assert "Would save session to file" in result.stdout
    assert output.name in result.stdout.replace("\n", "")  # Rich wraps long paths
