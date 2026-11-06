"""Keeps scripts/mutation_check_trajectory.py honest: a mutant that no longer applies proves nothing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "mutation_check_trajectory", ROOT / "scripts" / "mutation_check_trajectory.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


@pytest.mark.parametrize("mutant", MODULE.MUTANTS, ids=lambda m: m.name.split()[0])
def test_every_mutant_still_applies_to_the_code(mutant):
    source = (ROOT / mutant.path).read_text(encoding="utf-8")
    assert mutant.old in source
    assert mutant.old != mutant.new


def test_mutant_ids_are_unique():
    ids = [m.name.split()[0] for m in MODULE.MUTANTS]
    assert len(ids) == len(set(ids))


def test_every_test_file_the_check_runs_exists():
    assert all((ROOT / path).is_file() for path in MODULE.TESTS)
