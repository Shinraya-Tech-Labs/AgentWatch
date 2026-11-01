"""Tests for CLI version flag and subcommand (Issue #149)."""

from __future__ import annotations

from typer.testing import CliRunner

from panoptis import __version__
from panoptis.cli.main import app

runner = CliRunner()


def test_version_flag_outputs_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_version_subcommand_outputs_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_version_subcommand_shows_python():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Python" in result.output


def test_version_subcommand_shows_platform():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Platform" in result.output
