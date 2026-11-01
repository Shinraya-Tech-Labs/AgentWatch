"""Validation for Slack and PagerDuty notification channel configurations."""

from __future__ import annotations

import logging
import re
from typing import cast

logger = logging.getLogger(__name__)

# Slack webhook URLs must match: https://hooks.slack.com/services/T.../B.../...
_SLACK_WEBHOOK_RE = re.compile(
    r"^https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+$"
)

# PagerDuty routing keys are 32-character hex strings
_PAGERDUTY_KEY_RE = re.compile(r"^[a-f0-9]{32}$")

# PagerDuty webhook URLs must start with https://events.pagerduty.com/
_PAGERDUTY_WEBHOOK_RE = re.compile(r"^https://events\.pagerduty\.com/.*$")


class ChannelConfigError(ValueError):
    """Raised when a notification channel configuration is invalid."""


def validate_slack_webhook(url: str) -> None:
    """Validate a Slack webhook URL format.

    Args:
        url: The Slack webhook URL to validate.

    Raises:
        ChannelConfigError: If the URL does not match the expected Slack format.
    """
    if not url or not _SLACK_WEBHOOK_RE.match(url):
        raise ChannelConfigError(
            "Invalid Slack webhook URL. "
            "Expected format: https://hooks.slack.com/services/TXXXXXXXX/BXXXXXXXX/XXXXXXXX"
        )
    logger.debug("Slack webhook URL validated successfully.")


def validate_pagerduty_key(key: str) -> None:
    """Validate a PagerDuty routing key format.

    Args:
        key: The PagerDuty routing key to validate.

    Raises:
        ChannelConfigError: If the key does not match the expected 32-char hex format.
    """
    if not key or not _PAGERDUTY_KEY_RE.match(key):
        raise ChannelConfigError(
            "Invalid PagerDuty routing key. Expected a 32-character hexadecimal string."
        )
    logger.debug("PagerDuty routing key validated successfully.")


def validate_pagerduty_webhook(url: str) -> None:
    """Validate a PagerDuty webhook URL format.

    Args:
        url: The PagerDuty webhook URL to validate.

    Raises:
        ChannelConfigError: If the URL does not match the expected PagerDuty format.
    """
    if not url or not _PAGERDUTY_WEBHOOK_RE.match(url):
        raise ChannelConfigError(
            "Invalid PagerDuty webhook URL. Expected format: https://events.pagerduty.com/..."
        )
    logger.debug("PagerDuty webhook URL validated successfully.")


def validate_channels(
    slack_webhook_url: str | None = None,
    pagerduty_webhook_url: str | None = None,
    pagerduty_routing_key: str | None = None,
) -> None:
    """Validate all provided notification channel configurations.

    Call this at startup to catch invalid configurations before any alerts fire.
    Empty strings are treated as unset/disabled.

    If either pagerduty_webhook_url or pagerduty_routing_key is provided,
    both must be present and valid (incomplete PagerDuty config fails fast).

    Args:
        slack_webhook_url: Optional Slack webhook URL.
        pagerduty_webhook_url: Optional PagerDuty webhook URL.
        pagerduty_routing_key: Optional PagerDuty routing key.

    Raises:
        ChannelConfigError: If any provided value is invalid or incomplete.
    """
    if slack_webhook_url == "":
        slack_webhook_url = None
    if pagerduty_webhook_url == "":
        pagerduty_webhook_url = None
    if pagerduty_routing_key == "":
        pagerduty_routing_key = None

    if slack_webhook_url is not None:
        validate_slack_webhook(slack_webhook_url)

    # Incomplete PagerDuty config should fail fast
    pd_url_present = pagerduty_webhook_url is not None
    pd_key_present = pagerduty_routing_key is not None

    if pd_url_present or pd_key_present:
        if not pd_url_present:
            raise ChannelConfigError(
                "Incomplete PagerDuty configuration: "
                "pagerduty_webhook_url is required when pagerduty_routing_key is set."
            )
        if not pd_key_present:
            raise ChannelConfigError(
                "Incomplete PagerDuty configuration: "
                "pagerduty_routing_key is required when pagerduty_webhook_url is set."
            )
        # Type narrowing: prior checks at lines 100-113 guarantee non-None here.
        validate_pagerduty_webhook(cast(str, pagerduty_webhook_url))
        validate_pagerduty_key(cast(str, pagerduty_routing_key))
