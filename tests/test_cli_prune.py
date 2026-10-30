from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()


def test_prune_command_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "pruned_db_sessions": 5,
        "pruned_trace_files": 5,
        "pruned_checkpoint_files": 2,
        "dry_run": False,
    }

    mock_client = AsyncMock()
    mock_client.request.return_value = mock_resp

    with patch(
        "httpx.AsyncClient",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_client), __aexit__=AsyncMock()
        ),
    ):
        result = runner.invoke(app, ["session", "prune", "--older-than", "30d"])

    assert result.exit_code == 0
    assert "Database Sessions" in result.stdout
    assert "5" in result.stdout
    assert "Prune complete" in result.stdout


def test_prune_command_dry_run():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "pruned_db_sessions": 3,
        "pruned_trace_files": 3,
        "pruned_checkpoint_files": 0,
        "dry_run": True,
    }

    mock_client = AsyncMock()
    mock_client.request.return_value = mock_resp

    with patch(
        "httpx.AsyncClient",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_client), __aexit__=AsyncMock()
        ),
    ):
        result = runner.invoke(app, ["session", "prune", "--older-than", "12h", "--dry-run"])

    assert result.exit_code == 0
    assert "Database Sessions" in result.stdout
    assert "3" in result.stdout
    assert "Dry-run complete. No files or database records were actually deleted." in result.stdout


def test_prune_command_invalid_duration():
    # Test '30x'
    result = runner.invoke(app, ["session", "prune", "--older-than", "30x"])
    assert result.exit_code != 0
    assert "Invalid duration format" in result.output

    # Test 'abc'
    result = runner.invoke(app, ["session", "prune", "--older-than", "abc"])
    assert result.exit_code != 0
    assert "Invalid duration format" in result.output

    # Test '-5d'
    result = runner.invoke(app, ["session", "prune", "--older-than", "-5d"])
    assert result.exit_code != 0
    assert "Expected positive number" in result.output


def test_prune_command_connection_error():
    mock_client = AsyncMock()
    mock_client.request.side_effect = httpx.ConnectError("Connection refused")

    with patch(
        "httpx.AsyncClient",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_client), __aexit__=AsyncMock()
        ),
    ):
        result = runner.invoke(app, ["session", "prune", "--older-than", "30d"])

    assert result.exit_code == 1
    assert "Failed to connect to API" in result.stdout
