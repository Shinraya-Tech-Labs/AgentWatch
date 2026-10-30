"""API key checks use a constant-time comparison and keep their accept/reject behaviour."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from panoptis.api import server


def test_key_matches_only_the_exact_key(monkeypatch):
    monkeypatch.setattr(server, "_API_KEY", "s3cret-key")
    assert server._key_matches("s3cret-key")
    assert not server._key_matches("s3cret-kez")
    assert not server._key_matches("s3cret")
    assert not server._key_matches("")
    assert not server._key_matches(None)


def test_no_configured_key_matches_nothing(monkeypatch):
    monkeypatch.setattr(server, "_API_KEY", None)
    assert not server._key_matches("anything")


def test_require_api_key_rejects_a_wrong_key(monkeypatch):
    monkeypatch.setattr(server, "_API_KEY", "s3cret-key")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    server._require_api_key("s3cret-key")  # accepted
    with pytest.raises(HTTPException) as exc:
        server._require_api_key("wrong")
    assert exc.value.status_code == 401
