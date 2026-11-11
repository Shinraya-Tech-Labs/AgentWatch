"""Unit tests for panopti demo command and ActiveGuard simulation."""

from __future__ import annotations

import io
from unittest.mock import patch

from panopti.cli import main
from panopti.demo import ActiveGuard, run_interactive_demo


def test_active_guard_nominal():
    guard = ActiveGuard()
    verdict = guard.evaluate_intent(
        tool_name="database_query",
        arguments={"query": "SELECT * FROM users;"},
        current_confidence=0.99,
    )
    assert verdict.allowed is True
    assert guard.circuit_breaker_state == "CLOSED"
    assert verdict.risk_level == "LOW"


def test_active_guard_destructive_intercept():
    guard = ActiveGuard()
    verdict = guard.evaluate_intent(
        tool_name="database_query",
        arguments={"query": "DROP TABLE users;"},
        current_confidence=0.95,
    )
    assert verdict.allowed is False
    assert guard.circuit_breaker_state == "OPEN"
    assert "Destructive SQL invariant" in verdict.reason
    assert "DROP" in verdict.reason
    assert verdict.risk_level == "CRITICAL"


def test_run_interactive_demo_fast():
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        code = run_interactive_demo(fast=True, no_color=True)
    assert code == 0
    output = buf.getvalue()
    assert "PANOPTI SCRIPTED ILLUSTRATION" in output
    assert "DROP TABLE users;" in output
    assert "PANOPTI INTERCEPT TRIGGERED" in output
    assert "OUTCOME: SIMULATED COMMAND VETOED, NO DATABASE INVOLVED" in output


def test_cli_demo_subcommand():
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        code = main(["demo", "--fast", "--no-color"])
    assert code == 0
    output = buf.getvalue()
    assert "PANOPTI SCRIPTED ILLUSTRATION" in output
    assert "WITNESS RECORD SIGNED WITH AN EPHEMERAL DEMO KEY" in output
