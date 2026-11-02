"""TO-024: local agent and MCP discovery.

Fixture home/project directories with known config files, and fake psutil-like process
objects. Detection must never be reported as protection: only evidence of enforcement
(the gateway in the command, the hook in settings, a gateway parent) is MONITORED.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from panoptis.protect import discovery as d
from panoptis.protect.claude_hook import hook_settings


def write(path: Path, doc: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc, encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    return h


def desktop_config(home: Path) -> Path:
    for runtime, path, _ in d.config_locations(home):
        if runtime == "claude-desktop":
            return path
    raise AssertionError


def by_name(items: list[d.Discovery]) -> dict[str, d.Discovery]:
    return {i.name: i for i in items}


# ── configuration files ──────────────────────────────────────────────────────────


def test_configured_servers_are_classified_by_evidence(home):
    write(
        desktop_config(home),
        {
            "mcpServers": {
                "fs": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/w"],
                },
                "guarded": {
                    "command": sys.executable,
                    "args": [
                        "-m",
                        "panoptis.protect.mcp_gateway",
                        "stdio",
                        "--",
                        "npx",
                        "server-x",
                    ],
                },
                "remote": {"type": "http", "url": "https://mcp.example.com/mcp?token=abc123"},
                "broken": "not an object",
            }
        },
    )
    items = by_name(d.scan_configs(home))
    assert items["fs"].status == d.UNMONITORED and items["fs"].runtime == "claude-desktop"
    assert items["guarded"].status == d.MONITORED
    assert items["remote"].status == d.UNKNOWN and "abc123" not in json.dumps(
        items["remote"].to_dict()
    )
    assert items["broken"].status == d.UNKNOWN
    for i in items.values():
        assert i.source and i.discovered_at > 0 and 0 <= i.confidence <= 1 and i.reason


def test_secrets_in_config_are_never_collected(home):
    write(
        home / ".cursor" / "mcp.json",
        {
            "mcpServers": {
                "gh": {
                    "command": "npx",
                    "args": [
                        "server-github",
                        "--token",
                        "ghp_" + "a" * 36,
                        "--api-key=sk-live-xyz123456",
                    ],
                    "env": {"GITHUB_TOKEN": "ghp_" + "b" * 36},
                }
            }
        },
    )
    items = d.scan_configs(home)
    blob = json.dumps([i.to_dict() for i in items])
    assert "ghp_" not in blob and "sk-live" not in blob
    gh = by_name(items)["gh"]
    assert gh.details["env_vars"] == ["GITHUB_TOKEN"]  # the name only, never the value
    assert "[REDACTED]" in gh.details["args"] and "--api-key=[REDACTED]" in gh.details["args"]


def test_editor_runtimes_are_unsupported_but_their_servers_classified(home, tmp_path):
    proj = tmp_path / "proj"
    write(
        proj / ".vscode" / "mcp.json",
        {"servers": {"db": {"command": "uvx", "args": ["mcp-server-sqlite"]}}},
    )
    write(home / ".codeium" / "windsurf" / "mcp_config.json", {"mcpServers": {}})
    items = d.scan_configs(home, [proj])
    runtimes = {(i.runtime, i.status) for i in items if i.kind == "runtime"}
    assert ("vscode", d.UNSUPPORTED) in runtimes and ("windsurf", d.UNSUPPORTED) in runtimes
    assert by_name(items)["db"].status == d.UNMONITORED


def test_claude_code_is_monitored_only_with_the_hook(home, tmp_path):
    proj = tmp_path / "proj"
    write(
        home / ".claude.json",
        {
            "mcpServers": {},
            "projects": {
                str(proj): {"mcpServers": {"local": {"command": "node", "args": ["srv.js"]}}}
            },
        },
    )
    items = d.scan_configs(home, [proj])
    cc = next(i for i in items if i.kind == "runtime" and i.runtime == "claude-code")
    assert cc.status == d.UNMONITORED
    assert by_name(items)["local (proj)"].status == d.UNMONITORED
    write(proj / ".claude" / "settings.local.json", hook_settings())
    cc = next(
        i
        for i in d.scan_configs(home, [proj])
        if i.kind == "runtime" and i.runtime == "claude-code"
    )
    assert cc.status == d.MONITORED and cc.details["hook_files"]


def test_a_look_alike_hook_is_not_protection(home, tmp_path):
    proj = tmp_path / "proj"
    write(
        proj / ".claude" / "settings.json",
        {
            "hooks": {
                "PostToolUse": [
                    {"hooks": [{"type": "command", "command": "python claude_hook_entry.py"}]}
                ],
                "PreToolUse": [{"hooks": [{"type": "command", "command": "echo agentwatch"}]}],
            }
        },
    )
    cc = next(i for i in d.scan_configs(home, [proj]) if i.kind == "runtime")
    assert cc.status == d.UNMONITORED


@pytest.mark.parametrize(
    "content,reason", [("{not json", "invalid JSON"), ('{"mcpServers": [1, 2]}', "not an object")]
)
def test_malformed_config_files_are_unknown_not_errors(home, content, reason):
    write(home / ".cursor" / "mcp.json", content)
    items = d.scan_configs(home)
    assert items and all(i.status == d.UNKNOWN for i in items) and reason in items[0].reason


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions, non-root"
)
def test_unreadable_config_is_unknown(home):
    f = write(home / ".cursor" / "mcp.json", {"mcpServers": {}})
    f.chmod(0)
    try:
        items = d.scan_configs(home)
        assert items[0].status == d.UNKNOWN and "permission" in items[0].reason
    finally:
        f.chmod(0o600)


def test_oversized_config_is_not_read(home, monkeypatch):
    monkeypatch.setattr(d, "MAX_CONFIG_BYTES", 10)
    write(home / ".cursor" / "mcp.json", {"mcpServers": {"x": {"command": "a"}}})
    items = d.scan_configs(home)
    assert items[0].status == d.UNKNOWN and "larger than" in items[0].reason


def test_no_config_means_nothing_reported(home):
    assert d.scan_configs(home) == []


# ── processes ─────────────────────────────────────────────────────────────────────


class FakeProc:
    def __init__(self, pid, name, cmdline, *, cwd=None, parent=None, ports=(), deny=False):
        self._info = {"pid": pid, "name": name, "cmdline": cmdline}
        self._cwd, self._parent, self._ports, self._deny = cwd, parent, ports, deny

    @property
    def info(self):
        if self._deny:
            raise PermissionError("access denied")
        return self._info

    def cwd(self):
        if self._cwd is None:
            raise PermissionError("access denied")
        return str(self._cwd)

    def parent(self):
        return self._parent

    def as_dict(self, attrs):
        return {k: self._info.get(k) for k in attrs}

    def net_connections(self, kind="tcp"):
        class C:
            def __init__(self, port):
                self.status = "LISTEN"
                self.laddr = type("A", (), {"port": port})()

        return [C(p) for p in self._ports]


def test_processes_are_identified_and_classified(home, tmp_path):
    proj = tmp_path / "proj"
    write(proj / ".claude" / "settings.local.json", hook_settings())
    gateway = FakeProc(
        10,
        "python.exe",
        ["python", "-m", "panoptis.protect.mcp_gateway", "stdio", "--", "npx", "srv"],
    )
    procs = [
        FakeProc(
            1,
            "claude.exe",
            [
                r"C:\Users\u\AppData\Roaming\Claude\claude-code\2.1\claude.exe",
                "--output-format",
                "stream-json",
            ],
            cwd=proj,
        ),
        FakeProc(2, "claude", ["/usr/local/bin/claude"], cwd=tmp_path),  # no hook there
        FakeProc(3, "claude", ["claude", "-p", "x"], cwd=None),
        FakeProc(
            4, "claude.exe", [r"C:\Program Files\WindowsApps\Claude_4.1_x64__abc\app\claude.exe"]
        ),
        FakeProc(
            5,
            "claude.exe",
            [r"C:\Program Files\WindowsApps\Claude_4.1_x64__abc\app\claude.exe", "--type=renderer"],
        ),
        gateway,
        FakeProc(
            11,
            "node.exe",
            ["node", "/x/@modelcontextprotocol/server-filesystem/dist/index.js"],
            parent=gateway,
        ),
        FakeProc(
            12,
            "node.exe",
            ["node", "/x/@modelcontextprotocol/server-github/index.js"],
            parent=FakeProc(99, "cursor.exe", ["cursor"]),
            ports=[7777],
        ),
        FakeProc(13, "node.exe", ["node", "/x/mcp-server-fetch"], parent=None),
        FakeProc(
            20, "python.exe", ["python", "train.py", "--epochs", "3"]
        ),  # unrelated: never reported
        FakeProc(21, "aider", ["aider", "--model", "x", "--api-key", "sk-secret-value-123"]),
        FakeProc(22, "x", [], deny=True),
    ]
    items = {int(i.source.split(":")[1]): i for i in d.scan_processes(home=home, processes=procs)}
    assert set(items) == {
        1,
        2,
        3,
        4,
        10,
        11,
        12,
        13,
        21,
    }  # helpers, unrelated and inaccessible skipped
    assert items[1].runtime == "claude-code" and items[1].status == d.MONITORED
    assert items[2].status == d.UNMONITORED  # detected, but no hook: never shown as protected
    assert items[3].status == d.UNKNOWN
    assert items[4].runtime == "claude-desktop" and items[4].status == d.UNSUPPORTED
    assert items[10].runtime == "agentwatch-gateway" and items[10].status == d.MONITORED
    assert (
        items[11].status == d.MONITORED
        and items[12].status == d.UNMONITORED
        and items[13].status == d.UNKNOWN
    )
    assert items[12].details["listening_ports"] == [7777]
    assert items[21].status == d.UNSUPPORTED and "sk-secret" not in json.dumps(items[21].to_dict())


def test_process_scan_is_bounded(home):
    procs = [FakeProc(i, "claude", ["claude"], cwd=None) for i in range(50)]
    items = d.scan_processes(home=home, processes=procs, max_processes=10)
    assert len([i for i in items if i.source.startswith("pid:")]) == 10
    assert items[-1].reason.startswith("stopped after 10")


def test_missing_psutil_is_reported(monkeypatch, home):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "psutil":
            raise ImportError("no psutil")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    items = d.scan_processes(home=home)
    assert len(items) == 1 and items[0].status == d.UNKNOWN and "psutil" in items[0].reason


def test_discover_real_machine_smoke(home):
    inv = d.discover(home=home, processes=True)
    assert set(inv) == {"discovered_at", "platform", "counts", "items"}
    for item in inv["items"]:
        assert item["status"] in (d.MONITORED, d.UNMONITORED, d.UNKNOWN, d.UNSUPPORTED)


# ── wrapping a server behind the gateway (explicit, reversible) ─────────────────────


def test_wrap_is_a_dry_run_unless_applied(home, tmp_path):
    cfg = write(
        tmp_path / "mcp.json",
        {
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "server-fs", "/w"], "env": {"A": "1"}},
                "other": {"command": "x"},
            }
        },
    )
    before = cfg.read_text(encoding="utf-8")
    plan = d.wrap_server(cfg, "fs")
    assert plan["changed"] and not plan["applied"] and cfg.read_text(encoding="utf-8") == before
    done = d.wrap_server(cfg, "fs", apply=True)
    assert done["applied"] and Path(done["backup"]).read_text(encoding="utf-8") == before
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    fs = doc["mcpServers"]["fs"]
    assert fs["args"][:3] == ["-m", "panoptis.protect.mcp_gateway", "stdio"] and fs["args"][
        -4:
    ] == ["npx", "-y", "server-fs", "/w"]
    assert fs["env"] == {"A": "1"} and doc["mcpServers"]["other"] == {"command": "x"}
    assert by_name([d.classify_server("x", "fs", fs, str(cfg))])["fs"].status == d.MONITORED
    assert d.wrap_server(cfg, "fs", apply=True)["changed"] is False  # idempotent


def test_wrap_refuses_what_it_cannot_do(tmp_path):
    cfg = write(tmp_path / "mcp.json", {"mcpServers": {"remote": {"url": "https://x/mcp"}}})
    with pytest.raises(ValueError, match="not a stdio server"):
        d.wrap_server(cfg, "remote", apply=True)
    with pytest.raises(KeyError):
        d.wrap_server(cfg, "missing")


def test_record_inventory_in_the_ledger(tmp_path, home):
    from panoptis.protect.ledger import KIND_DISCOVERY, ProtectLedger

    store = f"sqlite:///{(tmp_path / 'ev.db').as_posix()}"
    write(home / ".cursor" / "mcp.json", {"mcpServers": {"fs": {"command": "npx"}}})
    inv = d.discover(home=home, processes=False)
    d.record_inventory(inv, store=store, tenant_id="acme")
    lg = ProtectLedger(store)
    try:
        rows = lg.query((KIND_DISCOVERY,), tenant_id="acme")
        assert rows and rows[0]["payload"]["counts"] == inv["counts"]
    finally:
        lg.store.close()


def test_cli_discover_json(home, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from panoptis.cli.main import app

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    write(home / ".cursor" / "mcp.json", {"mcpServers": {"fs": {"command": "npx"}}})
    r = CliRunner().invoke(
        app, ["protect", "discover", "--no-processes", "--json", "--project", str(tmp_path)]
    )
    assert r.exit_code == 0, r.output
    inv = json.loads(r.stdout)
    assert {i["name"]: i["status"] for i in inv["items"] if i["kind"] == "mcp_server"} == {
        "fs": "UNMONITORED"
    }
