"""Coverage floors for security-sensitive modules (on top of the global --cov-fail-under).

    pytest --cov=panoptis --cov-report=json && python scripts/coverage_gate.py [coverage.json]

The floors sit a little below the measured coverage, so a change that drops tests for an
enforcement path fails CI. Modules exercised mainly in subprocesses (the MCP gateway's
stdio/HTTP transports, the Claude Code hook bootstrap) are covered by end-to-end tests
that coverage does not see; their floors reflect only the in-process part.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FLOORS: dict[str, float] = {
    # enforcement boundary and pipeline
    "panoptis/protect/enforce.py": 85,
    "panoptis/protect/pipeline.py": 85,
    "panoptis/protect/model.py": 90,
    "panoptis/protect/threats.py": 90,
    "panoptis/protect/arguments.py": 80,
    "panoptis/protect/output.py": 90,
    "panoptis/protect/rbac.py": 85,
    "panoptis/protect/approvals.py": 90,
    "panoptis/protect/loops.py": 90,
    "panoptis/protect/intent.py": 85,
    "panoptis/protect/control.py": 88,
    "panoptis/protect/ledger.py": 85,
    "panoptis/protect/claude_hook.py": 90,
    "panoptis/protect/discovery.py": 90,
    "panoptis/protect/integrations.py": 75,
    "panoptis/protect/mcp_gateway.py": 45,  # transports run in subprocesses in the E2E tests
    # tenant authentication and the protection API
    "panoptis/api/auth.py": 90,
    "panoptis/api/tenancy.py": 95,
    "panoptis/api/tenant_auth.py": 95,
    "panoptis/api/tenant_store_sql.py": 85,
    "panoptis/api/protect_api.py": 85,
    "panoptis/api/protect_approvals.py": 90,
    "panoptis/api/protect_control.py": 90,
    "panoptis/api/protect_rbac.py": 90,
    # evidence integrity
    "panoptis/evidence/segments.py": 95,
    "panoptis/evidence/redaction.py": 90,
    "panoptis/evidence/verifier.py": 70,  # also run as a standalone process in the tests
    # legacy gate and adapters
    "panoptis/core/safety.py": 85,
    "panoptis/core/watcher.py": 85,
    "panoptis/adapters/generic.py": 85,
}


def main(path: str = "coverage.json") -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = {
        k.replace("\\", "/"): v["summary"]["percent_covered"] for k, v in data["files"].items()
    }
    failures = []
    for module, floor in FLOORS.items():
        got = next((pct for f, pct in files.items() if f.endswith(module)), None)
        if got is None:
            failures.append(f"{module}: not measured")
        elif got + 1e-9 < floor:
            failures.append(f"{module}: {got:.1f}% < {floor}%")
        else:
            print(f"ok   {module}: {got:.1f}% (floor {floor}%)")
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
