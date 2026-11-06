"""The presentation demo runs end to end against the real product, and every check passes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "walkthrough" / "live_demo.py"


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "DATABASE_URL"} | {
        "PYTHONIOENCODING": "utf-8"
    }


def test_preflight_passes_and_starts_nothing(tmp_path):
    out = tmp_path / "out"
    run = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(DEMO), "--check", "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_env(),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert not out.exists()


def test_every_scene_passes_its_own_checks(tmp_path):
    run = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(DEMO), "--fast", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=_env(),
    )
    assert run.returncode == 0, run.stdout + run.stderr
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert results["failed"] == 0
    assert len(results["checks"]) == 24  # see "Check counts" in examples/walkthrough/README.md
    assert results["failed"] == 0
    assert len({c["scene"] for c in results["checks"]}) == 7
    assert (tmp_path / "transcript.svg").exists()
