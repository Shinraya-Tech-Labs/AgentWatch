"""Local-first mode (`panoptis up`): profile, loopback-only sign-in, demo tenant, static UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from panoptis import local


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("AGENTWATCH_HOME", str(h))
    monkeypatch.delenv("AGENTWATCH_STORE", raising=False)
    return h


def test_profile_is_created_once_and_reused(home):
    first = local.load_or_create_profile()
    again = local.load_or_create_profile()
    assert first == again
    assert first.operator_key != first.demo_key and len(first.operator_key) > 30
    if os.name == "posix":
        assert (local.profile_path().stat().st_mode & 0o777) == 0o600


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


def test_local_env_adds_the_demo_tenant_and_keeps_user_values(home, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_API_KEY", "user-operator")
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=k-acme")
    monkeypatch.delenv(local.LOCAL_FLAG, raising=False)
    p = local.load_or_create_profile()
    local.apply_local_env(p)
    assert os.environ["AGENTWATCH_API_KEY"] == "user-operator"
    assert os.environ["AGENTWATCH_TENANT_KEYS"] == f"acme=k-acme,demo={p.demo_key}"
    assert os.environ[local.LOCAL_FLAG] == "1"
    session = local.local_session(p)
    assert [x["tenant_id"] for x in session["profiles"]] == ["default", "demo"]
    assert session["profiles"][0]["key"] == "user-operator"
    assert session["profiles"][1]["sample_data"] is True


def test_demo_tenant_is_seeded_once_with_real_decisions(home):
    from panoptis.protect import ApprovalBroker, ProtectLedger
    from panoptis.protect.ledger import KIND_DECISION

    assert local.seed_demo_tenant() == 7
    assert local.seed_demo_tenant() == 0, "a second start does not add more sample data"
    ledger = ProtectLedger(None, seal_every=0)
    try:
        rows = ledger.query((KIND_DECISION,), tenant_id=local.DEMO_TENANT, limit=50)
        verdicts = {r["payload"]["verdict"] for r in rows}
        assert {"allow", "block"} <= verdicts
        assert all(r["payload"]["action"]["agent_id"] == local.DEMO_AGENT for r in rows)
        assert not ledger.query((KIND_DECISION,), tenant_id="default", limit=5), (
            "sample data never lands in the user's own tenant"
        )
        pending = ApprovalBroker(ledger).pending(tenant_id=local.DEMO_TENANT)
        assert [p["arguments"]["command"] for p in pending] == ["git push --force origin main"]
    finally:
        ledger.store.close()


_SERVER_PROBE = textwrap.dedent(
    """
    import json, sys
    from fastapi.testclient import TestClient
    from panoptis.api.server import app

    out = {}
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        r = c.get("/api/local/session", headers={"host": "localhost:8000"})
        out["session"] = r.status_code
        body = r.json() if r.status_code == 200 else {}
        keys = {p["id"]: p["key"] for p in body.get("profiles", [])}
        out["rebinding"] = c.get("/api/local/session", headers={"host": "evil.example"}).status_code
        out["cross_site"] = c.get(
            "/api/local/session", headers={"host": "localhost", "sec-fetch-site": "cross-site"}
        ).status_code
        out["no_key"] = c.get("/api/v1/protect/status").status_code
        if keys:
            out["local_tenant"] = c.get(
                "/api/v1/protect/status", headers={"x-api-key": keys["local"]}
            ).json()["tenant_id"]
            out["demo_tenant"] = c.get(
                "/api/v1/protect/status", headers={"x-api-key": keys["demo"]}
            ).json()["tenant_id"]
            out["demo_kill"] = c.post(
                "/api/v1/protect/control/kill",
                json={"reason": "t", "actor": "t"},
                headers={"x-api-key": keys["demo"]},
            ).status_code
        page = c.get("/")
        out["index"] = [page.status_code, "PANOPTIS-TEST-PAGE" in page.text]
        out["frame"] = page.headers.get("x-frame-options")
        out["health"] = c.get("/health").status_code
    print("RESULT" + json.dumps(out))
    """
)


def _probe(tmp_path: Path, home: Path, local_flag: bool) -> dict:
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
        AGENTWATCH_TENANT_KEYS=f"demo={p.demo_key}",
        AGENTWATCH_KILL_FILE=str(tmp_path / "kill"),
        PANOPTIS_DASHBOARD_DIR=str(dash),
        AGENTWATCH_FORWARD_EVENTS="0",
    )
    if local_flag:
        env["PANOPTIS_LOCAL_UI"] = "1"
    r = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", _SERVER_PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, r.stdout[-2000:] + r.stderr[-4000:]
    return json.loads(line[len("RESULT") :])


def test_local_server_signs_in_loopback_only_and_serves_the_dashboard(tmp_path, home):
    out = _probe(tmp_path, home, local_flag=True)
    assert out["session"] == 200
    assert out["rebinding"] == 404, "a page that rebinds its own name to 127.0.0.1 gets nothing"
    assert out["cross_site"] == 404
    assert out["no_key"] == 401, "local mode still requires the key for the API"
    assert out["local_tenant"] == "default" and out["demo_tenant"] == "demo"
    assert out["demo_kill"] == 403, "the demo key cannot pull the global emergency stop"
    assert out["index"] == [200, True] and out["frame"] == "DENY"
    assert out["health"] == 200


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
