"""Alert routing for Slack and PagerDuty webhooks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from panoptis.alerting.channels import validate_channels
from panoptis.core.schema import AgentEvent, RiskLevel
from panoptis.security.abuse_detection import AbuseEvent

logger = logging.getLogger(__name__)


@dataclass
class AlertingConfig:
    slack_webhook_url: str | None = None
    pagerduty_webhook_url: str | None = None
    pagerduty_routing_key: str | None = None
    webhook_signing_secret: str | None = None
    min_risk_for_pagerduty: RiskLevel = RiskLevel.HIGH


class AlertingEngine:
    def __init__(self, config: AlertingConfig | None = None):
        self._config = config or AlertingConfig()
        validate_channels(
            slack_webhook_url=self._config.slack_webhook_url,
            pagerduty_webhook_url=self._config.pagerduty_webhook_url,
            pagerduty_routing_key=self._config.pagerduty_routing_key,
        )

    async def alert_event(self, event: AgentEvent) -> dict[str, bool]:
        payload = self._build_payload(event)
        sent = {"slack": False, "pagerduty": False}
        if self._config.slack_webhook_url:
            sent["slack"] = await self._post(self._config.slack_webhook_url, payload["slack"])
        risk_level = event.safety.risk_level if event.safety else RiskLevel.SAFE
        if self._config.pagerduty_webhook_url and self._should_page(risk_level):
            sent["pagerduty"] = await self._post(
                self._config.pagerduty_webhook_url,
                payload["pagerduty"],
            )
        return sent

    async def alert_abuse(self, event: AbuseEvent) -> dict[str, bool]:
        """Surface an entitlement-abuse event to the configured admin channels."""
        payload = self._build_abuse_payload(event)
        sent = {"slack": False, "pagerduty": False}
        if self._config.slack_webhook_url:
            sent["slack"] = await self._post(self._config.slack_webhook_url, payload["slack"])
        if self._config.pagerduty_webhook_url:
            sent["pagerduty"] = await self._post(
                self._config.pagerduty_webhook_url, payload["pagerduty"]
            )
        return sent

    async def send_test_alert(
        self, summary: str = "PANOPTIS Alerting Health Check"
    ) -> dict[str, bool]:
        """Send a diagnostic test alert across configured channels to verify integration."""
        sent = {"slack": False, "pagerduty": False}
        if self._config.slack_webhook_url:
            slack_payload = {
                "text": f"PANOPTIS Test Alert: {summary}",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "🔔 PANOPTIS Channel Test"},
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Status:* Channel operational\n*Message:* {summary}",
                        },
                    },
                ],
            }
            sent["slack"] = await self._post(self._config.slack_webhook_url, slack_payload)
        if self._config.pagerduty_webhook_url:
            pd_payload = {
                "routing_key": self._config.pagerduty_routing_key or "",
                "event_action": "trigger",
                "payload": {
                    "summary": f"[PANOPTIS Diagnostic] {summary}",
                    "severity": "info",
                    "source": "panoptis-doctor",
                },
            }
            sent["pagerduty"] = await self._post(self._config.pagerduty_webhook_url, pd_payload)
        return sent

    def _build_abuse_payload(self, event: AbuseEvent) -> dict[str, dict[str, Any]]:
        summary = (
            f"PANOPTIS entitlement abuse: {event.subject} used on "
            f"{event.distinct_devices} devices within {event.window_seconds}s"
        )
        devices = ", ".join(f"`{mid}`" for mid in event.machine_ids)
        return {
            "slack": {
                "text": summary,
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*{summary}*"}},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Devices:* {devices}"},
                    },
                ],
            },
            "pagerduty": {
                "routing_key": self._config.pagerduty_routing_key or "",
                "event_action": "trigger",
                "payload": {
                    "summary": summary,
                    "source": "panoptis",
                    "severity": "error",
                    "custom_details": {
                        "subject": event.subject,
                        "machine_ids": list(event.machine_ids),
                        "distinct_devices": event.distinct_devices,
                    },
                },
            },
        }

    def _should_page(self, risk_level: RiskLevel) -> bool:
        order = [
            RiskLevel.SAFE,
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ]
        return order.index(risk_level) >= order.index(self._config.min_risk_for_pagerduty)

    def _build_payload(self, event: AgentEvent) -> dict[str, dict[str, Any]]:
        tool = event.tool_call.tool_name if event.tool_call else event.event_type.value
        risk = event.safety.risk_level.value if event.safety else "safe"
        reasons = event.safety.reasons if event.safety else []
        summary = f"PANOPTIS {event.status.value}: {tool} ({risk})"
        return {
            "slack": {
                "text": summary,
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*{summary}*"}},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "\n".join(
                                [
                                    f"*Session:* `{event.session_id}`",
                                    f"*Agent:* `{event.agent_id}`",
                                    f"*Event:* `{event.event_type.value}`",
                                    f"*Reasons:* {', '.join(reasons) or 'n/a'}",
                                ]
                            ),
                        },
                    },
                ],
            },
            "pagerduty": {
                "routing_key": self._config.pagerduty_routing_key or "",
                "event_action": "trigger",
                "payload": {
                    "summary": summary,
                    "source": "panoptis",
                    "severity": "critical" if risk == "critical" else "error",
                    "custom_details": {
                        "session_id": event.session_id,
                        "agent_id": event.agent_id,
                        "event_type": event.event_type.value,
                        "reasons": reasons,
                    },
                },
            },
        }

    async def _post(self, url: str, payload: dict[str, Any], max_retries: int = 3) -> bool:
        import asyncio
        import json

        from panoptis.security.webhook_signing import generate_webhook_signature

        try:
            payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
            headers = {"Content-Type": "application/json"}
            if self._config.webhook_signing_secret:
                headers["X-AgentWatch-Signature"] = generate_webhook_signature(
                    payload_bytes, self._config.webhook_signing_secret
                )
        except Exception:
            logger.warning("Webhook signing/serialization failed", exc_info=True)
            return False

        delay = 0.5
        async with httpx.AsyncClient(timeout=5.0) as client:
            for attempt in range(max_retries):
                try:
                    response = await client.post(url, content=payload_bytes, headers=headers)
                    response.raise_for_status()
                    return True
                except Exception as exc:
                    logger.warning(
                        "Alert delivery failed for %s (attempt %d/%d): %s",
                        url,
                        attempt + 1,
                        max_retries,
                        exc,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay)
                        delay *= 2
        return False
