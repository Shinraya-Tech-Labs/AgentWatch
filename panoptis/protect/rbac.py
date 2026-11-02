"""Per-agent role-based tool authorization (WP-05).

Policy (YAML or JSON)::

    version: "1.0"
    default: deny               # deny | allow: agents/tools no rule covers
    agents:                     # agent id (glob) -> roles
      coder-*: [coder]
      research-bot: [researcher]
    roles:                      # alias: agent_roles
      researcher:
        allowed_tools: [web_search, read_file, fetch_url]
        forbidden_tools: [execute_shell, "write_*"]
        rate_limits: {max_calls_per_minute: 30, per_session_per_minute: 10, per_tool: {web_search: 10}}
      coder:
        allowed_tools: [read_file, write_file, execute_shell, git]
        require_approval: [git]               # always escalate these tools
        restricted_arguments:
          execute_shell:
            blocked_commands: ["rm -rf*", "sudo *", "curl * | sh"]
            allowed_commands: [git, pytest, ls, cat, python]  # command names
            allowed_paths: ["./**", "/workspace/**"]
            blocked_paths: ["**/.env", "~/.ssh/**"]
            allowed_hosts: [pypi.org, "*.github.com"]
            fields: {timeout: {max: 600}, branch: {pattern: "^feature/"}}

    tenants:                    # optional: agent bindings that apply in one tenant only
      acme:
        inherit: false          # default true: the top-level agents also apply in acme
        agents: {coder-*: [coder]}

Tool names and agent ids are matched with shell-style globs. Forbidden beats allowed;
an agent with several roles is allowed a tool if any role allows it and none forbids it.
Every decision names the role and rule that produced it. Authorization is bound to the
call's tenant: an agent's roles are looked up for the tenant the call was authenticated
for, and rate-limit counters are kept per tenant (and per session for session limits).

:class:`PolicyStore` validates a whole policy before swapping it in atomically; an
invalid file leaves the previous policy active and reports why. Duplicate keys, unknown
keys, a tool both allowed and forbidden by one role and unknown roles are errors. A store
with a file path re-reads it when it changes (at most every ``auto_reload_s``), and only
activates a file that was stable while it was read. Rate-limit counters survive reloads.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from panoptis.protect.arguments import ArgumentProfile, split_command_line
from panoptis.protect.model import Finding, Severity, Stage, Verdict
from panoptis.security.owasp import OwaspVector


class PolicyError(ValueError):
    """The policy document is invalid; ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


_SUPPORTED_VERSIONS = re.compile(r"^1(\.\d+)?$")
_ROLE_KEYS = {
    "allowed_tools",
    "forbidden_tools",
    "rate_limits",
    "restricted_arguments",
    "require_approval",
    "description",
}
_RESTRICT_KEYS = {
    "blocked_commands",
    "allowed_commands",
    "allowed_paths",
    "blocked_paths",
    "allowed_hosts",
    "blocked_hosts",
    "fields",
    "max_length",
}
_FIELD_KEYS = {"pattern", "enum", "max", "min", "max_length"}
_TOP_KEYS = {"version", "default", "agents", "roles", "agent_roles", "description", "tenants"}
_RATE_KEYS = {"max_calls_per_minute", "per_session_per_minute", "per_tool"}


class _UniqueKeyLoader(yaml.SafeLoader):
    """A YAML loader that rejects duplicate keys instead of keeping the last one."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)  # type: ignore[no-untyped-call]


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass
class ArgumentRestriction:
    blocked_commands: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)
    blocked_hosts: list[str] = field(default_factory=list)
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_length: int | None = None


@dataclass
class Role:
    name: str
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    require_approval: list[str] = field(default_factory=list)
    max_calls_per_minute: int | None = None
    per_session_per_minute: int | None = None
    per_tool_per_minute: dict[str, int] = field(default_factory=dict)
    restrictions: dict[str, ArgumentRestriction] = field(default_factory=dict)  # tool glob -> rules


@dataclass
class RBACPolicy:
    version: str
    default: str  # deny | allow
    agents: dict[str, list[str]]
    roles: dict[str, Role]
    digest: str
    source: str = "inline"
    tenants: dict[str, dict[str, Any]] = field(default_factory=dict)  # tenant -> {inherit, agents}

    def bindings_for(self, tenant_id: str = "default") -> dict[str, list[str]]:
        """The agent → roles bindings in force for ``tenant_id``."""
        t = self.tenants.get(tenant_id)
        if t is None:
            return dict(self.agents)
        out = dict(self.agents) if t.get("inherit", True) else {}
        for pattern, roles in t.get("agents", {}).items():
            out[pattern] = list(dict.fromkeys([*out.get(pattern, []), *roles]))
        return out

    def roles_for(self, agent_id: str, tenant_id: str = "default") -> list[Role]:
        names: list[str] = []
        for pattern, roles in self.bindings_for(tenant_id).items():
            if fnmatch.fnmatchcase(agent_id, pattern):
                names.extend(r for r in roles if r not in names)
        return [self.roles[n] for n in names if n in self.roles]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default": self.default,
            "digest": self.digest,
            "source": self.source,
            "agents": self.agents,
            "roles": {
                n: {
                    "allowed_tools": r.allowed_tools,
                    "forbidden_tools": r.forbidden_tools,
                    "require_approval": r.require_approval,
                    "rate_limits": {
                        "max_calls_per_minute": r.max_calls_per_minute,
                        "per_session_per_minute": r.per_session_per_minute,
                        "per_tool": r.per_tool_per_minute,
                    },
                    "restricted_arguments": {t: vars(a) for t, a in r.restrictions.items()},
                }
                for n, r in self.roles.items()
            },
            "tenants": self.tenants,
        }


def _strlist(v: Any, where: str, errors: list[str]) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        errors.append(f"{where}: expected a list of strings")
        return []
    if any(not x.strip() for x in v):
        errors.append(f"{where}: empty pattern")
    return list(v)


def _nonneg_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def parse_policy(doc: Any, *, source: str = "inline") -> RBACPolicy:
    """Validate and compile a policy document. Raises :class:`PolicyError` listing all errors."""
    errors: list[str] = []
    if isinstance(doc, (str, bytes)):
        try:
            doc = yaml.load(doc, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass
        except yaml.YAMLError as exc:
            raise PolicyError([f"not valid YAML/JSON: {exc}"]) from exc
    if not isinstance(doc, dict):
        raise PolicyError(["policy must be a mapping"])
    for k in doc:
        if k not in _TOP_KEYS:
            errors.append(f"unknown top-level key {k!r}")
    version = str(doc.get("version", "1.0"))
    if not _SUPPORTED_VERSIONS.match(version):
        errors.append(f"unsupported policy version {version!r} (supported: 1.x)")
    if "roles" in doc and "agent_roles" in doc:
        errors.append("give either 'roles' or 'agent_roles', not both")
    default = str(doc.get("default", "deny")).lower()
    if default not in ("deny", "allow"):
        errors.append("default must be 'deny' or 'allow'")
    roles_doc = doc.get("roles", doc.get("agent_roles", {})) or {}
    if not isinstance(roles_doc, dict):
        errors.append("roles must be a mapping of role name to role")
        roles_doc = {}
    roles: dict[str, Role] = {}
    for name, rd in roles_doc.items():
        where = f"roles.{name}"
        if not isinstance(rd, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        for k in rd:
            if k not in _ROLE_KEYS:
                errors.append(f"{where}: unknown key {k!r}")
        role = Role(
            name=str(name),
            allowed_tools=_strlist(rd.get("allowed_tools"), f"{where}.allowed_tools", errors),
            forbidden_tools=_strlist(rd.get("forbidden_tools"), f"{where}.forbidden_tools", errors),
            require_approval=_strlist(
                rd.get("require_approval"), f"{where}.require_approval", errors
            ),
        )
        both = sorted(set(role.allowed_tools) & set(role.forbidden_tools))
        if both:
            errors.append(f"{where}: {both} both allowed and forbidden (ambiguous)")
        rl = rd.get("rate_limits") or {}
        if not isinstance(rl, dict):
            errors.append(f"{where}.rate_limits: must be a mapping")
        else:
            for k in rl:
                if k not in _RATE_KEYS:
                    errors.append(f"{where}.rate_limits: unknown key {k!r}")
            for key in ("max_calls_per_minute", "per_session_per_minute"):
                m = rl.get(key)
                if m is not None and not _nonneg_int(m):
                    errors.append(f"{where}.rate_limits.{key}: must be a non-negative integer")
                else:
                    setattr(role, key, m)
            pt = rl.get("per_tool") or {}
            if not isinstance(pt, dict) or not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in pt.values()
            ):
                errors.append(
                    f"{where}.rate_limits.per_tool: must map tool globs to non-negative integers"
                )
            else:
                role.per_tool_per_minute = {str(k): int(v) for k, v in pt.items()}
        ra = rd.get("restricted_arguments") or {}
        if not isinstance(ra, dict):
            errors.append(f"{where}.restricted_arguments: must map tool names to restrictions")
            ra = {}
        for tool, rules in ra.items():
            w2 = f"{where}.restricted_arguments.{tool}"
            if not isinstance(rules, dict):
                errors.append(f"{w2}: must be a mapping")
                continue
            for k in rules:
                if k not in _RESTRICT_KEYS:
                    errors.append(f"{w2}: unknown key {k!r}")
            fields = rules.get("fields") or {}
            if not isinstance(fields, dict):
                errors.append(f"{w2}.fields: must be a mapping")
                fields = {}
            for fname, spec in fields.items():
                if not isinstance(spec, dict):
                    errors.append(f"{w2}.fields.{fname}: must be a mapping")
                    continue
                for k in spec:
                    if k not in _FIELD_KEYS:
                        errors.append(f"{w2}.fields.{fname}: unknown key {k!r}")
                for k in ("max", "min"):
                    if k in spec and (
                        not isinstance(spec[k], (int, float)) or isinstance(spec[k], bool)
                    ):
                        errors.append(f"{w2}.fields.{fname}.{k}: must be a number")
                if "enum" in spec and not isinstance(spec["enum"], list):
                    errors.append(f"{w2}.fields.{fname}.enum: must be a list")
                if "max_length" in spec and not (
                    _nonneg_int(spec["max_length"]) and spec["max_length"] > 0
                ):
                    errors.append(f"{w2}.fields.{fname}.max_length: must be a positive integer")
                if (
                    isinstance(spec.get("max"), (int, float))
                    and isinstance(spec.get("min"), (int, float))
                    and spec["min"] > spec["max"]
                ):
                    errors.append(f"{w2}.fields.{fname}: min is greater than max")
                if "pattern" in spec:
                    try:
                        re.compile(str(spec["pattern"]))
                    except re.error as exc:
                        errors.append(f"{w2}.fields.{fname}.pattern: invalid regex ({exc})")
            ml = rules.get("max_length")
            if ml is not None and (not _nonneg_int(ml) or ml <= 0):
                errors.append(f"{w2}.max_length: must be a positive integer")
            role.restrictions[str(tool)] = ArgumentRestriction(
                blocked_commands=_strlist(
                    rules.get("blocked_commands"), f"{w2}.blocked_commands", errors
                ),
                allowed_commands=_strlist(
                    rules.get("allowed_commands"), f"{w2}.allowed_commands", errors
                ),
                allowed_paths=_strlist(rules.get("allowed_paths"), f"{w2}.allowed_paths", errors),
                blocked_paths=_strlist(rules.get("blocked_paths"), f"{w2}.blocked_paths", errors),
                allowed_hosts=_strlist(rules.get("allowed_hosts"), f"{w2}.allowed_hosts", errors),
                blocked_hosts=_strlist(rules.get("blocked_hosts"), f"{w2}.blocked_hosts", errors),
                fields={str(k): dict(v) for k, v in fields.items() if isinstance(v, dict)},
                max_length=ml if isinstance(ml, int) else None,
            )
        roles[str(name)] = role
    agents_doc = doc.get("agents") or {}
    agents: dict[str, list[str]] = {}
    if not isinstance(agents_doc, dict):
        errors.append("agents must map agent ids (globs) to role lists")
    else:
        for aid, rl2 in agents_doc.items():
            names = _strlist(rl2, f"agents.{aid}", errors)
            for n in names:
                if n not in roles:
                    errors.append(f"agents.{aid}: unknown role {n!r}")
            agents[str(aid)] = names
    tenants: dict[str, dict[str, Any]] = {}
    tenants_doc = doc.get("tenants") or {}
    if not isinstance(tenants_doc, dict):
        errors.append("tenants must map tenant ids to {inherit, agents}")
        tenants_doc = {}
    for tid, td in tenants_doc.items():
        w = f"tenants.{tid}"
        if not isinstance(td, dict):
            errors.append(f"{w}: must be a mapping")
            continue
        for k in td:
            if k not in ("inherit", "agents"):
                errors.append(f"{w}: unknown key {k!r}")
        inherit = td.get("inherit", True)
        if not isinstance(inherit, bool):
            errors.append(f"{w}.inherit: must be true or false")
        tad = td.get("agents") or {}
        if not isinstance(tad, dict):
            errors.append(f"{w}.agents: must map agent ids (globs) to role lists")
            tad = {}
        bindings: dict[str, list[str]] = {}
        for aid, rl3 in tad.items():
            names = _strlist(rl3, f"{w}.agents.{aid}", errors)
            for n in names:
                if n not in roles:
                    errors.append(f"{w}.agents.{aid}: unknown role {n!r}")
            bindings[str(aid)] = names
        tenants[str(tid)] = {"inherit": bool(inherit), "agents": bindings}
    if errors:
        raise PolicyError(errors)
    canonical = json.dumps(doc, sort_keys=True, default=str)
    return RBACPolicy(
        version=version,
        default=default,
        agents=agents,
        roles=roles,
        digest=hashlib.sha256(canonical.encode()).hexdigest()[:16],
        source=source,
        tenants=tenants,
    )


def _glob_any(value: str, patterns: list[str]) -> str | None:
    for p in patterns:
        if fnmatch.fnmatchcase(value, p) or fnmatch.fnmatchcase(value.lower(), p.lower()):
            return p
    return None


def _path_glob(path: str, patterns: list[str]) -> str | None:
    norm = path.replace("\\", "/")
    home = str(Path.home()).replace("\\", "/")
    variants = {norm, norm.replace(home, "~", 1), os.path.normpath(norm).replace("\\", "/")}
    if not norm.startswith(("/", "~", "./", "../")) and not re.match(r"^[A-Za-z]:", norm):
        variants.add("./" + norm)
    for p in patterns:
        for v in variants:
            if fnmatch.fnmatch(v, p) or fnmatch.fnmatch(v, p.replace("**/", "")):
                return p
    return None


class RateLimiter:
    """Sliding 60-second windows keyed by tuples such as (tenant, agent, scope).

    Thread-safe; injectable clock. Memory is bounded: past ``max_keys`` keys, idle keys
    are dropped (an idle key's window is empty, so dropping it changes no decision).
    """

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_keys: int = 100_000
    ) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, ...], deque[float]] = {}
        self._lock = threading.Lock()
        self.max_keys = max_keys

    def _window(self, key: tuple[str, ...], now: float, window: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= window:
            q.popleft()
        return q

    def check_and_add(
        self, key: tuple[str, ...], limit: int, window: float = 60.0
    ) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            q = self._window(key, now, window)
            if len(q) >= limit:
                return False, len(q)
            q.append(now)
            if len(self._hits) > self.max_keys:
                self._prune(now, window)
            return True, len(q)

    def peek(self, key: tuple[str, ...], window: float = 60.0) -> int:
        with self._lock:
            return len(self._window(key, self._clock(), window))

    def _prune(self, now: float, window: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= window]:
            del self._hits[k]

    def __len__(self) -> int:
        return len(self._hits)


class RBACEngine:
    """Evaluate a tool call against the active policy."""

    def __init__(self, store: PolicyStore, limiter: RateLimiter | None = None) -> None:
        self.store = store
        self.limiter = limiter if limiter is not None else RateLimiter()

    def authorize(
        self,
        agent_id: str,
        tool: str,
        arguments: dict[str, Any],
        profile: ArgumentProfile,
        *,
        tenant_id: str = "default",
        session_id: str | None = None,
        dry_run: bool = False,
    ) -> list[Finding]:
        """Authorize one call. ``dry_run`` evaluates rate limits without counting the call."""
        self.store.maybe_reload()
        policy = self.store.policy
        if policy is None:
            return [
                Finding(
                    Stage.RBAC,
                    "RBAC_NO_POLICY",
                    Severity.INFO,
                    "No RBAC policy configured; tool authorization not enforced",
                    {},
                    None,
                    Verdict.ALLOW,
                )
            ]
        ev = {
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "tool": tool,
            "policy_digest": policy.digest,
        }
        roles = policy.roles_for(agent_id, tenant_id)
        if not roles:
            if policy.default == "deny":
                return [
                    Finding(
                        Stage.RBAC,
                        "RBAC_UNKNOWN_AGENT",
                        Severity.CRITICAL,
                        f"Agent '{agent_id}' has no role in tenant '{tenant_id}' and the policy is default-deny",
                        ev,
                        OwaspVector.EXCESSIVE_PERMISSIONS.value,
                        Verdict.BLOCK,
                    )
                ]
            return [
                Finding(
                    Stage.RBAC,
                    "RBAC_DEFAULT_ALLOW",
                    Severity.INFO,
                    f"Agent '{agent_id}' has no role; policy default is allow",
                    ev,
                    None,
                    Verdict.ALLOW,
                )
            ]
        names = [r.name for r in roles]
        ev["roles"] = names
        for r in roles:
            pat = _glob_any(tool, r.forbidden_tools)
            if pat:
                return [
                    Finding(
                        Stage.RBAC,
                        "RBAC_TOOL_FORBIDDEN",
                        Severity.CRITICAL,
                        f"Role '{r.name}' forbids '{tool}' (rule {pat!r})",
                        {**ev, "role": r.name, "rule": pat},
                        OwaspVector.EXCESSIVE_PERMISSIONS.value,
                        Verdict.BLOCK,
                    )
                ]
        allowing = [(r, p) for r in roles if (p := _glob_any(tool, r.allowed_tools))]
        if not allowing:
            explicit = any(r.allowed_tools for r in roles)
            if explicit or policy.default == "deny":
                return [
                    Finding(
                        Stage.RBAC,
                        "RBAC_TOOL_NOT_ALLOWED",
                        Severity.CRITICAL,
                        f"No role of '{agent_id}' ({', '.join(names)}) allows '{tool}'",
                        ev,
                        OwaspVector.EXCESSIVE_PERMISSIONS.value,
                        Verdict.BLOCK,
                    )
                ]
        out: list[Finding] = []
        role, rule = allowing[0] if allowing else (roles[0], "default:allow")
        ev = {**ev, "role": role.name, "rule": rule}
        # argument restrictions from every matching role
        for r in roles:
            for tool_glob, restriction in r.restrictions.items():
                if fnmatch.fnmatchcase(tool, tool_glob):
                    out.extend(
                        self._restrictions(r.name, tool, arguments, profile, restriction, ev)
                    )
        if any(f.recommends is Verdict.BLOCK for f in out):
            return out
        # rate limits (counted only for calls RBAC would let through). All limits are
        # checked first and counted only if none is exceeded, so a refused call never
        # consumes another limit's budget.
        limits: list[tuple[tuple[str, ...], int, str]] = []
        for r in roles:
            for glob, limit in r.per_tool_per_minute.items():
                if fnmatch.fnmatchcase(tool, glob):
                    limits.append(
                        (
                            (tenant_id, agent_id, f"tool:{glob}"),
                            limit,
                            f"{limit} '{glob}' calls per minute (role {r.name})",
                        )
                    )
            if r.max_calls_per_minute is not None:
                limits.append(
                    (
                        (tenant_id, agent_id, f"role:{r.name}"),
                        r.max_calls_per_minute,
                        f"{r.max_calls_per_minute} calls per minute (role {r.name})",
                    )
                )
            if r.per_session_per_minute is not None and session_id is not None:
                limits.append(
                    (
                        (tenant_id, agent_id, f"session:{session_id}:{r.name}"),
                        r.per_session_per_minute,
                        f"{r.per_session_per_minute} calls per minute in session {session_id!r} (role {r.name})",
                    )
                )
        for key, limit, what in limits:
            if self.limiter.peek(key) >= limit:
                return [
                    *out,
                    Finding(
                        Stage.RBAC,
                        "RBAC_RATE_LIMIT",
                        Severity.HIGH,
                        f"'{agent_id}' exceeded {what}",
                        {**ev, "limit": limit, "window_s": 60, "scope": key[2]},
                        OwaspVector.TOOL_ABUSE.value,
                        Verdict.BLOCK,
                    ),
                ]
        if not dry_run:
            for key, limit, _ in limits:
                self.limiter.check_and_add(key, limit)
        for r in roles:
            pat = _glob_any(tool, r.require_approval)
            if pat:
                out.append(
                    Finding(
                        Stage.RBAC,
                        "RBAC_REQUIRES_APPROVAL",
                        Severity.HIGH,
                        f"Role '{r.name}' requires human approval for '{tool}'",
                        {**ev, "rule": pat},
                        OwaspVector.EXCESSIVE_PERMISSIONS.value,
                        Verdict.ESCALATE_HITL,
                    )
                )
        out.append(
            Finding(
                Stage.RBAC,
                "RBAC_ALLOWED",
                Severity.INFO,
                f"Role '{role.name}' allows '{tool}' (rule {rule!r})",
                ev,
                None,
                Verdict.ALLOW,
            )
        )
        return out

    def effective_permissions(self, agent_id: str, tenant_id: str = "default") -> dict[str, Any]:
        """What ``agent_id`` may do in ``tenant_id`` under the active policy, and why."""
        self.store.maybe_reload()
        policy = self.store.policy
        if policy is None:
            return {"agent_id": agent_id, "tenant_id": tenant_id, "policy": None, "enforced": False}
        roles = policy.roles_for(agent_id, tenant_id)
        matched = [p for p in policy.bindings_for(tenant_id) if fnmatch.fnmatchcase(agent_id, p)]
        out: dict[str, Any] = {
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "policy": {
                "digest": policy.digest,
                "version": policy.version,
                "default": policy.default,
            },
            "enforced": True,
            "bindings": matched,
            "roles": [r.name for r in roles],
            "allowed_tools": sorted({t for r in roles for t in r.allowed_tools}),
            "forbidden_tools": sorted({t for r in roles for t in r.forbidden_tools}),
            "require_approval": sorted({t for r in roles for t in r.require_approval}),
            "restricted_arguments": {
                f"{r.name}:{t}": {k: v for k, v in vars(a).items() if v}
                for r in roles
                for t, a in r.restrictions.items()
            },
            "rate_limits": {
                r.name: {
                    "max_calls_per_minute": r.max_calls_per_minute,
                    "per_session_per_minute": r.per_session_per_minute,
                    "per_tool": r.per_tool_per_minute,
                }
                for r in roles
            },
        }
        if not roles:
            out["effect"] = "deny_all" if policy.default == "deny" else "allow_all"
        elif not any(r.allowed_tools for r in roles) and policy.default == "allow":
            out["effect"] = "allow_all_except_forbidden"
        else:
            out["effect"] = "allowed_tools_only"
        return out

    def _restrictions(
        self,
        role: str,
        tool: str,
        arguments: dict[str, Any],
        profile: ArgumentProfile,
        r: ArgumentRestriction,
        ev: dict[str, Any],
    ) -> list[Finding]:
        out: list[Finding] = []

        def deny(code: str, msg: str, **extra: Any) -> None:
            out.append(
                Finding(
                    Stage.RBAC,
                    code,
                    Severity.CRITICAL,
                    msg,
                    {**ev, "role": role, **extra},
                    OwaspVector.EXCESSIVE_PERMISSIONS.value,
                    Verdict.BLOCK,
                )
            )

        command_texts = [c.text for c in profile.commands]
        if not command_texts:
            command_texts = [
                v
                for k, v in arguments.items()
                if isinstance(v, str) and k.lower() in ("command", "cmd", "script")
            ]
        whole = " ; ".join(command_texts)
        for pat in r.blocked_commands:
            if any(
                fnmatch.fnmatch(t.lower(), pat.lower())
                or fnmatch.fnmatch(t.lower(), pat.lower() + "*")
                for t in [whole, *command_texts]
            ) or ("*" not in pat and pat.lower() in whole.lower()):
                deny(
                    "RBAC_COMMAND_BLOCKED",
                    f"Role '{role}' blocks commands matching {pat!r} for '{tool}'",
                    pattern=pat,
                )
                break
        if r.allowed_commands:
            cmds = profile.commands or [c for t in command_texts for c in split_command_line(t)]
            for c in cmds:
                if not _glob_any(c.name, r.allowed_commands):
                    deny(
                        "RBAC_COMMAND_NOT_ALLOWED",
                        f"Role '{role}' does not allow the command '{c.name}' in '{tool}'",
                        command=c.name,
                        allowed=r.allowed_commands,
                    )
                    break
        for p in profile.paths:
            hit = _path_glob(p, r.blocked_paths)
            if hit:
                deny(
                    "RBAC_PATH_BLOCKED",
                    f"Role '{role}' blocks the path {p!r} (rule {hit!r})",
                    target=p,
                    rule=hit,
                )
                break
        if r.allowed_paths:
            for p in profile.paths:
                if "://" in p:
                    continue
                if not _path_glob(p, r.allowed_paths):
                    deny(
                        "RBAC_PATH_NOT_ALLOWED",
                        f"Role '{role}' does not allow the path {p!r} for '{tool}'",
                        target=p,
                        allowed=r.allowed_paths,
                    )
                    break
        for h in sorted(profile.hosts):
            hit = _glob_any(h, r.blocked_hosts)
            if hit:
                deny("RBAC_HOST_BLOCKED", f"Role '{role}' blocks the host {h!r}", host=h, rule=hit)
                break
        if r.allowed_hosts:
            for h in sorted(profile.hosts):
                if not _glob_any(h, r.allowed_hosts):
                    deny(
                        "RBAC_HOST_NOT_ALLOWED",
                        f"Role '{role}' does not allow the host {h!r} for '{tool}'",
                        host=h,
                        allowed=r.allowed_hosts,
                    )
                    break
        if r.max_length is not None:
            size = len(json.dumps(arguments, default=str))
            if size > r.max_length:
                deny(
                    "RBAC_ARGUMENTS_TOO_LARGE",
                    f"Arguments are {size} characters; role '{role}' allows {r.max_length}",
                    size=size,
                )
        for name, spec in r.fields.items():
            if name not in arguments:
                continue
            v = arguments[name]
            if "enum" in spec and v not in spec["enum"]:
                deny(
                    "RBAC_FIELD_NOT_ALLOWED",
                    f"'{name}'={v!r} is not one of {spec['enum']}",
                    field=name,
                )
            if "pattern" in spec and not re.search(str(spec["pattern"]), str(v)):
                deny(
                    "RBAC_FIELD_PATTERN",
                    f"'{name}'={str(v)[:80]!r} does not match {spec['pattern']!r}",
                    field=name,
                )
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if "max" in spec and v > spec["max"]:
                    deny("RBAC_FIELD_MAX", f"'{name}'={v} exceeds {spec['max']}", field=name)
                if "min" in spec and v < spec["min"]:
                    deny("RBAC_FIELD_MIN", f"'{name}'={v} is below {spec['min']}", field=name)
            if "max_length" in spec and len(str(v)) > int(spec["max_length"]):
                deny(
                    "RBAC_FIELD_LENGTH", f"'{name}' is longer than {spec['max_length']}", field=name
                )
        return out


class PolicyStore:
    """Holds the active policy; reloads atomically and never activates an invalid one."""

    def __init__(
        self,
        policy: RBACPolicy | None = None,
        path: str | Path | None = None,
        *,
        auto_reload_s: float | None = 2.0,
    ) -> None:
        self._policy = policy
        self._lock = threading.Lock()
        self._reload_lock = threading.Lock()
        self.path = Path(path) if path else None
        self.auto_reload_s = auto_reload_s
        self._checked_at = time.monotonic()
        self._mtime: float | None = None
        self._stat: tuple[int, float] | None = None
        self.last_error: list[str] | None = None
        self.loaded_at: float | None = time.time() if policy else None
        self.history: list[dict[str, Any]] = []
        if self.path and policy is None:
            self.reload()

    @property
    def policy(self) -> RBACPolicy | None:
        return self._policy

    def set(self, policy: RBACPolicy | None) -> None:
        with self._lock:
            prev = self._policy.digest if self._policy else None
            self._policy = policy
            self.loaded_at = time.time()
            self.last_error = None
            self.history.append(
                {
                    "at": self.loaded_at,
                    "from": prev,
                    "to": policy.digest if policy else None,
                    "ok": True,
                }
            )

    def load_document(self, doc: Any, *, source: str = "inline") -> RBACPolicy:
        """Validate ``doc``; on success swap it in, on failure keep the current policy and raise."""
        try:
            policy = parse_policy(doc, source=source)
        except PolicyError as exc:
            with self._lock:
                self.last_error = exc.errors
                self.history.append(
                    {
                        "at": time.time(),
                        "from": self._policy.digest if self._policy else None,
                        "to": None,
                        "ok": False,
                        "errors": exc.errors,
                    }
                )
            raise
        self.set(policy)
        return policy

    def reload(self) -> RBACPolicy | None:
        if self.path is None:
            return self._policy
        try:
            before = self.path.stat()
            text = self.path.read_text(encoding="utf-8")
            after = self.path.stat()
        except OSError as exc:
            with self._lock:
                self.last_error = [f"cannot read {self.path}: {exc}"]
            raise PolicyError(self.last_error) from exc
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            # the file changed while it was read (a non-atomic write in progress): never
            # activate what may be a truncated policy; the next check reads it again
            with self._lock:
                self.last_error = [f"{self.path} changed while it was read; not activated"]
            raise PolicyError(self.last_error)
        self._mtime = after.st_mtime
        self._stat = (after.st_size, after.st_mtime_ns)
        return self.load_document(text, source=str(self.path))

    def reload_if_changed(self) -> bool:
        """Poll the policy file; returns True when a new valid policy was activated."""
        if self.path is None:
            return False
        try:
            st = self.path.stat()
        except OSError:
            return False
        if self._stat is not None and (st.st_size, st.st_mtime_ns) == self._stat:
            return False
        with self._reload_lock:
            try:
                self.reload()
            except PolicyError:
                if self.last_error and "changed while it was read" in self.last_error[0]:
                    return False  # retry next time
                self._stat = (st.st_size, st.st_mtime_ns)  # do not retry the same broken file
                return False
        return True

    def maybe_reload(self) -> None:
        """Called on every authorization: re-check the file at most every ``auto_reload_s``."""
        if self.path is None or self.auto_reload_s is None:
            return
        now = time.monotonic()
        if now - self._checked_at < self.auto_reload_s:
            return
        self._checked_at = now
        self.reload_if_changed()

    def status(self) -> dict[str, Any]:
        p = self._policy
        return {
            "configured": p is not None,
            "digest": p.digest if p else None,
            "version": p.version if p else None,
            "default": p.default if p else None,
            "source": p.source if p else None,
            "loaded_at": self.loaded_at,
            "last_error": self.last_error,
            "roles": sorted(p.roles) if p else [],
            "agents": p.agents if p else {},
            "tenants": sorted(p.tenants) if p else [],
            "auto_reload_s": self.auto_reload_s if self.path else None,
            "history": self.history[-20:],
        }
