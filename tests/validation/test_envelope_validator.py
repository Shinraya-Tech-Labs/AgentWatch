from __future__ import annotations

from datetime import UTC, datetime, timedelta

from panoptis.validation.envelope import validate_event_stream


def _evt(eid, ts, **extra):
    base = {
        "event_id": eid,
        "operation": "tool.call",
        "kind": "TOOL_INVOCATION",
        "tenant_id": "t",
        "interp_id": "i",
        "normalizer": "n@1",
        "time": {"start": ts.isoformat()},
    }
    base.update(extra)
    return base


def test_valid_stream():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [_evt("e1", t0), _evt("e2", t0 + timedelta(seconds=1))]
    report = validate_event_stream(events)
    assert report.valid
    assert report.total == 2


def test_missing_field():
    events = [{"event_id": "e1", "operation": "x", "kind": "K"}]
    report = validate_event_stream(events)
    assert not report.valid
    assert any(w.code == "MISSING_FIELD" for w in report.warnings)


def test_duplicate_id():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [_evt("e1", t0), _evt("e1", t0 + timedelta(seconds=1))]
    report = validate_event_stream(events)
    assert any(w.code == "DUPLICATE_EVENT_ID" for w in report.warnings)


def test_clock_skew():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    events = [_evt("e1", t0 + timedelta(seconds=5)), _evt("e2", t0)]
    report = validate_event_stream(events)
    assert any(w.code == "CLOCK_SKEW_DETECTED" for w in report.warnings)
