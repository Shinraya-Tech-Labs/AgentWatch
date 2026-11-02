"""Local agent and MCP discovery (TO-024).

Finds agent runtimes and MCP servers on this machine and says, for each one, whether
AgentWatch actually enforces on it. Detection is never taken as protection:

``MONITORED``    evidence that enforcement is in place: an MCP server whose configured
                 command runs the AgentWatch gateway, a Claude Code settings file with the
                 AgentWatch PreToolUse hook, a running AgentWatch gateway process.
``UNMONITORED``  a supported runtime or MCP server without that evidence.
``UNKNOWN``      the status cannot be determined (a URL-only server, an unreadable file,
                 a process whose working directory cannot be read).
``UNSUPPORTED``  a known runtime AgentWatch has no enforcement integration for (its own
                 built-in tools run outside any hook; only MCP servers behind the gateway
                 are covered).

Sources, all bounded and read-only:

* known MCP / agent configuration files (Claude Desktop, Claude Code, Cursor, VS Code,
  Windsurf), at fixed paths under the home directory and the given project roots;
* running processes (``psutil``, if installed): name and command line only, matched
  against known runtime signatures; at most ``max_processes`` are examined;
* listening TCP ports, only for processes already recognised above.

What is never collected: environment variables or their values, file contents other
than the MCP/hook entries, process memory, and command-line secrets (arguments are
redacted). Nothing is killed or modified; :func:`wrap_server` rewrites one config entry
to run behind the gateway only when called with ``apply=True`` (it writes a backup).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MONITORED = "MONITORED"
UNMONITORED = "UNMONITORED"
UNKNOWN = "UNKNOWN"
UNSUPPORTED = "UNSUPPORTED"

GATEWAY_MARKERS = ("panoptis.protect.mcp_gateway", "mcp-gateway", "mcp_gateway")
HOOK_MARKER = "claude_hook_entry.py"
MAX_CONFIG_BYTES = 2 * 1024 * 1024


@dataclass
class Discovery:
    kind: str  # runtime | mcp_server | process | port
    runtime: str  # claude-code, claude-desktop, cursor, vscode, windsurf, mcp-server, agentwatch-gateway, ...
    name: str
    status: str
    source: str  # file path, "pid:<n>", "port:<n>"
    confidence: float
    reason: str
    discovered_at: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── redaction ────────────────────────────────────────────────────────────────────

_SECRETISH_FLAG = re.compile(
    r"(?i)^--?(api[-_]?key|token|secret|password|passwd|auth|access[-_]?key|bearer)(=.*)?$"
)


def redact_args(args: Iterable[str]) -> list[str]:
    """Command-line arguments with anything that looks like a credential removed."""
    from panoptis.protect.threats import mask_secrets

    out: list[str] = []
    hide_next = False
    for a in args:
        a = str(a)
        if hide_next:
            out.append("[REDACTED]")
            hide_next = False
            continue
        m = _SECRETISH_FLAG.match(a)
        if m:
            if m.group(2):
                out.append(a.split("=", 1)[0] + "=[REDACTED]")
            else:
                out.append(a)
                hide_next = True
            continue
        masked, _ = mask_secrets(a)
        out.append(masked[:300])
    return out


# ── configuration files ──────────────────────────────────────────────────────────


def _appdata(home: Path) -> Path:
    return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")


def config_locations(home: Path, projects: Iterable[Path] = ()) -> list[tuple[str, Path, str]]:
    """(runtime, path, format) for every known config file; the file need not exist."""
    locs: list[tuple[str, Path, str]] = []
    if sys.platform == "win32":
        locs.append(
            (
                "claude-desktop",
                _appdata(home) / "Claude" / "claude_desktop_config.json",
                "mcpServers",
            )
        )
    elif sys.platform == "darwin":
        locs.append(
            (
                "claude-desktop",
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
                "mcpServers",
            )
        )
    else:
        locs.append(
            (
                "claude-desktop",
                home / ".config" / "Claude" / "claude_desktop_config.json",
                "mcpServers",
            )
        )
    locs += [
        ("claude-code", home / ".claude.json", "claude_json"),
        ("claude-code", home / ".claude" / "settings.json", "claude_settings"),
        ("cursor", home / ".cursor" / "mcp.json", "mcpServers"),
        ("windsurf", home / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
    ]
    for root in projects:
        locs += [
            ("claude-code", root / ".mcp.json", "mcpServers"),
            ("claude-code", root / ".claude" / "settings.json", "claude_settings"),
            ("claude-code", root / ".claude" / "settings.local.json", "claude_settings"),
            ("cursor", root / ".cursor" / "mcp.json", "mcpServers"),
            ("vscode", root / ".vscode" / "mcp.json", "vscode"),
        ]
    return locs


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        size = path.stat().st_size
        if size > MAX_CONFIG_BYTES:
            return None, f"larger than {MAX_CONFIG_BYTES} bytes; not read"
        return json.loads(path.read_text(encoding="utf-8")), None
    except PermissionError:
        return None, "permission denied"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"unreadable: {type(exc).__name__}"
    except ValueError as exc:
        return None, f"invalid JSON: {exc}"


def _is_gateway_command(command: str, args: list[str]) -> bool:
    line = " ".join([command, *args])
    return any(m in line for m in GATEWAY_MARKERS)


def classify_server(runtime: str, name: str, spec: Any, source: str) -> Discovery:
    """One configured MCP server entry."""
    if not isinstance(spec, dict):
        return Discovery(
            "mcp_server", runtime, str(name), UNKNOWN, source, 0.3, "server entry is not an object"
        )
    command = str(spec.get("command") or "")
    args = [str(a) for a in spec.get("args") or [] if isinstance(a, (str, int, float))]
    url = spec.get("url") or spec.get("serverUrl")
    details: dict[str, Any] = {
        "command": Path(command).name if command else None,
        "args": redact_args(args),
        "transport": spec.get("type") or ("http" if url else "stdio"),
        "env_vars": sorted(
            str(k) for k in (spec.get("env") or {}) if isinstance(spec.get("env"), dict)
        ),
    }
    if url:
        details["url"] = re.sub(r"(?i)([?&](token|key|api_key)=)[^&]+", r"\1[REDACTED]", str(url))
    if spec.get("disabled") is True:
        details["disabled"] = True
    if command and _is_gateway_command(command, args):
        return Discovery(
            "mcp_server",
            runtime,
            str(name),
            MONITORED,
            source,
            0.95,
            "the configured command runs the AgentWatch MCP gateway",
            details=details,
        )
    if url:
        return Discovery(
            "mcp_server",
            runtime,
            str(name),
            UNKNOWN,
            source,
            0.5,
            "remote server URL: AgentWatch cannot tell whether it is a gateway URL",
            details=details,
        )
    if command:
        return Discovery(
            "mcp_server",
            runtime,
            str(name),
            UNMONITORED,
            source,
            0.9,
            "the server is started directly, not through the AgentWatch gateway",
            details=details,
        )
    return Discovery(
        "mcp_server", runtime, str(name), UNKNOWN, source, 0.3, "no command or url", details=details
    )


def _hook_installed(doc: Any) -> bool:
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    if not isinstance(hooks, dict):
        return False
    for entry in hooks.get("PreToolUse") or []:
        for h in (entry or {}).get("hooks") or [] if isinstance(entry, dict) else []:
            if isinstance(h, dict) and HOOK_MARKER in str(h.get("command", "")):
                return True
    return False


def scan_configs(home: Path | None = None, projects: Iterable[Path] = ()) -> list[Discovery]:
    home = home or Path.home()
    out: list[Discovery] = []
    hook_files: list[str] = []
    claude_code_seen = False
    for runtime, path, fmt in config_locations(home, projects):
        if not path.is_file():
            continue
        doc, err = _read_json(path)
        src = str(path)
        if err:
            out.append(
                Discovery("runtime", runtime, path.name, UNKNOWN, src, 0.4, f"config file {err}")
            )
            continue
        if runtime == "claude-code":
            claude_code_seen = True
        if fmt == "claude_settings":
            if _hook_installed(doc):
                hook_files.append(src)
            continue
        servers: dict[str, Any] = {}
        if fmt == "mcpServers" and isinstance(doc, dict):
            servers = doc.get("mcpServers") or {}
        elif fmt == "vscode" and isinstance(doc, dict):
            servers = doc.get("servers") or {}
        elif fmt == "claude_json" and isinstance(doc, dict):
            servers = dict(doc.get("mcpServers") or {})
            for proj, pdoc in (
                (doc.get("projects") or {}).items() if isinstance(doc.get("projects"), dict) else []
            ):
                for n, spec in (
                    ((pdoc or {}).get("mcpServers") or {}).items() if isinstance(pdoc, dict) else []
                ):
                    servers[f"{n} ({Path(str(proj)).name})"] = spec
        if not isinstance(servers, dict):
            out.append(
                Discovery(
                    "runtime", runtime, path.name, UNKNOWN, src, 0.4, "mcpServers is not an object"
                )
            )
            continue
        for name, spec in list(servers.items())[:500]:
            out.append(classify_server(runtime, name, spec, src))
        if runtime in ("cursor", "vscode", "windsurf"):
            out.append(
                Discovery(
                    "runtime",
                    runtime,
                    runtime,
                    UNSUPPORTED,
                    src,
                    0.8,
                    f"{runtime}'s built-in agent tools have no AgentWatch hook; only its MCP servers can be put behind the gateway",
                )
            )
    if claude_code_seen or hook_files:
        if hook_files:
            out.append(
                Discovery(
                    "runtime",
                    "claude-code",
                    "claude-code",
                    MONITORED,
                    hook_files[0],
                    0.85,
                    "the AgentWatch PreToolUse hook is installed",
                    details={"hook_files": hook_files},
                )
            )
        else:
            out.append(
                Discovery(
                    "runtime",
                    "claude-code",
                    "claude-code",
                    UNMONITORED,
                    str(home / ".claude"),
                    0.8,
                    "Claude Code settings found without the AgentWatch PreToolUse hook",
                )
            )
    return out


# ── processes and ports ──────────────────────────────────────────────────────────

# (runtime, matcher over the lower-cased process name and command line, status if found)
_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "agentwatch-gateway",
        re.compile(
            r"panoptis[./\\]protect[./\\]mcp_gateway|panoptis(\.exe)?\s.*protect\s+mcp-gateway|-m\s+panoptis\.protect\.mcp_gateway"
        ),
    ),
    (
        # the desktop app's main process (not its Electron helpers, skipped below)
        "claude-desktop",
        re.compile(
            r"[\\/]windowsapps[\\/]claude_[^\\/]*[\\/]app[\\/]claude\.exe"
            r"|[\\/]anthropicclaude[\\/].*claude\.exe"
            r"|claude\.app[\\/]contents[\\/]macos[\\/]claude(\s|$)"
        ),
    ),
    (
        "claude-code",
        re.compile(
            r"[\\/]claude-code[\\/]|@anthropic-ai[\\/]claude-code"
            r"|(^|[\\/\s])claude(\.exe|\.cmd)?(\s|$)"
        ),
    ),
    ("cursor", re.compile(r"(^|[\\/])cursor(\.exe)?(\s|$)")),
    ("codex", re.compile(r"(^|[\\/\s])codex(\.exe|\.cmd)?(\s|$)|@openai[\\/]codex")),
    ("aider", re.compile(r"(^|[\\/\s])aider(\.exe)?(\s|$)")),
    ("goose", re.compile(r"(^|[\\/\s])goose(\.exe)?\s+(session|run)")),
    (
        "mcp-server",
        re.compile(
            r"@modelcontextprotocol[\\/]server-|mcp-server-|\bmcp[_-]server\b|fastmcp|-m\s+mcp\b"
        ),
    ),
)
_UNSUPPORTED_RUNTIMES = {"cursor", "codex", "aider", "goose", "claude-desktop"}


def classify_process(name: str, cmdline: list[str]) -> str | None:
    text = " ".join([name, *cmdline]).lower()
    if " --type=" in text:
        return None  # an Electron/Chromium helper (renderer, gpu, utility) of an app, not a runtime
    for runtime, pat in _SIGNATURES:
        if pat.search(text):
            return runtime
    return None


def _settings_have_hook(cwd: Path | None, home: Path) -> bool | None:
    files = [home / ".claude" / "settings.json"]
    if cwd is not None:
        files += [cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"]
    found = False
    for f in files:
        if f.is_file():
            doc, err = _read_json(f)
            if err is None and _hook_installed(doc):
                found = True
    return found


def scan_processes(
    *,
    home: Path | None = None,
    max_processes: int = 5000,
    include_ports: bool = True,
    processes: Iterable[Any] | None = None,
) -> list[Discovery]:
    """Recognised agent and MCP processes. ``processes`` (psutil-like objects) is for tests."""
    home = home or Path.home()
    if processes is None:
        try:
            import psutil
        except ImportError:
            return [
                Discovery(
                    "process",
                    "unknown",
                    "process scan",
                    UNKNOWN,
                    "psutil",
                    0.0,
                    "psutil is not installed; running agents were not scanned (pip install psutil)",
                )
            ]
        processes = psutil.process_iter(["pid", "name", "cmdline"])
    out: list[Discovery] = []
    me = os.getpid()
    for i, proc in enumerate(processes):
        if i >= max_processes:
            out.append(
                Discovery(
                    "process",
                    "unknown",
                    "process scan",
                    UNKNOWN,
                    "psutil",
                    0.5,
                    f"stopped after {max_processes} processes",
                )
            )
            break
        try:
            info = proc.info if hasattr(proc, "info") else proc.as_dict(["pid", "name", "cmdline"])
            pid, name, cmdline = (
                info.get("pid"),
                str(info.get("name") or ""),
                [str(c) for c in info.get("cmdline") or []],
            )
        except Exception:  # noqa: BLE001, S112  # nosec B112 - access denied or the process exited
            continue
        if pid == me:
            continue
        runtime = classify_process(name, cmdline)
        if runtime is None:
            continue
        details: dict[str, Any] = {
            "pid": pid,
            "process": name,
            "args": redact_args(cmdline[1:])[:40],
        }
        if runtime == "agentwatch-gateway":
            status, conf, reason = MONITORED, 0.95, "an AgentWatch MCP gateway process"
        elif runtime == "claude-code":
            try:
                cwd = Path(proc.cwd())
            except Exception:  # noqa: BLE001
                cwd = None
            has = _settings_have_hook(cwd, home)
            if cwd is None:
                status, conf, reason = (
                    UNKNOWN,
                    0.4,
                    "working directory not readable; cannot tell which settings apply",
                )
            elif has:
                status, conf, reason = (
                    MONITORED,
                    0.75,
                    "a settings file that applies to it has the AgentWatch hook",
                )
            else:
                status, conf, reason = (
                    UNMONITORED,
                    0.75,
                    "no applicable settings file has the AgentWatch hook",
                )
            if cwd is not None:
                details["cwd"] = str(cwd)
        elif runtime == "mcp-server":
            parent = None
            try:
                p = proc.parent()
                parent = p.as_dict(["name", "cmdline"]) if p else None
            except Exception:  # noqa: BLE001
                parent = None
            pline = " ".join(
                [
                    str((parent or {}).get("name") or ""),
                    *[str(c) for c in (parent or {}).get("cmdline") or []],
                ]
            )
            if parent and any(m in pline for m in GATEWAY_MARKERS):
                status, conf, reason = MONITORED, 0.85, "started by an AgentWatch MCP gateway"
            elif parent is None:
                status, conf, reason = UNKNOWN, 0.4, "parent process not readable"
            else:
                status, conf, reason = UNMONITORED, 0.75, "not started by an AgentWatch MCP gateway"
        elif runtime in _UNSUPPORTED_RUNTIMES:
            status, conf, reason = (
                UNSUPPORTED,
                0.7,
                f"{runtime}'s own tools have no AgentWatch integration; put its MCP servers behind the gateway",
            )
        else:
            status, conf, reason = UNKNOWN, 0.3, "recognised, status unknown"
        if include_ports:
            try:
                ports = sorted(
                    {c.laddr.port for c in proc.net_connections(kind="tcp") if c.status == "LISTEN"}
                )[:20]
            except Exception:  # noqa: BLE001
                ports = []
            if ports:
                details["listening_ports"] = ports
        out.append(
            Discovery(
                "process",
                runtime,
                name or runtime,
                status,
                f"pid:{pid}",
                conf,
                reason,
                details=details,
            )
        )
    return out


def discover(
    *, home: Path | None = None, projects: Iterable[Path] = (), processes: bool = True
) -> dict[str, Any]:
    """The full inventory: configured servers, runtimes and running processes."""
    found = scan_configs(home, projects)
    if processes:
        found += scan_processes(home=home)
    counts: dict[str, int] = {}
    for d in found:
        counts[d.status] = counts.get(d.status, 0) + 1
    return {
        "discovered_at": time.time(),
        "platform": sys.platform,
        "counts": counts,
        "items": [d.to_dict() for d in found],
    }


# ── lifecycle: put one configured server behind the gateway ──────────────────────


def wrap_server(
    path: Path,
    name: str,
    *,
    apply: bool = False,
    python: str | None = None,
    key: str = "mcpServers",
) -> dict[str, Any]:
    """Rewrite the ``name`` entry of an MCP config file to run behind the gateway.

    Returns the old and new entries. Writes only with ``apply=True``, after saving
    ``<file>.agentwatch.bak``; never touches other entries or running processes.
    """
    doc, err = _read_json(path)
    if err or not isinstance(doc, dict):
        raise ValueError(f"cannot read {path}: {err or 'not an object'}")
    servers = doc.get(key)
    if not isinstance(servers, dict) or name not in servers:
        raise KeyError(f"{path} has no {key}.{name}")
    spec = servers[name]
    if not isinstance(spec, dict) or not spec.get("command"):
        raise ValueError(
            f"{key}.{name} is not a stdio server (no command); put an HTTP server behind the gateway with 'mcp-gateway http'"
        )
    if _is_gateway_command(str(spec["command"]), [str(a) for a in spec.get("args") or []]):
        return {"changed": False, "reason": "already behind the gateway", "entry": spec}
    new = dict(spec)
    new["command"] = python or sys.executable
    new["args"] = [
        "-m",
        "panoptis.protect.mcp_gateway",
        "stdio",
        "--server-name",
        name,
        "--",
        str(spec["command"]),
        *[str(a) for a in spec.get("args") or []],
    ]
    result = {
        "changed": True,
        "file": str(path),
        "server": name,
        "before": spec,
        "after": new,
        "applied": False,
    }
    if apply:
        backup = path.with_name(path.name + ".agentwatch.bak")
        shutil.copy2(path, backup)
        servers[name] = new
        tmp = path.with_name(path.name + ".agentwatch.tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        result.update(applied=True, backup=str(backup))
    return result


def record_inventory(
    inventory: dict[str, Any], *, store: str | None = None, tenant_id: str = "default"
) -> str | None:
    """Store an inventory in the evidence ledger (``protect.discovery``) for the dashboard."""
    from panoptis.protect.ledger import KIND_DISCOVERY, ProtectLedger

    lg = ProtectLedger(store or os.environ.get("AGENTWATCH_STORE") or None)
    try:
        return lg.record(
            KIND_DISCOVERY,
            inventory,
            {"platform": inventory.get("platform")},
            tenant_id=tenant_id,
        )
    finally:
        lg.store.close()
