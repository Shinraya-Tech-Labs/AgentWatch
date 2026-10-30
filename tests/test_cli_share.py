import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()

# A trace carrying both a structural secret (api_keys) and free-text PII
# (email/SSN) so we can assert both redaction passes ran before upload.
DUMMY_TRACE = {
    "session": {"session_id": "abc-123", "status": "success"},
    "api_keys": ["sk-live-supersecret"],
    "events": [
        {
            "tool_call": {
                "tool_name": "bash",
                "raw_command": "echo user bob@acme.com SSN 123-45-6789",
            }
        }
    ],
}


@pytest.fixture
def trace_file(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(DUMMY_TRACE), encoding="utf-8")
    return path


@pytest.fixture
def mock_post():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "url": "https://share.agentwatch.dev/t/xyz789",
            "expires_at": "2026-07-12T00:00:00Z",
        }
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__.return_value = mock_instance
        mock_client_cls.return_value = mock_instance
        yield mock_instance, mock_response


def test_share_uploads_sanitized_trace(trace_file, mock_post):
    mock_instance, _ = mock_post

    result = runner.invoke(app, ["share", str(trace_file)])
    assert result.exit_code == 0
    assert "https://share.agentwatch.dev/t/xyz789" in result.stdout

    mock_instance.post.assert_called_once()
    call = mock_instance.post.call_args
    assert call[0][0].endswith("/api/v1/share")

    uploaded = json.dumps(call[1]["json"]["trace"])
    # Structural secret dropped, and free-text PII masked.
    assert "sk-live-supersecret" not in uploaded
    assert "bob@acme.com" not in uploaded
    assert "123-45-6789" not in uploaded
    assert "[REDACTED]" in uploaded
    assert call[1]["json"]["expires_days"] == 7


def test_share_dry_run_does_not_upload(trace_file, mock_post):
    mock_instance, _ = mock_post

    result = runner.invoke(app, ["share", str(trace_file), "--dry-run"])
    assert result.exit_code == 0
    mock_instance.post.assert_not_called()
    assert "sk-live-supersecret" not in result.stdout
    assert "bob@acme.com" not in result.stdout


def test_share_custom_url(trace_file, mock_post):
    mock_instance, _ = mock_post

    result = runner.invoke(app, ["share", str(trace_file), "--share-url", "https://my.host/"])
    assert result.exit_code == 0
    assert mock_instance.post.call_args[0][0] == "https://my.host/api/v1/share"


def test_share_builds_url_from_token(trace_file, mock_post):
    mock_instance, mock_response = mock_post
    mock_response.json.return_value = {"id": "tok42"}

    result = runner.invoke(app, ["share", str(trace_file)])
    assert result.exit_code == 0
    assert "https://share.agentwatch.dev/t/tok42" in result.stdout


def test_share_missing_file():
    result = runner.invoke(app, ["share", "/no/such/trace.json"])
    assert result.exit_code == 1
    assert "File not found" in result.stdout


def test_share_upstream_error(trace_file):
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "bad gateway"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=mock_response
        )
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__.return_value = mock_instance
        mock_client_cls.return_value = mock_instance

        result = runner.invoke(app, ["share", str(trace_file)])
    assert result.exit_code == 1
    assert "Share upload failed" in result.stdout
