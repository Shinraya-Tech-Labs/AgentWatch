"""TO-011/TO-035: the support matrix is complete and every enforcement claim is tested."""

from __future__ import annotations

import pkgutil
from pathlib import Path

import panoptis.adapters
from panoptis.protect.support import MATRIX, POST, PRE

ROOT = Path(__file__).resolve().parents[2]


def test_every_adapter_module_is_classified():
    modules = {
        f"panoptis.adapters.{m.name}"
        for m in pkgutil.iter_modules(panoptis.adapters.__path__)
        if m.name != "base"
    }
    classified = {s.entry_point for s in MATRIX}
    missing = sorted(
        m for m in modules if not any(e == m or e.startswith(m + ".") for e in classified)
    )
    assert not missing, f"adapters without a support-matrix entry: {missing}"


def test_every_enforcement_claim_names_an_existing_test():
    for s in MATRIX:
        if s.stage == PRE:
            assert s.test, f"{s.integration} claims enforcement without a test"
            assert (ROOT / s.test).is_file(), f"{s.integration}: {s.test} does not exist"


def test_observation_only_entries_say_so():
    for s in MATRIX:
        if s.stage == POST:
            assert "observation" in s.limitations.lower() or "already run" in s.limitations, (
                s.integration
            )


def test_the_docs_matrix_matches_the_code():
    doc = (ROOT / "docs" / "ADAPTER_SUPPORT.md").read_text(encoding="utf-8")
    for s in MATRIX:
        assert s.integration in doc, f"docs/ADAPTER_SUPPORT.md is missing {s.integration!r}"
        row = next(line for line in doc.splitlines() if line.startswith(f"| {s.integration} |"))
        assert s.stage in row, f"docs and code disagree on {s.integration!r}"
