"""Linear Temporal Logic (LTL) Multi-Hop Trajectory Engine.

Implements multi-turn temporal trace verification (based on Siu, He, Song et al.,
UC Berkeley & Duke, 2026):
Prevents multi-hop split exfiltration attacks where an agent divides an exploit across
discrete, seemingly benign turns:
    Turn 1: Read sensitive credentials
    Turn 2: Stage data into scratchpad
    Turn 3: Exfiltrate staged data over network

Formally checks Linear Temporal Logic (LTL) safety properties over the session causal graph:
    Formula 1 (Confidentiality): G(ReadSensitive -> G(!NetworkEgress))
    Formula 2 (Integrity):       G(UntrustedIngest -> G(!UncheckedShellExecution))
    Formula 3 (Persistence):     G(ReadCredentials -> G(!WriteStartupScripts))
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from panoptis.protect.model import Finding, Severity, Stage, ToolAction, Verdict

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryStep:
    call_id: str
    tool: str
    arguments: dict[str, Any]
    reads_sensitive: bool = False
    writes_staging: bool = False
    network_egress: bool = False
    untrusted_ingest: bool = False
    timestamp: float = field(default_factory=time.time)


_SENSITIVE_READ_TOOLS = {"read_file", "cat", "view_file", "get_secret", "fetch_credentials"}
_SENSITIVE_PATHS = ["*id_rsa*", "*.env*", "*credential*", "*/etc/shadow*", "*token*"]
_PERSISTENCE_PATHS = ["*.bashrc*", "*.profile*", "*/crontab*", "*/systemd/*", "*.zshrc*"]
_UNTRUSTED_INGEST_TOOLS = {"fetch_url", "web_search", "read_email", "get_issue"}
_NETWORK_EGRESS_TOOLS = {"http_post", "fetch_url", "curl", "wget", "send_email", "webhook"}
_SHELL_EXEC_TOOLS = {
    "execute_shell",
    "bash",
    "sh",
    "shell",
    "terminal",
    "run_command",
    "run_terminal_command",
    "execute_command",
    "run_python",
    "powershell",
}
_COMMAND_ARGS = ("command", "cmd", "script")
_APPENDING_WRITE_TOOLS = ("append_file", "edit_file")
_WRITE_TOOLS = ("write_file", *_APPENDING_WRITE_TOOLS)
# echo/printf text redirected into a file, the shell's form of write_file and append_file
_SHELL_WRITE = re.compile(r"\b(?:echo|printf)\s+(?P<body>.+?)\s*(?P<op>>>?)\s*(?P<path>[^\s;&|]+)")

# Indicators of a reverse shell or a fetch-and-execute stager. Each alone is ordinary code:
# a rule needs one from both halves of a pair, so subprocess plus requests does not match.
_NETWORK_CHANNEL = ("socket.socket", "/dev/tcp")
_SHELL_SPAWN = ("subprocess", "pty.spawn", "os.system", "os.dup2", "/bin/sh", "/bin/bash")
_REMOTE_FETCH = ("urllib", "requests.get", "requests.post", "http.client", "b64decode")
_DYNAMIC_EXEC = ("eval(", "exec(")
_INDICATORS = (*_NETWORK_CHANNEL, *_SHELL_SPAWN, *_REMOTE_FETCH, *_DYNAMIC_EXEC)
# Every proper prefix of an indicator: a write ending in one could finish in the next write.
_OPEN_PREFIXES = frozenset(k[:i] for k in _INDICATORS for i in range(1, len(k)))
_MAX_PREFIX_CHARS = max(map(len, _OPEN_PREFIXES))
_OVERFLOW_PATH = "<untracked paths>"  # every write past a cap lands here, so nothing is forgotten
_MAX_PATH_CHARS = 256
_MAX_RECORDED_ARGS = 32
_MAX_RECORDED_VALUE_CHARS = 256
_MAX_ANTECEDENT_CHARS = 64
_SEALED_HASHES = 4


def _path_key(path: str) -> str:
    """A bounded key for a path of any length."""
    if len(path) <= _MAX_PATH_CHARS:
        return path
    return "sha256:" + hashlib.sha256(path.encode("utf-8", "surrogatepass")).hexdigest()


def _bounded_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """What a step keeps of its arguments: their shape, never unbounded content."""
    items = list(arguments.items())[:_MAX_RECORDED_ARGS]
    return {str(k)[:64]: str(v)[:_MAX_RECORDED_VALUE_CHARS] for k, v in items}


def _dangerous(indicators: set[str]) -> bool:
    return bool(
        (indicators.intersection(_NETWORK_CHANNEL) and indicators.intersection(_SHELL_SPAWN))
        or (indicators.intersection(_REMOTE_FETCH) and indicators.intersection(_DYNAMIC_EXEC))
    )


def _open_prefix(text: str) -> str:
    """The longest end of ``text`` that could still become an indicator."""
    for size in range(min(_MAX_PREFIX_CHARS, len(text)), 0, -1):
        if text[-size:] in _OPEN_PREFIXES:
            return text[-size:]
    return ""


@dataclass(slots=True)
class FileAssembly:
    """What a session has written to one file: bounded however much it writes."""

    indicators: frozenset[str] = frozenset()
    tail: str = ""

    def add(self, content: str) -> None:
        text = (self.tail + content).lower()
        found = frozenset(k for k in _INDICATORS if k in text)
        if found:
            self.indicators |= found
        self.tail = _open_prefix(text)

    def holds_anything(self) -> bool:
        return bool(self.indicators or self.tail)


class _Antecedent(NamedTuple):
    """The step that first made a session's later actions risky."""

    tool: str
    call_id: str

    @classmethod
    def of(cls, action: ToolAction) -> _Antecedent:
        return cls(action.tool[:_MAX_ANTECEDENT_CHARS], action.call_id[:_MAX_ANTECEDENT_CHARS])


@dataclass(slots=True)
class SessionFacts:
    """Everything the rules need about one session, kept apart from its step history."""

    sensitive_read: _Antecedent | None = None
    untrusted_ingest: _Antecedent | None = None
    files: dict[str, FileAssembly] = field(default_factory=dict)
    overflowed: bool = False

    def holds_anything(self) -> bool:
        return bool(self.sensitive_read or self.untrusted_ingest or self.files or self.overflowed)


_NO_FACTS = SessionFacts()
# A sealed session is judged as if it had done everything. The rules only ever get stricter as
# facts are added, so sealing can never turn a verdict into a weaker one.
_UNKNOWN = _Antecedent("unknown", "unknown")
_WORST_CASE = SessionFacts(
    sensitive_read=_UNKNOWN,
    untrusted_ingest=_UNKNOWN,
    files={"<sealed session>": FileAssembly(indicators=frozenset(_INDICATORS))},
    overflowed=True,
)


class _SealedSessions:
    """Sessions whose exact facts were given up: a fixed-size Bloom filter.

    It has no false negatives, so a sealed session is always recognised however many follow it.
    It can have false positives, which refuse an innocent session, and they grow as it fills.
    Nothing is ever removed: forgetting a sealed session takes an operator's ``release_tenant``.
    """

    def __init__(self, bits: int, key: bytes) -> None:
        self._bits = bytearray((bits + 7) // 8)
        self._size = bits
        self._key = key
        self.count = 0

    def _positions(self, session: str) -> list[int]:
        digest = hashlib.blake2b(
            session.encode("utf-8", "surrogatepass"), key=self._key, digest_size=4 * _SEALED_HASHES
        ).digest()
        return [
            int.from_bytes(digest[i : i + 4], "big") % self._size for i in range(0, len(digest), 4)
        ]

    def add(self, session: str) -> None:
        for position in self._positions(session):
            self._bits[position >> 3] |= 1 << (position & 7)
        self.count += 1

    def __contains__(self, session: str) -> bool:
        return all(self._bits[p >> 3] >> (p & 7) & 1 for p in self._positions(session))

    def fill(self) -> float:
        return int.from_bytes(self._bits, "big").bit_count() / self._size


@dataclass(slots=True)
class _TenantHistory:
    """One tenant's ordinary step history. Nothing the rules need lives here."""

    sessions: OrderedDict[str, list[TrajectoryStep]] = field(default_factory=OrderedDict)
    steps: int = 0


@dataclass(slots=True)
class _Tenant:
    """One tenant's security state. Facts, paths and the filter are its own budget."""

    facts: OrderedDict[str, SessionFacts] = field(default_factory=OrderedDict)
    tracked_files: int = 0
    sealed: _SealedSessions | None = None

    def is_sealed(self, session: str) -> bool:
        # an exact record always wins: a session with one was never sealed
        return session not in self.facts and self.sealed is not None and session in self.sealed


class LTLTrajectoryVerifier:
    """Verifies temporal safety invariants across multi-step agent trajectory traces.

    Two kinds of state are kept apart. Step history is ordinary and the rules never read it, so
    it can always be evicted; when it is full, the tenant using the most gives some up. Compact
    ``SessionFacts`` hold everything the rules need, and only sessions with something relevant
    have them. All of it is bounded per session, per tenant and across tenants.

    Facts are never silently dropped. Under pressure they only become coarser, toward refusing:

    - Past a path cap, a session's writes merge into one entry, so their indicators still
      combine, and execution in that session needs a human (``LTL_FILE_TRACKING_OVERFLOW``).
    - When a tenant's table is full, its least recently active session is sealed. Its exact facts
      are replaced by an entry in a fixed-size filter, and it is judged as if it had done
      everything (``LTL_STATE_OVERFLOW``). New sessions keep working, and a verdict can only
      get stricter. A filter that fills up refuses more of that tenant's new sessions by mistake.
    - A tenant takes a place in the tenant table only when a step would create security state,
      and ``verify_action`` reserves it before the step runs, so a step always has somewhere to
      go. When the table is full, a tenant with no place is refused such steps. It has no facts,
      so its other steps are as safe as they look.

    One tenant's sessions, paths and sealed sessions cannot use up another's, and a tenant keeps
    its place until it is released. Capacity comes back when files are overwritten with nothing
    relevant, and through ``release_session`` and ``release_tenant``, which only the host may
    call. Nothing expires with time: an attacker could simply wait.
    """

    def __init__(
        self,
        max_sessions: int = 5_000,
        max_steps_per_session: int = 2_000,
        max_files_per_session: int = 1_024,
        max_tracked_files: int = 8_192,
        *,
        max_history_steps: int = 50_000,
        max_facts_per_tenant: int = 4_096,
        max_tenants: int = 256,
        sealed_filter_bits: int = 1 << 21,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if max_steps_per_session < 3:
            # room for the two sticky steps and one recent step
            raise ValueError("max_steps_per_session must be at least 3")
        if max_files_per_session < 1 or max_tracked_files < 1:
            raise ValueError("max_files_per_session and max_tracked_files must be at least 1")
        if max_history_steps < max_steps_per_session:
            raise ValueError("max_history_steps must hold at least one whole session")
        if max_facts_per_tenant < 1 or max_tenants < 1:
            raise ValueError("max_facts_per_tenant and max_tenants must be at least 1")
        if sealed_filter_bits < 8:
            raise ValueError("sealed_filter_bits must be at least 8")
        self.max_sessions = max_sessions  # sessions with step history, across all tenants
        self.max_steps_per_session = max_steps_per_session
        self.max_history_steps = max_history_steps  # steps of history, across all tenants
        self.max_files_per_session = max_files_per_session
        self.max_tracked_files = max_tracked_files  # per tenant
        self.max_facts_per_tenant = max_facts_per_tenant
        self.max_tenants = max_tenants
        self.sealed_filter_bits = sealed_filter_bits  # per tenant, allocated at its first seal
        self._lock = threading.RLock()
        self._seal_key = secrets.token_bytes(16)  # so nobody can pick ids that collide on purpose
        self._tenants: dict[str, _Tenant] = {}
        self._history: dict[str, _TenantHistory] = {}
        self._history_sessions = 0
        self._history_steps = 0
        self._refused_tenant_calls = 0

    @property
    def traces(self) -> dict[str, list[TrajectoryStep]]:
        """Step history by ``tenant/session``: a copy for inspection, not for the rules."""
        with self._lock:
            return {
                f"{tid}/{sid}": steps
                for tid, history in self._history.items()
                for sid, steps in history.sessions.items()
            }

    @property
    def modified_files(self) -> dict[str, dict[str, FileAssembly]]:
        """Tracked files by ``tenant/session``: a copy for inspection."""
        with self._lock:
            return {
                f"{tid}/{sid}": facts.files
                for tid, tenant in self._tenants.items()
                for sid, facts in tenant.facts.items()
            }

    def _admit(self, tenant_id: str) -> _Tenant | None:
        """A tenant's place in the table, or None when it is full. A place lasts until
        ``release_tenant``: displacing a tenant could lose a step it is about to record."""
        if len(self._tenants) >= self.max_tenants:
            self._refused_tenant_calls += 1
            if self._refused_tenant_calls & (self._refused_tenant_calls - 1) == 0:
                logger.warning(
                    "trajectory state: tenant table full (%d), refused %d step(s) that would "
                    "create security state",
                    self.max_tenants,
                    self._refused_tenant_calls,
                )
            return None
        tenant = self._tenants[tenant_id] = _Tenant()
        return tenant

    def _forget_history(self, tenant_id: str, session: str) -> bool:
        history = self._history.get(tenant_id)
        trace = history.sessions.pop(session, None) if history is not None else None
        if history is None or trace is None:
            return False
        history.steps -= len(trace)
        self._history_steps -= len(trace)
        self._history_sessions -= 1
        if not history.sessions:
            del self._history[tenant_id]
        return True

    def _evict_history(self, size: Callable[[_TenantHistory], int]) -> None:
        """Give up the oldest step history of the tenant using the most."""
        tenant_id = max(self._history, key=lambda t: size(self._history[t]))
        self._forget_history(tenant_id, next(iter(self._history[tenant_id].sessions)))

    def _remember(self, tenant_id: str, session: str, step: TrajectoryStep) -> None:
        history = self._history.get(tenant_id)
        trace = history.sessions.get(session) if history is not None else None
        if history is None or trace is None:
            while self._history_sessions >= self.max_sessions:
                self._evict_history(lambda h: len(h.sessions))
            history = self._history.setdefault(tenant_id, _TenantHistory())
            trace = history.sessions[session] = []
            self._history_sessions += 1
        else:
            history.sessions.move_to_end(session)
        before = len(trace)
        trace.append(step)
        self._compact(trace)
        history.steps += len(trace) - before
        self._history_steps += len(trace) - before
        while self._history_steps > self.max_history_steps:
            self._evict_history(lambda h: h.steps)

    def _seal_oldest(self, tenant: _Tenant, tenant_id: str) -> None:
        """Make room in a full table without forgetting: see the class docstring."""
        session, facts = tenant.facts.popitem(last=False)
        tenant.tracked_files -= len(facts.files)
        if tenant.sealed is None:
            tenant.sealed = _SealedSessions(self.sealed_filter_bits, self._seal_key)
        tenant.sealed.add(session)
        count = tenant.sealed.count
        if count & (count - 1) == 0:
            logger.warning(
                "trajectory state: tenant %s table full (%d), %d session(s) sealed, filter %.0f%% "
                "full; sealed sessions are refused shell, network and persistence writes",
                tenant_id,
                self.max_facts_per_tenant,
                count,
                tenant.sealed.fill() * 100,
            )

    def _open_facts(self, tenant: _Tenant, tenant_id: str, session: str) -> SessionFacts:
        facts = tenant.facts.get(session)
        if facts is not None:
            tenant.facts.move_to_end(session)
            return facts
        if len(tenant.facts) >= self.max_facts_per_tenant:
            self._seal_oldest(tenant, tenant_id)
        facts = tenant.facts[session] = SessionFacts()
        return facts

    def _compact(self, trace: list[TrajectoryStep]) -> None:
        """Bound one session's history, keeping the first step behind each sticky fact."""
        if len(trace) <= self.max_steps_per_session:
            return
        keep = [
            next((s for s in trace if s.reads_sensitive), None),
            next((s for s in trace if s.untrusted_ingest), None),
        ]
        recent = trace[-max(1, self.max_steps_per_session // 2) :]
        trace[:] = [s for s in keep if s is not None and s not in recent] + recent

    def _record_write(
        self,
        tenant: _Tenant,
        tenant_id: str,
        session: str,
        path: str,
        content: str,
        *,
        append: bool,
    ) -> None:
        key = _path_key(path)
        facts = tenant.facts.get(session)
        files = facts.files if facts is not None else {}
        if facts is not None and facts.overflowed and key not in files:
            key, append = _OVERFLOW_PATH, True  # paths past a cap share one entry
        assembly = files[key] if append and key in files else FileAssembly()
        assembly.add(content)
        if not assembly.holds_anything():
            if facts is not None and key in files:  # the file no longer holds anything relevant
                del files[key]
                tenant.tracked_files -= 1
                if not facts.holds_anything():
                    del tenant.facts[session]
            return
        facts = self._open_facts(tenant, tenant_id, session)
        files = facts.files
        if key not in files:
            if (
                len(files) >= self.max_files_per_session
                or tenant.tracked_files >= self.max_tracked_files
            ):
                facts.overflowed = True
                if _OVERFLOW_PATH not in files:
                    files[_OVERFLOW_PATH] = FileAssembly()
                    tenant.tracked_files += 1
                files[_OVERFLOW_PATH].add(content)
                return
            tenant.tracked_files += 1
        files[key] = assembly  # an overwrite replaces what the file held

    @staticmethod
    def _ids(action: ToolAction) -> tuple[str, str]:
        return (
            getattr(action, "tenant_id", "default") or "default",
            getattr(action, "session_id", "default") or "default",
        )

    def record_step(self, action: ToolAction, result: Any = None) -> None:
        """Record an executed tool action into the session's causal trajectory graph."""
        with self._lock:
            self._record_step(action)

    @staticmethod
    def _classify(action: ToolAction) -> tuple[bool, bool]:
        """Whether a step reads something sensitive, and whether it ingests untrusted input."""
        args_str = str(action.arguments).lower()
        reads_sens = False
        if action.tool in _SENSITIVE_READ_TOOLS or "read" in action.tool.lower():
            for pat in _SENSITIVE_PATHS:
                if fnmatch.fnmatch(args_str, f"*{pat.lower()}*"):
                    reads_sens = True
                    break
        return reads_sens, action.tool in _UNTRUSTED_INGEST_TOOLS

    @classmethod
    def _writes(cls, action: ToolAction) -> Iterator[tuple[str, str, bool]]:
        """Each file a step writes, as (path, content, whether it appends)."""
        if action.tool in _WRITE_TOOLS:
            path = str(action.arguments.get("path", action.arguments.get("file_path", "")))
            content = str(action.arguments.get("content", action.arguments.get("replacement", "")))
            if path and content:
                yield path, content, action.tool in _APPENDING_WRITE_TOOLS
        elif cls._executes(action):
            for m in _SHELL_WRITE.finditer(cls._command_text(action)):
                yield m["path"], m["body"].strip("'\""), m["op"] == ">>"

    @classmethod
    def _creates_state(cls, action: ToolAction) -> bool:
        """Whether this step would give a tenant with no facts something to remember."""
        reads_sens, untrusted = cls._classify(action)
        if reads_sens or untrusted:
            return True
        for _, content, _ in cls._writes(action):
            assembly = FileAssembly()
            assembly.add(content)
            if assembly.holds_anything():
                return True
        return False

    def _record_step(self, action: ToolAction) -> None:
        tenant_id, session = self._ids(action)
        reads_sens, untrusted = self._classify(action)
        self._remember(
            tenant_id,
            session,
            TrajectoryStep(
                call_id=action.call_id,
                tool=action.tool,
                arguments=_bounded_arguments(action.arguments),
                reads_sensitive=reads_sens,
                writes_staging=action.tool in ("write_file", "append_file"),
                network_egress=action.tool in _NETWORK_EGRESS_TOOLS,
                untrusted_ingest=untrusted,
            ),
        )

        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            if not self._creates_state(action):
                return
            tenant = self._admit(tenant_id)
            if tenant is None:
                return  # verify_action refuses this step: only a caller skipping it gets here
        if tenant.is_sealed(session):
            return  # judged as having done everything already: nothing more to learn
        if session in tenant.facts:
            tenant.facts.move_to_end(session)

        if reads_sens or untrusted:
            facts = self._open_facts(tenant, tenant_id, session)
            if reads_sens and facts.sensitive_read is None:
                facts.sensitive_read = _Antecedent.of(action)
            if untrusted and facts.untrusted_ingest is None:
                facts.untrusted_ingest = _Antecedent.of(action)

        # Track multi-file piecewise code assembly (Workflow-Level Jailbreak defense)
        for path, content, append in self._writes(action):
            self._record_write(tenant, tenant_id, session, path, content, append=append)

    def session_context(self, action: ToolAction) -> dict[str, bool]:
        """What the session has done, for callers outside the rules. Never read this from
        step history: that can be evicted, and facts cannot."""
        with self._lock:
            tenant_id, session = self._ids(action)
            tenant = self._tenants.get(tenant_id)
            facts = _NO_FACTS
            if tenant is not None:
                sealed = tenant.is_sealed(session)
                facts = _WORST_CASE if sealed else tenant.facts.get(session, _NO_FACTS)
            return {
                "has_read_sensitive": facts.sensitive_read is not None,
                "has_untrusted_ingest": facts.untrusted_ingest is not None,
            }

    def capacity(self, tenant_id: str = "default") -> dict[str, Any]:
        """How much of its budget a tenant is using, for operators."""
        with self._lock:
            tenant = self._tenants.get(tenant_id, _Tenant())
            return {
                "tenants": len(self._tenants),
                "max_tenants": self.max_tenants,
                "sessions_with_facts": len(tenant.facts),
                "max_facts_per_tenant": self.max_facts_per_tenant,
                "tracked_files": tenant.tracked_files,
                "max_tracked_files": self.max_tracked_files,
                "sealed_sessions": tenant.sealed.count if tenant.sealed else 0,
                "sealed_filter_fill": tenant.sealed.fill() if tenant.sealed else 0.0,
            }

    def release_session(self, session_id: str, tenant_id: str = "default") -> bool:
        """Forget a session the host knows has ended, and get its capacity back.

        Only the host may call this, never an agent: a session released while it is still
        running starts again with no history. A sealed session stays sealed. Returns whether
        anything was released.
        """
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            facts = tenant.facts.pop(session_id, None) if tenant is not None else None
            if tenant is not None and facts is not None:
                tenant.tracked_files -= len(facts.files)
            forgotten = self._forget_history(tenant_id, session_id)
            return forgotten or facts is not None

    def release_tenant(self, tenant_id: str) -> bool:
        """Forget a tenant entirely, sealed sessions included, and give up its place.

        This is an operator's decision to accept the loss: anything those sessions did is
        forgotten. It is the only way to clear a filter. Returns whether the tenant was known.
        """
        with self._lock:
            state = self._tenants.pop(tenant_id, None)
            history = self._history.pop(tenant_id, None)
            if history is not None:
                self._history_sessions -= len(history.sessions)
                self._history_steps -= history.steps
            if state is None and history is None:
                return False
            logger.warning("trajectory state: tenant %s released by the host", tenant_id)
            return True

    def has_occurred_within(
        self,
        session_id: str,
        predicate: Any,
        within_seconds: float,
        tenant_id: str = "default",
    ) -> bool:
        """Metric past-time diamond operator (diamond_[0, within_seconds] phi)."""
        now = time.time()
        with self._lock:
            history = self._history.get(tenant_id)
            trace = list(history.sessions.get(session_id, ())) if history is not None else []
        for s in reversed(trace):
            if (now - s.timestamp) > within_seconds:
                break
            if predicate(s):
                return True
        return False

    @staticmethod
    def _command_text(action: ToolAction) -> str:
        return " ".join(str(action.arguments[k]) for k in _COMMAND_ARGS if k in action.arguments)

    @classmethod
    def _executes(cls, action: ToolAction) -> bool:
        """A shell tool, or any tool handed a command to run, whatever it is called."""
        return action.tool in _SHELL_EXEC_TOOLS or bool(cls._command_text(action))

    def verify_action(self, action: ToolAction) -> list[Finding]:
        """Check all temporal LTL formulas against proposed action and session trace."""
        with self._lock:
            return self._verify_action(action)

    def _state_overflow(self, message: str, **evidence: Any) -> Finding:
        return Finding(
            stage=Stage.THREAT,
            code="LTL_STATE_OVERFLOW",
            severity=Severity.HIGH,
            message=message,
            evidence=evidence,
            recommends=Verdict.BLOCK,
        )

    def _verify_action(self, action: ToolAction) -> list[Finding]:
        tenant_id, session = self._ids(action)
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            if not self._creates_state(action):
                return []  # nothing is known about this tenant, and this step adds nothing
            tenant = self._admit(tenant_id)  # reserved now, so the step has somewhere to go
            if tenant is None:
                return [
                    self._state_overflow(
                        "Trajectory state is tracking as many tenants as it can, so this tenant "
                        "has no place, and this step would create state that could not be kept. "
                        "It is refused until a tenant is released",
                        max_tenants=self.max_tenants,
                    )
                ]
        sealed = tenant.is_sealed(session)
        facts = _WORST_CASE if sealed else tenant.facts.get(session, _NO_FACTS)
        if session in tenant.facts:
            tenant.facts.move_to_end(session)
        findings: list[Finding] = []
        executes = self._executes(action)

        if facts.overflowed and executes:
            findings.append(
                Finding(
                    stage=Stage.THREAT,
                    code="LTL_FILE_TRACKING_OVERFLOW",
                    severity=Severity.HIGH,
                    message=(
                        "This session wrote more paths than can be tracked individually, so "
                        "execution in it needs human review"
                    ),
                    evidence={"max_files_per_session": self.max_files_per_session},
                    recommends=Verdict.ESCALATE_HITL,
                )
            )

        args_str = str(action.arguments).lower()

        # Check LTL Formula 1: G(ReadSensitive -> G(!NetworkEgress))
        sens_step = facts.sensitive_read
        if sens_step is not None and action.tool in _NETWORK_EGRESS_TOOLS:
            # Multi-turn exfiltration chain detected!
            findings.append(
                Finding(
                    stage=Stage.THREAT,
                    code="LTL_MULTI_HOP_EXFILTRATION_DETECTED",
                    severity=Severity.CRITICAL,
                    message=(
                        f"LTL Invariant Violation G(ReadSensitive -> G(!NetworkEgress)): "
                        f"Session previously read sensitive data in turn '{sens_step.tool}' "
                        f"(call {sens_step.call_id[:12]}); subsequent network egress via '{action.tool}' is blocked"
                    ),
                    evidence={
                        "violated_ltl": "G(ReadSensitive -> G(!NetworkEgress))",
                        "antecedent_call_id": sens_step.call_id,
                        "antecedent_tool": sens_step.tool,
                        "consequent_tool": action.tool,
                    },
                    recommends=Verdict.BLOCK,
                )
            )

        # Check LTL Formula 2: G(UntrustedIngest -> G(!UncheckedShellExecution))
        ingest_step = facts.untrusted_ingest
        if ingest_step is not None and executes:
            findings.append(
                Finding(
                    stage=Stage.THREAT,
                    code="LTL_UNTRUSTED_INPUT_TO_SHELL_CHAIN",
                    severity=Severity.HIGH,
                    message=(
                        f"LTL Invariant Warning G(UntrustedIngest -> G(!ShellExecution)): "
                        f"Session previously ingested untrusted external input via '{ingest_step.tool}'; "
                        f"subsequent code execution via '{action.tool}' requires human review"
                    ),
                    evidence={
                        "violated_ltl": "G(UntrustedIngest -> G(!ShellExecution))",
                        "antecedent_tool": ingest_step.tool,
                        "consequent_tool": action.tool,
                    },
                    recommends=Verdict.ESCALATE_HITL,
                )
            )

        # Check LTL Formula 3: G(ReadCredentials -> G(!WriteStartupScripts))
        if sens_step is not None and action.tool in ("write_file", "append_file", "edit_file"):
            for p_pat in _PERSISTENCE_PATHS:
                if fnmatch.fnmatch(args_str, f"*{p_pat.lower()}*"):
                    findings.append(
                        Finding(
                            stage=Stage.THREAT,
                            code="LTL_PERSISTENCE_BACKDOOR_CHAIN",
                            severity=Severity.CRITICAL,
                            message=(
                                f"LTL Invariant Violation G(ReadCredentials -> G(!WriteStartupScripts)): "
                                f"Attempting to modify startup script '{args_str[:60]}' after reading credentials"
                            ),
                            evidence={"target": args_str[:80]},
                            recommends=Verdict.BLOCK,
                        )
                    )
                    break

        # Check LTL Formula 4: G(FilesAssembledWithExploitChain -> G(!ExecuteShell))
        # Defeats: Workflow-Level Jailbreak Construction (Alan Turing Inst. & Warwick 2026)
        if executes:
            accumulated_files = facts.files
            if accumulated_files:
                seen = {k for f in accumulated_files.values() for k in f.indicators}
                if _dangerous(seen):
                    findings.append(
                        Finding(
                            stage=Stage.THREAT,
                            code="LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED",
                            severity=Severity.CRITICAL,
                            message=(
                                f"LTL Workflow-Level Jailbreak (WLJ) Invariant Violation: "
                                f"Agent assembled dangerous code across {len(accumulated_files)} modified file(s) "
                                f"({list(accumulated_files.keys())[:3]}); execution via '{action.tool}' is blocked"
                            ),
                            evidence={
                                "violated_ltl": "G(FilesAssembledWithExploitChain -> G(!ExecuteShell))",
                                "assembled_files": list(accumulated_files.keys()),
                                "consequent_tool": action.tool,
                            },
                            recommends=Verdict.BLOCK,
                        )
                    )

        if sealed and findings:
            findings.insert(
                0,
                self._state_overflow(
                    "This session's exact history was sealed when its tenant's trajectory state "
                    "filled up, so it is judged as if it had done everything the rules look for. "
                    "Start a new session, or ask an operator to release the tenant",
                    sealed_sessions=tenant.sealed.count if tenant.sealed else 0,
                ),
            )
        return findings
