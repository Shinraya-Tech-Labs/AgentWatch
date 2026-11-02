"""Structured analysis of tool-call arguments (WP-01).

Regex over a flattened string misses structure: ``rm -rf "$DIR"`` inside ``$(...)``, a
``DELETE`` with no ``WHERE`` in the second of two stacked statements, ``os.system`` reached
through an alias in a code argument. This module extracts every string from the arguments
(recursively, decoding base64 and URL encoding, normalising homoglyphs), recognises what
kind of content each one is, and parses it:

* shell commands are split into pipelines/command lists, command substitutions are
  recursed into, wrappers (``sudo``, ``env``, ``xargs``, ...) are unwrapped, and each
  command is classified by its name and flags;
* Python code is parsed with :mod:`ast` and dangerous calls are resolved through imports
  and aliases;
* SQL is split into statements and destructive statements (``DROP``, ``TRUNCATE``,
  ``DELETE``/``UPDATE`` without a ``WHERE``, tautological ``WHERE``) are recognised;
* file paths and URLs are extracted and checked for sensitive locations, traversal,
  private/metadata hosts and known exfiltration endpoints.

The output is a list of :class:`Finding` plus an :class:`ArgumentProfile` that later stages
(RBAC argument restrictions, intent grounding) reuse, so the arguments are parsed once.
"""

from __future__ import annotations

import ast
import base64
import binascii
import ipaddress
import re
import shlex
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.injection import _normalize
from panoptis.protect.model import Finding, Severity, Stage, Verdict
from panoptis.security.owasp import OwaspVector

# ── what a string is ───────────────────────────────────────────────────────────────

SHELL_KEYS = {
    "command",
    "cmd",
    "shell",
    "script",
    "bash",
    "sh",
    "powershell",
    "exec",
    "commandline",
}
CODE_KEYS = {"code", "python", "source", "program", "snippet"}
SQL_KEYS = {"sql", "query", "statement", "stmt"}
PATH_KEYS = {
    "path",
    "file",
    "filename",
    "file_path",
    "filepath",
    "target",
    "dest",
    "destination",
    "src",
    "source_path",
    "directory",
    "dir",
}
URL_KEYS = {"url", "uri", "endpoint", "href", "webhook", "link", "host"}

SHELL_TOOLS = re.compile(
    r"(?i)(^|[_.\-])(bash|shell|sh|exec|execute|terminal|run_command|cmd|powershell|subprocess|run)($|[_.\-])"
)
CODE_TOOLS = re.compile(r"(?i)(python|code_interpreter|run_code|execute_code|eval|jupyter|repl)")
SQL_TOOLS = re.compile(r"(?i)(sql|query|database|db_|postgres|mysql|sqlite|bigquery|snowflake)")
NETWORK_TOOLS = re.compile(
    r"(?i)(http|fetch|request|curl|wget|post|upload|send|email|mail|slack|webhook|message|notify|publish|tweet|discord)"
)

# ── sensitive locations ────────────────────────────────────────────────────────────

SENSITIVE_PATHS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[/\\])\.ssh([/\\]|$)|id_(rsa|ed25519|ecdsa|dsa)\b"), "ssh keys"),
    (re.compile(r"(^|[/\\])\.aws[/\\](credentials|config)\b"), "AWS credentials"),
    (
        re.compile(r"(^|[/\\])\.(env|envrc)(\.[\w.-]+)?$|(^|[/\\])\.env\b"),
        "environment secrets file",
    ),
    (re.compile(r"(^|[/\\])\.(netrc|pgpass|git-credentials|npmrc|pypirc)\b"), "credential file"),
    (re.compile(r"(^|[/\\])\.(kube|docker)[/\\]config\b"), "cluster/registry credentials"),
    (re.compile(r"(^|[/\\])\.gnupg([/\\]|$)"), "GPG keyring"),
    (
        re.compile(
            r"^(?:[A-Za-z]:)?[/\\]etc/(shadow|gshadow|sudoers)\b|^(?:[A-Za-z]:)?[/\\]etc/passwd\b"
        ),
        "system account database",
    ),
    (re.compile(r"(?i)[/\\]system32[/\\]config[/\\](sam|system|security)\b"), "Windows SAM hive"),
    (re.compile(r"(?i)(^|[/\\])(credentials|secrets?)\.(json|ya?ml|toml|txt)$"), "secrets file"),
)
SYSTEM_PATHS = re.compile(
    r"^(/|/etc|/usr|/bin|/sbin|/boot|/lib|/lib64|/var|/root|/home|/opt|/sys|/proc|/dev|~|\$HOME|"
    r"[A-Za-z]:\\?|[A-Za-z]:\\(Windows|Program Files|Users))(/|\\)?$",
    re.I,
)
# anything under a system directory (SYSTEM_PATHS only matches the directories themselves)
SYSTEM_PREFIX = re.compile(
    r"^(/etc|/usr|/bin|/sbin|/boot|/lib|/lib64|/root|/sys|/proc|/dev|[A-Za-z]:[\\/](Windows|Program Files))[\\/]",
    re.I,
)
EXFIL_HOSTS = re.compile(
    r"(?i)(pastebin\.com|paste\.ee|hastebin|transfer\.sh|file\.io|0x0\.st|ngrok(-free)?\.(io|app)|"
    r"webhook\.site|requestbin|pipedream\.net|interact\.sh|oast\.(fun|me|pro|live|site|online)|burpcollaborator|"
    r"temp\.sh|anonfiles|catbox\.moe|discord(app)?\.com/api/webhooks)"
)
METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "100.100.100.200", "fd00:ec2::254"}

# ── shell command knowledge ────────────────────────────────────────────────────────

WRAPPERS = {
    "sudo",
    "doas",
    "env",
    "nohup",
    "time",
    "nice",
    "ionice",
    "timeout",
    "xargs",
    "command",
    "builtin",
    "exec",
    "stdbuf",
    "chroot",
    "su",
}
PRIVILEGE = {"sudo", "doas", "su", "pkexec", "runas"}
SHELLS = {
    "sh",
    "bash",
    "zsh",
    "dash",
    "ksh",
    "fish",
    "pwsh",
    "powershell",
    "cmd",
    "python",
    "python3",
    "perl",
    "ruby",
    "node",
    "php",
}
DOWNLOADERS = {"curl", "wget", "iwr", "invoke-webrequest", "irm", "invoke-restmethod", "fetch"}
NETWORK_CMDS = DOWNLOADERS | {
    "nc",
    "ncat",
    "netcat",
    "scp",
    "sftp",
    "rsync",
    "ftp",
    "telnet",
    "socat",
    "ssh",
}
READ_CMDS = {
    "cat",
    "less",
    "more",
    "head",
    "tail",
    "type",
    "get-content",
    "gc",
    "base64",
    "xxd",
    "od",
    "strings",
    "tar",
    "zip",
    "gzip",
}
READ_ONLY_CMDS = {
    "ls",
    "dir",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "grep",
    "rg",
    "pwd",
    "echo",
    "which",
    "whoami",
    "wc",
    "stat",
    "file",
    "du",
    "df",
    "ps",
    "env",
    "printenv",
    "git",
    "tree",
    "date",
    "uname",
    "type",
    "get-childitem",
    "get-content",
    "select-string",
    "sort",
    "uniq",
    "diff",
}


@dataclass
class ShellCommand:
    """One simple command inside a command line."""

    name: str
    argv: list[str]
    text: str
    privileged: bool = False
    piped_to_next: bool = False


@dataclass
class ArgumentProfile:
    """What the arguments contain, parsed once and shared with later stages."""

    strings: list[tuple[str, str]] = field(default_factory=list)  # (json path, text)
    commands: list[ShellCommand] = field(default_factory=list)
    sql_statements: list[str] = field(default_factory=list)
    sql_tables: set[str] = field(default_factory=set)
    paths: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    hosts: set[str] = field(default_factory=set)
    destructive: bool = False  # some part of the call deletes, overwrites or mutates state
    mutating: bool = False  # writes anything (superset of destructive)
    network: bool = False  # sends data off the host
    reads_sensitive: bool = False

    @property
    def text(self) -> str:
        return "\n".join(t for _, t in self.strings)

    def command_names(self) -> list[str]:
        return [c.name for c in self.commands]


# ── deep string extraction ─────────────────────────────────────────────────────────

_B64 = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")
_URLENC = re.compile(r"%[0-9A-Fa-f]{2}")


def _decoded_variants(text: str) -> list[tuple[str, str]]:
    """Encodings an attacker can wrap a payload in; each decoded form is scanned too."""
    out: list[tuple[str, str]] = []
    norm = _normalize(text)
    if norm != text:
        out.append(("normalized", norm))
    if _URLENC.search(text):
        dec = urllib.parse.unquote(text)
        if dec != text:
            out.append(("urldecoded", dec))
    stripped = text.strip()
    if _B64.match(stripped) and len(stripped) % 4 == 0:
        try:
            raw = base64.b64decode(stripped, validate=True)
            dec = raw.decode("utf-8")
            if dec and sum(c.isprintable() or c in "\n\t" for c in dec) / len(dec) > 0.95:
                out.append(("base64", dec))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    return out


def extract_strings(value: Any, path: str = "$", depth: int = 0) -> list[tuple[str, str]]:
    """Every string in ``value`` with its JSON path; decoded variants get a suffix path."""
    if depth > 12:
        return []
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path, value))
        for kind, dec in _decoded_variants(value):
            found.append((f"{path}<{kind}>", dec))
    elif isinstance(value, dict):
        for k, v in value.items():
            found.extend(extract_strings(v, f"{path}.{k}", depth + 1))
    elif isinstance(value, (list, tuple, set)):
        for i, v in enumerate(value):
            found.extend(extract_strings(v, f"{path}[{i}]", depth + 1))
    elif value is not None and not isinstance(value, (int, float, bool)):
        found.append((path, str(value)))
    return found


def _key_of(path: str) -> str:
    leaf = re.split(r"[.\[]", path.split("<")[0])[-1].rstrip("]")
    return leaf.lower()


# ── shell parsing ──────────────────────────────────────────────────────────────────

_SUBST = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`")


def _split_segments(line: str) -> list[tuple[str, str]]:
    """Split on ``;`` ``&&`` ``||`` ``|`` ``&`` and newlines *outside quotes*.

    Returns ``(segment, separator_after)`` pairs; quoted separators (``bash -c 'a | b'``)
    stay inside their segment, so the quoted payload is analysed as one argument.
    """
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(line[i : i + 2])
            i += 2
            continue
        two = line[i : i + 2]
        if two in ("&&", "||"):
            out.append(("".join(buf), two))
            buf = []
            i += 2
            continue
        if ch in ";\n|":
            out.append(("".join(buf), ch))
            buf = []
            i += 1
            continue
        if (
            ch == "&"
            and not (buf and buf[-1] in (">", "&"))
            and line[i + 1 : i + 2] not in (">", "&")
        ):
            out.append(("".join(buf), "&"))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append(("".join(buf), ""))
    return out


def split_command_line(line: str, depth: int = 0) -> list[ShellCommand]:
    """Split a shell command line into simple commands (pipelines, lists, substitutions)."""
    if depth > 4 or not line.strip():
        return []
    commands: list[ShellCommand] = []
    for m in _SUBST.finditer(line):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        commands.extend(split_command_line(inner or "", depth + 1))
    flat = _SUBST.sub(" SUBST ", line)
    pipe_source: str | None = None
    assigned: dict[str, str] = {}  # x=rm; $x -rf /   (simple same-line assignments)
    for segment, sep in _split_segments(flat):
        segment = segment.strip()
        if not segment:
            pipe_source = None
            continue
        try:
            argv = shlex.split(segment, posix=True)
        except ValueError:
            argv = segment.split()
        privileged = False
        last_wrapper: str | None = None
        only_assignments = all(re.match(r"^[A-Za-z_]\w*=", a) for a in argv)
        # unwrap leading env assignments and wrappers (sudo -u x, env A=B, timeout 5, xargs -0)
        while argv:
            head = argv[0].rsplit("/", 1)[-1].lower()
            if (
                "=" in argv[0]
                and not argv[0].startswith("-")
                and re.match(r"^[A-Za-z_]\w*=", argv[0])
            ):
                if only_assignments:
                    k, _, v = argv[0].partition("=")
                    assigned[k] = v
                argv = argv[1:]
                continue
            if head in WRAPPERS:
                privileged = privileged or head in PRIVILEGE
                last_wrapper = head
                argv = argv[1:]
                while argv and (argv[0].startswith("-") or re.match(r"^\d+[smhd]?$", argv[0])):
                    flag = argv[0]
                    argv = argv[1:]
                    if flag in ("-u", "-g", "-n", "-I", "-d", "-s", "-k", "--user") and argv:
                        argv = argv[1:]
                continue
            break
        if not argv and last_wrapper is not None:
            # a wrapper with nothing after it is a command itself: `sudo su -`, `sudo -i`
            # (an interactive root shell), a bare `env` (prints the environment)
            argv = [last_wrapper]
        if not argv:
            pipe_source = None
            continue
        var = re.match(r"^\$\{?([A-Za-z_]\w*)\}?$", argv[0])
        if var and var.group(1) in assigned:
            argv = [assigned[var.group(1)], *argv[1:]]
        name = argv[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if name.endswith(".exe"):
            name = name[:-4]
        commands.append(
            ShellCommand(
                name=name, argv=argv, text=segment, privileged=privileged, piped_to_next=sep == "|"
            )
        )
        # text piped into an interpreter is code: echo '...' | sh
        if pipe_source is not None and name in SHELLS and len(argv) == 1:
            commands.extend(split_command_line(pipe_source, depth + 1))
        if name == "base64" and pipe_source is not None and {"-d", "--decode", "-D"} & set(argv):
            # echo <b64> | base64 -d | sh: what reaches the shell is the decoded text
            try:
                pipe_source = base64.b64decode(pipe_source.strip(), validate=True).decode()
            except (binascii.Error, UnicodeDecodeError, ValueError):
                pipe_source = None
            if sep != "|":
                pipe_source = None
            continue
        pipe_source = " ".join(argv[1:]) if name in ("echo", "printf") and sep == "|" else None
        # find ... -exec CMD {} ;   (the executed command is a command too)
        if name == "find":
            for i, a in enumerate(argv):
                if a in ("-exec", "-execdir", "-ok", "-okdir"):
                    rest = []
                    for b in argv[i + 1 :]:
                        if b in (";", "+", r"\;"):
                            break
                        rest.append(b)
                    if rest:
                        commands.extend(split_command_line(shlex.join(rest), depth + 1))
        # bash -c "..." / sh -c / powershell -Command "..."
        for i, a in enumerate(argv[1:], start=1):
            if a in ("-c", "-Command", "-command", "/c", "-EncodedCommand", "-enc") and i + 1 < len(
                argv
            ):
                payload = argv[i + 1]
                if a.lower() in ("-encodedcommand", "-enc"):
                    try:
                        payload = base64.b64decode(payload).decode("utf-16-le")
                    except (binascii.Error, UnicodeDecodeError, ValueError):
                        pass
                commands.extend(split_command_line(payload, depth + 1))
                break
    return commands


def _sets_setuid(mode: str) -> bool:
    """chmod mode that sets setuid (4xxx) or setgid (2xxx), numerically or as +s."""
    m = re.fullmatch(r"0?([0-7])[0-7]{3}", mode)
    if m:
        return bool(int(m.group(1)) & 6)
    return bool(re.fullmatch(r"[ugoa]*[+=][rwxXst]*s[rwxXst]*", mode))


def _flags(argv: list[str]) -> set[str]:
    out: set[str] = set()
    for a in argv[1:]:
        if a.startswith("--"):
            out.add(a.split("=", 1)[0].lower())
        elif a.startswith("-") and len(a) > 1 and not a[1:].isdigit():
            out.update(f"-{c}" for c in a[1:].lower())
            out.add(a.lower())
    return out


def _operands(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if not a.startswith("-")]


def is_system_path(p: str) -> bool:
    p = p.strip().strip("'\"")
    if p in ("*", "/*", "~/*", ".*", "$HOME/*"):
        return True
    return bool(SYSTEM_PATHS.match(p.rstrip("*")))


_CLIMB = re.compile(r"^(?:\.{1,2}[/\\])*\.\.[/\\]")


def sensitive_path(p: str) -> str | None:
    norm = p.strip().strip("'\"").replace("\\\\", "\\")
    candidates = [norm]
    if _CLIMB.match(norm):
        # ../../../../etc/shadow climbs out of the working directory towards the root:
        # judge where it lands as an absolute path too (/etc/shadow)
        rest = re.sub(r"^(?:\.{1,2}[/\\])+", "", norm)
        candidates.append("/" + rest.replace("\\", "/"))
    for candidate in candidates:
        for pat, label in SENSITIVE_PATHS:
            if pat.search(candidate):
                return label
    return None


def _host_of(url: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(url if "://" in url else f"http://{url}")
        return (parsed.hostname or "").lower() or None
    except ValueError:
        return None


def _parse_ipv4_part(part: str) -> int:
    if part.lower().startswith("0x"):
        return int(part, 16)
    if len(part) > 1 and part.startswith("0"):
        return int(part, 8)
    return int(part, 10)


def canonical_host(host: str) -> str:
    """The address a host string really names, the way resolvers read it.

    ``2852039166``, ``0xa9fea9fe``, ``0251.0376.0251.0376`` and
    ``::ffff:169.254.169.254`` are all 169.254.169.254.
    """
    h = host.strip("[]").lower()
    try:
        ip = ipaddress.ip_address(h)
        mapped = getattr(ip, "ipv4_mapped", None)
        return str(mapped or ip)
    except ValueError:
        pass
    parts = h.split(".")
    if 1 <= len(parts) <= 4 and all(re.fullmatch(r"0x[0-9a-f]+|[0-9]+", p) for p in parts):
        try:
            nums = [_parse_ipv4_part(p) for p in parts]
            value = 0
            for n in nums[:-1]:
                if n > 255:
                    return h
                value = value * 256 + n
            value = value * 256 ** (4 - len(parts) + 1) + nums[-1]
            if 0 <= value < 2**32:
                return str(ipaddress.IPv4Address(value))
        except ValueError:
            pass
    return h


def _is_private_host(host: str) -> bool:
    if host in ("localhost", "localhost.localdomain") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(canonical_host(host))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


_URL_RE = re.compile(r"\b(?:https?|ftp|wss?|file)://[^\s'\"<>|;)]+", re.I)
_PATH_RE = re.compile(r"(?:(?<=\s)|^)((?:~|\.{1,2}|[A-Za-z]:)?[/\\][\w.\-~/\\* $]+)")


# ── the analyzer ───────────────────────────────────────────────────────────────────


class ArgumentAnalyzer:
    """Parse tool-call arguments and report what is dangerous in them."""

    def analyze(
        self, tool: str, arguments: dict[str, Any]
    ) -> tuple[ArgumentProfile, list[Finding]]:
        profile = ArgumentProfile(strings=extract_strings(arguments))
        findings: list[Finding] = []
        shell_tool = bool(SHELL_TOOLS.search(tool))
        code_tool = bool(CODE_TOOLS.search(tool))
        sql_tool = bool(SQL_TOOLS.search(tool))
        profile.network = bool(NETWORK_TOOLS.search(tool))
        lowered_tool = tool.lower()
        if re.search(
            r"(write|delete|remove|drop|update|insert|create|move|rename|put|patch|exec|run|send|post|kill|deploy|push)",
            lowered_tool,
        ):
            profile.mutating = True
        if re.search(
            r"(delete|remove|drop|destroy|truncate|purge|wipe|kill|terminate|format|reset)",
            lowered_tool,
        ):
            profile.destructive = True

        for path, text in profile.strings:
            key = _key_of(path)
            if not text.strip():
                continue
            is_shell = key in SHELL_KEYS or (
                shell_tool and key in SHELL_KEYS | {"args", "input", "arguments", "argv", ""}
            )
            is_code = key in CODE_KEYS or (code_tool and key not in PATH_KEYS | URL_KEYS)
            is_sql = key in SQL_KEYS or (sql_tool and key not in PATH_KEYS | URL_KEYS)
            if is_shell or (shell_tool and len(profile.strings) == 1):
                findings.extend(self._shell(path, text, profile))
            if is_code:
                findings.extend(self._python(path, text, profile))
            if is_sql:
                findings.extend(self._sql(path, text, profile))
            if key in PATH_KEYS:
                self._add_path(text, profile)
            if key in URL_KEYS:
                self._add_url(text, profile)
            for m in _URL_RE.finditer(text):
                self._add_url(m.group(0), profile)
        findings.extend(self._paths_and_urls(profile))
        if profile.reads_sensitive and profile.network:
            findings.append(
                Finding(
                    Stage.THREAT,
                    "EXFIL_SENSITIVE_READ_AND_SEND",
                    Severity.CRITICAL,
                    "The call reads a credential/secret location and sends data over the network",
                    {"hosts": sorted(profile.hosts), "paths": profile.paths[:10]},
                    OwaspVector.DATA_EXFILTRATION.value,
                    Verdict.BLOCK,
                )
            )
        return profile, _dedupe(findings)

    # ── shell ──────────────────────────────────────────────────────────────────────
    def _shell(self, path: str, text: str, profile: ArgumentProfile) -> list[Finding]:
        findings: list[Finding] = []
        cmds = split_command_line(text)
        profile.commands.extend(cmds)
        names = [c.name for c in cmds]
        for c in cmds:
            findings.extend(self._classify_command(path, c, profile))
        # the environment (where credentials live) piped to a network command
        for i, c in enumerate(cmds[:-1]):
            dumps_env = c.name in ("env", "printenv", "set", "export") and len(c.argv) == 1
            if dumps_env and c.piped_to_next and any(d.name in NETWORK_CMDS for d in cmds[i + 1 :]):
                findings.append(
                    Finding(
                        Stage.THREAT,
                        "EXFIL_ENVIRONMENT",
                        Severity.CRITICAL,
                        "Sends the process environment (credentials, tokens) over the network",
                        {"path": path, "command": text[:300]},
                        OwaspVector.DATA_EXFILTRATION.value,
                        Verdict.BLOCK,
                    )
                )
        # downloader piped into an interpreter: curl ... | sh
        for i, c in enumerate(cmds[:-1]):
            nxt = cmds[i + 1]
            if c.name in DOWNLOADERS and nxt.name in SHELLS and c.piped_to_next:
                findings.append(
                    Finding(
                        Stage.THREAT,
                        "RCE_DOWNLOAD_PIPE_SHELL",
                        Severity.CRITICAL,
                        f"Downloads remote content and pipes it into {nxt.name}",
                        {"path": path, "command": text[:300]},
                        OwaspVector.UNSAFE_CODE_EXEC.value,
                        Verdict.BLOCK,
                    )
                )
        if re.search(r"/dev/tcp/|\bnc(at)?\b[^|;]*\s-e\s|mkfifo[^;]*\bnc\b|socat[^;]*exec:", text):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "REVERSE_SHELL",
                    Severity.CRITICAL,
                    "Opens a reverse shell",
                    {"path": path, "command": text[:300]},
                    OwaspVector.UNSAFE_CODE_EXEC.value,
                    Verdict.BLOCK,
                )
            )
        if re.search(r"(?i)\b(iex|invoke-expression)\b", text) and any(
            n in DOWNLOADERS for n in names
        ) | bool(re.search(r"(?i)downloadstring|iwr|irm", text)):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "RCE_POWERSHELL_IEX",
                    Severity.CRITICAL,
                    "Downloads and evaluates a remote PowerShell script",
                    {"path": path, "command": text[:300]},
                    OwaspVector.UNSAFE_CODE_EXEC.value,
                    Verdict.BLOCK,
                )
            )
        if re.search(r"(?i)\bbase64\s+(-d|--decode)\s*\|\s*(sh|bash|zsh|dash)\b", text):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "OBFUSCATED_PIPE_SHELL",
                    Severity.CRITICAL,
                    "Decodes base64 payload and pipes directly into shell",
                    {"path": path, "command": text[:300]},
                    OwaspVector.UNSAFE_CODE_EXEC.value,
                    Verdict.BLOCK,
                )
            )
        if re.search(r"(?i)\bpowershell\b.*-(enc|encodedcommand)\b", text):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "RCE_POWERSHELL_ENCODED",
                    Severity.CRITICAL,
                    "Executes base64-encoded command in PowerShell",
                    {"path": path, "command": text[:300]},
                    OwaspVector.UNSAFE_CODE_EXEC.value,
                    Verdict.BLOCK,
                )
            )
        if re.search(r"(?i)\beval\s+\$\(curl\b|\beval\s+`curl\b", text):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "RCE_DOWNLOAD_EVAL",
                    Severity.CRITICAL,
                    "Downloads and evaluates remote payload via eval",
                    {"path": path, "command": text[:300]},
                    OwaspVector.UNSAFE_CODE_EXEC.value,
                    Verdict.BLOCK,
                )
            )
        if re.search(
            r"(?i)(>>?\s*~?/?\S*(\.bashrc|\.zshrc|\.profile|authorized_keys|crontab))|\bcrontab\s+-|systemctl\s+enable|schtasks\s+/create|reg\s+add\s+\S*\\run\b",
            text,
        ):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "PERSISTENCE",
                    Severity.HIGH,
                    "Installs persistence (shell profile, authorized_keys, cron, service or Run key)",
                    {"path": path, "command": text[:300]},
                    OwaspVector.EXCESSIVE_PERMISSIONS.value,
                    Verdict.ESCALATE_HITL,
                )
            )
            profile.mutating = True
        for m in re.finditer(r">\s*(/etc/\S+|/dev/sd\w+|/boot/\S+|[A-Za-z]:\\Windows\\\S+)", text):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "OVERWRITE_SYSTEM_FILE",
                    Severity.CRITICAL,
                    f"Redirects output over a system location ({m.group(1)})",
                    {"path": path, "target": m.group(1)},
                    OwaspVector.TOOL_ABUSE.value,
                    Verdict.BLOCK,
                )
            )
            profile.destructive = profile.mutating = True
        if re.search(r":\(\)\s*\{\s*:\|:&\s*\};:", text):
            findings.append(
                Finding(
                    Stage.THREAT,
                    "FORK_BOMB",
                    Severity.CRITICAL,
                    "Fork bomb",
                    {"path": path},
                    OwaspVector.TOOL_ABUSE.value,
                    Verdict.BLOCK,
                )
            )
        return findings

    def _classify_command(
        self, path: str, c: ShellCommand, profile: ArgumentProfile
    ) -> list[Finding]:
        out: list[Finding] = []
        flags, ops, n = _flags(c.argv), _operands(c.argv), c.name
        ev = {"path": path, "command": c.text[:300]}

        def add(
            code: str, sev: Severity, msg: str, threat: OwaspVector, rec: Verdict, **extra: Any
        ) -> None:
            out.append(Finding(Stage.THREAT, code, sev, msg, {**ev, **extra}, threat.value, rec))

        if c.privileged:
            add(
                "PRIVILEGE_ESCALATION",
                Severity.HIGH,
                "Runs with elevated privileges (sudo/doas/su)",
                OwaspVector.EXCESSIVE_PERMISSIONS,
                Verdict.ESCALATE_HITL,
            )
        if n.startswith("$"):
            add(
                "DYNAMIC_COMMAND",
                Severity.HIGH,
                f"The command to run comes from a variable ({c.argv[0]}); it cannot be checked",
                OwaspVector.UNSAFE_CODE_EXEC,
                Verdict.ESCALATE_HITL,
            )
        if n == "chmod" and any(_sets_setuid(a) for a in c.argv[1:]):
            add(
                "SETUID_BIT",
                Severity.CRITICAL,
                "Sets the setuid/setgid bit, a privilege-escalation primitive",
                OwaspVector.EXCESSIVE_PERMISSIONS,
                Verdict.BLOCK,
            )
        if (
            (
                n in ("usermod", "gpasswd", "adduser", "addgroup", "dseditgroup")
                and re.search(r"\b(sudo|wheel|admin|root|administrators)\b", c.text, re.I)
            )
            or (n == "net" and re.search(r"(?i)localgroup\s+administrators\b.*/add", c.text))
            or n == "visudo"
            or (n == "tee" and any("sudoers" in o for o in ops))
        ):
            add(
                "PRIVILEGE_GRANT",
                Severity.HIGH,
                "Grants administrative privileges (admin group membership or sudoers)",
                OwaspVector.EXCESSIVE_PERMISSIONS,
                Verdict.ESCALATE_HITL,
            )
        if n not in READ_ONLY_CMDS:
            profile.mutating = True
        for op in ops:
            self._add_path(op, profile)
            if (
                n in READ_CMDS | {"cp", "scp", "rsync", "curl", "nc", "tar", "zip"}
                or n in DOWNLOADERS
            ):
                label = sensitive_path(op)
                if label:
                    profile.reads_sensitive = True
        if n in (
            "rm",
            "del",
            "erase",
            "rmdir",
            "rd",
            "remove-item",
            "ri",
            "shred",
            "unlink",
            "srm",
        ):
            profile.destructive = True
            recursive = bool(
                flags & {"-r", "-R".lower(), "--recursive", "/s", "-recurse"}
            ) or n in ("rmdir", "rd")
            critical = [o for o in ops if is_system_path(o)]
            if critical and (recursive or "-f" in flags or "*" in "".join(critical)):
                add(
                    "FS_DELETE_CRITICAL",
                    Severity.CRITICAL,
                    f"Recursive/forced deletion of a system or home path: {', '.join(critical)}",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.BLOCK,
                    targets=critical,
                )
            elif recursive and (
                "-f" in flags or "--force" in flags or "/q" in flags or "-force" in flags
            ):
                add(
                    "FS_DELETE_RECURSIVE",
                    Severity.HIGH,
                    f"Recursive forced deletion: {' '.join(ops)[:120]}",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ESCALATE_HITL,
                    targets=ops,
                )
            elif n == "shred":
                add(
                    "FS_SHRED",
                    Severity.HIGH,
                    "Irrecoverably overwrites files",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ESCALATE_HITL,
                    targets=ops,
                )
            else:
                add(
                    "FS_DELETE",
                    Severity.LOW,
                    f"Deletes files: {' '.join(ops)[:120]}",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ALLOW,
                    targets=ops,
                )
        elif n == "find" and "-delete" in c.argv:
            profile.destructive = profile.mutating = True
            roots = [o for o in ops if not o.startswith(("(", ")", "!"))][:1]
            if roots and is_system_path(roots[0]):
                add(
                    "FS_DELETE_CRITICAL",
                    Severity.CRITICAL,
                    f"find -delete over a system or home path: {roots[0]}",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.BLOCK,
                    targets=roots,
                )
            else:
                add(
                    "FS_DELETE",
                    Severity.MEDIUM,
                    f"find -delete under {roots[0] if roots else '.'}",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ALLOW,
                    targets=roots,
                )
        elif n in ("mkfs", "fdisk", "parted", "diskpart", "format", "wipefs") or n.startswith(
            "mkfs."
        ):
            profile.destructive = True
            add(
                "DISK_FORMAT",
                Severity.CRITICAL,
                "Formats or repartitions a disk",
                OwaspVector.TOOL_ABUSE,
                Verdict.BLOCK,
            )
        elif n == "dd" and any(
            a.startswith("of=/dev/") or a.lower().startswith("of=\\\\.\\") for a in c.argv
        ):
            profile.destructive = True
            add(
                "DISK_RAW_WRITE",
                Severity.CRITICAL,
                "Writes raw bytes to a block device",
                OwaspVector.TOOL_ABUSE,
                Verdict.BLOCK,
            )
        elif n in ("chmod", "chown", "icacls", "takeown"):
            profile.mutating = True
            if any(is_system_path(o) for o in ops) and (flags & {"-r", "--recursive", "/t"}):
                add(
                    "PERMISSIONS_SYSTEM_RECURSIVE",
                    Severity.CRITICAL,
                    "Recursively changes permissions of a system path",
                    OwaspVector.EXCESSIVE_PERMISSIONS,
                    Verdict.BLOCK,
                )
            elif any(re.fullmatch(r"0?[0-7]?777|a\+rwx|\+s|u\+s|[ugo]*\+s", o) for o in ops):
                add(
                    "PERMISSIONS_WORLD_WRITABLE",
                    Severity.HIGH,
                    "Makes files world-writable or setuid",
                    OwaspVector.EXCESSIVE_PERMISSIONS,
                    Verdict.ESCALATE_HITL,
                )
        elif n in (
            "shutdown",
            "reboot",
            "halt",
            "poweroff",
            "init",
            "stop-computer",
            "restart-computer",
        ):
            profile.destructive = True
            add(
                "HOST_SHUTDOWN",
                Severity.HIGH,
                "Shuts down or reboots the host",
                OwaspVector.TOOL_ABUSE,
                Verdict.ESCALATE_HITL,
            )
        elif n in ("kill", "pkill", "killall", "taskkill", "stop-process"):
            profile.destructive = True
            if any(o in ("1", "-1") for o in ops) or "-9" in c.argv and "1" in ops:
                add(
                    "KILL_INIT",
                    Severity.CRITICAL,
                    "Kills PID 1 or every process",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.BLOCK,
                )
            else:
                add(
                    "PROCESS_KILL",
                    Severity.MEDIUM,
                    "Terminates processes",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ALLOW,
                )
        elif n == "git":
            sub = ops[0] if ops else ""
            if sub == "push" and flags & {"--force", "-f", "--force-with-lease", "--mirror"}:
                profile.destructive = profile.mutating = True
                add(
                    "GIT_FORCE_PUSH",
                    Severity.HIGH,
                    "Force-pushes, rewriting remote history",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ESCALATE_HITL,
                )
            elif (
                sub == "reset"
                and "--hard" in flags
                or sub == "clean"
                and flags & {"-f", "-x", "-d"}
            ):
                profile.destructive = profile.mutating = True
                add(
                    "GIT_DISCARD_WORK",
                    Severity.MEDIUM,
                    "Discards uncommitted work (reset --hard / clean)",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.ALLOW,
                )
            elif sub in (
                "push",
                "commit",
                "merge",
                "rebase",
                "tag",
                "checkout",
                "switch",
                "pull",
                "am",
                "apply",
                "rm",
                "mv",
                "branch",
            ):
                profile.mutating = True
            if sub == "branch" and flags & {"-d", "-D".lower()}:
                profile.destructive = True
        elif n in (
            "kubectl",
            "helm",
            "terraform",
            "pulumi",
            "docker",
            "podman",
            "aws",
            "gcloud",
            "az",
        ):
            joined = " ".join(c.argv[1:]).lower()
            if re.search(
                r"\b(delete|destroy|rm|prune|purge|terminate-instances|delete-bucket|uninstall)\b",
                joined,
            ) or (n == "aws" and " rm " in f" {joined} " and "--recursive" in joined):
                profile.destructive = profile.mutating = True
                sev = (
                    Severity.CRITICAL
                    if re.search(
                        r"--all\b|--all-namespaces|-A\b|system prune -a|destroy\s+-auto-approve|--recursive",
                        joined,
                    )
                    else Severity.HIGH
                )
                add(
                    "INFRA_DESTROY",
                    sev,
                    f"Destroys infrastructure or cloud resources ({n})",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.BLOCK if sev is Severity.CRITICAL else Verdict.ESCALATE_HITL,
                )
            else:
                profile.mutating = True
        elif n in ("psql", "mysql", "sqlite3", "sqlcmd", "mongo", "mongosh", "redis-cli"):
            for i, a in enumerate(c.argv):
                if a in ("-c", "-e", "--command", "--execute", "-Q", "--eval") and i + 1 < len(
                    c.argv
                ):
                    out.extend(self._sql(path, c.argv[i + 1], profile))
            if n == "redis-cli" and re.search(r"(?i)\b(flushall|flushdb)\b", c.text):
                profile.destructive = True
                add(
                    "DATASTORE_FLUSH",
                    Severity.CRITICAL,
                    "Flushes a Redis datastore",
                    OwaspVector.TOOL_ABUSE,
                    Verdict.BLOCK,
                )
        if n in NETWORK_CMDS:
            profile.network = True
            for o in ops:
                if re.match(
                    r"^(https?|ftp)://|^[\w.-]+\.[a-z]{2,}(/|$)|^\d+\.\d+\.\d+\.\d+", o, re.I
                ):
                    self._add_url(o, profile)
            uploads = [
                a
                for a in c.argv
                if a.startswith("@")
                or "=@" in a
                or a
                in (
                    "-T",
                    "--upload-file",
                    "-F",
                    "--form",
                    "--data-binary",
                    "-d",
                    "--data",
                    "--data-raw",
                    "-X",
                    "POST",
                )
            ]
            if n in ("curl", "wget") and uploads:
                files = [
                    a.split("@", 1)[1] for a in c.argv if "@" in a and not a.startswith("http")
                ]
                for f in files:
                    self._add_path(f, profile)
                    if sensitive_path(f):
                        profile.reads_sensitive = True
            if n in ("scp", "rsync", "nc", "ncat", "netcat", "socat", "ftp", "sftp"):
                for o in ops:
                    if sensitive_path(o):
                        profile.reads_sensitive = True
        if n in ("python", "python3", "node", "perl", "ruby", "php") and any(
            a in ("-c", "-e") for a in c.argv
        ):
            i = next(i for i, a in enumerate(c.argv) if a in ("-c", "-e"))
            if i + 1 < len(c.argv) and n.startswith("python"):
                out.extend(self._python(path, c.argv[i + 1], profile))
        return out

    # ── python ─────────────────────────────────────────────────────────────────────
    _DANGEROUS_CALLS: dict[str, tuple[str, Severity, str, OwaspVector, Verdict]] = {
        "os.system": (
            "PY_SHELL_EXEC",
            Severity.HIGH,
            "os.system runs a shell command",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
        "os.popen": (
            "PY_SHELL_EXEC",
            Severity.HIGH,
            "os.popen runs a shell command",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
        "subprocess.run": (
            "PY_SUBPROCESS",
            Severity.MEDIUM,
            "Spawns a subprocess",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "subprocess.call": (
            "PY_SUBPROCESS",
            Severity.MEDIUM,
            "Spawns a subprocess",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "subprocess.check_call": (
            "PY_SUBPROCESS",
            Severity.MEDIUM,
            "Spawns a subprocess",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "subprocess.check_output": (
            "PY_SUBPROCESS",
            Severity.MEDIUM,
            "Spawns a subprocess",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "subprocess.Popen": (
            "PY_SUBPROCESS",
            Severity.MEDIUM,
            "Spawns a subprocess",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "eval": (
            "PY_DYNAMIC_EVAL",
            Severity.HIGH,
            "eval() of dynamic code",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
        "exec": (
            "PY_DYNAMIC_EVAL",
            Severity.HIGH,
            "exec() of dynamic code",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
        "compile": (
            "PY_DYNAMIC_EVAL",
            Severity.MEDIUM,
            "compile() of dynamic code",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "__import__": (
            "PY_DYNAMIC_IMPORT",
            Severity.MEDIUM,
            "Dynamic import",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ALLOW,
        ),
        "shutil.rmtree": (
            "PY_RMTREE",
            Severity.HIGH,
            "Recursively deletes a directory tree",
            OwaspVector.TOOL_ABUSE,
            Verdict.ESCALATE_HITL,
        ),
        "os.remove": (
            "PY_DELETE",
            Severity.LOW,
            "Deletes a file",
            OwaspVector.TOOL_ABUSE,
            Verdict.ALLOW,
        ),
        "os.unlink": (
            "PY_DELETE",
            Severity.LOW,
            "Deletes a file",
            OwaspVector.TOOL_ABUSE,
            Verdict.ALLOW,
        ),
        "os.rmdir": (
            "PY_DELETE",
            Severity.LOW,
            "Deletes a directory",
            OwaspVector.TOOL_ABUSE,
            Verdict.ALLOW,
        ),
        "pathlib.Path.unlink": (
            "PY_DELETE",
            Severity.LOW,
            "Deletes a file",
            OwaspVector.TOOL_ABUSE,
            Verdict.ALLOW,
        ),
        "socket.socket": (
            "PY_RAW_SOCKET",
            Severity.MEDIUM,
            "Opens a raw network socket",
            OwaspVector.DATA_EXFILTRATION,
            Verdict.ALLOW,
        ),
        "pickle.loads": (
            "PY_UNSAFE_DESERIALIZE",
            Severity.HIGH,
            "Deserialises untrusted pickle data",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
        "marshal.loads": (
            "PY_UNSAFE_DESERIALIZE",
            Severity.HIGH,
            "Deserialises marshal data",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
        "ctypes.CDLL": (
            "PY_NATIVE_LOAD",
            Severity.HIGH,
            "Loads a native library",
            OwaspVector.UNSAFE_CODE_EXEC,
            Verdict.ESCALATE_HITL,
        ),
    }
    _NET_CALLS = (
        "requests.post",
        "requests.put",
        "httpx.post",
        "httpx.put",
        "urllib.request.urlopen",
        "requests.get",
        "httpx.get",
        "smtplib.SMTP",
        "ftplib.FTP",
    )

    def _python(self, path: str, text: str, profile: ArgumentProfile) -> list[Finding]:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return []
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    aliases[a.asname or a.name.split(".")[0]] = (
                        a.name if a.asname else a.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"

        def qualname(fn: ast.expr) -> str | None:
            parts: list[str] = []
            while isinstance(fn, ast.Attribute):
                parts.append(fn.attr)
                fn = fn.value
            if isinstance(fn, ast.Name):
                parts.append(aliases.get(fn.id, fn.id))
                return ".".join(reversed(parts))
            return None

        out: list[Finding] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            q = qualname(node.func)
            if q is None:
                continue
            literal_args = [
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            if q in self._DANGEROUS_CALLS:
                code, sev, msg, threat, rec = self._DANGEROUS_CALLS[q]
                if q in ("os.system", "os.popen") or q.startswith("subprocess."):
                    for s in literal_args:
                        out.extend(self._shell(f"{path}<python:{q}>", s, profile))
                    for arg in node.args:
                        if isinstance(arg, (ast.List, ast.Tuple)):
                            argv = [
                                e.value
                                for e in arg.elts
                                if isinstance(e, ast.Constant) and isinstance(e.value, str)
                            ]
                            if argv:
                                out.extend(
                                    self._shell(f"{path}<python:{q}>", shlex.join(argv), profile)
                                )
                if q == "shutil.rmtree" and any(is_system_path(s) for s in literal_args):
                    sev, rec, code, msg = (
                        Severity.CRITICAL,
                        Verdict.BLOCK,
                        "PY_RMTREE_CRITICAL",
                        f"rmtree of a system path {literal_args}",
                    )
                if code == "PY_DELETE" and any(
                    SYSTEM_PREFIX.match(s) or sensitive_path(s) for s in literal_args
                ):
                    sev, rec, code, msg = (
                        Severity.HIGH,
                        Verdict.ESCALATE_HITL,
                        "PY_DELETE_SYSTEM",
                        f"Deletes a system or sensitive file {literal_args}",
                    )
                if code in ("PY_RMTREE", "PY_RMTREE_CRITICAL", "PY_DELETE", "PY_DELETE_SYSTEM"):
                    profile.destructive = profile.mutating = True
                out.append(
                    Finding(
                        Stage.THREAT,
                        code,
                        sev,
                        msg,
                        {"path": path, "call": q, "line": node.lineno},
                        threat.value,
                        rec,
                    )
                )
            if q in self._NET_CALLS or q.endswith(".sendall") or q.endswith(".send"):
                profile.network = True
                for s in literal_args:
                    if s.startswith(("http://", "https://")):
                        self._add_url(s, profile)
            if (
                q
                in (
                    "open",
                    "builtins.open",
                    "io.open",
                    "pathlib.Path.read_text",
                    "pathlib.Path.open",
                )
                and literal_args
            ):
                self._add_path(literal_args[0], profile)
                if sensitive_path(literal_args[0]):
                    profile.reads_sensitive = True
                mode = next((a.value for a in node.args[1:2] if isinstance(a, ast.Constant)), None)
                if mode and any(c in str(mode) for c in "wax+"):
                    profile.mutating = True
        return out

    # ── SQL ────────────────────────────────────────────────────────────────────────
    _SQL_START = re.compile(
        r"(?is)^\s*(select|with|insert|update|delete|drop|truncate|alter|create|grant|revoke|merge|replace|exec|execute|copy|attach|pragma|vacuum|show|describe|explain)\b"
    )

    def _sql(self, path: str, text: str, profile: ArgumentProfile) -> list[Finding]:
        stripped = re.sub(r"'(?:[^']|'')*'", "''", text)  # drop string literals before splitting
        stripped = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/", " ", stripped)
        statements = [s.strip() for s in stripped.split(";") if s.strip()]
        if not statements or not any(self._SQL_START.match(s) for s in statements):
            return []
        out: list[Finding] = []
        profile.sql_statements.extend(statements)
        ev = {"path": path, "sql": text[:300]}
        if len([s for s in statements if self._SQL_START.match(s)]) > 1:
            out.append(
                Finding(
                    Stage.THREAT,
                    "SQL_STACKED",
                    Severity.HIGH,
                    "Several stacked SQL statements in one call",
                    ev,
                    OwaspVector.TOOL_ABUSE.value,
                    Verdict.ESCALATE_HITL,
                )
            )
        for s in statements:
            low = s.lower()
            for m in re.finditer(
                r"\b(?:from|into|update|table|join|truncate)\s+(?:if\s+exists\s+)?([\w.\"`\[\]]+)",
                low,
            ):
                profile.sql_tables.add(m.group(1).strip('"`[]'))
            where = re.search(r"\bwhere\b(.*)$", low, re.S)
            tautology = bool(
                where
                and re.search(
                    r"(?:^|\s|\()(?:1\s*=\s*1|true|'([^']*)'\s*=\s*'\1'|(\w+)\s*=\s*\2)(?:\s|\)|$)|\bor\s+1\s*=\s*1",
                    where.group(1),
                )
            )
            if re.match(r"\s*drop\s+(table|database|schema|view|index|user|role)", low):
                profile.destructive = profile.mutating = True
                obj = re.match(r"\s*drop\s+(\w+)", low).group(1)  # type: ignore[union-attr]
                sev = Severity.CRITICAL if obj in ("database", "schema", "table") else Severity.HIGH
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SQL_DROP",
                        sev,
                        f"DROP {obj.upper()}",
                        ev,
                        OwaspVector.TOOL_ABUSE.value,
                        Verdict.BLOCK if sev is Severity.CRITICAL else Verdict.ESCALATE_HITL,
                    )
                )
            elif re.match(r"\s*truncate\b", low):
                profile.destructive = profile.mutating = True
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SQL_TRUNCATE",
                        Severity.CRITICAL,
                        "TRUNCATE removes every row",
                        ev,
                        OwaspVector.TOOL_ABUSE.value,
                        Verdict.BLOCK,
                    )
                )
            elif re.match(r"\s*delete\b", low):
                profile.destructive = profile.mutating = True
                if not where or tautology:
                    out.append(
                        Finding(
                            Stage.THREAT,
                            "SQL_DELETE_ALL",
                            Severity.CRITICAL,
                            "DELETE without a restricting WHERE clause removes every row",
                            ev,
                            OwaspVector.TOOL_ABUSE.value,
                            Verdict.BLOCK,
                        )
                    )
                else:
                    out.append(
                        Finding(
                            Stage.THREAT,
                            "SQL_DELETE",
                            Severity.LOW,
                            "DELETE with a WHERE clause",
                            ev,
                            OwaspVector.TOOL_ABUSE.value,
                            Verdict.ALLOW,
                        )
                    )
            elif re.match(r"\s*update\b", low):
                profile.mutating = True
                if not where or tautology:
                    profile.destructive = True
                    out.append(
                        Finding(
                            Stage.THREAT,
                            "SQL_UPDATE_ALL",
                            Severity.HIGH,
                            "UPDATE without a restricting WHERE clause changes every row",
                            ev,
                            OwaspVector.TOOL_ABUSE.value,
                            Verdict.ESCALATE_HITL,
                        )
                    )
            elif re.match(r"\s*(grant|revoke)\b", low):
                profile.mutating = True
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SQL_PRIVILEGE_CHANGE",
                        Severity.HIGH,
                        "Changes database privileges",
                        ev,
                        OwaspVector.EXCESSIVE_PERMISSIONS.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
            elif re.match(r"\s*(alter|create|insert|merge|replace|copy|attach)\b", low):
                profile.mutating = True
                if re.match(r"\s*copy\b.*\bto\s+program\b", low) or re.match(r"\s*attach\b", low):
                    out.append(
                        Finding(
                            Stage.THREAT,
                            "SQL_FILE_OR_PROGRAM",
                            Severity.HIGH,
                            "COPY ... TO PROGRAM / ATTACH reaches outside the database",
                            ev,
                            OwaspVector.UNSAFE_CODE_EXEC.value,
                            Verdict.ESCALATE_HITL,
                        )
                    )
            elif re.match(r"\s*select\b", low) and tautology:
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SQL_TAUTOLOGY",
                        Severity.HIGH,
                        "SELECT with a tautological WHERE (possible injection)",
                        ev,
                        OwaspVector.TOOL_ABUSE.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
            if re.search(
                r"(?i)\bunion\s+(all\s+)?select\b|\b(sleep|benchmark)\s*\(|@@version\b|\binformation_schema\b|\bor\s*\(?\s*select\b",
                text,
            ):
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SQL_INJECTION",
                        Severity.HIGH,
                        "SQL query contains common injection signature (UNION / SLEEP / metadata extraction)",
                        ev,
                        OwaspVector.TOOL_ABUSE.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
            if re.search(r"\b(xp_cmdshell|load_file|into\s+outfile|pg_read_file|lo_import)\b", low):
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SQL_FILESYSTEM",
                        Severity.CRITICAL,
                        "SQL reaches the host filesystem or shell",
                        ev,
                        OwaspVector.UNSAFE_CODE_EXEC.value,
                        Verdict.BLOCK,
                    )
                )
        return out

    # ── paths and URLs ─────────────────────────────────────────────────────────────
    def _add_path(self, text: str, profile: ArgumentProfile) -> None:
        t = text.strip().strip("'\"")
        if (
            t
            and len(t) < 512
            and t not in profile.paths
            and ("/" in t or "\\" in t or t.startswith((".", "~")) or "." in t)
        ):
            profile.paths.append(t)
            if sensitive_path(t):
                profile.reads_sensitive = True

    def _add_url(self, text: str, profile: ArgumentProfile) -> None:
        t = text.strip().strip("'\"")
        if t not in profile.urls:
            profile.urls.append(t)
        host = _host_of(t)
        if host:
            profile.hosts.add(host)

    def _paths_and_urls(self, profile: ArgumentProfile) -> list[Finding]:
        out: list[Finding] = []
        for p in profile.paths:
            label = sensitive_path(p)
            if label:
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SENSITIVE_PATH",
                        Severity.HIGH,
                        f"Accesses {label}: {p}",
                        {"target": p},
                        OwaspVector.DATA_EXFILTRATION.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
            if re.search(r"(^|[/\\])\.\.([/\\]).*\.\.([/\\])", p) or p.count("..") >= 3:
                out.append(
                    Finding(
                        Stage.THREAT,
                        "PATH_TRAVERSAL",
                        Severity.HIGH,
                        f"Deep parent-directory traversal: {p}",
                        {"target": p},
                        OwaspVector.TRUST_BOUNDARY.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
        for u in profile.urls:
            if u.lower().startswith("file://"):
                local = urllib.parse.unquote(u[7:])
                label = sensitive_path(local)
                if label:
                    profile.reads_sensitive = True
                if profile.network:
                    out.append(
                        Finding(
                            Stage.THREAT,
                            "LOCAL_FILE_URL",
                            Severity.HIGH,
                            f"A network tool is asked to read a local file ({local})",
                            {"url": u},
                            OwaspVector.TRUST_BOUNDARY.value,
                            Verdict.ESCALATE_HITL,
                        )
                    )
            host = _host_of(u)
            if not host:
                continue
            if host in METADATA_HOSTS or canonical_host(host) in METADATA_HOSTS:
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SSRF_METADATA",
                        Severity.CRITICAL,
                        f"Requests the cloud metadata service ({host})",
                        {"url": u},
                        OwaspVector.TRUST_BOUNDARY.value,
                        Verdict.BLOCK,
                    )
                )
            elif _is_private_host(host) and profile.network:
                out.append(
                    Finding(
                        Stage.THREAT,
                        "SSRF_PRIVATE",
                        Severity.HIGH,
                        f"Requests a private/loopback address ({host})",
                        {"url": u},
                        OwaspVector.TRUST_BOUNDARY.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
            if EXFIL_HOSTS.search(u):
                profile.network = True
                out.append(
                    Finding(
                        Stage.THREAT,
                        "EXFIL_ENDPOINT",
                        Severity.HIGH,
                        f"Sends to a known paste/tunnel/webhook-capture service ({host})",
                        {"url": u},
                        OwaspVector.DATA_EXFILTRATION.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
        return out


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    out = []
    for f in findings:
        key = (f.code, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
