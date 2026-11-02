"""TO-022: per-agent RBAC, enforced before execution.

Allowed/forbidden tools, restricted arguments, rate limits (per agent, tool and session),
default deny and explicit exceptions, policy validation, safe reload, tenant boundaries,
and the effective-permissions API. Every tool is a harmless stub that records whether it
ran.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from panoptis.protect import ActionBlocked, PolicyStore, ProtectionPipeline, ToolAction
from panoptis.protect.enforce import enforce_call
from panoptis.protect.rbac import PolicyError, RateLimiter, RBACEngine, parse_policy
from panoptis.protect.threats import ThreatAnalyzer

POLICY = """
version: "1.0"
default: deny
agents:
  coder: [coder]
  "research-*": [researcher]
  limited: [limited]
roles:
  coder:
    allowed_tools: [bash, read_file, write_file]
    require_approval: [write_file]
    restricted_arguments:
      bash:
        blocked_commands: ["sudo *"]
        allowed_commands: [ls, cat, git, pytest]
        blocked_paths: ["**/.env"]
        allowed_hosts: [pypi.org]
      read_file:
        allowed_paths: ["./**", "src/**"]
        fields: {encoding: {enum: [utf-8, ascii]}}
  researcher:
    allowed_tools: [web_search, read_file]
    forbidden_tools: [bash, "write_*"]
    rate_limits: {per_tool: {web_search: 2}}
  limited:
    allowed_tools: [read_file]
    rate_limits: {per_session_per_minute: 2}
tenants:
  globex:
    inherit: false
    agents: {coder: [researcher]}
"""

_ta = ThreatAnalyzer()


def engine(doc: str = POLICY, clock=None) -> RBACEngine:
    store = PolicyStore()
    store.load_document(doc)
    return RBACEngine(store, RateLimiter(clock) if clock else None)


def authz(e: RBACEngine, agent: str, tool: str, args: dict[str, Any] | None = None, **kw: Any):
    args = args or {}
    return e.authorize(agent, tool, args, _ta.analyze(tool, args).profile, **kw)


def codes(fs) -> list[str]:
    return [f.code for f in fs]


def blocked(fs) -> bool:
    return any(f.recommends.value == "block" for f in fs)


# ── tool matrix ─────────────────────────────────────────────────────────────────────


def test_allowed_tool_is_authorized_with_role_and_rule():
    fs = authz(engine(), "coder", "read_file", {"path": "src/a.py"})
    f = next(f for f in fs if f.code == "RBAC_ALLOWED")
    assert not blocked(fs) and f.evidence["role"] == "coder" and f.evidence["rule"] == "read_file"


def test_tool_outside_the_allow_list_is_denied():
    fs = authz(engine(), "coder", "send_email", {"to": "a@example.org"})
    assert codes(fs) == ["RBAC_TOOL_NOT_ALLOWED"] and blocked(fs)


def test_forbidden_tool_is_denied_even_if_another_glob_would_allow_it():
    fs = authz(engine(), "research-1", "write_notes")
    assert codes(fs) == ["RBAC_TOOL_FORBIDDEN"] and fs[0].evidence["rule"] == "write_*"


def test_forbidden_beats_allowed_across_roles():
    doc = POLICY.replace("coder: [coder]", "coder: [coder, researcher]")
    assert codes(authz(engine(doc), "coder", "bash", {"command": "ls"})) == ["RBAC_TOOL_FORBIDDEN"]


def test_require_approval_escalates():
    fs = authz(engine(), "coder", "write_file", {"path": "a.txt", "content": "x"})
    assert "RBAC_REQUIRES_APPROVAL" in codes(fs) and not blocked(fs)


# ── restricted arguments ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args,code",
    [
        ({"command": "sudo ls"}, "RBAC_COMMAND_BLOCKED"),
        ({"command": "ls && whoami"}, "RBAC_COMMAND_NOT_ALLOWED"),
        ({"command": "cat config/.env"}, "RBAC_PATH_BLOCKED"),
        ({"command": "git clone https://example.org/x.git"}, "RBAC_HOST_NOT_ALLOWED"),
    ],
)
def test_restricted_shell_arguments_are_denied(args, code):
    fs = authz(engine(), "coder", "bash", args)
    assert code in codes(fs) and blocked(fs)


def test_permitted_shell_arguments_pass():
    assert not blocked(authz(engine(), "coder", "bash", {"command": "git status && pytest -q"}))


def test_restricted_paths_and_fields():
    e = engine()
    assert "RBAC_PATH_NOT_ALLOWED" in codes(authz(e, "coder", "read_file", {"path": "/etc/passwd"}))
    assert "RBAC_FIELD_NOT_ALLOWED" in codes(
        authz(e, "coder", "read_file", {"path": "src/a.py", "encoding": "latin-1"})
    )
    assert not blocked(authz(e, "coder", "read_file", {"path": "src/a.py", "encoding": "utf-8"}))


# ── rate limits ──────────────────────────────────────────────────────────────────


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_per_tool_rate_limit_and_window_expiry():
    clock = Clock()
    e = engine(clock=clock)
    assert [blocked(authz(e, "research-1", "web_search", {"q": str(i)})) for i in range(3)] == [
        False,
        False,
        True,
    ]
    clock.t += 61
    assert not blocked(authz(e, "research-1", "web_search", {"q": "again"}))


def test_rate_limits_are_per_agent_and_per_tenant():
    clock = Clock()
    e = engine(clock=clock)
    for _ in range(2):
        authz(e, "research-1", "web_search", {"q": "x"})
    assert blocked(authz(e, "research-1", "web_search", {"q": "x"}))
    assert not blocked(authz(e, "research-2", "web_search", {"q": "x"}))
    assert not blocked(authz(e, "research-1", "web_search", {"q": "x"}, tenant_id="acme"))


def test_per_session_rate_limit():
    e = engine(clock=Clock())
    r = [
        blocked(authz(e, "limited", "read_file", {"path": "a"}, session_id="s1")) for _ in range(3)
    ]
    assert r == [False, False, True]
    assert not blocked(authz(e, "limited", "read_file", {"path": "a"}, session_id="s2"))


def test_dry_run_and_refused_calls_do_not_consume_budget():
    e = engine(clock=Clock())
    for _ in range(5):
        authz(e, "research-1", "web_search", {"q": "x"}, dry_run=True)
    assert not blocked(authz(e, "research-1", "web_search", {"q": "x"}))
    assert not blocked(authz(e, "research-1", "web_search", {"q": "x"}))
    assert blocked(authz(e, "research-1", "web_search", {"q": "x"}))


def test_rate_limiter_memory_is_bounded():
    clock = Clock()
    lim = RateLimiter(clock, max_keys=50)
    for i in range(500):
        lim.check_and_add(("t", f"a{i}", "x"), 5)
        clock.t += 61  # each key idle by the next insert
    assert len(lim) <= 51


# ── default deny and exceptions ──────────────────────────────────────────────────


def test_unknown_agent_is_denied_by_default():
    assert codes(authz(engine(), "stranger", "read_file")) == ["RBAC_UNKNOWN_AGENT"]


def test_default_allow_is_an_explicit_exception():
    doc = POLICY.replace("default: deny", "default: allow")
    fs = authz(engine(doc), "stranger", "read_file")
    assert codes(fs) == ["RBAC_DEFAULT_ALLOW"] and not blocked(fs)
    # roles still apply to known agents under default allow
    assert blocked(authz(engine(doc), "research-1", "bash", {"command": "ls"}))


def test_no_policy_is_reported_not_silently_enforced():
    e = RBACEngine(PolicyStore())
    assert codes(authz(e, "anyone", "bash", {"command": "ls"})) == ["RBAC_NO_POLICY"]


# ── validation ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "doc,error",
    [
        ("roles: {a: {allowed_tools: [x]}}\nroles: {b: {}}", "duplicate key"),
        ("version: '2.0'", "unsupported policy version"),
        ("roles: {a: {allowed_tools: [x], forbidden_tools: [x]}}", "both allowed and forbidden"),
        ("agents: {bot: [ghost]}", "unknown role"),
        ("roles: {a: {allowed_tool: [x]}}", "unknown key"),
        ("default: maybe", "default must be"),
        ("roles: {a: {rate_limits: {max_calls_per_minute: -1}}}", "non-negative"),
        ("roles: {a: {rate_limits: {per_session_per_minute: x}}}", "non-negative"),
        ("roles: {a: {restricted_arguments: {t: {fields: {n: {max: big}}}}}}", "must be a number"),
        (
            "roles: {a: {restricted_arguments: {t: {fields: {n: {min: 5, max: 1}}}}}}",
            "min is greater",
        ),
        ("roles: {a: {restricted_arguments: {t: {fields: {n: {pattern: '('}}}}}}", "invalid regex"),
        ("roles: {a: {allowed_tools: ['']}}", "empty pattern"),
        ("tenants: {acme: {agents: {bot: [ghost]}}}", "unknown role"),
        ("tenants: {acme: {inherit: maybe}}", "inherit"),
        ("roles: {}\nagent_roles: {}", "not both"),
        ("- a list", "must be a mapping"),
    ],
)
def test_invalid_or_ambiguous_policies_are_rejected(doc, error):
    with pytest.raises(PolicyError) as exc:
        parse_policy(doc)
    assert error in str(exc.value)


def test_all_errors_are_reported_at_once():
    with pytest.raises(PolicyError) as exc:
        parse_policy("default: maybe\nroles: {a: {allowed_tool: [x]}}\nagents: {b: [ghost]}")
    assert len(exc.value.errors) == 3


# ── reload ──────────────────────────────────────────────────────────────────────


def test_invalid_reload_keeps_the_previous_policy(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text(POLICY, encoding="utf-8")
    store = PolicyStore(path=f, auto_reload_s=0)
    e = RBACEngine(store)
    digest = store.policy.digest
    f.write_text(POLICY + "\nroles: {}\n", encoding="utf-8")  # duplicate key: invalid
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert blocked(authz(e, "research-1", "bash", {"command": "ls"}))  # old policy still enforced
    assert store.policy.digest == digest and "duplicate key" in store.last_error[0]


def test_file_changes_are_picked_up_automatically(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text(POLICY, encoding="utf-8")
    store = PolicyStore(path=f, auto_reload_s=0)
    e = RBACEngine(store)
    assert blocked(authz(e, "research-1", "bash", {"command": "ls"}))
    f.write_text(
        POLICY.replace(
            "forbidden_tools: [bash, ",
            "allowed_tools: [web_search, read_file, bash]\n    forbidden_tools: [",
        ).replace("allowed_tools: [web_search, read_file]\n    allowed_tools", "allowed_tools"),
        encoding="utf-8",
    )
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert not blocked(authz(e, "research-1", "bash", {"command": "ls"}))
    assert store.history[-1]["ok"]


def test_a_file_changing_while_it_is_read_is_not_activated(tmp_path, monkeypatch):
    f = tmp_path / "policy.yaml"
    f.write_text(POLICY, encoding="utf-8")
    store = PolicyStore(path=f, auto_reload_s=0)
    digest = store.policy.digest
    # simulate a writer in progress: the file grows between the two stats
    real_read = type(f).read_text

    def racing_read(self, *a, **kw):
        text = real_read(self, *a, **kw)
        with open(self, "a", encoding="utf-8") as fh:
            fh.write("\n# still writing\n")
        return (
            text.split("roles:")[0] + "roles: {coder: {}, limited: {}}\n"
        )  # a truncated, valid-looking policy

    monkeypatch.setattr(type(f), "read_text", racing_read)
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert store.reload_if_changed() is False
    assert store.policy.digest == digest


def test_reload_has_no_window_without_a_policy(tmp_path):
    """Concurrent authorizations during reloads always see one complete policy."""
    import threading

    f = tmp_path / "policy.yaml"
    f.write_text(POLICY, encoding="utf-8")
    store = PolicyStore(path=f, auto_reload_s=0)
    e = RBACEngine(store)
    seen: list[list[str]] = []
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            seen.append(codes(authz(e, "stranger", "bash", {"command": "ls"})))

    t = threading.Thread(target=worker)
    t.start()
    for i in range(20):
        store.load_document(POLICY + f"\ndescription: rev {i}\n")
    stop.set()
    t.join()
    assert seen and all(c == ["RBAC_UNKNOWN_AGENT"] for c in seen)


# ── tenant boundaries ─────────────────────────────────────────────────────────────


def test_tenant_bindings_override_global_ones():
    e = engine()
    assert not blocked(authz(e, "coder", "bash", {"command": "ls"}))  # default tenant: coder role
    fs = authz(e, "coder", "bash", {"command": "ls"}, tenant_id="globex")  # globex: researcher only
    assert codes(fs) == ["RBAC_TOOL_FORBIDDEN"] and fs[0].evidence["tenant_id"] == "globex"
    # inherit: false — global bindings do not apply in globex
    assert codes(authz(e, "research-1", "web_search", {"q": "x"}, tenant_id="globex")) == [
        "RBAC_UNKNOWN_AGENT"
    ]


def test_pipeline_binds_authorization_to_the_call_tenant(ledger):
    store = PolicyStore()
    store.load_document(POLICY)
    p = ProtectionPipeline(policy=store, ledger=ledger)
    runs: list[str] = []
    ok = ToolAction(agent_id="coder", session_id="s", tool="bash", arguments={"command": "ls"})
    assert enforce_call(p, ok, lambda a: runs.append("default") or "ok") == "ok"
    other = ToolAction(
        agent_id="coder",
        session_id="s",
        tenant_id="globex",
        tool="bash",
        arguments={"command": "ls"},
    )
    with pytest.raises(ActionBlocked) as exc:
        enforce_call(p, other, lambda a: runs.append("globex"))
    assert runs == ["default"]
    assert any(f.code == "RBAC_TOOL_FORBIDDEN" for f in exc.value.decision.findings)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("send_email", {"to": "a@example.org"}),
        ("bash", {"command": "sudo ls"}),
        ("bash", {"command": "ls; whoami"}),
        ("read_file", {"path": "/etc/passwd"}),
    ],
)
def test_unauthorized_tools_and_arguments_never_execute(ledger, tool, args):
    store = PolicyStore()
    store.load_document(POLICY)
    p = ProtectionPipeline(policy=store, ledger=ledger)
    runs = []
    a = ToolAction(agent_id="coder", session_id="s", tool=tool, arguments=args)
    with pytest.raises(ActionBlocked):
        enforce_call(p, a, lambda x: runs.append(1))
    assert runs == []


# ── effective permissions ─────────────────────────────────────────────────────────


def test_effective_permissions():
    e = engine()
    perm = e.effective_permissions("coder")
    assert perm["roles"] == ["coder"] and perm["effect"] == "allowed_tools_only"
    assert perm["allowed_tools"] == ["bash", "read_file", "write_file"]
    assert perm["require_approval"] == ["write_file"]
    assert "coder:bash" in perm["restricted_arguments"]
    assert e.effective_permissions("coder", "globex")["roles"] == ["researcher"]
    assert e.effective_permissions("stranger")["effect"] == "deny_all"


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from panoptis.api import protect_rbac, server

    store = PolicyStore()
    store.load_document(POLICY)
    protect_rbac.set_policy_store(store)
    monkeypatch.setenv("AGENTWATCH_TENANT_KEYS", "acme=key-acme,globex=key-globex")
    monkeypatch.setattr(server, "_CLOUD_MODE", False)
    monkeypatch.setattr(server, "_API_KEY", "operator-key")
    with TestClient(server.app) as c:
        yield c
    protect_rbac.set_policy_store(None)


def test_api_effective_permissions_are_tenant_scoped(api):
    r = api.get("/api/v1/protect/rbac/agents/coder/permissions", headers={"X-API-Key": "key-acme"})
    assert (
        r.status_code == 200 and r.json()["roles"] == ["coder"] and r.json()["tenant_id"] == "acme"
    )
    r = api.get(
        "/api/v1/protect/rbac/agents/coder/permissions", headers={"X-API-Key": "key-globex"}
    )
    assert r.json()["roles"] == ["researcher"]


def test_api_policy_shows_only_the_callers_bindings(api):
    body = api.get("/api/v1/protect/rbac/policy", headers={"X-API-Key": "key-globex"}).json()
    assert body["agents"] == {"coder": ["researcher"]} and "tenants" not in body
    body = api.get("/api/v1/protect/rbac/policy", headers={"X-API-Key": "key-acme"}).json()
    assert "research-*" in body["agents"]


def test_api_check_explains_denials_without_consuming_budget(api):
    h = {"X-API-Key": "key-acme"}
    r = api.post(
        "/api/v1/protect/rbac/check",
        json={"agent_id": "coder", "tool": "bash", "arguments": {"command": "sudo ls"}},
        headers=h,
    ).json()
    assert r["authorized"] is False and r["verdict"] == "block"
    assert any("blocks commands matching 'sudo *'" in m for m in r["reasons"])
    r = api.post(
        "/api/v1/protect/rbac/check",
        json={"agent_id": "coder", "tool": "bash", "arguments": {"command": "ls"}},
        headers=h,
    ).json()
    assert r["authorized"] is True
    for _ in range(5):
        api.post(
            "/api/v1/protect/rbac/check",
            json={"agent_id": "research-1", "tool": "web_search", "arguments": {"q": "x"}},
            headers=h,
        )
    r = api.post(
        "/api/v1/protect/rbac/check",
        json={"agent_id": "research-1", "tool": "web_search", "arguments": {"q": "x"}},
        headers=h,
    ).json()
    assert r["authorized"] is True


def test_api_requires_a_key(api):
    assert api.get("/api/v1/protect/rbac/policy").status_code == 401
