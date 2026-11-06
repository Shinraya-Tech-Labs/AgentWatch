"""A test that never finishes must end the run with its stack, not hold it forever."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_a_hung_test_ends_the_run_and_names_itself(tmp_path):
    (tmp_path / "test_hangs.py").write_text(
        "import threading\n\n\ndef test_that_never_finishes():\n    threading.Event().wait()\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AGENTWATCH_TEST_TIMEOUT": "3",
        "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT)]),
    }
    run = subprocess.run(  # noqa: S603 - fixed argv
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
            str(tmp_path / "test_hangs.py"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode not in (0, 5)
    assert "test_that_never_finishes" in run.stderr
    assert "Timeout" in run.stderr
