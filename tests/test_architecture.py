"""TO-034: every module is reachable, and every module without a runtime entry point is
classified in docs/ARCHITECTURE_MAP.md (so nothing becomes silently dead or unsupported)."""

from __future__ import annotations

import importlib
import importlib.util
import re
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("module_map", ROOT / "scripts" / "module_map.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)


@pytest.fixture(scope="module")
def reachability():
    mods = mm.modules()
    graph = {m: mm.imports_of(m, p, mods) for m, p in mods.items()}
    runtime = mm.reach([e for e in mm.ENTRY_POINTS if e in mods], graph)
    tested = mm.reach(sorted(mm.test_imports(mods)), graph)
    return mods, runtime, tested


def test_no_module_is_unreachable(reachability):
    mods, runtime, tested = reachability
    orphans = sorted(set(mods) - runtime - tested)
    assert not orphans, (
        f"modules no entry point or test reaches (wire, test or remove them): {orphans}"
    )


def test_every_module_without_an_entry_point_is_classified(reachability):
    mods, runtime, tested = reachability
    doc = (ROOT / "docs" / "ARCHITECTURE_MAP.md").read_text(encoding="utf-8")
    classified = set(re.findall(r"^\| `([a-z0-9_.]+)` \|", doc, re.M))
    unwired = sorted(set(mods) - runtime)
    missing = [m for m in unwired if m not in classified]
    assert not missing, f"classify these in docs/ARCHITECTURE_MAP.md: {missing}"
    stale = sorted(m for m in classified if m in runtime or m not in mods)
    assert not stale, f"docs/ARCHITECTURE_MAP.md lists modules that are wired or gone: {stale}"


@pytest.mark.parametrize(
    "module,target",
    [("panoptis.cost.caching", "SemanticCache"), ("panoptis.cost.router", "ModelRouter")],
)
def test_deprecated_shims_warn_and_still_work(module, target):
    import sys

    sys.modules.pop(module, None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mod = importlib.import_module(module)
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
    assert hasattr(mod, target)


def test_removed_dead_modules_stay_removed():
    for name in (
        "panoptis.security.encryption",
        "panoptis.security.key_storage",
        "panoptis.telemetry.ingestion",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
