"""Tests for Slack and PagerDuty channel configuration validation."""

from __future__ import annotations

import pytest

from panoptis.alerting.channels import (
    ChannelConfigError,
    validate_channels,
    validate_pagerduty_key,
    validate_pagerduty_webhook,
    validate_slack_webhook,
)

# ---------------------------------------------------------------------------
# Slack webhook tests
# ---------------------------------------------------------------------------


def test_valid_slack_webhook():
    validate_slack_webhook("https://hooks.slack.com/services/TABC12345/BABC12345/abcdefghijklmnop")


def test_invalid_slack_webhook_wrong_domain():
    with pytest.raises(ChannelConfigError, match="Invalid Slack webhook URL"):
        validate_slack_webhook("https://evil.com/services/TABC/BABC/xyz")


def test_invalid_slack_webhook_missing_parts():
    with pytest.raises(ChannelConfigError, match="Invalid Slack webhook URL"):
        validate_slack_webhook("https://hooks.slack.com/services/TABC")


def test_invalid_slack_webhook_empty_string():
    with pytest.raises(ChannelConfigError, match="Invalid Slack webhook URL"):
        validate_slack_webhook("")


# ---------------------------------------------------------------------------
# PagerDuty routing key tests
# ---------------------------------------------------------------------------


def test_valid_pagerduty_key():
    validate_pagerduty_key("a" * 32)


def test_invalid_pagerduty_key_too_short():
    with pytest.raises(ChannelConfigError, match="Invalid PagerDuty routing key"):
        validate_pagerduty_key("abc123")


def test_invalid_pagerduty_key_non_hex():
    with pytest.raises(ChannelConfigError, match="Invalid PagerDuty routing key"):
        validate_pagerduty_key("z" * 32)


def test_invalid_pagerduty_key_empty_string():
    with pytest.raises(ChannelConfigError, match="Invalid PagerDuty routing key"):
        validate_pagerduty_key("")


# ---------------------------------------------------------------------------
# PagerDuty webhook tests
# ---------------------------------------------------------------------------


def test_valid_pagerduty_webhook():
    validate_pagerduty_webhook("https://events.pagerduty.com/v2/enqueue")


def test_invalid_pagerduty_webhook_wrong_domain():
    with pytest.raises(ChannelConfigError, match="Invalid PagerDuty webhook URL"):
        validate_pagerduty_webhook("https://evil.com/v2/enqueue")


def test_invalid_pagerduty_webhook_http():
    with pytest.raises(ChannelConfigError, match="Invalid PagerDuty webhook URL"):
        validate_pagerduty_webhook("http://events.pagerduty.com/v2/enqueue")


def test_invalid_pagerduty_webhook_empty_string():
    with pytest.raises(ChannelConfigError, match="Invalid PagerDuty webhook URL"):
        validate_pagerduty_webhook("")


# ---------------------------------------------------------------------------
# validate_channels (combined) tests
# ---------------------------------------------------------------------------


def test_validate_channels_all_none():
    validate_channels()


def test_validate_channels_valid_slack_only():
    validate_channels(
        slack_webhook_url="https://hooks.slack.com/services/TABC12345/BABC12345/abcdefghijklmnop"
    )


def test_validate_channels_invalid_slack_raises():
    with pytest.raises(ChannelConfigError):
        validate_channels(slack_webhook_url="not-a-valid-url")


def test_validate_channels_empty_slack_is_ignored():
    validate_channels(slack_webhook_url="")


def test_validate_channels_valid_pagerduty():
    validate_channels(
        pagerduty_webhook_url="https://events.pagerduty.com/v2/enqueue",
        pagerduty_routing_key="a" * 32,
    )


def test_validate_channels_invalid_pagerduty_key_raises():
    with pytest.raises(ChannelConfigError):
        validate_channels(
            pagerduty_webhook_url="https://events.pagerduty.com/v2/enqueue",
            pagerduty_routing_key="invalid-key",
        )


def test_validate_channels_missing_pagerduty_key_raises():
    with pytest.raises(ChannelConfigError, match="Incomplete PagerDuty"):
        validate_channels(
            pagerduty_webhook_url="https://events.pagerduty.com/v2/enqueue",
            pagerduty_routing_key=None,
        )


def test_validate_channels_missing_pagerduty_url_raises():
    with pytest.raises(ChannelConfigError, match="Incomplete PagerDuty"):
        validate_channels(
            pagerduty_webhook_url=None,
            pagerduty_routing_key="a" * 32,
        )


def test_validate_channels_empty_pagerduty_webhook_is_ignored():
    validate_channels(pagerduty_webhook_url="")


def test_validate_channels_empty_pagerduty_routing_key_is_ignored():
    validate_channels(pagerduty_routing_key="")
