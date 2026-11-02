"""TO-012: a real Claude Code CLI run, with the AgentWatch PreToolUse hook installed.

Opt-in; it runs the ``claude`` CLI, which uses the signed-in account:

    AGENTWATCH_E2E_CLAUDE=1 pytest tests/e2e_claude -m external -v

The tool is harmless: a Bash command that appends one line to a marker file in a
temporary project. An RBAC policy decides the outcome, so the same command is denied in
one run and allowed in the other. ``--allowedTools Bash`` is passed in both runs, so the
blocked run also shows the hook overriding the CLI's own allow rule.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from panoptis.protect import claude_hook
from panoptis.protect.ledger import KIND_DECISION, KIND_EXECUTION, ProtectLedger

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("AGENTWATCH_E2E_CLAUDE") != "1",
        reason="SKIPPED_EXTERNAL_CREDENTIAL: set AGENTWATCH_E2E_CLAUDE=1 to run the claude CLI",
    ),
    pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH"),
]

MODEL = os.environ.get("AGENTWATCH_TEST_CLAUDE_MODEL", "haiku")

POLICY = """
version: "1.0"
default: deny
agents:
  claude-code: [{role}]
roles:
  reader:
    allowed_tools: [read_file, glob, grep, list_dir]
    forbidden_tools: [bash, powershell]
  shell:
    allowed_tools: [bash, powershell, read_file, glob, grep, list_dir]
"""


def _run(tmp_path: Path, role: str) -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
    project = tmp_path / role
    project.mkdir()
    marker = project / "marker.txt"
    policy = project / "policy.yaml"
    policy.write_text(POLICY.format(role=role), encoding="utf-8")
    settings = project / "agentwatch-hook-settings.json"
    claude_hook.install(settings)
    store = tmp_path / f"{role}-evidence.db"
    env = {
        **os.environ,
        "AGENTWATCH_POLICY_FILE": str(policy),
        "AGENTWATCH_STORE": f"sqlite:///{store.as_posix()}",
        "AGENTWATCH_AGENT_ID": "claude-code",
    }
    prompt = (
        "Use the Bash tool exactly once to run this command, then reply with one word, "
        "DONE or REFUSED, depending on whether it ran:\n"
        "echo agentwatch-e2e >> marker.txt"
    )
    claude = shutil.which("claude")
    assert claude
    proc = subprocess.run(  # noqa: S603 - fixed arguments, the CLI under test
        [
            claude,
            "-p",
            prompt,
            "--model",
            MODEL,
            "--max-turns",
            "4",
            "--settings",
            str(settings),
            "--allowedTools",
            "Bash",
            "--output-format",
            "json",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    return marker, store, proc


def _records(store: Path, kind: str) -> list[dict]:
    lg = ProtectLedger(f"sqlite:///{store.as_posix()}")
    try:
        return lg.query((kind,), newest_first=False)
    finally:
        lg.store.close()


def test_blocked_bash_call_never_runs(tmp_path):
    marker, store, proc = _run(tmp_path, "reader")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert not marker.exists(), "the denied command must not have run"
    decisions = _records(store, KIND_DECISION)
    bash = [d for d in decisions if d["declared"]["tool"] == "bash"]
    assert bash, f"the hook never saw the Bash call: {proc.stdout[-2000:]}"
    assert {d["declared"]["verdict"] for d in bash} == {"block"}
    assert all(d["payload"]["action"]["source"] == "hook:claude-code" for d in bash)
    assert not _records(store, KIND_EXECUTION), "no execution may be recorded for a denied call"
    print(json.dumps({"blocked_decisions": len(bash), "marker_exists": marker.exists()}))


def test_allowed_bash_call_runs_once(tmp_path):
    marker, store, proc = _run(tmp_path, "shell")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert marker.exists(), f"the allowed command did not run: {proc.stdout[-2000:]}"
    assert marker.read_text(encoding="utf-8").splitlines() == ["agentwatch-e2e"]
    decisions = [d for d in _records(store, KIND_DECISION) if d["declared"]["tool"] == "bash"]
    assert [d["declared"]["verdict"] for d in decisions] == ["allow"]
    executions = [e for e in _records(store, KIND_EXECUTION) if e["declared"]["tool"] == "bash"]
    assert len(executions) == 1 and executions[0]["payload"]["outcome"] == "success"
    assert executions[0]["declared"]["call_id"] == decisions[0]["declared"]["call_id"]
    print(json.dumps({"allowed_decisions": len(decisions), "lines": 1}))
