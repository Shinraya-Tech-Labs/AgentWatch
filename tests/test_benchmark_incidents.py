"""Guards for the published incident benchmark (README "Measured Behaviour").

The corpus is the contract: if someone swaps in easy negatives, lowers the attack
count, or lets the README table drift from the code, the README stops being
evidence and becomes a claim again. These asserts keep it evidence.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from benchmarks import incidents


@pytest.fixture(scope="module")
def results() -> incidents.Results:
    return incidents.run()


def test_corpus_is_balanced_and_well_formed() -> None:
    labels = Counter(case.label for case in incidents.CORPUS)
    assert labels == {"attack": 40, "benign": 40}
    assert len({case.case_id for case in incidents.CORPUS}) == len(incidents.CORPUS)
    assert all(case.command.strip() and case.category.strip() for case in incidents.CORPUS)


def test_recall_floor_holds(results: incidents.Results) -> None:
    assert results.recall >= incidents.MIN_RECALL


def test_readme_publishes_the_measured_numbers(results: incidents.Results) -> None:
    readme = (Path(incidents.__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    missing = [row for row in incidents.metric_rows(results) if row not in readme]
    assert not missing, f"README benchmark table is stale, run --write: {missing}"
