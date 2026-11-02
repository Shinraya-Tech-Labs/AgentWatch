"""Tests for panoptis.storage.migration."""

from __future__ import annotations

from panoptis.storage.migration import (
    downgrade_schema,
    get_migration_status,
    rollback_derived_schema,
    upgrade_schema,
)
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


def test_downgrade_and_upgrade_schema(tmp_path):
    db_file = tmp_path / "test.db"
    store = Store(f"sqlite:///{db_file}")

    # Initially at v2
    init_status = get_migration_status(store.engine)
    assert init_status.installed_version == 2

    # Downgrade to v1
    down_res = downgrade_schema(store.engine, target_version=1)
    assert down_res["status"] == "downgraded"
    assert down_res["to_version"] == 1
    assert "dropped_v2_crypto_shredding_tables" in down_res["actions"]

    v1_status = get_migration_status(store.engine)
    assert v1_status.installed_version == 1
    assert v1_status.is_up_to_date is False  # Target is v2
    assert "aw3_data_keys" in v1_status.missing_tables
    assert "aw3_erasures" in v1_status.missing_tables
    assert v1_status.is_append_only_guarded is True  # Core triggers intact

    # Upgrade back to v2
    up_res = upgrade_schema(store.engine, target_version=2)
    assert up_res["status"] == "upgraded"
    assert up_res["to_version"] == 2
    assert "created_v2_crypto_shredding_tables" in up_res["actions"]

    v2_status = get_migration_status(store.engine)
    assert v2_status.installed_version == 2
    assert v2_status.is_up_to_date is True
    assert v2_status.missing_tables == []
