from unittest.mock import patch

from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()


def test_server_start_dry_run():
    result = runner.invoke(
        app,
        ["server", "start", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "DRY-RUN MODE" in result.stdout
    assert "http://0.0.0.0:8000" in result.stdout
    assert "Dashboard" in result.stdout
    assert "disabled" in result.stdout
    assert "Server was not started." in result.stdout


def test_server_start_dry_run_custom_options():
    result = runner.invoke(
        app,
        [
            "server",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--reload",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "127.0.0.1:9000" in result.stdout
    assert "enabled" in result.stdout
    assert "--reload" in result.stdout


@patch.dict("sys.modules", {"uvicorn": None})
def test_server_start_without_uvicorn():
    result = runner.invoke(
        app,
        ["server", "start"],
    )

    assert result.exit_code == 1
    assert "uvicorn not installed" in result.stdout


@patch("uvicorn.run")
def test_server_start_success(mock_run):
    result = runner.invoke(
        app,
        ["server", "start"],
    )

    assert result.exit_code == 0
    mock_run.assert_called_once()
