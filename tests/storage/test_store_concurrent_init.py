"""A brand-new SQLite store opened by many threads and processes at once.

The dashboard's first load opened three stores on one fresh file in parallel (decisions,
approvals, controls); each ran `create_all` and switched the file to WAL on every new
connection. On Windows that answered "table aw3_observations already exists" and
"database is locked". These tests open a fresh file concurrently and expect every opener
to succeed.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from panoptis.storage.store import Store

ROOT = Path(__file__).resolve().parents[2]


def _url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_many_threads_initialise_one_fresh_file(tmp_path):
    url = _url(tmp_path / "fresh.db")
    n = 12
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []
    stores: list[Store] = []
    lock = threading.Lock()

    def open_and_use() -> None:
        try:
            barrier.wait(10)
            st = Store(url)
            st.count_observations("default")  # a read on the new connection
            with lock:
                stores.append(st)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=open_and_use) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    try:
        assert errors == [], errors[:3]
        assert len(stores) == n
    finally:
        for st in stores:
            st.close()


_OPENER = textwrap.dedent(
    """
    import sys, time
    from panoptis.storage.store import Store
    start = float(sys.argv[2])
    while time.time() < start:
        time.sleep(0.001)
    st = Store(sys.argv[1])
    st.count_observations("default")
    st.close()
    print("ok")
    """
)


def test_several_processes_initialise_one_fresh_file(tmp_path):
    """An agent and `panoptis up` can both be the first to open the store."""
    import time

    url = _url(tmp_path / "fresh.db")
    start = str(time.time() + 3.0)
    procs = [
        subprocess.Popen(  # noqa: S603 - fixed argv
            [sys.executable, "-c", _OPENER, url, start],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
        )
        for _ in range(4)
    ]
    results = [p.communicate(timeout=120) for p in procs]
    failures = [err[-600:] for p, (out, err) in zip(procs, results) if p.returncode != 0]
    assert failures == [], failures


def test_wal_is_set_once_and_every_connection_waits_for_locks(tmp_path):
    st = Store(_url(tmp_path / "fresh.db"))
    try:
        with st.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30_000
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        st.close()


def test_an_in_memory_store_still_works(tmp_path):
    st = Store("sqlite:///:memory:")
    try:
        assert st.count_observations("default") == 0
    finally:
        st.close()


@pytest.mark.parametrize("schema_version_ok", [True])
def test_reopening_an_initialised_store_does_not_rewrite_it(tmp_path, schema_version_ok):
    url = _url(tmp_path / "fresh.db")
    Store(url).close()
    st = Store(url)
    try:
        assert st.count_observations("default") == 0
    finally:
        st.close()


def test_the_api_reads_decisions_approvals_and_controls_from_one_store(monkeypatch, tmp_path):
    """One store per API process: three openers on a fresh file was the first-load race."""
    from panoptis.api import protect_api, protect_approvals, protect_control

    monkeypatch.setenv("AGENTWATCH_STORE", _url(tmp_path / "api.db"))
    for mod, setter in (
        (protect_api, "set_ledger"),
        (protect_approvals, "set_broker"),
        (protect_control, "set_control"),
    ):
        getattr(mod, setter)(None)
    try:
        ledger = protect_api.get_ledger()
        assert protect_approvals.get_broker().ledger is ledger
        assert protect_control.get_control().ledger is ledger
    finally:
        protect_api.get_ledger().store.close()
        protect_api.set_ledger(None)
        protect_approvals.set_broker(None)
        protect_control.set_control(None)
