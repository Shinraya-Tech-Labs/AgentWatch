"""Tests for concurrent entitlement-usage abuse detection (issue #463)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from panoptis.alerting.engine import AlertingConfig, AlertingEngine
from panoptis.security.abuse_detection import AbuseEvent, EntitlementUsageTracker

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_single_device_is_not_abuse():
    tracker = EntitlementUsageTracker()
    assert tracker.record("user", "device-a", now=_T0) is None
    assert tracker.active_devices("user", now=_T0) == {"device-a"}


def test_second_device_is_flagged():
    tracker = EntitlementUsageTracker()
    tracker.record("user", "device-a", now=_T0)
    event = tracker.record("user", "device-b", now=_T0 + timedelta(seconds=10))
    assert isinstance(event, AbuseEvent)
    assert event.distinct_devices == 2
    assert set(event.machine_ids) == {"device-a", "device-b"}


def test_same_device_set_is_not_realerted():
    tracker = EntitlementUsageTracker()
    tracker.record("user", "device-a", now=_T0)
    assert tracker.record("user", "device-b", now=_T0) is not None
    assert tracker.record("user", "device-a", now=_T0) is None


def test_old_sightings_age_out():
    tracker = EntitlementUsageTracker(window=timedelta(minutes=15))
    tracker.record("user", "device-a", now=_T0)
    later = _T0 + timedelta(hours=1)
    assert tracker.record("user", "device-b", now=later) is None
    assert tracker.active_devices("user", now=later) == {"device-b"}


def test_subjects_are_independent():
    tracker = EntitlementUsageTracker()
    assert tracker.record("alice", "device-a", now=_T0) is None
    assert tracker.record("bob", "device-b", now=_T0) is None


def test_invalid_max_devices():
    with pytest.raises(ValueError):
        EntitlementUsageTracker(max_devices=0)


def test_event_to_dict():
    tracker = EntitlementUsageTracker()
    tracker.record("user", "device-a", now=_T0)
    event = tracker.record("user", "device-b", now=_T0)
    assert event is not None
    assert event.to_dict()["distinct_devices"] == 2


def _event() -> AbuseEvent:
    return AbuseEvent(subject="user", machine_ids=("a", "b"), window_seconds=900, detected_at=_T0)


def test_alert_abuse_posts_to_slack(monkeypatch):
    class MockResponse:
        def raise_for_status(self):
            pass

    async def mock_post(self, url, content=None, headers=None):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    engine = AlertingEngine(
        AlertingConfig(
            slack_webhook_url="https://hooks.slack.com/services/TTEST1234/BTEST1234/abcdefghijklmn"
        )
    )
    sent = asyncio.run(engine.alert_abuse(_event()))
    assert sent["slack"] is True


def test_alert_abuse_noop_without_channels():
    engine = AlertingEngine(AlertingConfig())
    assert asyncio.run(engine.alert_abuse(_event())) == {
        "slack": False,
        "pagerduty": False,
    }
