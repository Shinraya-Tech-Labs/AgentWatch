"""Tests for Compounding Multi-Step Confidence Decay Tracker.

Verifies:
- Mathematical correctness of log-space compounding.
- Acceptance of individual confidence scores in [0.0, 1.0] and rejection of invalid values.
- Explicit handling of empty inputs, zero confidence, and 1.0 confidence.
- Configurable penalties for low-certainty tool calls and ambiguous extractions.
- CUMULATIVE_CONFIDENCE_COLLAPSE warning emission below the critical floor (default 0.40).
- Deep-sequence numerical stability and reset functionality.
- Support for Confidence and ComputationalEvent objects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from panoptis.causality.decay import (
    CUMULATIVE_CONFIDENCE_COLLAPSE,
    DEFAULT_AMBIGUOUS_EXTRACTION_PENALTY,
    DEFAULT_CRITICAL_FLOOR,
    DEFAULT_LOW_CERTAINTY_TOOL_PENALTY,
    ConfidenceCollapseWarning,
    ConfidenceDecayTracker,
    StepRecord,
    calculate_cumulative_confidence,
)


@dataclass
class DummyConfidence:
    value: float
    basis: str = "test"
    calibrated: bool = False


@dataclass
class DummyEvent:
    confidence_observation: DummyConfidence
    event_id: str = "evt_123"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Mathematical Correctness & Compounding Decay
# ─────────────────────────────────────────────────────────────────────────────


def test_ten_step_geometric_decay():
    """Verify 10 steps of 0.90 compound to approximately 0.348678."""
    tracker = ConfidenceDecayTracker()
    for _ in range(10):
        tracker.add_step(0.90)

    expected = 0.90**10
    assert math.isclose(tracker.cumulative_confidence, expected, rel_tol=1e-9)
    assert tracker.step_count == 10
    assert tracker.is_collapsed is True
    assert len(tracker.warnings) >= 1
    # 0.9^9 ~ 0.3874, so collapse happens on step index 9 (the 10th step)
    collapse_step = tracker.steps[9]
    assert collapse_step.warning is not None
    assert collapse_step.warning.code == CUMULATIVE_CONFIDENCE_COLLAPSE


def test_functional_calculate_cumulative_confidence():
    """Verify calculate_cumulative_confidence pure function."""
    scores = [0.95, 0.90, 0.85]
    expected = 0.95 * 0.90 * 0.85
    result = calculate_cumulative_confidence(scores)
    assert math.isclose(result, expected, rel_tol=1e-9)


def test_add_steps_batch():
    """Verify add_steps convenience batch method."""
    tracker = ConfidenceDecayTracker()
    records = tracker.add_steps([0.9, 0.8, 0.7])
    assert len(records) == 3
    assert isinstance(records[0], StepRecord)
    assert tracker.step_count == 3
    expected = 0.9 * 0.8 * 0.7
    assert math.isclose(tracker.cumulative_confidence, expected, rel_tol=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Boundary Values & Edge Cases
# ─────────────────────────────────────────────────────────────────────────────


def test_empty_tracker_state():
    """Empty tracker should default to cumulative confidence 1.0 with no warnings."""
    tracker = ConfidenceDecayTracker()
    assert tracker.step_count == 0
    assert tracker.cumulative_confidence == 1.0
    assert tracker.log_cumulative_confidence == 0.0
    assert tracker.is_collapsed is False
    assert tracker.warnings == []
    assert tracker.steps == []
    assert calculate_cumulative_confidence([]) == 1.0


def test_perfect_confidence_steps():
    """Steps with confidence 1.0 should keep cumulative confidence at 1.0."""
    tracker = ConfidenceDecayTracker()
    for _ in range(5):
        tracker.add_step(1.0)

    assert tracker.cumulative_confidence == 1.0
    assert tracker.log_cumulative_confidence == 0.0
    assert tracker.is_collapsed is False
    assert tracker.warnings == []


def test_zero_confidence_initial():
    """Zero confidence step immediately collapses cumulative confidence to 0.0."""
    tracker = ConfidenceDecayTracker()
    record = tracker.add_step(0.0)

    assert record.cumulative_confidence == 0.0
    assert record.log_cumulative_confidence == -float("inf")
    assert tracker.cumulative_confidence == 0.0
    assert tracker.log_cumulative_confidence == -float("inf")
    assert tracker.is_collapsed is True
    assert len(tracker.warnings) == 1
    assert tracker.warnings[0].code == CUMULATIVE_CONFIDENCE_COLLAPSE


def test_zero_confidence_in_middle():
    """A zero confidence step in the middle locks cumulative confidence at 0.0."""
    tracker = ConfidenceDecayTracker()
    tracker.add_step(0.95)
    assert tracker.cumulative_confidence == 0.95

    tracker.add_step(0.0)
    assert tracker.cumulative_confidence == 0.0
    assert tracker.is_collapsed is True

    # Subsequent positive step cannot recover zero cumulative confidence
    tracker.add_step(0.99)
    assert tracker.cumulative_confidence == 0.0
    assert tracker.log_cumulative_confidence == -float("inf")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Input Validation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("invalid_score", [-0.01, -1.0, 1.01, 2.5, -100.0])
def test_out_of_bounds_confidence_raises_value_error(invalid_score: float):
    """Confidence scores outside [0.0, 1.0] must raise ValueError."""
    tracker = ConfidenceDecayTracker()
    with pytest.raises(ValueError, match="within \\[0.0, 1.0\\]"):
        tracker.add_step(invalid_score)


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_confidence_raises_value_error(invalid_score: float):
    """NaN and infinite confidence values must raise ValueError."""
    tracker = ConfidenceDecayTracker()
    with pytest.raises(ValueError, match="finite"):
        tracker.add_step(invalid_score)


@pytest.mark.parametrize("invalid_type", ["0.9", None, True, False, [0.5], {"score": 0.8}])
def test_invalid_confidence_type_raises_type_error(invalid_type):
    """Non-numeric confidence types must raise TypeError."""
    tracker = ConfidenceDecayTracker()
    with pytest.raises(TypeError, match="numeric float"):
        tracker.add_step(invalid_type)


@pytest.mark.parametrize("invalid_floor", [-0.1, 1.1, float("nan"), "0.4"])
def test_invalid_critical_floor_raises(invalid_floor):
    """Invalid critical floor values must be rejected."""
    with pytest.raises((ValueError, TypeError)):
        ConfidenceDecayTracker(critical_floor=invalid_floor)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_penalty", [0.0, -0.5, 1.05, float("nan"), "0.85"])
def test_invalid_penalty_multiplier_raises(invalid_penalty):
    """Penalty multipliers outside (0.0, 1.0] must be rejected."""
    with pytest.raises((ValueError, TypeError)):
        ConfidenceDecayTracker(low_certainty_tool_penalty=invalid_penalty)  # type: ignore[arg-type]

    with pytest.raises((ValueError, TypeError)):
        ConfidenceDecayTracker(ambiguous_extraction_penalty=invalid_penalty)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Configurable Penalties
# ─────────────────────────────────────────────────────────────────────────────


def test_low_certainty_tool_penalty():
    """Low-certainty tool calls apply tool penalty multiplier."""
    tracker = ConfidenceDecayTracker(low_certainty_tool_penalty=0.85)
    record = tracker.add_step(1.0, is_low_certainty_tool=True)

    assert math.isclose(record.effective_confidence, 0.85, rel_tol=1e-9)
    assert math.isclose(tracker.cumulative_confidence, 0.85, rel_tol=1e-9)
    assert record.penalties_applied == (0.85,)


def test_ambiguous_extraction_penalty():
    """Ambiguous extractions apply ambiguous extraction penalty multiplier."""
    tracker = ConfidenceDecayTracker(ambiguous_extraction_penalty=0.80)
    record = tracker.add_step(1.0, is_ambiguous_extraction=True)

    assert math.isclose(record.effective_confidence, 0.80, rel_tol=1e-9)
    assert math.isclose(tracker.cumulative_confidence, 0.80, rel_tol=1e-9)
    assert record.penalties_applied == (0.80,)


def test_compounding_both_penalties():
    """Applying both penalties multiplies both factors."""
    tracker = ConfidenceDecayTracker(
        low_certainty_tool_penalty=0.85,
        ambiguous_extraction_penalty=0.80,
    )
    record = tracker.add_step(0.90, is_low_certainty_tool=True, is_ambiguous_extraction=True)

    expected_effective = 0.90 * 0.85 * 0.80  # 0.612
    assert math.isclose(record.effective_confidence, expected_effective, rel_tol=1e-9)
    assert math.isclose(tracker.cumulative_confidence, expected_effective, rel_tol=1e-9)
    assert record.penalties_applied == (0.85, 0.80)


def test_custom_penalties_list():
    """Custom penalty multipliers can be passed per-step."""
    tracker = ConfidenceDecayTracker()
    record = tracker.add_step(1.0, custom_penalties=[0.90, 0.50])

    assert math.isclose(record.effective_confidence, 0.45, rel_tol=1e-9)
    assert record.penalties_applied == (0.90, 0.50)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Critical Floor & Collapse Warnings
# ─────────────────────────────────────────────────────────────────────────────


def test_default_critical_floor_value():
    """Default critical floor must be 0.40."""
    tracker = ConfidenceDecayTracker()
    assert tracker.critical_floor == DEFAULT_CRITICAL_FLOOR
    assert tracker.critical_floor == 0.40
    assert tracker.ambiguous_extraction_penalty == DEFAULT_AMBIGUOUS_EXTRACTION_PENALTY


def test_warning_emission_below_critical_floor():
    """Emit CUMULATIVE_CONFIDENCE_COLLAPSE warning as soon as cumulative confidence < floor."""
    collapses = []

    def on_collapse_cb(warning: ConfidenceCollapseWarning):
        collapses.append(warning)

    tracker = ConfidenceDecayTracker(critical_floor=0.50, on_collapse=on_collapse_cb)

    r1 = tracker.add_step(0.80)
    assert r1.warning is None
    assert tracker.is_collapsed is False

    r2 = tracker.add_step(0.60)  # 0.80 * 0.60 = 0.48 < 0.50
    assert r2.warning is not None
    assert r2.warning.code == CUMULATIVE_CONFIDENCE_COLLAPSE
    assert r2.warning.step_index == 1
    assert math.isclose(r2.warning.cumulative_confidence, 0.48, rel_tol=1e-9)
    assert r2.warning.critical_floor == 0.50
    assert tracker.is_collapsed is True
    assert len(tracker.warnings) == 1
    assert len(collapses) == 1
    assert collapses[0].code == CUMULATIVE_CONFIDENCE_COLLAPSE


def test_custom_critical_floor():
    """Custom critical floor threshold is respected."""
    tracker = ConfidenceDecayTracker(critical_floor=0.75)
    r1 = tracker.add_step(0.80)
    assert r1.warning is None

    r2 = tracker.add_step(0.90)  # 0.80 * 0.90 = 0.72 < 0.75
    assert r2.warning is not None
    assert r2.warning.critical_floor == 0.75


# ─────────────────────────────────────────────────────────────────────────────
# 6. Numerical Stability in Long Sequences
# ─────────────────────────────────────────────────────────────────────────────


def test_deep_sequence_numerical_stability():
    """Verify log-space stability over 1000 steps without float overflow/underflow crash."""
    tracker = ConfidenceDecayTracker(critical_floor=0.01)
    for _ in range(1000):
        tracker.add_step(0.99)

    assert tracker.step_count == 1000
    expected_log = 1000 * math.log(0.99)
    assert math.isclose(tracker.log_cumulative_confidence, expected_log, rel_tol=1e-9)
    assert tracker.cumulative_confidence > 0.0
    assert tracker.cumulative_confidence < 0.01
    assert tracker.is_collapsed is True


def test_tiny_probabilities_underflow_protection():
    """Sequence with very small confidences remains mathematically sound."""
    tracker = ConfidenceDecayTracker(critical_floor=0.01)
    for _ in range(50):
        tracker.add_step(0.10)

    # 0.10^50 = 1e-50
    assert tracker.cumulative_confidence == pytest.approx(1e-50, rel=1e-5)
    assert tracker.log_cumulative_confidence == pytest.approx(50 * math.log(0.10), rel=1e-5)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Confidence & ComputationalEvent Object Support
# ─────────────────────────────────────────────────────────────────────────────


def test_support_confidence_object():
    """Tracker extracts score from Confidence-like objects."""
    tracker = ConfidenceDecayTracker()
    conf = DummyConfidence(0.85, basis="sensor")
    record = tracker.add_step(conf)

    assert math.isclose(record.raw_confidence, 0.85, rel_tol=1e-9)
    assert math.isclose(tracker.cumulative_confidence, 0.85, rel_tol=1e-9)


def test_support_computational_event_object():
    """Tracker extracts score from ComputationalEvent-like objects."""
    tracker = ConfidenceDecayTracker()
    event = DummyEvent(confidence_observation=DummyConfidence(0.75, basis="sensor"))
    record = tracker.add_step(event)

    assert math.isclose(record.raw_confidence, 0.75, rel_tol=1e-9)
    assert math.isclose(tracker.cumulative_confidence, 0.75, rel_tol=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Reset & Serialization
# ─────────────────────────────────────────────────────────────────────────────


def test_tracker_reset():
    """Reset restores the tracker to clean initial state."""
    tracker = ConfidenceDecayTracker()
    tracker.add_step(0.5)
    tracker.add_step(0.2)
    assert tracker.step_count == 2
    assert tracker.is_collapsed is True

    tracker.reset()
    assert tracker.step_count == 0
    assert tracker.cumulative_confidence == 1.0
    assert tracker.log_cumulative_confidence == 0.0
    assert tracker.is_collapsed is False
    assert tracker.warnings == []
    assert tracker.steps == []


def test_serialization_to_dict():
    """StepRecord and ConfidenceCollapseWarning serialize cleanly to dict."""
    tracker = ConfidenceDecayTracker(critical_floor=0.50)
    record = tracker.add_step(0.40, is_low_certainty_tool=True, step_id="step_1")

    d = record.to_dict()
    assert d["step_index"] == 0
    assert d["step_id"] == "step_1"
    assert d["raw_confidence"] == 0.40
    assert d["effective_confidence"] == 0.40 * DEFAULT_LOW_CERTAINTY_TOOL_PENALTY
    assert d["penalties_applied"] == [DEFAULT_LOW_CERTAINTY_TOOL_PENALTY]
    assert d["warning"] is not None
    assert d["warning"]["code"] == CUMULATIVE_CONFIDENCE_COLLAPSE
