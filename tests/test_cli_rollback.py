import re
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from panoptis.cli.main import app
from panoptis.rollback.engine import RollbackResult, RollbackStatus

runner = CliRunner()


@patch(
    "panoptis.rollback.engine.RollbackEngine.rollback_session",
    new_callable=AsyncMock,
)
def test_rollback_success(mock_rollback):
    """Test successful session rollback to a specific step."""
    mock_rollback.return_value = RollbackResult(
        checkpoint_id="mock_ckpt",
        status=RollbackStatus.COMPLETED,
        rolled_back_files=["file1.txt", "file2.txt"],
        rolled_back_git_ref="abcdef12",
        error=None,
    )

    result = runner.invoke(app, ["session", "rollback", "test_session", "--to-step", "5"])

    assert result.exit_code == 0
    assert "Rollback complete" in result.output
    assert "Restored 2 files" in result.output
    assert "Rolled back git to abcdef12" in result.output

    mock_rollback.assert_called_once_with("test_session", to_step=5)


@patch(
    "panoptis.rollback.engine.RollbackEngine.rollback_session",
    new_callable=AsyncMock,
)
def test_rollback_failure(mock_rollback):
    """Test session rollback failure due to missing checkpoints."""
    mock_rollback.return_value = RollbackResult(
        checkpoint_id="mock_ckpt",
        status=RollbackStatus.FAILED,
        rolled_back_files=[],
        rolled_back_git_ref=None,
        error="Checkpoints missing",
    )

    result = runner.invoke(app, ["session", "rollback", "test_session", "--to-step", "5"])

    assert result.exit_code == 1
    assert "Rollback failed" in result.output
    assert "Checkpoints missing" in result.output


def test_rollback_missing_to_step():
    """Test validation error when --to-step is missing."""
    result = runner.invoke(app, ["session", "rollback", "test_session"])
    assert result.exit_code != 0
    clean_output = re.sub(r"\x1b\[.*?m", "", result.output)
    assert "--to-step" in clean_output


@patch(
    "panoptis.rollback.engine.RollbackEngine.rollback_session",
    new_callable=AsyncMock,
)
def test_rollback_to_step_zero(mock_rollback):
    """Test session rollback handles step 0 correctly."""
    mock_rollback.return_value = RollbackResult(
        checkpoint_id="mock_ckpt",
        status=RollbackStatus.COMPLETED,
        rolled_back_files=[],
        rolled_back_git_ref=None,
        error=None,
    )

    result = runner.invoke(app, ["session", "rollback", "test_session", "--to-step", "0"])

    assert result.exit_code == 0
    mock_rollback.assert_called_once_with("test_session", to_step=0)
