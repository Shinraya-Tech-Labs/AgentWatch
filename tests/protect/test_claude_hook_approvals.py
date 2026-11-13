"""A call the Claude Code hook holds for approval is decided by a person in PANOPTIS.

Regression for a real run (Windows, Claude Code 2.1.322, auto mode): the hook answered an
ESCALATE_HITL recursive delete with ``permissionDecision: "ask"``, Claude Code's auto mode
approved it, and the folder was deleted while the dashboard said "Held for approval".

``AutoApprovingHost`` below behaves like Claude Code in auto mode: it runs a call unless
the hook says ``deny``. With the default ``--approvals dashboard`` the hook must answer
``allow`` only after a person approved it in PANOPTIS, and ``deny`` otherwise.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from panoptis.protect import ApprovalBroker, ProtectLedger, claude_hook
from panoptis.protect.ledger import KIND_APPROVAL_REQUEST, KIND_DECISION

BASH_DELETE = "ls -la important && rm -rf ./important && ls -la"
PS_DELETE = "Remove-Item -Recurse -Force important"
DELETES = [("Bash", BASH_DELETE), ("PowerShell", PS_DELETE)]


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    # the default store and kill file live under AGENTWATCH_HOME: never the real one
    monkeypatch.setenv("AGENTWATCH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENTWATCH_STORE", raising=False)
    monkeypatch.delenv("AGENTWATCH_APPROVAL_URL", raising=False)


def pre(tool: str, command: str, use_id: str = "toolu_del") -> dict[str, Any]:
    return {
        "session_id": "sess-del",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command, "description": "clean up"},
        "tool_use_id": use_id,
    }


class AutoApprovingHost:
    """Claude Code in auto mode: anything the hook does not deny runs (``ask`` included)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.ran = False

    def run(self, answer: dict[str, Any]) -> None:
        if answer.get("hookSpecificOutput", {}).get("permissionDecision") != "deny":
            self.ran = True
            shutil.rmtree(self.folder)


@pytest.fixture
def workspace(tmp_path) -> dict[str, Any]:
    folder = tmp_path / "important"
    folder.mkdir()
    (folder / "keep.txt").write_text("precious", encoding="utf-8")
    store = f"sqlite:///{(tmp_path / 'hook.db').as_posix()}"
    env = {
        "AGENTWATCH_STORE": store,  # a shared store is a reachable approval channel
        "AGENTWATCH_HOME": str(tmp_path / "home"),
        "AGENTWATCH_KILL_FILE": str(tmp_path / "protect.kill"),
        "AGENTWATCH_HOOK_APPROVAL_TIMEOUT": "20",
    }
    ledger = ProtectLedger(store)
    yield {"folder": folder, "env": env, "ledger": ledger, "store": store}
    ledger.store.close()


def dashboard(
    ledger: ProtectLedger, approved: bool, seen: list[dict[str, Any]]
) -> threading.Thread:
    """A person on the dashboard: waits for the held call, then approves or rejects it."""
    broker = ApprovalBroker(ledger)

    def decide() -> None:
        deadline = time.time() + 15
        while time.time() < deadline:
            pending = broker.pending()
            if pending:
                req = pending[0]
                seen.append(req)
                broker.resolve(
                    req["request_id"],
                    approved,
                    "operator@dashboard",
                    expected_digest=req["action_digest"],
                    comment="checked" if approved else "keep that folder",
                )
                return
            time.sleep(0.05)

    t = threading.Thread(target=decide, daemon=True)
    t.start()
    return t


def answer_of(out: dict[str, Any]) -> str | None:
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def last_decision(ledger: ProtectLedger) -> dict[str, Any]:
    return ledger.query((KIND_DECISION,))[0]["payload"]


@pytest.mark.parametrize(("tool", "command"), DELETES)
def test_rejected_on_the_dashboard_the_folder_survives_an_auto_approving_host(
    workspace, tool, command
):
    seen: list[dict[str, Any]] = []
    person = dashboard(workspace["ledger"], approved=False, seen=seen)
    host = AutoApprovingHost(workspace["folder"])
    out = claude_hook.handle(pre(tool, command), env=workspace["env"])
    person.join(5)
    host.run(out)

    assert seen, "the call waited in the approval queue"
    assert seen[0]["state"] == "pending" and seen[0]["arguments"]["command"] == command
    assert answer_of(out) == "deny"
    assert "rejected in PANOPTIS" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert not host.ran and (workspace["folder"] / "keep.txt").exists()
    row = last_decision(workspace["ledger"])
    assert row["verdict"] == "block" and row["approval"]["state"] == "rejected"


@pytest.mark.parametrize(("tool", "command"), DELETES)
def test_approved_on_the_dashboard_the_call_runs(workspace, tool, command):
    seen: list[dict[str, Any]] = []
    person = dashboard(workspace["ledger"], approved=True, seen=seen)
    host = AutoApprovingHost(workspace["folder"])
    out = claude_hook.handle(pre(tool, command), env=workspace["env"])
    person.join(5)
    host.run(out)

    assert answer_of(out) == "allow", out
    assert "operator@dashboard" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert host.ran and not workspace["folder"].exists()
    row = last_decision(workspace["ledger"])
    assert row["verdict"] == "allow" and row["approval"]["state"] == "approved"


@pytest.mark.parametrize(("tool", "command"), DELETES)
def test_no_decision_before_the_deadline_is_a_denial(workspace, tool, command):
    env = {**workspace["env"], "AGENTWATCH_HOOK_APPROVAL_TIMEOUT": "0.5"}
    host = AutoApprovingHost(workspace["folder"])
    out = claude_hook.handle(pre(tool, command), env=env)
    host.run(out)

    assert answer_of(out) == "deny"
    assert "before the deadline" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert workspace["folder"].exists()
    assert last_decision(workspace["ledger"])["approval"]["state"] == "expired"


@pytest.mark.parametrize(("tool", "command"), DELETES)
def test_with_no_dashboard_running_the_call_is_denied_not_asked(tmp_path, tool, command):
    folder = tmp_path / "important"
    folder.mkdir()
    home = tmp_path / "home"  # no `panoptis up` for this home: nothing can approve
    env = {"AGENTWATCH_HOME": str(home), "AGENTWATCH_KILL_FILE": str(tmp_path / "k")}
    host = AutoApprovingHost(folder)
    out = claude_hook.handle(pre(tool, command), env=env)
    host.run(out)

    assert answer_of(out) == "deny"
    why = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no PANOPTIS dashboard is running" in why and "panoptis up" in why
    assert folder.exists()
    ledger = ProtectLedger(None)  # the home's default store, where the hook recorded it
    try:
        row = last_decision(ledger)
        assert row["verdict"] == "block" and row["approval"]["state"] == "unavailable"
        assert not ledger.query((KIND_APPROVAL_REQUEST,)), "nothing was queued, so nothing is held"
    finally:
        ledger.store.close()


def test_claude_opt_in_asks_and_records_the_call_as_handed_to_the_host(workspace):
    env = {**workspace["env"], "AGENTWATCH_HOOK_APPROVALS": "claude"}
    out = claude_hook.handle(pre("Bash", BASH_DELETE), env=env)
    assert answer_of(out) == "ask"
    row = last_decision(workspace["ledger"])
    assert row["verdict"] == "escalate_hitl"
    assert row["approval"] == {"state": "handed_to_host", "host": "claude-code"}
    assert not workspace["ledger"].query((KIND_APPROVAL_REQUEST,)), "PANOPTIS holds nothing"


def test_reachability_of_the_dashboard(tmp_path, monkeypatch):
    home = tmp_path / "home"
    assert "no PANOPTIS dashboard is running" in claude_hook.dashboard_unreachable(
        {"AGENTWATCH_HOME": str(home)}
    )
    monkeypatch.setattr("panoptis.local.running_server", lambda h: 8000 if h == home else None)
    assert claude_hook.dashboard_unreachable({"AGENTWATCH_HOME": str(home)}) is None
    monkeypatch.setattr(claude_hook, "_answers", lambda url: False)
    assert "does not answer" in claude_hook.dashboard_unreachable(
        {"AGENTWATCH_APPROVAL_URL": "http://127.0.0.1:9/"}
    )
    monkeypatch.setattr(claude_hook, "_answers", lambda url: url == "http://h:1/health")
    assert claude_hook.dashboard_unreachable({"AGENTWATCH_APPROVAL_URL": "http://h:1/"}) is None
    assert claude_hook.dashboard_unreachable({"AGENTWATCH_STORE": "sqlite:///x.db"}) is None


# ── installation: dashboard by default, timeouts that cannot let a call slip through ──


def entry(**kw: Any) -> dict[str, Any]:
    return claude_hook.hook_settings(**kw)["hooks"]["PreToolUse"][0]["hooks"][0]


def test_dashboard_approvals_are_the_default_and_wait_shorter_than_the_hook():
    e = entry()
    assert e["timeout"] == 300
    assert e["command"].endswith("--approvals dashboard --approval-timeout 240")
    assert entry(timeout_s=600)["command"].endswith("--approval-timeout 540")
    assert entry(timeout_s=60)["command"].endswith("--approval-timeout 30")
    assert entry(timeout_s=120, approval_timeout_s=60)["timeout"] == 120
    with pytest.raises(ValueError, match="shorter than the hook timeout"):
        entry(timeout_s=60, approval_timeout_s=55)
    with pytest.raises(ValueError, match="approvals must be"):
        entry(approvals="host")


def test_claude_approvals_are_an_explicit_opt_in():
    e = entry(approvals="claude")
    assert e["command"].endswith("--approvals claude") and e["timeout"] == 30


@pytest.mark.parametrize(
    "argv",
    [["--approvals", "bogus"], ["--approval-timeout", "0"], ["--nope"], ["--approvals"]],
)
def test_bad_hook_options_block(argv, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_STORE", f"sqlite:///{(tmp_path / 'o.db').as_posix()}")
    err = io.StringIO()
    code = claude_hook.main(io.StringIO(json.dumps(pre("Bash", "ls"))), io.StringIO(), err, argv)
    assert code == 2 and "blocking" in err.getvalue()


def test_cli_install_defaults_to_dashboard_and_accepts_claude(tmp_path):
    from typer.testing import CliRunner

    from panoptis.cli.main import app

    settings = tmp_path / "settings.local.json"
    runner = CliRunner()
    res = runner.invoke(app, ["protect", "claude-hook", "install", "--settings", str(settings)])
    assert res.exit_code == 0, res.output
    hook = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "--approvals dashboard" in hook["command"] and hook["timeout"] == 300
    res = runner.invoke(
        app,
        ["protect", "claude-hook", "install", "--settings", str(settings), "--approvals", "claude"],
    )
    assert res.exit_code == 0 and "auto or bypass mode" in res.output
    hook = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "--approvals claude" in hook["command"]
    res = runner.invoke(
        app,
        [
            "protect",
            "claude-hook",
            "install",
            "--settings",
            str(settings),
            "--timeout",
            "30",
            "--approval-timeout",
            "25",
        ],
    )
    assert res.exit_code == 2 and "shorter than the hook timeout" in " ".join(res.output.split())


# ── the installed command, in its own process, as Claude Code runs it ──────────────


@pytest.mark.parametrize("approved", [False, True])
def test_the_installed_command_waits_for_the_dashboard(tmp_path, approved):
    folder = tmp_path / "important"
    folder.mkdir()
    store = f"sqlite:///{(tmp_path / 'proc.db').as_posix()}"
    ledger = ProtectLedger(store)
    try:
        ledger.query((KIND_DECISION,))  # create the schema before two processes share it
        command = entry(timeout_s=60, approval_timeout_s=30)["command"]
        env = {
            **os.environ,
            "AGENTWATCH_STORE": store,
            "AGENTWATCH_HOME": str(tmp_path / "home"),
            "AGENTWATCH_KILL_FILE": str(tmp_path / "k"),
        }
        seen: list[dict[str, Any]] = []
        person = dashboard(ledger, approved=approved, seen=seen)
        proc = subprocess.run(  # noqa: S602 - exactly the command string Claude Code runs
            command,
            shell=True,
            input=json.dumps(pre("Bash", BASH_DELETE)),
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        person.join(5)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        host = AutoApprovingHost(folder)
        host.run(out)
        assert seen, "the hook process held the call in the approval queue"
        assert answer_of(out) == ("allow" if approved else "deny")
        assert folder.exists() is not approved
    finally:
        ledger.store.close()


# ── upgrade path: hooks installed by an older PANOPTIS ─────────────────────────────


def run_hook(argv: list[str], payload: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
    out, err = io.StringIO(), io.StringIO()
    code = claude_hook.main(io.StringIO(json.dumps(payload)), out, err, argv)
    return code, json.loads(out.getvalue() or "{}"), err.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        [],  # 0.4.2 and earlier: no options at all
        ["--approvals", "claude"],  # no --hook-schema: still an older command line
        ["--hook-schema", "1", "--approvals", "claude"],
    ],
)
@pytest.mark.parametrize(("tool", "command"), DELETES)
def test_an_outdated_install_denies_escalations_instead_of_asking(
    workspace, monkeypatch, argv, tool, command
):
    for k, v in workspace["env"].items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("AGENTWATCH_HOOK_APPROVALS", "claude")  # the environment cannot opt in
    host = AutoApprovingHost(workspace["folder"])
    code, out, err = run_hook(argv, pre(tool, command))
    host.run(out)

    assert code == 0, err
    assert answer_of(out) == "deny"
    why = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "older PANOPTIS" in why and "claude-hook install" in why
    assert workspace["folder"].exists()
    assert not workspace["ledger"].query((KIND_APPROVAL_REQUEST,)), "an old hook never waits"
    assert last_decision(workspace["ledger"])["approval"]["state"] == "unavailable"


def test_an_outdated_install_still_allows_ordinary_calls(workspace, monkeypatch):
    for k, v in workspace["env"].items():
        monkeypatch.setenv(k, v)
    code, out, err = run_hook([], pre("Bash", "ls -la"))
    assert code == 0 and out == {}, err


def test_a_current_install_with_claude_approvals_asks(workspace, monkeypatch):
    for k, v in workspace["env"].items():
        monkeypatch.setenv(k, v)
    argv = ["--hook-schema", str(claude_hook.HOOK_SCHEMA), "--approvals", "claude"]
    code, out, err = run_hook(argv, pre("Bash", BASH_DELETE))
    assert code == 0 and answer_of(out) == "ask", err


def old_settings(path: Path, command: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    group = {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}
    path.write_text(json.dumps({"hooks": {"PreToolUse": [group]}}), encoding="utf-8")
    return path


OLD_COMMAND = '"C:/Python/python.exe" "D:/x/panoptis/protect/claude_hook_entry.py"'


def test_outdated_installs_are_found_in_project_and_user_settings(tmp_path):
    project, home = tmp_path / "proj", tmp_path / "user"
    stale = old_settings(project / ".claude" / "settings.local.json", OLD_COMMAND)
    fresh = home / ".claude" / "settings.json"
    claude_hook.install(fresh)
    other = old_settings(project / ".claude" / "settings.json", "my-linter --fast")
    candidates = claude_hook.settings_candidates(project, home)
    assert stale in candidates and fresh in candidates and other in candidates
    assert claude_hook.outdated_installs(candidates) == [stale]
    claude_hook.install(stale)  # re-installing fixes it
    assert claude_hook.outdated_installs(candidates) == []


def test_the_outdated_warning_names_the_file_and_the_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local = old_settings(tmp_path / ".claude" / "settings.local.json", OLD_COMMAND)
    user = old_settings(tmp_path / "user" / ".claude" / "settings.json", OLD_COMMAND)
    assert claude_hook.outdated_warning(local) == (
        f"Claude Code hook in {local} is outdated, run panoptis protect claude-hook install again"
    )
    assert claude_hook.outdated_warning(user).endswith(f"install --settings {user} again")
    assert claude_hook.outdated_warnings([local, tmp_path / "missing.json"]) == [
        claude_hook.outdated_warning(local)
    ]


def test_installed_schema_and_claude_code_versions():
    assert claude_hook.installed_schema(OLD_COMMAND) == 1
    assert claude_hook.installed_schema(claude_hook.hook_command()) == claude_hook.HOOK_SCHEMA
    assert claude_hook.parse_version("2.1.322 (Claude Code)") == (2, 1, 322)
    assert claude_hook.parse_version("claude: command not found") is None
    assert claude_hook.supports_on_failure_block((2, 1, 295))
    assert not claude_hook.supports_on_failure_block((2, 1, 229))
    assert not claude_hook.supports_on_failure_block(None)


@pytest.mark.parametrize(
    ("version", "flags", "expected"),
    [
        ((2, 1, 322), [], True),
        ((2, 1, 229), [], False),
        (None, [], False),
        ((2, 1, 322), ["--no-on-failure-block"], False),
        (None, ["--on-failure-block"], True),
    ],
)
def test_install_turns_on_failure_block_on_when_claude_code_supports_it(
    tmp_path, monkeypatch, version, flags, expected
):
    from typer.testing import CliRunner

    cli = importlib.import_module("panoptis.cli.main")

    monkeypatch.setattr(cli, "_claude_code_version", lambda: version)
    settings = tmp_path / "s.json"
    res = CliRunner().invoke(
        cli.app, ["protect", "claude-hook", "install", "--settings", str(settings), *flags]
    )
    assert res.exit_code == 0, res.output
    hook = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert (hook.get("onFailure") == "block") is expected


def test_doctor_warns_about_an_outdated_hook(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    cli = importlib.import_module("panoptis.cli.main")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)  # no docker probe
    old_settings(tmp_path / ".claude" / "settings.local.json", OLD_COMMAND)
    res = CliRunner().invoke(cli.app, ["doctor"])
    text = " ".join(res.output.split())
    assert "is outdated, run panoptis protect claude-hook install again" in text, res.output
