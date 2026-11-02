"""Standard-library webhook alert dispatcher for Slack and Discord."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

_TIMEOUT_SECONDS = 3.0


def _post(url: str, payload: dict[str, Any]) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS):  # noqa: S310
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def send_slack_alert(webhook_url: str, event_data: dict[str, Any]) -> bool:
    """Send a Slack Block Kit alert."""
    text = event_data.get("message", "Panopti alert")
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Panopti Alert*\n{text}"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Rule: {event_data.get('rule', 'n/a')}"},
                    {"type": "mrkdwn", "text": f"Time: {event_data.get('timestamp', 'n/a')}"},
                ],
            },
        ]
    }
    return _post(webhook_url, payload)


def send_discord_alert(webhook_url: str, event_data: dict[str, Any]) -> bool:
    """Send a Discord embed alert with a red danger border."""
    payload = {
        "embeds": [
            {
                "title": "Panopti Alert",
                "description": event_data.get("message", "Panopti alert"),
                "color": 0xFF0000,
                "fields": [
                    {"name": "Rule", "value": str(event_data.get("rule", "n/a")), "inline": True},
                    {
                        "name": "Timestamp",
                        "value": str(event_data.get("timestamp", "n/a")),
                        "inline": True,
                    },
                ],
            }
        ]
    }
    return _post(webhook_url, payload)
