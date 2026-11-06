"""Tests must not reach the application's real stores or audit log."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ["AGENTWATCH_HOME"])


def test_the_test_home_is_a_temporary_directory():
    assert HOME.is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_the_audit_log_is_outside_the_working_directory():
    from panoptis.api import server

    assert Path(os.environ["AGENTWATCH_AUDIT_LOG_PATH"]).parent == HOME
    assert server._audit_log_path.parent == HOME


def test_the_v3_store_is_outside_the_working_directory():
    assert os.environ["AGENTWATCH_STORE"].endswith(f"{HOME.as_posix()}/agentwatch-v3.db")


def _pytest(tmp_path: Path, test_body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_writes.py").write_text(test_body, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)])}
    return subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--rootdir",
            str(tmp_path),
            "-c",
            os.devnull,
            str(tmp_path / "test_writes.py"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_run_that_writes_real_data_fails_and_names_the_file(tmp_path):
    run = _pytest(
        tmp_path,
        "from pathlib import Path\n\n\ndef test_writes():\n"
        "    Path('data').mkdir()\n"
        "    Path('data/audit-log.jsonl').write_text('x')\n",
    )
    assert run.returncode == 1
    assert "1 passed" in run.stdout
    assert "This test run changed real data: data/audit-log.jsonl" in run.stderr


def test_a_run_that_leaves_real_data_alone_passes(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "agentwatch-v3.db").write_bytes(b"real")
    run = _pytest(tmp_path, "def test_nothing():\n    pass\n")
    assert run.returncode == 0, run.stderr
