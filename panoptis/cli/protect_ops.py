"""``panoptis protect status|decisions|agents|approvals|rbac|doctor`` (TO-028).

Every inspection command runs in one of two modes:

* **API mode** (``--api URL`` or ``AGENTWATCH_API_URL``, with ``--key`` or
  ``AGENTWATCH_API_KEY``): the command calls the protection API, so it is authorized
  exactly like the API (tenant from the key, operator-only routes refused to tenants);
* **local mode** (default): the command reads the evidence store directly
  (``--store`` / ``AGENTWATCH_STORE``). Access to the store file is operator access.

Errors (an unreachable API, a refused key, an unreadable store) print one line and exit
with a non-zero status; secrets are never printed (keys are never echoed, records are
already redacted at storage).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer

EXIT_DENIED = 3
EXIT_UNAVAILABLE = 4

API_OPTION = typer.Option(
    None, "--api", help="AgentWatch API URL (default: AGENTWATCH_API_URL; omit for local mode)."
)
KEY_OPTION = typer.Option(
    None, "--key", help="API key (default: AGENTWATCH_API_KEY). Never printed."
)
STORE_OPTION = typer.Option(
    None, "--store", help="Evidence store for local mode (default: AGENTWATCH_STORE)."
)
TENANT_OPTION = typer.Option(
    "default", "--tenant", help="Tenant (local mode; in API mode the key decides)."
)


def _out(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def _die(msg: str, code: int = 1) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code)


class Api:
    def __init__(self, url: str, key: str | None) -> None:
        self.url = url.rstrip("/")
        self.key = key

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        import httpx

        headers = {"X-API-Key": self.key} if self.key else {}
        try:
            r = httpx.request(
                method,
                f"{self.url}/api/v1/protect{path}",
                params={k: v for k, v in (params or {}).items() if v is not None},
                json=body,
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            _die(f"API unavailable at {self.url} ({type(exc).__name__})", EXIT_UNAVAILABLE)
        if r.status_code in (401, 403):
            _die(f"not authorized ({r.status_code}): {_detail(r)}", EXIT_DENIED)
        if r.status_code == 404:
            _die(f"not found: {_detail(r)}")
        if r.status_code >= 400:
            _die(
                f"API error {r.status_code}: {_detail(r)}",
                EXIT_UNAVAILABLE if r.status_code >= 500 else 1,
            )
        return r.json()


def _detail(r: Any) -> str:
    try:
        d = r.json()
        return str(d.get("detail") or d.get("error") or d)[:300]
    except ValueError:
        return r.text[:300]


def _api(api: str | None, key: str | None) -> Api | None:
    url = api or os.environ.get("AGENTWATCH_API_URL")
    if not url:
        return None
    return Api(url, key or os.environ.get("AGENTWATCH_API_KEY"))


def _ledger(store: str | None) -> Any:
    from panoptis.protect.ledger import ProtectLedger

    try:
        return ProtectLedger(store or os.environ.get("AGENTWATCH_STORE") or None, seal_every=0)
    except Exception as exc:  # noqa: BLE001
        _die(
            f"evidence store unavailable ({type(exc).__name__}: {str(exc)[:200]})", EXIT_UNAVAILABLE
        )


def _local(store: str | None, tenant: str, path: str, params: dict[str, Any] | None = None) -> Any:
    """Answer an API path from the local store, with the API's own handlers."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from panoptis.api import protect_api, protect_approvals, protect_control
    from panoptis.protect.approvals import ApprovalBroker
    from panoptis.protect.control import ControlState

    lg = _ledger(store)
    protect_api.set_ledger(lg)
    protect_approvals.set_broker(ApprovalBroker(lg))
    protect_control.set_control(ControlState(lg, refresh_s=0.0))
    app = FastAPI()
    app.include_router(protect_api.build_router(lambda: None, lambda: tenant))
    app.include_router(protect_approvals.build_router(lambda: None, lambda: tenant))
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(
                f"/api/v1/protect{path}",
                params={k: v for k, v in (params or {}).items() if v is not None},
            )
        if r.status_code == 404:
            _die(f"not found: {_detail(r)}")
        if r.status_code >= 400:
            _die(
                f"error {r.status_code}: {_detail(r)}",
                EXIT_UNAVAILABLE if r.status_code >= 500 else 1,
            )
        return r.json()
    finally:
        protect_api.set_ledger(None)
        protect_approvals.set_broker(None)
        protect_control.set_control(None)
        lg.store.close()


def _get(
    api: str | None, key: str | None, store: str | None, tenant: str, path: str, **params: Any
) -> Any:
    client = _api(api, key)
    if client is not None:
        return client.request("GET", path, params=params)
    return _local(store, tenant, path, params)


def register(protect_app: typer.Typer) -> None:
    @protect_app.command("status")
    def status(
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
        store: str | None = STORE_OPTION,
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Protection summary: verdicts, kill switch, suspensions, policy, evidence head."""
        _out(_get(api, key, store, tenant, "/status"))

    @protect_app.command("decisions")
    def decisions(
        verdict: str | None = typer.Option(
            None, "--verdict", help="allow | block | escalate_hitl | mutate_and_allow"
        ),
        tool: str | None = typer.Option(None, "--tool"),
        agent: str | None = typer.Option(None, "--agent"),
        stage: str | None = typer.Option(
            None, "--stage", help="threat | rbac | loop | intent | hitl | killswitch | …"
        ),
        limit: int = typer.Option(20, "--limit"),
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
        store: str | None = STORE_OPTION,
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Recent protection decisions, newest first."""
        _out(
            _get(
                api,
                key,
                store,
                tenant,
                "/decisions",
                verdict=verdict,
                tool=tool,
                agent_id=agent,
                stage=stage,
                limit=limit,
            )
        )

    @protect_app.command("agents")
    def agents(
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
        store: str | None = STORE_OPTION,
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Agents and sessions seen, with counts and suspension state (seen is not protected: see 'discover')."""
        _out(_get(api, key, store, tenant, "/agents"))

    approvals_app = typer.Typer(
        name="approvals", help="List and decide escalated tool calls.", no_args_is_help=True
    )
    protect_app.add_typer(approvals_app, name="approvals")

    @approvals_app.command("list")
    def approvals_list(
        state: str | None = typer.Option(
            "pending", "--state", help="pending | approved | rejected | expired | cancelled | all"
        ),
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
        store: str | None = STORE_OPTION,
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Approval requests of the tenant."""
        _out(_get(api, key, store, tenant, "/approvals", state=None if state == "all" else state))

    def _decide(
        request_id: str,
        approved: bool,
        digest: str,
        by: str | None,
        comment: str | None,
        api: str | None,
        key: str | None,
        store: str | None,
        tenant: str,
    ) -> None:
        decided_by = by or os.environ.get("USER") or os.environ.get("USERNAME") or "cli"
        client = _api(api, key)
        if client is not None:
            _out(
                client.request(
                    "POST",
                    f"/approvals/{request_id}",
                    body={
                        "approved": approved,
                        "action_digest": digest,
                        "decided_by": decided_by,
                        "comment": comment,
                    },
                )
            )
            return
        from panoptis.protect.approvals import ApprovalBroker, ApprovalError

        lg = _ledger(store)
        try:
            broker = ApprovalBroker(lg)
            if broker.get(request_id, tenant_id=tenant) is None:
                _die(f"approval request {request_id} not found in tenant {tenant}")
            try:
                out = broker.resolve(
                    request_id,
                    approved,
                    f"{tenant}:cli:{decided_by}",
                    comment=comment,
                    tenant_id=tenant,
                    expected_digest=digest,
                )
            except ApprovalError as exc:
                _die(str(exc))
            _out(
                {
                    **(broker.get(request_id, tenant_id=tenant) or {}),
                    "applied": out.approved == approved,
                }
            )
        finally:
            lg.store.close()

    @approvals_app.command("approve")
    def approve(
        request_id: str = typer.Argument(...),
        digest: str = typer.Option(
            ..., "--digest", help="the action_digest shown by 'approvals list' (what you approve)"
        ),
        by: str | None = typer.Option(None, "--by"),
        comment: str | None = typer.Option(None, "--comment"),
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
        store: str | None = STORE_OPTION,
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Approve exactly the action with this digest."""
        _decide(request_id, True, digest, by, comment, api, key, store, tenant)

    @approvals_app.command("reject")
    def reject(
        request_id: str = typer.Argument(...),
        digest: str = typer.Option(..., "--digest"),
        by: str | None = typer.Option(None, "--by"),
        comment: str | None = typer.Option(None, "--comment"),
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
        store: str | None = STORE_OPTION,
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Reject the escalated call; it never runs."""
        _decide(request_id, False, digest, by, comment, api, key, store, tenant)

    rbac_app = typer.Typer(
        name="rbac",
        help="Validate RBAC policies and explain effective permissions.",
        no_args_is_help=True,
    )
    protect_app.add_typer(rbac_app, name="rbac")

    @rbac_app.command("validate")
    def rbac_validate(file: Path = typer.Argument(..., help="policy YAML/JSON")) -> None:
        """Validate a policy file; lists every problem. Exit 1 if invalid."""
        from panoptis.protect.rbac import PolicyError, parse_policy

        try:
            text = file.read_text(encoding="utf-8")
        except OSError as exc:
            _die(f"cannot read {file}: {exc}")
        try:
            p = parse_policy(text, source=str(file))
        except PolicyError as exc:
            _out({"valid": False, "errors": exc.errors})
            raise typer.Exit(1) from exc
        _out(
            {
                "valid": True,
                "digest": p.digest,
                "version": p.version,
                "default": p.default,
                "roles": sorted(p.roles),
                "agents": p.agents,
                "tenants": sorted(p.tenants),
            }
        )

    def _engine(file: Path | None) -> Any:
        from panoptis.protect.rbac import PolicyError, PolicyStore, RBACEngine

        path = file or (
            Path(os.environ["AGENTWATCH_POLICY_FILE"])
            if os.environ.get("AGENTWATCH_POLICY_FILE")
            else None
        )
        if path is None:
            _die("no policy: pass --policy or set AGENTWATCH_POLICY_FILE")
        try:
            return RBACEngine(PolicyStore(path=path))
        except PolicyError as exc:
            _die(f"invalid policy: {'; '.join(exc.errors)[:500]}")

    @rbac_app.command("permissions")
    def rbac_permissions(
        agent: str = typer.Argument(...),
        policy: Path | None = typer.Option(None, "--policy"),
        tenant: str = TENANT_OPTION,
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
    ) -> None:
        """What an agent may do (roles, tools, restrictions, rate limits)."""
        client = _api(api, key)
        if client is not None:
            _out(client.request("GET", f"/rbac/agents/{agent}/permissions"))
            return
        _out(_engine(policy).effective_permissions(agent, tenant))

    @rbac_app.command("check")
    def rbac_check(
        agent: str = typer.Argument(...),
        tool: str = typer.Argument(...),
        arguments: str = typer.Option("{}", "--args", help="tool arguments as JSON"),
        policy: Path | None = typer.Option(None, "--policy"),
        tenant: str = TENANT_OPTION,
        api: str | None = API_OPTION,
        key: str | None = KEY_OPTION,
    ) -> None:
        """Would this call be authorized? Exit 1 if not (a dry run: no rate budget used)."""
        try:
            args = json.loads(arguments)
        except ValueError as exc:
            _die(f"--args is not JSON: {exc}")
        if not isinstance(args, dict):
            _die("--args must be a JSON object")
        client = _api(api, key)
        if client is not None:
            out = client.request(
                "POST", "/rbac/check", body={"agent_id": agent, "tool": tool, "arguments": args}
            )
        else:
            from panoptis.protect.model import Verdict, strongest
            from panoptis.protect.threats import ThreatAnalyzer

            findings = _engine(policy).authorize(
                agent,
                tool,
                args,
                ThreatAnalyzer().analyze(tool, args).profile,
                tenant_id=tenant,
                dry_run=True,
            )
            v = strongest([f.recommends for f in findings])
            out = {
                "agent_id": agent,
                "tool": tool,
                "verdict": v.value,
                "authorized": v is Verdict.ALLOW,
                "reasons": [f.message for f in findings if f.recommends is not Verdict.ALLOW],
            }
        _out(out)
        if not out.get("authorized"):
            raise typer.Exit(1)

    @protect_app.command("doctor")
    def protect_doctor(
        store: str | None = STORE_OPTION,
        policy: Path | None = typer.Option(
            None, "--policy", help="default: AGENTWATCH_POLICY_FILE"
        ),
        tenant: str = TENANT_OPTION,
    ) -> None:
        """Diagnose the protection setup (store, evidence, policy, kill switch, hook). Exit 1 on a failure."""
        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool | None, detail: str) -> None:
            checks.append(
                {
                    "check": name,
                    "status": "ok" if ok else ("warn" if ok is None else "fail"),
                    "detail": detail,
                }
            )

        lg = None
        try:
            lg = _ledger(store)
            add(
                "evidence store",
                True,
                str(lg.store.engine.url.render_as_string(hide_password=True)),
            )
            rep = lg.store.verify(tenant).to_dict()
            add(
                "evidence integrity",
                rep["ok"],
                f"{rep['segments_checked']} segments, {rep['unsealed_observations']} unsealed"
                + (f"; {rep['errors'][0]}" if rep["errors"] else ""),
            )
        except typer.Exit:
            add("evidence store", False, "unavailable")
        from panoptis.protect.control import ControlState

        ctl = ControlState(lg, refresh_s=0.0).status() if lg else ControlState(None).status()
        add(
            "kill switch",
            None if ctl["killed"] or ctl["local_killed"] else True,
            "ACTIVE: every protected call is blocked"
            if (ctl["killed"] or ctl["local_killed"])
            else "not tripped",
        )
        path = policy or (
            Path(os.environ["AGENTWATCH_POLICY_FILE"])
            if os.environ.get("AGENTWATCH_POLICY_FILE")
            else None
        )
        if path is None:
            add("rbac policy", None, "none configured: tool authorization is not enforced")
        else:
            from panoptis.protect.rbac import PolicyError, parse_policy

            try:
                p = parse_policy(path.read_text(encoding="utf-8"), source=str(path))
                add("rbac policy", True, f"{path} (digest {p.digest}, default {p.default})")
            except (OSError, PolicyError) as exc:
                add("rbac policy", False, f"{path}: {str(exc)[:300]}")
        try:
            import psutil  # noqa: F401

            add("discovery", True, "psutil available for the process scan")
        except ImportError:
            add(
                "discovery",
                None,
                "psutil not installed: process discovery unavailable (pip install 'panoptis[discovery]')",
            )
        for settings in (
            Path(".claude") / "settings.local.json",
            Path(".claude") / "settings.json",
        ):
            if settings.is_file() and "claude_hook_entry.py" in settings.read_text(
                encoding="utf-8", errors="replace"
            ):
                add("claude code hook", True, f"installed in {settings}")
                break
        else:
            add(
                "claude code hook",
                None,
                "not installed in this project (panoptis protect claude-hook install)",
            )
        if lg is not None:
            lg.store.close()
        _out({"checks": checks})
        if any(c["status"] == "fail" for c in checks):
            raise typer.Exit(1)
