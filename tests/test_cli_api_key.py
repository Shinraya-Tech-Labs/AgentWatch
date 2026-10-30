"""CLI API-key header support (issue #358).

Verifies that the API-backed commands send the X-Api-Key header from the
--api-key flag or the AGENTWATCH_API_KEY env var, omit it when no key is set,
and report a clear message on 401 Unauthorized.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from panoptis.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_api_key_env(monkeypatch):
    """Isolate tests from a real AGENTWATCH_API_KEY in the environment."""
    monkeypatch.delenv("AGENTWATCH_API_KEY", raising=False)


def _sessions_resp():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    resp.json.return_value = {"sessions": []}
    return resp


def _patch_client(resp):
    """Return (mock_client, patch context) capturing .get() calls."""
    mock_client = AsyncMock()
    mock_client.get.return_value = resp
    ctx = patch(
        "httpx.AsyncClient",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_client),
            __aexit__=AsyncMock(return_value=False),
        ),
    )
    return mock_client, ctx


def _sent_headers(mock_client):
    return mock_client.get.call_args.kwargs["headers"]


def test_sessions_sends_api_key_from_flag():
    mock_client, ctx = _patch_client(_sessions_resp())
    with ctx:
        result = runner.invoke(app, ["session", "list", "--api-key", "secret-key"])
    assert result.exit_code == 0
    assert _sent_headers(mock_client) == {"X-Api-Key": "secret-key"}


def test_sessions_sends_api_key_from_env():
    mock_client, ctx = _patch_client(_sessions_resp())
    with ctx:
        result = runner.invoke(app, ["session", "list"], env={"AGENTWATCH_API_KEY": "env-key"})
    assert result.exit_code == 0
    assert _sent_headers(mock_client) == {"X-Api-Key": "env-key"}


def test_sessions_omits_header_when_no_key():
    mock_client, ctx = _patch_client(_sessions_resp())
    with ctx:
        result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 0
    assert _sent_headers(mock_client) == {}


def test_flag_overrides_env():
    mock_client, ctx = _patch_client(_sessions_resp())
    with ctx:
        result = runner.invoke(
            app, ["session", "list", "--api-key", "flag-key"], env={"AGENTWATCH_API_KEY": "env-key"}
        )
    assert result.exit_code == 0
    assert _sent_headers(mock_client) == {"X-Api-Key": "flag-key"}


def test_status_401_reports_auth_error():
    request = httpx.Request("GET", "http://localhost:8000/api/v1/dashboard/summary")
    response = httpx.Response(401, request=request)
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unauthorized", request=request, response=response
    )
    mock_client, ctx = _patch_client(resp)
    with ctx:
        result = runner.invoke(app, ["server", "status"])
    assert result.exit_code == 1
    assert "Authentication failed" in result.stdout
    assert "AGENTWATCH_API_KEY" in result.stdout


def test_export_sends_api_key_from_flag(tmp_path):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    resp.json.return_value = {"session": {}, "steps": []}
    mock_client, ctx = _patch_client(resp)
    out = tmp_path / "session.json"
    with ctx:
        result = runner.invoke(
            app, ["session", "export", "sess1", "--api-key", "exp-key", "--output", str(out)]
        )
    assert result.exit_code == 0
    assert _sent_headers(mock_client) == {"X-Api-Key": "exp-key"}


def test_sessions_401_uses_shared_handler():
    request = httpx.Request("GET", "http://localhost:8000/api/v1/sessions")
    response = httpx.Response(401, request=request)
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unauthorized", request=request, response=response
    )
    mock_client, ctx = _patch_client(resp)
    with ctx:
        result = runner.invoke(app, ["session", "list", "--api-key", "bad"])
    assert result.exit_code == 1
    assert "Authentication failed" in result.stdout
    assert "AGENTWATCH_API_KEY" in result.stdout


def test_session_prune_sends_api_key_from_flag():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.status_code = 200
    resp.json.return_value = {
        "pruned_db_sessions": 0,
        "pruned_trace_files": 0,
        "pruned_checkpoint_files": 0,
    }
    mock_client = AsyncMock()
    mock_client.request.return_value = resp
    ctx = patch(
        "httpx.AsyncClient",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_client),
            __aexit__=AsyncMock(return_value=False),
        ),
    )
    with ctx:
        result = runner.invoke(
            app, ["session", "prune", "--older-than", "30d", "--api-key", "prune-key"]
        )
    assert result.exit_code == 0
    assert mock_client.request.call_args.kwargs["headers"] == {"X-Api-Key": "prune-key"}


def test_compare_sends_api_key_on_all_requests():
    conf = MagicMock()
    conf.raise_for_status.return_value = None
    conf.status_code = 200
    conf.json.return_value = {"overall_score": 0.5, "goal_alignment": 0.5}
    rep = MagicMock()
    rep.raise_for_status.return_value = None
    rep.status_code = 200
    rep.json.return_value = {"steps": []}

    mock_client = AsyncMock()
    # confidence x2 then replay x2
    mock_client.get.side_effect = [conf, conf, rep, rep]
    ctx = patch(
        "httpx.AsyncClient",
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_client),
            __aexit__=AsyncMock(return_value=False),
        ),
    )
    with ctx:
        result = runner.invoke(app, ["legacy", "compare", "s1", "s2", "--api-key", "k"])
    assert result.exit_code == 0
    assert mock_client.get.call_count == 4
    for call in mock_client.get.call_args_list:
        assert call.kwargs["headers"] == {"X-Api-Key": "k"}
