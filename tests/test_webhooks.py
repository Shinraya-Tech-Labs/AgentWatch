from __future__ import annotations

import json
from unittest.mock import patch

from panopti.alerting.webhooks import send_discord_alert, send_slack_alert


def test_slack_payload():
    with patch("panopti.alerting.webhooks.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value = None
        ok = send_slack_alert(
            "https://example.com/slack", {"message": "boom", "rule": "r1", "timestamp": "t1"}
        )
        assert ok is True
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        assert "blocks" in payload


def test_discord_payload():
    with patch("panopti.alerting.webhooks.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__.return_value = None
        ok = send_discord_alert(
            "https://example.com/discord", {"message": "boom", "rule": "r1", "timestamp": "t1"}
        )
        assert ok is True
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        assert payload["embeds"][0]["color"] == 0xFF0000
