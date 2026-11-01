"""Per-session token and cost budget tracking."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal

from panoptis.core.schema import AgentEvent

# Maximum number of sessions held in memory at once.
# Keeps heap usage bounded on long-running deployments that process
# thousands of unique sessions per day.
_MAX_BUDGET_ENTRIES: int = 50_000

# Sessions with no activity for this many seconds are eligible for eviction.
_SESSION_TTL_SECONDS: float = 7_200  # 2 hours

# Minimum interval between full eviction scans (seconds).
# Prevents _maybe_evict() from performing an O(n) scan on every ingest
# when _budgets is at capacity and all sessions are still active.
_EVICTION_INTERVAL_SECONDS: float = 60.0


@dataclass
class SessionBudget:
    session_id: str
    token_budget: int
    usd_budget: Decimal
    tokens_used: int = 0
    usd_used: Decimal = Decimal("0.0")
    exceeded: bool = False
    warnings: list[str] = field(default_factory=list)
    last_active: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "token_budget": self.token_budget,
            "usd_budget": float(self.usd_budget),
            "tokens_used": self.tokens_used,
            "usd_used": float(round(self.usd_used, 6)),
            "exceeded": self.exceeded,
            "warnings": self.warnings,
        }


class CostTracker:
    def __init__(
        self,
        default_token_budget: int = 100_000,
        default_usd_budget: float | Decimal = 25.0,
        ttl_seconds: float | None = None,
    ):
        self._default_token_budget = default_token_budget
        self._default_usd_budget = Decimal(str(default_usd_budget))

        # Configure TTL
        if ttl_seconds is None:
            env_val = os.getenv("AGENTWATCH_SESSION_TTL_SECONDS") or os.getenv(
                "SESSION_TTL_SECONDS"
            )
            if env_val is not None:
                try:
                    self.ttl_seconds = float(env_val)
                except ValueError:
                    self.ttl_seconds = 3600.0
            else:
                self.ttl_seconds = 3600.0
        else:
            self.ttl_seconds = ttl_seconds

        self._budgets: dict[str, SessionBudget] = {}
        # Timestamp of the last full eviction scan. Initialised to 0 so the
        # first call to _maybe_evict() always runs a scan if warranted.
        self._last_eviction: float = 0.0
        self._last_cleanup: float = 0.0
        self._lock = threading.RLock()

    def configure_session(
        self,
        session_id: str,
        token_budget: int | None = None,
        usd_budget: float | Decimal | None = None,
    ) -> SessionBudget:
        with self._lock:
            self._cleanup_stale_sessions()
            budget = SessionBudget(
                session_id=session_id,
                token_budget=token_budget
                if token_budget is not None
                else self._default_token_budget,
                usd_budget=Decimal(str(usd_budget))
                if usd_budget is not None
                else self._default_usd_budget,
            )
            budget.last_accessed = time.monotonic()
            self._budgets[session_id] = budget
            return budget

    def ingest_event(self, event: AgentEvent) -> SessionBudget:
        with self._lock:
            self._cleanup_stale_sessions()
            budget = self._budgets.get(event.session_id) or self.configure_session(event.session_id)
            if event.token_usage:
                budget.tokens_used += event.token_usage.total_tokens
                cost = event.token_usage.estimated_cost_usd
                budget.usd_used += Decimal(str(cost or 0.0))
            budget.last_active = time.monotonic()
            budget.last_accessed = time.monotonic()
            self._evaluate(budget)
            self._maybe_evict()
            return budget

    def get_session(self, session_id: str) -> SessionBudget | None:
        with self._lock:
            self._cleanup_stale_sessions()
            budget = self._budgets.get(session_id)
            if budget is not None:
                budget.last_accessed = time.monotonic()
            return budget

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "tracked_sessions": len(self._budgets),
                "sessions_over_budget": sum(
                    1 for budget in self._budgets.values() if budget.exceeded
                ),
            }

    def _cleanup_stale_sessions(self, force: bool = False) -> None:
        """Remove stale sessions from the budgets map based on TTL.

        Protected by self._lock. If not force, cleanup only runs if at least
        60.0 seconds have elapsed since the last scan to avoid repeated O(n) overhead.
        """
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_cleanup < 60.0:
                return
            self._last_cleanup = now
            cutoff = now - self.ttl_seconds
            stale = [sid for sid, b in self._budgets.items() if b.last_accessed < cutoff]
            for sid in stale:
                del self._budgets[sid]

    def _maybe_evict(self) -> None:
        """Remove stale sessions when the store grows beyond its cap.

        Eviction runs only when two conditions are both true:
        1. The number of tracked sessions is at or above _MAX_BUDGET_ENTRIES.
        2. At least _EVICTION_INTERVAL_SECONDS have elapsed since the last scan.
        """
        with self._lock:
            if len(self._budgets) < _MAX_BUDGET_ENTRIES:
                return
            now = time.monotonic()
            if now - self._last_eviction < _EVICTION_INTERVAL_SECONDS:
                return
            self._last_eviction = now
            self._cleanup_stale_sessions(force=True)

    def _evaluate(self, budget: SessionBudget) -> None:
        budget.warnings = []
        budget.exceeded = False
        if budget.tokens_used >= budget.token_budget * 0.8:
            budget.warnings.append("token_budget_near_limit")
        if budget.usd_used >= budget.usd_budget * Decimal("0.8"):
            budget.warnings.append("usd_budget_near_limit")
        if budget.tokens_used > budget.token_budget or budget.usd_used > budget.usd_budget:
            budget.exceeded = True
            budget.warnings.append("budget_exceeded")
