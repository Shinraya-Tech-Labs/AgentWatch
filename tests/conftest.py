import datetime
import faulthandler
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

# v3 stores must never touch a developer's real ~/.agentwatch or ./data during tests
_AW3_TMP = tempfile.mkdtemp(prefix="panoptis-tests-")
os.environ["AGENTWATCH_HOME"] = _AW3_TMP
os.environ["AGENTWATCH_STORE"] = f"sqlite:///{_AW3_TMP}/agentwatch-v3.db".replace("\\", "/")
os.environ["AGENTWATCH_AUDIT_LOG_PATH"] = os.path.join(_AW3_TMP, "audit-log.jsonl")

# after the environment above: importing panoptis must see the isolated test store
import panoptis.memory.engine  # noqa: E402
import panoptis.scoring.drift  # noqa: E402

if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # noqa: UP017

panoptis.scoring.drift._st_model = panoptis.scoring.drift._ST_UNAVAILABLE


async def mock_load(self):
    self._disabled = True


panoptis.memory.engine.EmbeddingProvider._load = mock_load


@pytest.fixture(autouse=True)
def _isolated_evidence_anchors(tmp_path, monkeypatch):
    """Evidence verification keeps the last verified head on disk; never in the real home."""
    monkeypatch.setenv("AGENTWATCH_EVIDENCE_ANCHORS", str(tmp_path / "evidence-anchors"))


@pytest.fixture(autouse=True)
def _fresh_api_rate_limits():
    """Rate-limit counters of the API module are process-global; tests must not inherit
    another test's request budget (that made later tests fail with 429 by test order)."""
    import sys

    server = sys.modules.get("panoptis.api.server")
    if server is not None:
        server.reset_rate_limiter_for_tests()
        # the limits are globals too: a test that changes them must use monkeypatch
        limits = (server.RATE_WRITE, server.RATE_READ)
        assert limits == (
            int(os.getenv("API_RATE_LIMIT_WRITE", "200")),
            int(os.getenv("API_RATE_LIMIT_READ", "1000")),
        ), f"an earlier test leaked its API rate limits: {limits}"
    yield


# The application's defaults, relative to the directory the tests run from. In a checkout that
# holds real data, a test that reaches one of these has written to it.
_REAL_DATA_FILES = ("data/agentwatch-v3.db", "data/audit-log.jsonl")


def _real_data_fingerprint() -> dict[str, str | None]:
    return {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest() if Path(name).is_file() else None
        for name in _REAL_DATA_FILES
    }


def pytest_configure(config):
    # pytest captures fd 2 during tests, and a process that exits there would lose the stacks
    config._hang_report_fd = os.dup(2)
    config._real_data_before = _real_data_fingerprint()


def pytest_sessionfinish(session, exitstatus):
    """A run that changed real data fails, whatever its tests reported."""
    before = session.config._real_data_before
    after = _real_data_fingerprint()
    changed = [name for name in _REAL_DATA_FILES if before[name] != after[name]]
    if changed:
        os.write(
            session.config._hang_report_fd,
            f"\nThis test run changed real data: {', '.join(changed)}\n".encode(),
        )
        session.exitstatus = 1


def pytest_unconfigure(config):
    os.close(config._hang_report_fd)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """No test may hang a run. Past the limit, dump every thread's stack and exit non-zero: a
    stuck test fails loudly in minutes instead of holding the suite until someone looks. The
    slowest test takes about 45 seconds. Set AGENTWATCH_TEST_TIMEOUT=0 to attach a debugger."""
    limit = float(os.getenv("AGENTWATCH_TEST_TIMEOUT", "600"))
    if limit > 0:
        faulthandler.dump_traceback_later(limit, exit=True, file=item.config._hang_report_fd)
    try:
        return (yield)
    finally:
        faulthandler.cancel_dump_traceback_later()
