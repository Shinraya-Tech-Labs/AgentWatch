"""Unit tests for the verify-env CLI command."""

from __future__ import annotations

from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()


def test_verify_env_cli():
    result = runner.invoke(app, ["check-env"])
    assert result.exit_code == 0
    assert "PANOPTIS Environment Diagnostics" in result.stdout
    assert "Python Runtime" in result.stdout
    assert "Core Dependency" in result.stdout
