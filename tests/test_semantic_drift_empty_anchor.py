"""Regression tests for RSN-006 cross-session drift with empty summaries.

Issue #539: a blank/empty anchor summary embeds to a zero vector, and
cosine(v, zero) == 0, so every later session would read as maximal drift (1.0).
analyze() must anchor on a session that actually has signal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from panoptis.reasoning.semantic_drift import CrossSessionDrift


def _t(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def test_empty_anchor_does_not_force_false_divergence():
    d = CrossSessionDrift(drift_threshold=0.4)
    d.register("s1", "deploy the app", "", _t(1))  # anchor: empty summary
    summary = "I will build and deploy the application to production"
    d.register("s2", "deploy the app", summary, _t(2))
    d.register("s3", "deploy the app", summary, _t(3))
    alert = d.analyze("deploy the app")
    assert alert is not None
    assert alert.diverged is False
    assert alert.drift_magnitude < 0.4


def test_all_empty_summaries_return_no_signal():
    d = CrossSessionDrift(drift_threshold=0.4)
    d.register("s1", "ship it", "", _t(1))
    d.register("s2", "ship it", "   ", _t(2))  # whitespace-only -> zero vector
    assert d.analyze("ship it") is None


def test_single_session_with_signal_returns_none():
    d = CrossSessionDrift(drift_threshold=0.4)
    d.register("s1", "ship it", "", _t(1))  # empty
    d.register("s2", "ship it", "real summary text here", _t(2))  # only one w/ signal
    assert d.analyze("ship it") is None


def test_genuine_drift_still_detected():
    d = CrossSessionDrift(drift_threshold=0.2)
    d.register("s1", "optimize the pipeline", "tune the database query plan", _t(1))
    d.register("s2", "optimize the pipeline", "tune the database query plan", _t(2))
    d.register("s3", "optimize the pipeline", "compress images and shorten texts", _t(3))
    alert = d.analyze("optimize the pipeline")
    assert alert is not None
    assert alert.diverged is True
