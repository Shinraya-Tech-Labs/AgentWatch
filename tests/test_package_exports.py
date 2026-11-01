"""Every wildcard-re-exporting package must declare a resolvable __all__."""

from __future__ import annotations

import importlib

import pytest

# Packages whose __init__ uses `from .sub import *`; each must pin its public
# surface with an explicit __all__ so `import *` is honest and knowable.
WILDCARD_PACKAGES = [
    "adapters",
    "alerting",
    "core",
    "cost",
    "governance",
    "memory",
    "orchestration",
    "plugins",
    "reasoning",
    "replay",
    "rollback",
    "scoring",
    "tracing",
]


@pytest.mark.parametrize("pkg", WILDCARD_PACKAGES)
def test_package_defines_all(pkg):
    module = importlib.import_module(f"panoptis.{pkg}")
    assert isinstance(getattr(module, "__all__", None), list)
    assert module.__all__, "__all__ must not be empty"


@pytest.mark.parametrize("pkg", WILDCARD_PACKAGES)
def test_all_names_resolve(pkg):
    module = importlib.import_module(f"panoptis.{pkg}")
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert not missing, f"panoptis.{pkg}.__all__ lists undefined names: {missing}"


@pytest.mark.parametrize("pkg", WILDCARD_PACKAGES)
def test_all_has_no_duplicates(pkg):
    names = importlib.import_module(f"panoptis.{pkg}").__all__
    assert len(names) == len(set(names))


@pytest.mark.parametrize("pkg", WILDCARD_PACKAGES)
def test_star_import_matches_all(pkg):
    module = importlib.import_module(f"panoptis.{pkg}")
    ns: dict[str, object] = {}
    exec(f"from panoptis.{pkg} import *", ns)  # noqa: S102
    exported = {k for k in ns if not k.startswith("__")}
    assert exported == set(module.__all__)
