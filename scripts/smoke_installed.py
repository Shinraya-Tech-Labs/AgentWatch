"""Smoke test of an installed AgentWatch (run with the installed interpreter, outside the repo).

Checks that both shipped packages import, the console scripts run, and the core
protection path works end to end from the installed files: a tool is protected, a
dangerous call is blocked before it runs, the evidence is recorded and verifies, and the
standalone verifier is present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    import panopti  # noqa: F401
    import panoptis
    from panoptis.protect import ActionBlocked, ProtectionPipeline, ProtectLedger, protect_tool

    here = Path(panoptis.__file__).resolve()
    assert "site-packages" in str(here), f"not the installed package: {here}"
    tmp = Path(tempfile.mkdtemp())
    lg = ProtectLedger(f"sqlite:///{(tmp / 'e.db').as_posix()}", seal_every=0)
    p = ProtectionPipeline(ledger=lg)
    ran = []

    @protect_tool(pipeline=p, agent_id="smoke", session_id="s")
    def bash(command: str) -> str:
        ran.append(command)
        return "ok"

    assert bash("echo hi") == "ok"
    try:
        bash("rm -rf /")
        raise AssertionError("a dangerous call was not blocked")
    except ActionBlocked:
        pass
    assert ran == ["echo hi"]
    report = lg.verify(anchor=False)
    assert report["ok"], report
    verifier = here.parent / "evidence" / "verifier.py"
    assert verifier.is_file()
    for script in ("panoptis", "panopti"):
        exe = shutil.which(script, path=str(Path(sys.executable).parent))
        assert exe, f"console script {script} missing"
    out = subprocess.run([shutil.which("panoptis", path=str(Path(sys.executable).parent)), "--version"], capture_output=True, text=True, timeout=120)  # noqa: S603
    assert out.returncode == 0, out.stderr
    print(json.dumps({"ok": True, "version": getattr(panoptis, "__version__", "?"), "evidence": {"segments": report["segments"], "observations": report["observations"]}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
