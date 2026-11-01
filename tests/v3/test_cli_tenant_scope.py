"""TO-003: the CLI reads only the tenant it is asked for.

The CLI works on a local store with the OS user's permissions (a local trust boundary,
docs/TENANCY.md). Within that store, tenant-taking commands must not mix tenants, and
commands without --tenant use only ``default``.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agentwatch import instrument as aw
from agentwatch.cli.main import app
from agentwatch.runtime.engine import Engine
from agentwatch.storage.store import Store


def _ingest(store: Store, sink, tenant: str, runs: int) -> None:
    sink.drafts.clear()
    for i in range(runs):
        with aw.run(f"{tenant}-run-{i}"):
            with aw.span("TOOL_INVOCATION", "lookup", actor="agent:a") as s:
                s.output({"i": i})
    drafts = list(sink.drafts)
    for d in drafts:
        d.tenant_id = tenant
    Engine(store).ingest(drafts)


def test_cli_tenant_scoped_commands_do_not_mix_tenants(tmp_path, sink):
    url = f"sqlite:///{(tmp_path / 'aw.db').as_posix()}"
    store = Store(url)
    _ingest(store, sink, "acme", 2)
    _ingest(store, sink, "globex", 1)
    counts = {t: store.count_observations(t) for t in ("acme", "globex", "default")}
    store.close()
    assert counts["acme"] > counts["globex"] > 0 and counts["default"] == 0

    runner = CliRunner()
    for tenant in ("acme", "globex"):
        r = runner.invoke(app, ["evidence", "verify", "--store", url, "--tenant", tenant])
        assert r.exit_code == 0, r.output
        report = json.loads(r.output[r.output.index("{") :])
        assert report["observations_checked"] + report["unsealed_observations"] == counts[tenant]

    r = runner.invoke(app, ["runs", "--store", url, "--json"])
    assert r.exit_code == 0, r.output
    assert "acme-run" not in r.output and "globex-run" not in r.output
