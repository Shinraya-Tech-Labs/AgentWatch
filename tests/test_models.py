"""Tests for the database models and repository."""

from __future__ import annotations

import pytest

from panoptis.core.models import EventRecord, SessionRecord, get_database_url


def test_get_database_url_from_args():
    url = get_database_url(
        host="myhost",
        port=1234,
        database="mydb",
        user="myuser",
        password="mypassword",  # noqa: S106
    )
    assert "postgresql+asyncpg://myuser:mypassword@myhost:1234/mydb" == url


def test_get_database_url_raises_without_password(monkeypatch):
    # Ensure env is clear
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("PGPASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="Database password is not configured"):
        get_database_url(password=None)


def test_session_record_instantiation():
    record = SessionRecord(session_id="s1", agent_id="a1", status="running", goal="do something")
    assert record.session_id == "s1"
    assert record.agent_id == "a1"


def test_event_record_instantiation():
    record = EventRecord(event_id="e1", session_id="s1", event_type="tool_call", step_number=1)
    assert record.event_id == "e1"
    assert record.session_id == "s1"
