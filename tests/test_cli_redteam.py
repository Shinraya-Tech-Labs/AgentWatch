from unittest.mock import patch

from typer.testing import CliRunner

from panoptis.cli.main import app
from panoptis.security.owasp import OwaspVector
from panoptis.security.redteam import (
    AttackCategory,
    AttackResult,
    AttackScenario,
    ResilienceReport,
)

runner = CliRunner()


def make_report(defended: bool = True) -> ResilienceReport:
    scenario = AttackScenario(
        id="prompt-1",
        category=AttackCategory.PROMPT_INJECTION,
        vector=OwaspVector.PROMPT_INJECTION,
        payload="Ignore previous instructions",
        description="Prompt injection test",
    )

    result = AttackResult(
        scenario=scenario,
        defended=defended,
        detail="blocked" if defended else "missed",
    )

    return ResilienceReport([result])


@patch("panoptis.cli.main.console.print_json")
@patch("panoptis.security.redteam.RedTeamHarness.run")
def test_redteam_json(mock_run, mock_print_json):
    mock_run.return_value = make_report()

    result = runner.invoke(app, ["safety", "redteam", "--json"])

    assert result.exit_code == 0

    mock_run.assert_called_once()
    mock_print_json.assert_called_once()

    printed = mock_print_json.call_args.kwargs["data"]

    assert printed["resilience_score"] == 1.0
    assert printed["defended"] == 1
    assert printed["total"] == 1


@patch("panoptis.security.redteam.RedTeamHarness.run")
def test_redteam_success(mock_run):
    mock_run.return_value = make_report()

    result = runner.invoke(app, ["safety", "redteam"])

    assert result.exit_code == 0

    assert "Red-Team Resilience" in result.stdout
    assert "100%" in result.stdout
    assert "Defended: 1/1 attacks" in result.stdout

    assert "Scenario" in result.stdout
    assert "Category" in result.stdout
    assert "prompt-1" in result.stdout
    assert "prompt_injection" in result.stdout


@patch("panoptis.security.redteam.RedTeamHarness.run")
def test_redteam_bypassed(mock_run):
    mock_run.return_value = make_report(defended=False)

    result = runner.invoke(app, ["safety", "redteam"])

    assert result.exit_code == 0

    assert "0%" in result.stdout
    assert "Defended: 0/1 attacks" in result.stdout
    assert "bypassed defenses" in result.stdout
