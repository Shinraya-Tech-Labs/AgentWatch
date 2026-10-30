"""Large payloads (> INLINE_PAYLOAD_LIMIT) must persist as long as the observations that
reference them. Regression: a server-database store without AGENTWATCH_BLOB_DIR kept them
in memory, so they were lost when the process exited."""

from __future__ import annotations

from pathlib import Path

from panoptis.evidence.blobs import FsBlobStore, MemoryBlobStore
from panoptis.storage.store import panoptis_home, default_blob_store

PG = "postgresql+psycopg://u:p@db:5432/aw"


def test_server_database_never_gets_memory_blobs(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTWATCH_BLOB_DIR", raising=False)
    monkeypatch.setenv("AGENTWATCH_HOME", str(tmp_path))
    blobs = default_blob_store(PG)
    assert isinstance(blobs, FsBlobStore)
    assert Path(blobs.root) == tmp_path / "blobs"


def test_blob_dir_setting_wins_for_every_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_BLOB_DIR", str(tmp_path / "shared"))
    for url in (PG, f"sqlite:///{(tmp_path / 'aw.db').as_posix()}"):
        assert Path(default_blob_store(url).root) == tmp_path / "shared"
    assert Path(default_blob_store(PG, tmp_path / "explicit").root) == tmp_path / "explicit"


def test_sqlite_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTWATCH_BLOB_DIR", raising=False)
    db = tmp_path / "aw.db"
    assert Path(default_blob_store(f"sqlite:///{db.as_posix()}").root) == tmp_path / "blobs"
    assert isinstance(default_blob_store("sqlite:///:memory:"), MemoryBlobStore)


def test_empty_home_means_default_not_current_directory(monkeypatch):
    monkeypatch.setenv("AGENTWATCH_HOME", "")
    assert panoptis_home() == Path.home() / ".agentwatch"
