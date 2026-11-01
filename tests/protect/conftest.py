from __future__ import annotations

import pytest

from panoptis.protect import ApprovalBroker, PolicyStore, ProtectionPipeline, ProtectLedger


@pytest.fixture(autouse=True)
def _isolated_kill_file(tmp_path, monkeypatch):
    # every test gets its own kill-file sentinel, so a tripped switch never leaks
    monkeypatch.setenv("AGENTWATCH_KILL_FILE", str(tmp_path / "protect.kill"))


@pytest.fixture
def ledger(tmp_path):
    lg = ProtectLedger(f"sqlite:///{(tmp_path / 'aw.db').as_posix()}")
    yield lg
    lg.store.close()


POLICY = """
version: "1.0"
default: deny
agents:
  coder: [coder]
  "research-*": [researcher]
  ops: [operator]
roles:
  coder:
    allowed_tools: [bash, read_file, write_file, run_python]
    restricted_arguments:
      bash:
        blocked_commands: ["sudo *", "curl * | sh"]
        blocked_paths: ["**/.env", "~/.ssh/**"]
  researcher:
    allowed_tools: [web_search, fetch_url, read_file]
    forbidden_tools: [bash, "write_*"]
    rate_limits: {max_calls_per_minute: 100, per_tool: {web_search: 3}}
  operator:
    allowed_tools: ["*"]
    require_approval: [deploy]
"""


@pytest.fixture
def policy():
    store = PolicyStore()
    store.load_document(POLICY)
    return store


@pytest.fixture
def make_pipeline(ledger, policy):
    def make(approver=None, **kwargs):
        approvals = (
            ApprovalBroker(
                ledger, approver=approver, timeout_s=kwargs.pop("timeout_s", 2.0), poll_s=0.02
            )
            if approver is not False
            else None
        )
        return ProtectionPipeline(policy=policy, ledger=ledger, approvals=approvals, **kwargs)

    return make
