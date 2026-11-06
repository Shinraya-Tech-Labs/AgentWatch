"""Regenerating the historical compilation keeps its notice and restores no retired wording."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETIRED = (
    "Run interactive terminal hypervisor simulation",
    "Measure hypervisor latency overhead",
    "100% operational in local development and production",
    "Canonical Project Record",
    "Intercept every tool call",
)


@pytest.fixture
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_complete_docs", ROOT / "scripts" / "build_complete_docs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_builder_finds_the_repository_wherever_it_is_cloned(builder):
    assert builder.REPO_ROOT == ROOT


def test_regenerating_keeps_the_notice_and_restores_no_retired_wording(
    builder, monkeypatch, tmp_path
):
    out = tmp_path / "doc.md"
    monkeypatch.setattr(builder, "OUTPUT_FILE", out)

    builder.main()

    text = out.read_text(encoding="utf-8")
    assert text.startswith(builder.HISTORICAL_NOTICE)
    assert [phrase for phrase in RETIRED if phrase in text] == []


def test_the_committed_file_carries_the_same_notice(builder):
    committed = (ROOT / "COMPLETE_PROJECT_DOCUMENTATION.md").read_text(encoding="utf-8")
    assert committed.startswith(builder.HISTORICAL_NOTICE)
    assert [phrase for phrase in RETIRED if phrase in committed] == []
