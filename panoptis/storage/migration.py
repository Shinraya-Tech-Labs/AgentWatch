"""Database migration and rollback manager for AgentWatch persistent stores.

Provides schema verification, migration state reporting, and safe rollback
tooling for both SQLite and PostgreSQL backends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, inspect, select, text

from panoptis.storage import schema as s

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationStatus:
    """Current schema migration state."""

    installed_version: int | None
    target_version: int
    is_up_to_date: bool
    missing_tables: list[str]
    is_append_only_guarded: bool


def get_migration_status(engine: Engine) -> MigrationStatus:
    """Inspect the database schema and determine current migration state."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    expected_tables = {t.name for t in s.metadata.sorted_tables}

    missing = sorted(expected_tables - existing_tables)

    installed_version: int | None = None
    if s.meta_table.name in existing_tables:
        with engine.connect() as conn:
            val = conn.execute(
                select(s.meta_table.c.value).where(s.meta_table.c.key == "schema_version")
            ).scalar()
            if val is not None:
                try:
                    installed_version = int(val)
                except ValueError:
                    installed_version = None

    # Check append-only triggers guard
    is_sqlite = engine.dialect.name == "sqlite"
    triggers_active = False
    with engine.connect() as conn:
        if is_sqlite:
            # Check sqlite_master for trigger
            trig = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name='aw3_obs_no_update'"
                )
            ).scalar()
            triggers_active = trig is not None
        else:
            # Check pg_trigger
            trig = conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE tgname='aw3_obs_guard_t'")
            ).scalar()
            triggers_active = trig is not None

    up_to_date = (installed_version == s.SCHEMA_VERSION) and (len(missing) == 0)

    return MigrationStatus(
        installed_version=installed_version,
        target_version=s.SCHEMA_VERSION,
        is_up_to_date=up_to_date,
        missing_tables=missing,
        is_append_only_guarded=triggers_active,
    )


def rollback_derived_schema(engine: Engine, interp_id: str | None = None) -> dict[str, Any]:
    """Roll back derived interpretation tables without destroying immutable evidence.

    If an experimental interpretation or derived model failed during a release,
    this flushes interpretations/derived rows so the engine can safely reprocess
    from root evidence without dropping the whole database.
    """
    affected_tables = [
        s.derived,
        s.relation_members,
        s.relations,
        s.runs,
        s.entities,
    ]

    deleted_counts: dict[str, int] = {}
    with engine.begin() as conn:
        for tbl in affected_tables:
            stmt = tbl.delete()
            if interp_id:
                stmt = stmt.where(tbl.c.interp_id == interp_id)
            res = conn.execute(stmt)
            deleted_counts[tbl.name] = res.rowcount

    logger.info("Rolled back derived interpretation schema: %s", deleted_counts)
    return deleted_counts
