"""Local-first mode: `panoptis up` starts empty, sample data lives in a separate demo
workspace (`panoptis demo`, removed by `panoptis demo --clear`), loopback-only sign-in."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from panoptis import local

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("AGENTWATCH_HOME", str(h))
    for name in ("AGENTWATCH_STORE", "AGENTWATCH_KILL_FILE", "AGENTWATCH_EVIDENCE_ANCHORS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(local.WORKSPACE_ENV, raising=False)
    return h


def _decisions(store_home: Path) -> list[dict]:
    from panoptis.protect import ProtectLedger
    from panoptis.protect.ledger import KIND_DECISION

    db = store_home / "agentwatch.db"
    if not db.exists():
        return []
    ledger = ProtectLedger(f"sqlite:///{db.as_posix()}", seal_every=0)
    try:
        return ledger.query((KIND_DECISION,), tenant_id="default", limit=100)
    finally:
        ledger.store.close()


def test_profile_is_created_once_and_reused(home):
    first = local.load_or_create_profile()
    assert local.load_or_create_profile() == first
    assert len(first.operator_key) > 30
    if os.name == "posix":
        assert (local.profile_path().stat().st_mode & 0o777) == 0o600


def test_a_profile_from_0_4_0_still_loads(home):
    home.mkdir(parents=True)
    local.profile_path().write_text(
        json.dumps({"operator_key": "pnl_old", "demo_key": "pnd_old", "created_at": 1.0}),
        encoding="utf-8",
    )
    assert local.load_or_create_profile().operator_key == "pnl_old"


def test_a_corrupt_profile_is_reported_not_replaced(home):
    home.mkdir(parents=True)
    local.profile_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a valid PANOPTIS local profile"):
        local.load_or_create_profile()
    assert local.profile_path().read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    ("client", "host", "ok"),
    [
        ("127.0.0.1", "127.0.0.1:8000", True),
        ("127.0.0.1", "localhost:8000", True),
        ("::1", "[::1]:8000", True),
        ("127.0.0.1", "localhost", True),
        ("127.0.0.1", "evil.example:8000", False),  # DNS rebinding: loopback client, foreign name
        ("127.0.0.1", None, False),
        ("10.0.0.5", "localhost:8000", False),
        (None, "localhost:8000", False),
    ],
)
def test_loopback_guard(client, host, ok):
    assert local.is_loopback_request(client, host) is ok


def test_local_env_keeps_user_values_and_adds_no_tenants(home, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_API_KEY", "user-operator")
    monkeypatch.delenv("AGENTWATCH_TENANT_KEYS", raising=False)
    monkeypatch.delenv(local.LOCAL_FLAG, raising=False)
    local.apply_local_env(local.load_or_create_profile())
    assert os.environ["AGENTWATCH_API_KEY"] == "user-operator"
    assert "AGENTWATCH_TENANT_KEYS" not in os.environ
    assert os.environ[local.LOCAL_FLAG] == "1"
    session = local.local_session(local.load_or_create_profile())
    assert session["workspace"] == "local"
    assert [(p["key"], p["sample_data"]) for p in session["profiles"]] == [("user-operator", False)]


def test_sample_data_is_never_written_to_the_real_workspace(home):
    with pytest.raises(RuntimeError, match="only written to the demo workspace"):
        local.seed_demo_workspace()
    assert _decisions(home) == []


def test_demo_workspace_is_separate_seeded_once_and_cleared(home, monkeypatch):
    target = local.enter_demo_workspace(home)
    assert target == home / "demo"
    assert os.environ["AGENTWATCH_HOME"] == str(target)
    assert local.enter_demo_workspace() == target, "entering twice does not nest"
    assert local.seed_demo_workspace() == 7
    assert local.seed_demo_workspace() == 0, "a second run adds nothing"

    rows = _decisions(target)
    assert {"allow", "block"} <= {r["payload"]["verdict"] for r in rows}
    assert all(r["payload"]["action"]["agent_id"] == local.DEMO_AGENT for r in rows)
    assert _decisions(home) == [], "the real workspace stays empty"
    from panoptis.protect import ApprovalBroker, ProtectLedger

    ledger = ProtectLedger(None, seal_every=0)
    try:
        pending = ApprovalBroker(ledger).pending(tenant_id="default")
        assert [p["arguments"]["command"] for p in pending] == ["git push --force origin main"]
    finally:
        ledger.store.close()
    session = local.local_session(local.load_or_create_profile())
    assert session["workspace"] == "demo" and session["profiles"][0]["sample_data"] is True

    monkeypatch.setenv("AGENTWATCH_HOME", str(home))
    monkeypatch.delenv(local.WORKSPACE_ENV)
    changed, msg = local.clear_demo_workspace(home)
    assert changed, msg
    assert not target.exists() and home.exists()
    assert local.clear_demo_workspace(home)[0] is False


def test_clear_refuses_while_the_demo_server_runs(home, monkeypatch):
    (home / "demo").mkdir(parents=True)
    monkeypatch.setattr(local, "running_server", lambda h: 8001)
    changed, msg = local.clear_demo_workspace(home)
    assert not changed and "in use" in msg
    assert (home / "demo").exists()


def test_use_local_dashboard_records_what_the_default_pipeline_decides(home):
    from panoptis.protect import (
        ActionBlocked,
        protect_tool,
        set_default_pipeline,
        use_local_dashboard,
    )

    try:
        pipeline = use_local_dashboard()

        @protect_tool
        def run_shell(command: str) -> str:
            return "ok"

        assert run_shell(command="ls") == "ok"
        with pytest.raises(ActionBlocked):
            run_shell(command="rm -rf /")
        pipeline.ledger.store.close()
        verdicts = [r["payload"]["verdict"] for r in _decisions(home)]
        assert sorted(verdicts) == ["allow", "block"]
    finally:
        set_default_pipeline(None)


def _cli(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTWATCH_", "PANOPTIS_"))}
    env.update(AGENTWATCH_HOME=str(home), PYTHONIOENCODING="utf-8", COLUMNS="200")
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "panoptis", *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(ROOT),
    )


def test_cli_demo_seeds_the_demo_workspace_and_clear_removes_it(home):
    seeded = _cli(home, "demo", "--no-serve")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert len(_decisions(home / "demo")) >= 6
    assert _decisions(home) == []
    offline = _cli(home, "demo", "offline", "--approval-timeout", "0.5", "--json")
    assert offline.returncode == 0, offline.stdout[-3000:] + offline.stderr[-3000:]
    assert _decisions(home) == [], "demo offline also stays in the demo workspace"
    cleared = _cli(home, "demo", "--clear")
    assert cleared.returncode == 0 and "Removed the demo workspace" in cleared.stdout
    assert not (home / "demo").exists()


_SERVER_PROBE = textwrap.dedent(
    """
    import json
    from fastapi.testclient import TestClient
    from panoptis.api.server import app

    out = {}
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        r = c.get("/api/local/session", headers={"host": "localhost:8000"})
        out["session"] = r.status_code
        body = r.json() if r.status_code == 200 else {}
        out["workspace"] = body.get("workspace")
        out["sample"] = [p["sample_data"] for p in body.get("profiles", [])]
        key = (body.get("profiles") or [{}])[0].get("key")
        out["rebinding"] = c.get("/api/local/session", headers={"host": "evil.example"}).status_code
        out["cross_site"] = c.get(
            "/api/local/session", headers={"host": "localhost", "sec-fetch-site": "cross-site"}
        ).status_code
        out["no_key"] = c.get("/api/v1/protect/status").status_code
        if key:
            out["tenant"] = c.get(
                "/api/v1/protect/status", headers={"x-api-key": key}
            ).json()["tenant_id"]
        page = c.get("/")
        out["index"] = [page.status_code, "PANOPTIS-TEST-PAGE" in page.text]
        out["frame"] = page.headers.get("x-frame-options")
        out["health"] = c.get("/health").status_code
    print("RESULT" + json.dumps(out))
    """
)


def _probe(tmp_path: Path, home: Path, local_flag: bool, workspace: str | None = None) -> dict:
    dash = tmp_path / "dash"
    dash.mkdir(exist_ok=True)
    (dash / "index.html").write_text("<html>PANOPTIS-TEST-PAGE</html>", encoding="utf-8")
    p = local.load_or_create_profile()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AGENTWATCH_", "PANOPTIS_")) and k != "DATABASE_URL"
    }
    env.update(
        AGENTWATCH_HOME=str(home),
        AGENTWATCH_API_KEY=p.operator_key,
        AGENTWATCH_KILL_FILE=str(tmp_path / "kill"),
        PANOPTIS_DASHBOARD_DIR=str(dash),
        AGENTWATCH_FORWARD_EVENTS="0",
    )
    if local_flag:
        env["PANOPTIS_LOCAL_UI"] = "1"
    if workspace:
        env[local.WORKSPACE_ENV] = workspace
    r = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", _SERVER_PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, r.stdout[-2000:] + r.stderr[-4000:]
    return json.loads(line[len("RESULT") :])


def test_local_server_signs_in_loopback_only_and_serves_the_dashboard(tmp_path, home):
    out = _probe(tmp_path, home, local_flag=True)
    assert out["session"] == 200 and out["workspace"] == "local" and out["sample"] == [False]
    assert out["rebinding"] == 404, "a page that rebinds its own name to 127.0.0.1 gets nothing"
    assert out["cross_site"] == 404
    assert out["no_key"] == 401, "local mode still requires the key for the API"
    assert out["tenant"] == "default"
    assert out["index"] == [200, True] and out["frame"] == "DENY"
    assert out["health"] == 200


def test_the_demo_server_labels_its_data_as_samples(tmp_path, home):
    out = _probe(tmp_path, home, local_flag=True, workspace="demo")
    assert out["workspace"] == "demo" and out["sample"] == [True]


def test_without_local_mode_there_is_no_session_route_or_dashboard(tmp_path, home):
    out = _probe(tmp_path, home, local_flag=False)
    assert out["session"] == 404
    assert out["index"][0] == 404


def test_offline_demo_blocks_the_injected_calls(tmp_path):
    from panoptis import demo_live

    store = f"sqlite:///{(tmp_path / 'e.db').as_posix()}"
    said: list[str] = []
    out = demo_live.run_offline(approval_timeout_s=0.5, store=store, say=said.append)
    assert out.error is None
    assert out.ok, out.checks()
    by_cmd = {d["command"]: d["verdict"] for d in out.decisions if d["kind"] == "decision"}
    assert by_cmd[demo_live.EXFILTRATE] == "block"
    assert by_cmd["pytest tests/test_login.py -q"] == "allow"
    assert out.injection_removed, "the hidden instruction in the issue was quarantined"
    assert not any(c.startswith("curl") for c in out.executed)


def test_live_demo_refuses_to_start_without_a_key(monkeypatch):
    from panoptis import demo_live

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        demo_live.run_live(model=None, approval_timeout_s=1, store=None, say=print)
