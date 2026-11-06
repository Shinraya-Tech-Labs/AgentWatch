#!/usr/bin/env python3
"""Run ``live_demo.py --dashboard`` unattended, approving scene 3 in a real browser.

``--dashboard`` waits for a person to click Approve. This script is that person, so the
browser path can be reproduced from a clone::

    python examples/walkthrough/drive_dashboard.py --out <dir>

It needs a built frontend (``cd frontend && npm ci && npm run build``) and Playwright with
Chromium. It writes the demo's own ``results.json`` and transcripts to ``--out``, and exits
non-zero unless every check passed. See "Check counts" in this folder's README.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
TENANT_KEY = "walkthrough-key-acme"  # the demo's throwaway local key, see live_demo.py
EXPECTED_CHECKS = 23


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ns = ap.parse_args()
    ns.out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env |= {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    demo = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-u", str(HERE / "live_demo.py"), "--fast", "--dashboard"]
        + ["--out", str(ns.out)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    lines: list[str] = []
    found: dict[str, str] = {}

    def pump() -> None:
        assert demo.stdout is not None  # noqa: S101 - stdout=PIPE above
        for line in demo.stdout:
            lines.append(line)
            match = re.search(r"Dashboard: (http://\S+)/security", line)
            if match:
                found["web"] = match.group(1)

    threading.Thread(target=pump, daemon=True).start()
    started = time.time()
    while "web" not in found and time.time() - started < 120:
        time.sleep(0.2)
    if "web" not in found:
        demo.kill()
        print("".join(lines)[-800:])
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(found["web"] + "/security")
        page.get_by_label("API key").fill(TENANT_KEY)
        page.get_by_role("button", name="Sign in").click()
        page.get_by_text("tenant acme").wait_for()
        page.goto(found["web"] + "/security/approvals")
        page.get_by_role("button", name="Approve").first.wait_for(timeout=60_000)
        page.get_by_role("button", name="Approve").first.click()
        waited = time.time()
        while not any("checks passed" in x for x in lines) and time.time() - waited < 90:
            time.sleep(0.3)
        browser.close()

    time.sleep(1)
    assert demo.stdin is not None  # noqa: S101 - stdin=PIPE above
    demo.stdin.write("\n")
    demo.stdin.flush()
    code = demo.wait(40)
    checks = json.loads((ns.out / "results.json").read_text(encoding="utf-8"))
    total, failed = len(checks["checks"]), checks["failed"]
    print(f"exit {code}: {total - failed} of {total} checks passed")
    return 0 if code == 0 and failed == 0 and total == EXPECTED_CHECKS else 1


if __name__ == "__main__":
    raise SystemExit(main())
