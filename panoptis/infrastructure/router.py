import logging
import statistics
import time
from collections import deque
from enum import Enum, auto
from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from panoptis.core.schema import AgentEvent, EventType, TokenUsage
from panoptis.infrastructure.config import RouterConfig

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class ProviderHealth:
    def __init__(
        self, failure_threshold: int = 5, cooldown_sec: float = 30.0, timeout_sec: float = 10.0
    ):
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0

        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self.timeout_sec = timeout_sec

        self.latencies: deque[float] = deque(maxlen=50)

    def record_success(self, latency_ms: float) -> None:
        self.latencies.append(latency_ms)
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def can_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.cooldown_sec:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True


class ModelRouter:
    """
    Provider-agnostic Model Router with automatic fallback, circuit breakers,
    and cost-aware routing.
    """

    def __init__(self, config_provider: RouterConfig, cost_tracker: Any = None):
        self.config_provider = config_provider
        self.cost_tracker = cost_tracker
        self.providers: dict[str, ProviderHealth] = {}

        self.fallback_counts = 0
        self.routing_decisions: list[dict[str, Any]] = []

    def _get_health(self, provider: str) -> ProviderHealth:
        if provider not in self.providers:
            self.providers[provider] = ProviderHealth()
        return self.providers[provider]

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True
    )
    def _call_provider_with_retry(
        self, model: str, messages: list[dict[str, Any]], timeout: float, **kwargs
    ) -> Any:
        # We pass timeout directly to litellm to bound the retry attempt.
        return litellm.completion(model=model, messages=messages, timeout=timeout, **kwargs)

    def completion(self, messages: list[dict[str, Any]], **kwargs) -> Any:
        config = self.config_provider.get_config()

        primary = config.get("providers", {}).get("primary", "gpt-4o")
        fallbacks = config.get("providers", {}).get(
            "fallback", ["claude-3-haiku-20240307", "gemini-3.5-flash-lite"]
        )

        session_id = kwargs.pop("session_id", None)
        agent_id = kwargs.pop("agent_id", "router")

        if self.cost_tracker and session_id:
            budget = self.cost_tracker.get_session(session_id)
            if budget:
                if budget.exceeded:
                    raise RuntimeError("Budget exceeded, load shedding active.")

                # Downgrade if > 80% of budget used
                if (
                    budget.usd_budget > 0
                    and float(budget.usd_used) / float(budget.usd_budget) > 0.8
                ):
                    primary = fallbacks[0] if fallbacks else primary

        chain = [primary] + fallbacks

        for i, model in enumerate(chain):
            health = self._get_health(model)
            if not health.can_attempt():
                continue

            start_time = time.time()
            try:
                # bounded retry only on the individual provider
                res = self._call_provider_with_retry(
                    model, messages, timeout=health.timeout_sec, **kwargs
                )

                latency = (time.time() - start_time) * 1000
                health.record_success(latency)

                self.routing_decisions.append({"model": model, "reason": "success", "index": i})

                if self.cost_tracker and session_id:
                    usage = getattr(res, "usage", None)
                    if usage:
                        cost = 0.0
                        try:
                            cost = litellm.completion_cost(completion_response=res)
                        except Exception as e:
                            logger.debug("Failed to estimate completion cost: %s", e)

                        event = AgentEvent(
                            session_id=session_id,
                            agent_id=agent_id,
                            event_type=EventType.TOOL_CALL,
                            token_usage=TokenUsage(
                                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                                completion_tokens=getattr(usage, "completion_tokens", 0),
                                total_tokens=getattr(usage, "total_tokens", 0),
                                estimated_cost_usd=float(cost),
                            ),
                        )
                        self.cost_tracker.ingest_event(event)

                return res

            except Exception as e:
                health.record_failure()
                self.fallback_counts += 1
                logger.warning("Provider %s failed/timeout: %s. Triggering fallback.", model, e)

        raise RuntimeError("All configured providers failed or timed out.")

    def get_metrics(self) -> dict[str, Any]:
        """
        Expose metrics for observability.
        """
        metrics = {
            "fallback_counts": self.fallback_counts,
            "routing_decisions": self.routing_decisions[-100:],
            "providers": {},
        }
        for provider, health in self.providers.items():
            metrics["providers"][provider] = {
                "state": health.state.name,
                "failures": health.failures,
                "mean_latency_ms": statistics.mean(health.latencies) if health.latencies else 0.0,
            }
        return metrics
