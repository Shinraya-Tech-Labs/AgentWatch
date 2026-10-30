from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()

DUMMY_CONF_1 = {
    "session_id": "session-1",
    "overall_score": 0.74,
    "goal_alignment": 0.68,
    "consistency_score": 0.7,
    "anomaly_flags": ["hallucinated_success"],
    "hallucination_risk": "HIGH",
    "explanation": "Test explanation",
    "component_scores": {},
}

DUMMY_CONF_2 = {
    "session_id": "session-2",
    "overall_score": 0.91,
    "goal_alignment": 0.93,
    "consistency_score": 0.9,
    "anomaly_flags": [],
    "hallucination_risk": "LOW",
    "explanation": "Test explanation",
    "component_scores": {},
}

DUMMY_REPLAY_1 = {
    "session": {"session_id": "session-1"},
    "steps": [
        {"event": {"event_type": "TOOL_ERROR"}},
        {"event": {"event_type": "TOOL_ERROR"}},
        {"event": {"event_type": "TOOL_ERROR"}},
        {"event": {"event_type": "SAFETY_BLOCK"}},
        {"event": {"event_type": "SAFETY_BLOCK"}},
    ],
}

DUMMY_REPLAY_2 = {
    "session": {"session_id": "session-2"},
    "steps": [
        {"event": {"event_type": "TOOL_RESULT", "status": "success"}},
    ],
}


@pytest.fixture
def mock_httpx_client():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()

        def mock_get(url, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = 200

            if url.endswith("/session-1/confidence"):
                mock_response.json.return_value = DUMMY_CONF_1
            elif url.endswith("/session-2/confidence"):
                mock_response.json.return_value = DUMMY_CONF_2
            elif url.endswith("/session-1/replay"):
                mock_response.json.return_value = DUMMY_REPLAY_1
            elif url.endswith("/session-2/replay"):
                mock_response.json.return_value = DUMMY_REPLAY_2
            else:
                mock_response.status_code = 404

            if mock_response.status_code >= 400:
                req = httpx.Request("GET", url)
                resp = httpx.Response(mock_response.status_code, request=req)
                mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "mocked status error", request=req, response=resp
                )
            else:
                mock_response.raise_for_status.return_value = None

            return mock_response

        mock_instance.get.side_effect = mock_get

        mock_instance.__aenter__.return_value = mock_instance
        mock_client_cls.return_value = mock_instance
        yield mock_instance


def test_compare_success(mock_httpx_client):
    result = runner.invoke(app, ["legacy", "compare", "session-1", "session-2"])
    assert result.exit_code == 0
    assert "Overall Confidence" in result.stdout
    assert "0.74" in result.stdout
    assert "0.91" in result.stdout
    assert "Hallucination Risk" in result.stdout
    assert "HIGH" in result.stdout
    assert "LOW" in result.stdout
    assert "Failed Steps" in result.stdout
    assert "3" in result.stdout
    assert "Safety Blocks" in result.stdout
    assert "2" in result.stdout

    assert "Improvement Summary" in result.stdout
    assert "Confidence Increase:" in result.stdout
    assert "+23%" in result.stdout
    assert "Improved" in result.stdout
    assert "Session B" in result.stdout


def test_compare_not_found(mock_httpx_client):
    result = runner.invoke(app, ["legacy", "compare", "invalid-1", "session-2"])
    assert result.exit_code == 1
    assert "not found or has no confidence data" in result.stdout
