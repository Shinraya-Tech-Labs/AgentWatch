"""Alerting helpers for Panopti."""

from panopti.alerting.webhooks import send_discord_alert, send_slack_alert

__all__ = ["send_discord_alert", "send_slack_alert"]
