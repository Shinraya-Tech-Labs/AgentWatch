from decimal import Decimal
from unittest.mock import patch

import pytest

from panoptis.cost.tracker import CostTracker
from panoptis.infrastructure.config import RouterConfig
from panoptis.infrastructure.router import ModelRouter


class DummyConfig(RouterConfig):
    def __init__(self, config_dict):
        self._config = config_dict

    def get_config(self):
        return self._config


@pytest.fixture
def mock_config():
    return DummyConfig(
        {
            "providers": {
                "primary": "gpt-4o",
                "fallback": ["claude-3-haiku-20240307", "gemini-1.5-flash"],
            }
        }
    )


@patch("tenacity.nap.sleep")
def test_provider_fallback(mock_sleep, mock_config):
    router = ModelRouter(mock_config)

    with patch("panoptis.infrastructure.router.litellm.completion") as mock_completion:
        # First provider fails 3 times (due to tenacity retries), then second provider succeeds
        mock_completion.side_effect = [
            Exception("API error"),
            Exception("API error"),
            Exception("API error"),
            {"choices": [{"message": {"content": "hello"}}]},
        ]

        res = router.completion([{"role": "user", "content": "hi"}])
        assert res == {"choices": [{"message": {"content": "hello"}}]}

        metrics = router.get_metrics()
        assert metrics["fallback_counts"] == 1
        assert metrics["providers"]["gpt-4o"]["failures"] == 1
        assert metrics["providers"]["claude-3-haiku-20240307"]["failures"] == 0


@patch("tenacity.nap.sleep")
def test_circuit_breaker_transitions(mock_sleep, mock_config):
    router = ModelRouter(mock_config)

    with patch("panoptis.infrastructure.router.litellm.completion") as mock_completion:
        mock_completion.side_effect = Exception("API error")

        import contextlib

        for _ in range(5):
            with contextlib.suppress(Exception):
                router.completion([{"role": "user", "content": "hi"}])

        metrics = router.get_metrics()
        assert metrics["providers"]["gpt-4o"]["state"] == "OPEN"


def test_budget_routing_downgrade(mock_config):
    tracker = CostTracker()
    tracker.configure_session("sess1", usd_budget=10.0)
    # Use 9.0 out of 10.0 -> > 80%
    budget = tracker.get_session("sess1")
    assert budget is not None
    budget.usd_used = Decimal("9.0")

    router = ModelRouter(mock_config, cost_tracker=tracker)

    with patch("panoptis.infrastructure.router.litellm.completion") as mock_completion:
        mock_completion.return_value = {"choices": []}
        router.completion([{"role": "user", "content": "hi"}], session_id="sess1")

        metrics = router.get_metrics()
        # The primary choice was downgraded to the first fallback
        assert metrics["routing_decisions"][-1]["model"] == "claude-3-haiku-20240307"


def test_budget_routing_exceeded(mock_config):
    tracker = CostTracker()
    tracker.configure_session("sess2", usd_budget=10.0)
    budget = tracker.get_session("sess2")
    assert budget is not None
    budget.usd_used = Decimal("11.0")
    budget.exceeded = True

    router = ModelRouter(mock_config, cost_tracker=tracker)

    with pytest.raises(RuntimeError, match="Budget exceeded"):
        router.completion([{"role": "user", "content": "hi"}], session_id="sess2")
