"""Tests for panoptis.storage.migration."""

from __future__ import annotations

from panoptis.storage.migration import get_migration_status, rollback_derived_schema
from panoptis.storage.store import Store


def test_migration_status_reports_up_to_date(tmp_path):
    db_file = tmp_path / "test.db"
    store = Store(f"sqlite:///{db_file}")

    status = get_migration_status(store.engine)
    assert status.is_up_to_date is True
    assert status.installed_version == 2
    assert status.missing_tables == []
    assert status.is_append_only_guarded is True


def test_rollback_derived_schema(tmp_path):
    db_file = tmp_path / "test.db"
    store = Store(f"sqlite:///{db_file}")

    # Rollback derived interpretations
    counts = rollback_derived_schema(store.engine)
    assert isinstance(counts, dict)
    assert "aw3_runs" in counts
    assert "aw3_derived" in counts
