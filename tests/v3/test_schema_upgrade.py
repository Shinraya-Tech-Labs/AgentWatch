"""Opening an existing store: additive upgrades from older schema versions; newer refused.

Schema v1 is v2 without aw3_data_keys and aw3_erasures (crypto-shredding tables); nothing
else changed, so a v1 store is reproduced by removing those tables and the version mark.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from panoptis import instrument as aw
from panoptis.runtime.engine import Engine
from panoptis.storage import schema
from panoptis.storage.store import Store


def _version(store: Store) -> str:
    with store.engine.connect() as conn:
        return conn.execute(
            text("SELECT value FROM aw3_meta WHERE key = 'schema_version'")
        ).scalar_one()


def _record(sink) -> None:
    @aw.tool("t")
    def t(x: str) -> str:
        return x.upper()

    with aw.run("upgrade", subject_id="alice"):
        t("hello")


def test_v1_store_is_upgraded_in_place_and_keeps_its_evidence(tmp_path, sink):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    store = Store(url)
    _record(sink)
    Engine(store).ingest(sink.drafts)
    n = store.count_observations()
    with store.engine.begin() as conn:  # make it a v1 store
        conn.execute(text("DROP TABLE aw3_data_keys"))
        conn.execute(text("DROP TABLE aw3_erasures"))
        conn.execute(text("UPDATE aw3_meta SET value = '1' WHERE key = 'schema_version'"))
    store.close()

    upgraded = Store(url)
    assert _version(upgraded) == str(schema.SCHEMA_VERSION)
    assert upgraded.count_observations() == n
    assert upgraded.verify().ok
    engine = Engine(upgraded)
    engine.process()
    assert engine.erase_subject("default", "alice", reason="test")["subject"] == "alice"
    assert upgraded.erasures("default")  # the new tables work
    upgraded.close()


def test_store_from_a_newer_panoptis_is_refused(tmp_path):
    url = f"sqlite:///{(tmp_path / 'new.db').as_posix()}"
    store = Store(url)
    with store.engine.begin() as conn:
        conn.execute(
            text("UPDATE aw3_meta SET value = :v WHERE key = 'schema_version'"),
            {"v": str(schema.SCHEMA_VERSION + 1)},
        )
    store.close()
    with pytest.raises(Exception, match="newer"):
        Store(url)
