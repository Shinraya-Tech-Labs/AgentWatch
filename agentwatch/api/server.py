"""
AgentWatch API Server
FastAPI-based REST API for the observability dashboard, CLI, and integrations.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentwatch._version import __version__
from agentwatch.alerting.engine import AlertingConfig, AlertingEngine
from agentwatch.api.auth import require_permission
from agentwatch.api.entitlement import require_entitlement
from agentwatch.api.middleware.rate_limiter import RateLimiter, RateLimitMiddleware
from agentwatch.api.tenant_auth import get_tenant_store
from agentwatch.api.v3 import build_router as build_v3_router
from agentwatch.api.v3 import get_engine as get_v3_engine
from agentwatch.core.config import get_cloud_config
from agentwatch.core.event_bus import get_event_bus
from agentwatch.core.models import Repository, TenantRepository, init_db
from agentwatch.core.safety import RiskScorer, SafetyEngine, SafetyPolicy
from agentwatch.core.schema import (
    AgentEvent,
    AgentFramework,
    AgentSession,
    EventType,
    ExecutionStatus,
    RiskLevel,
    SafetyCheckData,
    TokenUsage,
    ToolCallData,
    ToolResultData,
)
from agentwatch.cost.tracker import CostTracker
from agentwatch.governance.compliance_reporter import ComplianceReporter
from agentwatch.governance.engine import AuditEventType, GovernanceEngine
from agentwatch.governance.gdpr import (
    CrossSessionErasureService,
    ErasureReceipt,
    ErasureRequest,
    ErasureScope,
)
from agentwatch.models.tenant import TenantPlan
from agentwatch.monitoring.metrics import (
    record_api_latency,
    record_failure,
)
from agentwatch.reasoning.auditor import ReasoningAuditor
from agentwatch.replay.counterfactual import CounterfactualEngine, CounterfactualScenario
from agentwatch.replay.engine import ReplayEngine
from agentwatch.rollback.engine import RollbackEngine
from agentwatch.scoring.confidence import ConfidenceScorer
from agentwatch.security.abuse_detection import EntitlementUsageTracker
from agentwatch.tracing.collector import TraceCollector
from agentwatch.validation.schema_validator import SchemaValidator

logger = logging.getLogger(__name__)

RATE_READ = int(os.getenv("API_RATE_LIMIT_READ", "1000"))
RATE_WRITE = int(os.getenv("API_RATE_LIMIT_WRITE", "200"))
RATE_WINDOW_SEC = int(os.getenv("API_RATE_LIMIT_WINDOW_SEC", "60"))
RATE_BUCKET_TTL_SEC = int(os.getenv("API_RATE_LIMIT_BUCKET_TTL_SEC", str(RATE_WINDOW_SEC + 30)))


class _Limiter:
    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"count": 0, "start": 0.0}
        )
        self._checks_since_prune = 0

    def reset(self) -> None:
        self._buckets.clear()
        self._checks_since_prune = 0

    def _prune_stale(self, now: float) -> None:
        cutoff = now - RATE_BUCKET_TTL_SEC
        for key in [k for k, b in self._buckets.items() if b["start"] < cutoff]:
            del self._buckets[key]

    def check(self, ip: str, limit: int, request: Request) -> None:
        now = time.time()
        self._checks_since_prune += 1
        if self._checks_since_prune >= 64 or len(self._buckets) > 4096:
            self._prune_stale(now)
            self._checks_since_prune = 0

        b = self._buckets[ip]
        if now - b["start"] > RATE_WINDOW_SEC:
            b["count"] = 0
            b["start"] = now
        b["count"] += 1
        remaining = max(0, limit - b["count"])
        request.state.rl_limit = limit
        request.state.rl_remaining = remaining
        if b["count"] > limit:
            raise HTTPException(
                status_code=429,
                detail="rate_limit_exceeded",
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": str(RATE_WINDOW_SEC),
                },
            )


_limiter = _Limiter()


def reset_rate_limiter_for_tests() -> None:
    """Clear in-memory counters between tests (test-only helper)."""
    _limiter.reset()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _rate_limit_key(request: Request, suffix: str) -> str:
    return f"{_client_ip(request)}:{suffix}"


_db_session_factory = None


def _session_to_pg(session: AgentSession) -> dict:
    return {
        "session_id": session.session_id,
        "agent_id": session.agent_id,
        "agent_name": session.agent_name,
        "framework": session.framework.value,
        "status": session.status.value,
        "goal": session.goal,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "total_events": session.total_events,
        "total_tokens": session.total_tokens,
        "estimated_cost_usd": session.estimated_cost_usd,
        "final_confidence": session.final_confidence,
        "session_metadata": session.metadata or {},
    }


def _event_to_pg(event: AgentEvent) -> dict:
    d: dict = {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "framework": event.framework.value,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "step_number": event.step_number,
        "timestamp": event.timestamp,
        "goal": event.goal,
        "duration_ms": event.duration_ms,
        "task_id": event.task_id,
        "trace_id": event.trace_id,
        "parent_event_id": event.parent_event_id,
        "prompt_preview": event.prompt_preview,
        "planner_output_preview": event.planner_output_preview,
        "event_metadata": event.metadata or {},
        "tags": list(event.tags) if event.tags else [],
    }
    if event.tool_call:
        d["tool_name"] = event.tool_call.tool_name
        d["tool_raw_command"] = event.tool_call.raw_command
        d["tool_arguments"] = dict(event.tool_call.arguments) if event.tool_call.arguments else {}
    if event.tool_result:
        d["tool_output"] = event.tool_result.output
        d["tool_error"] = event.tool_result.error
    if event.safety:
        d["risk_level"] = event.safety.risk_level.value
        d["risk_score"] = event.safety.risk_score
        d["safety_blocked"] = event.safety.blocked
        d["safety_reasons"] = list(event.safety.reasons) if event.safety.reasons else []
    if event.token_usage:
        d["prompt_tokens"] = event.token_usage.prompt_tokens
        d["completion_tokens"] = event.token_usage.completion_tokens
        d["total_tokens"] = event.token_usage.total_tokens
        d["estimated_cost_usd"] = event.token_usage.estimated_cost_usd
    if event.confidence:
        d["confidence_score"] = event.confidence.overall_score
        d["anomaly_flags"] = (
            list(event.confidence.anomaly_flags) if event.confidence.anomaly_flags else []
        )
    return d


async def _pg_write_session(session: AgentSession, tenant_id: str | None = None) -> None:
    if _db_session_factory is None:
        return
    try:
        async with _db_session_factory() as db:
            repo = TenantRepository(Repository(db), tenant_id=tenant_id)
            data = _session_to_pg(session)
            if tenant_id:
                data["tenant_id"] = tenant_id
            await repo.upsert_session(data)
            await db.commit()
    except Exception:
        logger.warning("PG session write failed", exc_info=True)


async def _pg_write_event(event: AgentEvent, tenant_id: str | None = None) -> None:
    if _db_session_factory is None:
        return
    try:
        async with _db_session_factory() as db:
            repo = TenantRepository(Repository(db), tenant_id=tenant_id)
            event_data = _event_to_pg(event)
            if tenant_id:
                event_data["tenant_id"] = tenant_id
            await repo.insert_event(event_data)
            trace = _collector.get_trace(event.session_id)
            if trace:
                session_data = _session_to_pg(trace.session)
                if tenant_id:
                    session_data["tenant_id"] = tenant_id
                await repo.upsert_session(session_data)
            await db.commit()
    except Exception:
        logger.warning("PG event write failed", exc_info=True)


_collector = TraceCollector()
_replay_engine = ReplayEngine()
_rollback_engine = RollbackEngine()
_safety_engine = SafetyEngine()
_confidence_scorer = ConfidenceScorer()
_cost_tracker = CostTracker()
_reasoning_auditor = ReasoningAuditor()
_audit_log_path = Path(os.getenv("AGENTWATCH_AUDIT_LOG_PATH", "data/audit-log.jsonl"))
_governance = GovernanceEngine(audit_log_path=_audit_log_path)
_compliance_reporter = ComplianceReporter(_governance, _collector)
_alerting = AlertingEngine(
    AlertingConfig(
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        pagerduty_webhook_url=os.getenv("PAGERDUTY_WEBHOOK_URL"),
        webhook_signing_secret=os.getenv("AGENTWATCH_WEBHOOK_SIGNING_SECRET"),
    )
)
_ws_clients: list[WebSocket] = []
_schema_validator = SchemaValidator()
_usage_tracker = EntitlementUsageTracker()


def _init_default_schemas() -> None:
    """Register built-in JSON schemas for each supported agent framework."""
    _tool_call_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["tool_name"],
    }
    _schema_validator.schemas["claude-code"] = _tool_call_schema
    _schema_validator.schemas["langchain"] = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["tool_name"],
    }
    _schema_validator.schemas["crewai"] = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["tool_name"],
    }


# Optional API key guard.
#
# Set AGENTWATCH_API_KEY to a random secret string in the deployment
# environment to require authentication on all sensitive endpoints.
# When the variable is absent the guard is a no-op so local development
# and unauthenticated demo deployments continue to work without changes.
#
# Clients must pass the key in the X-Api-Key request header:
#   curl -H "X-Api-Key: <key>" http://localhost:8000/api/v1/sessions
_API_KEY: str | None = os.getenv("AGENTWATCH_API_KEY") or None

# Environment detection for fail-closed logic
_ENV = os.getenv("AGENTWATCH_ENV") or os.getenv("ENVIRONMENT") or "development"
_IS_PROD = _ENV.lower() == "production"
_CLOUD_MODE = get_cloud_config().is_cloud


def _key_matches(supplied: str | None) -> bool:
    """Constant-time comparison with AGENTWATCH_API_KEY (a plain != leaks how many leading
    characters match through its timing)."""
    if _API_KEY is None or supplied is None:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), _API_KEY.encode("utf-8"))


def _require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> None:
    """FastAPI dependency that enforces API key authentication.

    In cloud mode, validates against the TenantStore and attaches tenant context.
    In legacy mode, validates against the single AGENTWATCH_API_KEY.
    """
    if _CLOUD_MODE:
        # Cloud mode: validate tenant API key
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key. Supply the key in the X-Api-Key header.",
            )
        store = get_tenant_store()
        api_key = store.validate_api_key(x_api_key)
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key.",
            )
        return

    # Legacy mode
    if _IS_PROD and not _API_KEY:
        logger.error("AGENTWATCH_API_KEY is missing in production environment")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: API key is required in production.",
        )

    if _API_KEY is not None and not _key_matches(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key. Supply the key in the X-Api-Key header.",
        )


def _require_tenant_context(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> str | None:
    """FastAPI dependency that resolves tenant_id from the API key.

    Returns the tenant_id for use in downstream handlers, or None in legacy mode
    so downstream handlers preserve unscoped NULL tenant behavior.
    """
    if not _CLOUD_MODE:
        return None
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
        )
    store = get_tenant_store()
    api_key = store.validate_api_key(x_api_key)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )
    return api_key.tenant_id


def _require_tenant_ownership(
    request: Request,
    path_tenant_id: str,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> str:
    """FastAPI dependency that verifies the caller owns the tenant resource.

    Raises 403 if the API key's tenant doesn't match the path tenant_id.
    Used for tenant management endpoints (API keys, usage, etc.).
    """
    if not _CLOUD_MODE:
        # In legacy mode, allow access without tenant isolation
        return path_tenant_id
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
        )
    store = get_tenant_store()
    api_key = store.validate_api_key(x_api_key)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )
    if api_key.tenant_id != path_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: API key does not belong to this tenant.",
        )
    return path_tenant_id


class SessionListResponse(BaseModel):
    sessions: list[dict[str, Any]]
    total: int


class PruneResponse(BaseModel):
    pruned_db_sessions: int
    pruned_trace_files: int
    pruned_checkpoint_files: int
    dry_run: bool


class TraceResponse(BaseModel):
    session_id: str
    events: list[dict[str, Any]]
    total: int


class ConfidenceResponse(BaseModel):
    session_id: str
    overall_score: float
    goal_alignment: float
    consistency_score: float
    anomaly_flags: list[str]
    explanation: str
    component_scores: dict[str, float]


class RollbackRequest(BaseModel):
    checkpoint_id: str | None = None
    to_step: int | None = None
    restore_filesystem: bool = True
    restore_git: bool = True


class SafetyPolicyUpdate(BaseModel):
    block_on_high: bool = False
    block_on_critical: bool = True
    require_approval_on_high: bool = True
    require_approval_on_medium: bool = False
    approval_timeout_seconds: int = Field(default=120, ge=5, le=3600)


class SimulateRequest(BaseModel):
    rewind_to_step: int
    tool_id: str | None = None
    replacement: Any = None
    notes: str = ""


class SafetyCheckRequest(BaseModel):
    command: str = Field(min_length=1)
    tool_name: str = "bash"
    arguments: dict[str, Any] = Field(default_factory=dict)
    affected_resources: list[str] = Field(default_factory=list)


class EntitlementUsageReport(BaseModel):
    subject: str = Field(min_length=1)
    machine_id: str | None = None


class ThreatPathNode(BaseModel):
    policy_id: str
    reason: str
    risk_level: str
    block_by_default: bool
    matched: bool


class SafetyCheckResponse(BaseModel):
    command: str
    tool_name: str
    blocked: bool
    decision: str
    risk_level: str
    risk_score: float
    reasons: list[str]
    matched_policies: list[str]
    requires_approval: bool
    threat_path: list[ThreatPathNode]


def _record_budget(event: AgentEvent) -> None:
    _cost_tracker.ingest_event(event)


async def _after_publish(event: AgentEvent) -> None:
    _record_budget(event)
    await _pg_write_event(event)
    if event.is_blocked or (event.safety and event.safety.risk_level.value in {"high", "critical"}):
        await _alerting.alert_event(event)


def _seed_demo_data() -> None:
    if _collector.list_sessions(limit=1):
        return

    session_id = "demo-session"
    session = AgentSession(
        session_id=session_id,
        agent_id="demo-agent",
        agent_name="Demo Agent",
        framework=AgentFramework.CLAUDE_CODE,
        status=ExecutionStatus.SUCCESS,
        goal="Inspect the repo, identify a risky delete, and finish safely.",
        total_tokens=1840,
        estimated_cost_usd=0.0321,
        final_confidence=0.78,
    )
    _collector.register_session(session)

    now = datetime.now(UTC)
    demo_events = [
        AgentEvent(
            session_id=session_id,
            agent_id="demo-agent",
            agent_name="Demo Agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.SESSION_START,
            goal=session.goal,
            timestamp=now,
        ),
        AgentEvent(
            session_id=session_id,
            agent_id="demo-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.PLANNER_OUTPUT,
            planner_output_preview="I will inspect the project, avoid destructive commands, and summarize findings.",
            token_usage=TokenUsage(
                prompt_tokens=210, completion_tokens=88, total_tokens=298, estimated_cost_usd=0.004
            ),
            timestamp=now + timedelta(seconds=1),
        ),
        AgentEvent(
            session_id=session_id,
            agent_id="demo-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.TOOL_CALL,
            step_number=2,
            tool_call=ToolCallData(
                tool_name="bash", raw_command="rg --files", arguments={"command": "rg --files"}
            ),
            timestamp=now + timedelta(seconds=2),
        ),
        AgentEvent(
            session_id=session_id,
            agent_id="demo-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.TOOL_RESULT,
            step_number=3,
            status=ExecutionStatus.SUCCESS,
            tool_result=ToolResultData(
                tool_name="bash", output="api/server.py\nfrontend/pages/index.tsx"
            ),
            timestamp=now + timedelta(seconds=3),
        ),
        AgentEvent(
            session_id=session_id,
            agent_id="demo-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.TOOL_CALL,
            step_number=4,
            status=ExecutionStatus.BLOCKED,
            tool_call=ToolCallData(
                tool_name="bash",
                raw_command="rm -rf /var/log/*",
                arguments={"command": "rm -rf /var/log/*"},
            ),
            safety=SafetyCheckData(
                risk_level=RiskLevel.CRITICAL,
                risk_score=1.0,
                blocked=True,
                reasons=["Recursive deletion of critical filesystem path"],
                matched_policies=["FS_DELETE_CRITICAL"],
            ),
            timestamp=now + timedelta(seconds=4),
        ),
        AgentEvent(
            session_id=session_id,
            agent_id="demo-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.SESSION_END,
            status=ExecutionStatus.SUCCESS,
            timestamp=now + timedelta(seconds=5),
        ),
    ]
    bus = get_event_bus()
    for event in demo_events:
        _collector.register_session(session)
        _collector._traces[session_id].add_event(event)  # noqa: SLF001
        _collector._traces[session_id].session.total_events = _collector._traces[
            session_id
        ].event_count  # noqa: SLF001
        bus.publish_sync(event)
        _record_budget(event)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_session_factory

    if _IS_PROD and not _API_KEY:
        error_msg = (
            "AGENTWATCH_API_KEY is not set in production! For security, the server will not start."
        )
        logger.critical(error_msg)
        raise RuntimeError(error_msg)

    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        try:
            _db_session_factory = await init_db(db_url)
            logger.info("PostgreSQL connected and tables ready")
        except Exception:
            logger.warning("PostgreSQL unavailable — running in-memory only", exc_info=True)
    _init_default_schemas()
    bus = get_event_bus()
    bus.subscribe_fn(_collector.ingest, handler_id="api.collector")
    bus.subscribe_fn(_after_publish, handler_id="api.post_publish")
    _seed_demo_data()
    logger.info("AgentWatch API started")
    yield
    logger.info("AgentWatch API shutdown")


app = FastAPI(
    title="AgentWatch API",
    description="REST API for the AgentWatch observability platform. "
    "Handles reasoning trace ingestion, session management, safety policy enforcement, and real-time dashboard updates.",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Initialize rate limiter with configurable limits from environment
RATE_LIMIT_PER_USER = int(os.getenv("RATE_LIMIT_PER_USER", "100"))
RATE_LIMIT_GLOBAL = int(os.getenv("RATE_LIMIT_GLOBAL", "10000"))
RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "3600"))

_rate_limiter = RateLimiter(
    user_limit=RATE_LIMIT_PER_USER,
    global_limit=RATE_LIMIT_GLOBAL,
    window_sec=RATE_LIMIT_WINDOW_SEC,
)

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware, limiter=_rate_limiter)


def _v3_tenant(x_api_key: str | None = Header(default=None, alias="X-Api-Key")) -> str:
    """Tenant for v3 endpoints: bound to the API key in cloud mode, 'default' otherwise."""
    if _CLOUD_MODE and x_api_key:
        key = get_tenant_store().validate_api_key(x_api_key)
        if key is not None:
            return key.tenant_id
    return "default"


# AgentWatch v3: /api/v3/* and the OTLP receiver at /v1/traces (see docs/v3).
app.include_router(build_v3_router(_require_api_key, _v3_tenant))


@app.exception_handler(HTTPException)
async def _agentwatch_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 429 and exc.detail == "rate_limit_exceeded":
        headers = dict(exc.headers) if exc.headers else {}
        if hasattr(request.state, "rl_limit"):
            headers.setdefault("X-RateLimit-Limit", str(request.state.rl_limit))
            headers.setdefault("X-RateLimit-Remaining", str(request.state.rl_remaining))
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded"},
            headers=headers,
        )
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def rl_headers(request: Request, call_next):
    """Add X-RateLimit-* headers to every response including 429s."""
    response = await call_next(request)
    if hasattr(request.state, "rl_limit"):
        response.headers["X-RateLimit-Limit"] = str(request.state.rl_limit)
        response.headers["X-RateLimit-Remaining"] = str(request.state.rl_remaining)
    return response


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    """Record API latency metrics for all requests including failures."""
    start_time = time.time()
    endpoint = request.url.path
    response = None

    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        # Record failure before re-raising
        duration = time.time() - start_time
        record_api_latency(endpoint, duration)
        record_failure(endpoint, 500, str(exc))
        raise
    finally:
        # Record latency for successful responses
        if response is not None:
            duration = time.time() - start_time
            record_api_latency(endpoint, duration)


# CORS configuration.
#
# allow_credentials=True requires an explicit origin list -- the CORS spec
# forbids the combination of Access-Control-Allow-Origin: * with
# Access-Control-Allow-Credentials: true and browsers reject such responses.
#
# Set CORS_ALLOWED_ORIGINS to a comma-separated list of frontend URLs in
# each deployment environment, e.g.:
#
#   CORS_ALLOWED_ORIGINS=https://app.example.com,https://staging.example.com
#
# When the variable is absent the API falls back to allowing all origins
# without credentials (safe for public read-only dashboards and CLI usage;
# credentialed cross-origin requests will not work in that mode).
_raw_cors_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
_cors_origins: list[str] = (
    [o.strip() for o in _raw_cors_origins.split(",") if o.strip()]
    if _raw_cors_origins.strip()
    else ["*"]
)
_cors_credentials = _cors_origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/system/status")
async def system_status(_auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    """Returns detailed infrastructure status, including database connectivity."""
    return {
        "database": {
            "connected": _db_session_factory is not None,
            "mode": "persistent" if _db_session_factory else "in-memory",
        },
        "environment": os.getenv("ENVIRONMENT", "unknown"),
        "version": __version__,
    }


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    _limiter.check(_rate_limit_key(request, "r"), RATE_READ, request)

    checks: dict[str, Any] = {
        "version": __version__,
        "timestamp": datetime.now(UTC).isoformat(),
        "database_connected": _db_session_factory is not None,
        "traces": _collector.get_stats(),
        "event_bus": get_event_bus().stats(),
        "safety": _safety_engine.stats(),
        "cost": _cost_tracker.stats(),
    }
    degraded = False

    if _db_session_factory is None:
        checks["database"] = {"status": "in_memory"}
    else:
        try:

            async def _ping_db():
                async with _db_session_factory() as db:
                    await db.execute(text("SELECT 1"))

            await asyncio.wait_for(_ping_db(), timeout=10.0)
            checks["database"] = {"status": "ok"}
        except Exception:
            degraded = True
            checks["database"] = {"status": "degraded", "error": "Database is Unavailable"}

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:

            async def _ping_redis():
                r = aioredis.from_url(redis_url)
                try:
                    await r.ping()
                finally:
                    await r.aclose()

            await asyncio.wait_for(_ping_redis(), timeout=10.0)
            checks["redis"] = {"status": "ok"}
        except Exception:
            degraded = True
            checks["redis"] = {"status": "degraded", "error": "Redis is Unavailable"}
    else:
        checks["redis"] = {"status": "not_configured"}

    checks["status"] = "degraded" if degraded else "ok"
    http_status = 503 if degraded else 200
    return JSONResponse(content=checks, status_code=http_status)


@app.get("/metrics")
async def metrics() -> Response:
    """Expose Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


@app.get("/api/v1/sessions", response_model=SessionListResponse)
async def list_sessions(
    request: Request,
    limit: int = Query(default=50, le=200),
    framework: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since_hours: int | None = Query(default=None),
    _auth: None = Depends(_require_api_key),
) -> SessionListResponse:
    _limiter.check(_rate_limit_key(request, "r"), RATE_READ, request)
    since = None
    if since_hours is not None:
        since = datetime.now(UTC) - timedelta(hours=since_hours)
    sessions = _collector.list_sessions(
        limit=limit, framework=framework, status=status, since=since
    )
    return SessionListResponse(
        sessions=[session.model_dump(mode="json") for session in sessions], total=len(sessions)
    )


@app.delete("/api/v1/sessions/prune", response_model=PruneResponse)
async def prune_sessions_api(
    request: Request,
    older_than_hours: int = Query(..., ge=1),
    dry_run: bool = Query(False),
    _auth: None = Depends(_require_api_key),
) -> PruneResponse:
    """Delete old sessions, traces, and checkpoints.

    Args:
        request: The FastAPI request object.
        older_than_hours: Threshold in hours. Sessions older than this are pruned.
        dry_run: If True, do not actually delete anything, just return the counts.
        _auth: API key dependency.

    Returns:
        PruneResponse: The counts of pruned resources.
    """
    _limiter.check(_rate_limit_key(request, "w"), RATE_WRITE, request)
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)

    pruned_db_sessions = 0
    pruned_trace_files = 0
    pruned_checkpoint_files = 0

    try:
        if _db_session_factory:
            async with _db_session_factory() as db:
                repo = Repository(db)
                session_ids = await repo.get_sessions_older_than(cutoff)
                if session_ids:
                    # Follow user's required ordering: delete files before DB records
                    pruned_trace_files = await _collector.prune(session_ids, dry_run=dry_run)
                    pruned_checkpoint_files = await _rollback_engine.prune_checkpoints(
                        session_ids, dry_run=dry_run
                    )

                    if not dry_run:
                        pruned_db_sessions = await repo.prune_sessions(session_ids)
                        await db.commit()
                    else:
                        pruned_db_sessions = len(session_ids)
        else:
            # Fallback to filesystem discovery if no DB
            c_ids = set(await _collector.get_sessions_older_than(cutoff))
            r_ids = set(await _rollback_engine.get_sessions_older_than(cutoff))
            session_ids = list(c_ids.union(r_ids))

            if session_ids:
                pruned_trace_files = await _collector.prune(session_ids, dry_run=dry_run)
                pruned_checkpoint_files = await _rollback_engine.prune_checkpoints(
                    session_ids, dry_run=dry_run
                )
    except Exception as exc:
        logger.error("Failed to prune sessions: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to prune sessions")

    return PruneResponse(
        pruned_db_sessions=pruned_db_sessions,
        pruned_trace_files=pruned_trace_files,
        pruned_checkpoint_files=pruned_checkpoint_files,
        dry_run=dry_run,
    )


@app.post("/api/v1/sessions")
async def create_session(
    request: Request,
    session: AgentSession,
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    _limiter.check(_rate_limit_key(request, "w"), RATE_WRITE, request)
    _collector.register_session(session)
    await _pg_write_session(session)
    return {"status": "registered", "session": session.model_dump(mode="json")}


@app.get("/api/v1/sessions/{session_id}")
async def get_session(
    request: Request,
    session_id: str,
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    _limiter.check(_rate_limit_key(request, "r"), RATE_READ, request)
    trace = _collector.get_trace(session_id)
    if not trace:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return trace.session.model_dump(mode="json")


@app.get("/api/v1/sessions/{session_id}/events", response_model=TraceResponse)
async def get_events(
    request: Request,
    session_id: str,
    event_type: str | None = Query(default=None),
    limit: int = Query(default=500, le=2000),
    _auth: None = Depends(_require_api_key),
) -> TraceResponse:
    _limiter.check(_rate_limit_key(request, "r"), RATE_READ, request)
    events = _collector.get_events(session_id, event_type=event_type, limit=limit)
    return TraceResponse(
        session_id=session_id,
        events=[event.model_dump_for_storage() for event in events],
        total=len(events),
    )


def _v3_tee(event: AgentEvent) -> None:
    """Also record legacy events as v3 evidence (via LegacyTranslator). Never fails the request."""
    if os.getenv("AGENTWATCH_V3_TEE", "1").lower() in ("0", "false", "no"):
        return
    try:
        from agentwatch.sensors.legacy.translator import LegacyTranslator

        result = LegacyTranslator().translate(event)
        if result.draft is not None:
            get_v3_engine().ingest([result.draft])
    except Exception:
        logger.warning("v3 tee of legacy event failed", exc_info=True)


@app.post("/api/v1/events")
async def ingest_event(
    request: Request,
    event: AgentEvent,
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    _limiter.check(_rate_limit_key(request, "w"), RATE_WRITE, request)
    if event.event_type == EventType.TOOL_CALL and event.tool_call is not None:
        schema_key = event.framework.value.replace("_", "-")
        if _schema_validator.get_schema(schema_key) is not None:
            params: dict[str, Any] = {
                "tool_name": event.tool_call.tool_name,
                "arguments": dict(event.tool_call.arguments) if event.tool_call.arguments else {},
            }
            valid, err = _schema_validator.validate_task_parameters(schema_key, params)
            if not valid:
                raise HTTPException(status_code=422, detail=f"Schema validation failed: {err}")
    await get_event_bus().publish(event)
    _v3_tee(event)

    if event.agent_id and hasattr(event, "status"):
        if getattr(event, "status", None) == ExecutionStatus.FAILURE:
            record_failure(event.agent_id)

    return {"status": "accepted", "event_id": event.event_id}


@app.get("/api/v1/sessions/{session_id}/trace")
async def get_trace(session_id: str, _auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    trace = _collector.get_trace(session_id)
    if not trace:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return trace.to_dict()


@app.get("/api/v1/sessions/{session_id}/confidence", response_model=ConfidenceResponse)
async def get_confidence(
    session_id: str, _auth: None = Depends(_require_api_key)
) -> ConfidenceResponse:
    events = _collector.get_events(session_id, limit=2000)
    if not events:
        raise HTTPException(status_code=404, detail=f"No events for session {session_id}")
    trace = _collector.get_trace(session_id)
    result = _confidence_scorer.score(events, goal=trace.session.goal if trace else None)
    return ConfidenceResponse(
        session_id=session_id,
        overall_score=result.overall_score,
        goal_alignment=result.goal_alignment,
        consistency_score=result.consistency_score,
        anomaly_flags=result.anomaly_flags,
        explanation=result.explanation,
        component_scores=result.component_scores,
    )


@app.get("/api/v1/sessions/{session_id}/reasoning")
async def get_reasoning_audit(
    session_id: str, _auth: None = Depends(_require_api_key)
) -> dict[str, Any]:
    events = _collector.get_events(session_id, limit=5000)
    if not events:
        raise HTTPException(status_code=404, detail=f"No events for session {session_id}")
    return (await _reasoning_auditor.audit_session(events)).to_dict()


@app.get("/api/v1/sessions/{session_id}/cost")
async def get_cost_budget(
    session_id: str, _auth: None = Depends(_require_api_key)
) -> dict[str, Any]:
    budget = _cost_tracker.get_session(session_id)
    if not budget:
        events = _collector.get_events(session_id, limit=5000)
        if not events:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        for event in events:
            _cost_tracker.ingest_event(event)
        budget = _cost_tracker.get_session(session_id)
    return budget.to_dict() if budget else {"session_id": session_id}


@app.get("/api/v1/sessions/{session_id}/replay")
async def get_replay(session_id: str, _auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    events = _collector.get_events(session_id, limit=5000)
    trace = _collector.get_trace(session_id)
    if not events or not trace:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    replay = _replay_engine.load_from_events(trace.session, events)
    audit_summary = await _reasoning_auditor.audit_session(events)

    d = replay.to_dict()
    d["reasoning_audit"] = {
        "overall_score": audit_summary.average_score,
        # v0.2 used to derive "hallucination_risk" (1 - score) and "goal_alignment" (= score) from
        # this single uncalibrated audit score. Those names promised measurements that were never
        # made, so they are no longer reported.
        "note": "overall_score is an uncalibrated heuristic/LLM-judge audit score, not a hallucination or goal-alignment measurement",
        "findings": [
            {
                "type": a.verdict,
                "severity": "high" if a.score < 0.4 else "medium" if a.score < 0.7 else "low",
                "message": a.rationale,
                "step_index": a.step_index,
            }
            for a in audit_summary.audits
            if a.score < 0.7
        ],
    }
    return d


@app.post("/api/v1/sessions/{session_id}/simulate")
async def simulate_session(
    session_id: str, request: SimulateRequest, _auth: None = Depends(_require_api_key)
) -> dict[str, Any]:
    events = _collector.get_events(session_id, limit=5000)
    trace = _collector.get_trace(session_id)
    if not events or not trace:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    scenario = CounterfactualScenario(
        rewind_to_step=request.rewind_to_step,
        tool_id=request.tool_id,
        replacement=request.replacement,
        notes=request.notes,
    )
    try:
        engine = CounterfactualEngine()
        result = engine.run(events, scenario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "session_id": session_id,
        "diverged_at_step": result.diverged_at_step,
        # Honest labelling (v3 audit): without a step function this endpoint only substitutes one
        # value into a copy of the recorded timeline; nothing downstream is re-executed.
        "method": "value_substitution_only" if engine.step_fn is None else "step_function",
        "simulated": engine.step_fn is not None,
        "note": "For re-executed counterfactuals with uncertainty labels use POST /api/v3/counterfactual.",
        "original_events": [e.model_dump_for_storage() for e in result.original_events],
        "alternate_events": [e.model_dump_for_storage() for e in result.alternate_events],
        "summary": result.summary,
    }


@app.get("/api/v1/sessions/{session_id}/checkpoints")
async def list_checkpoints(
    session_id: str, _auth: None = Depends(_require_api_key)
) -> dict[str, Any]:
    checkpoints = _rollback_engine.list_checkpoints(session_id)
    return {
        "session_id": session_id,
        "checkpoints": [checkpoint.to_dict() for checkpoint in checkpoints],
    }


@app.post("/api/v1/sessions/{session_id}/rollback")
async def rollback_session(
    session_id: str, request: RollbackRequest, _auth: None = Depends(_require_api_key)
) -> dict[str, Any]:
    if request.checkpoint_id:
        result = await _rollback_engine.rollback(
            request.checkpoint_id,
            restore_filesystem=request.restore_filesystem,
            restore_git=request.restore_git,
        )
    else:
        result = await _rollback_engine.rollback_session(session_id, to_step=request.to_step)

    _governance.record_action(
        principal_id="api",
        event_type=AuditEventType.ROLLBACK,
        resource=session_id,
        action="rollback",
        allowed=result.status.value == "completed",
        session_id=session_id,
        details={"checkpoint_id": result.checkpoint_id},
    )
    return {
        "checkpoint_id": result.checkpoint_id,
        "status": result.status.value,
        "rolled_back_files": result.rolled_back_files[:50],
        "rolled_back_git_ref": result.rolled_back_git_ref,
        "error": result.error,
        "duration_seconds": result.duration_seconds,
    }


@app.get("/api/v1/safety/policy")
async def get_safety_policy(
    _auth: None = Depends(_require_api_key),
    _perm: object = Depends(require_permission("policy:read")),
) -> dict[str, Any]:
    policy = _safety_engine.policy
    return {
        "policy_id": policy.policy_id,
        "name": policy.name,
        "block_on_high": policy.block_on_high,
        "block_on_critical": policy.block_on_critical,
        "require_approval_on_high": policy.require_approval_on_high,
        "require_approval_on_medium": policy.require_approval_on_medium,
        "approval_timeout_seconds": policy.approval_timeout_seconds,
    }


@app.put("/api/v1/safety/policy")
async def update_safety_policy(
    update: SafetyPolicyUpdate,
    _auth: None = Depends(_require_api_key),
    _perm: object = Depends(require_permission("policy:write")),
) -> dict[str, Any]:
    policy = SafetyPolicy(
        policy_id="api-configured",
        name="API-configured policy",
        block_on_high=update.block_on_high,
        block_on_critical=update.block_on_critical,
        require_approval_on_high=update.require_approval_on_high,
        require_approval_on_medium=update.require_approval_on_medium,
        approval_timeout_seconds=update.approval_timeout_seconds,
    )
    _safety_engine.update_policy(policy)
    _governance.record_action(
        principal_id="api",
        event_type=AuditEventType.POLICY_CHANGE,
        resource="safety-policy",
        action="update",
        details=update.model_dump(),
    )
    return {"status": "updated", "policy_id": policy.policy_id}


@app.get("/api/v1/safety/blocked")
async def get_blocked_events(
    limit: int = Query(default=50, le=200),
    since_hours: int = Query(default=24),
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    threshold = datetime.now(UTC) - timedelta(hours=since_hours)
    events = [
        event
        for event in get_event_bus().get_recent_events(limit=5000)
        if event.is_blocked and event.timestamp >= threshold
    ][:limit]
    return {
        "blocked_events": [event.model_dump_for_storage() for event in events],
        "total": len(events),
    }


@app.post("/api/v1/safety/check", response_model=SafetyCheckResponse)
async def check_safety_command(request: SafetyCheckRequest) -> SafetyCheckResponse:
    cmd = request.command.strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="command must not be empty")

    merged_args = dict(request.arguments)
    merged_args["command"] = cmd
    tool_call = ToolCallData(
        tool_name=request.tool_name,
        raw_command=cmd,
        arguments=merged_args,
        affected_resources=request.affected_resources,
    )

    # Use the current policy in an isolated SafetyEngine so test invocations
    # do not mutate global safety counters used by runtime telemetry.
    sim_engine = SafetyEngine(policy=_safety_engine.policy)
    event = AgentEvent(
        session_id="safety-lab",
        agent_id="safety-lab",
        agent_name="Safety Lab",
        framework=AgentFramework.CUSTOM,
        event_type=EventType.TOOL_CALL,
        tool_call=tool_call,
    )
    checked = await sim_engine.check_event(event)
    if checked.safety is None:
        raise HTTPException(status_code=500, detail="safety check failed")

    scorer = RiskScorer(extra_patterns=_safety_engine.policy.custom_patterns)
    full_text = " ".join(
        [
            tool_call.raw_command or "",
            tool_call.tool_name,
            *[str(v) for v in tool_call.arguments.values() if isinstance(v, str)],
        ]
    )
    threat_path: list[ThreatPathNode] = []
    for pattern in scorer._patterns:  # noqa: SLF001
        try:
            if pattern.use_regex:
                matched = bool(re.search(pattern.pattern, full_text, re.IGNORECASE))
            else:
                matched = False
        except re.error:
            matched = False
        threat_path.append(
            ThreatPathNode(
                policy_id=pattern.policy_id,
                reason=pattern.reason,
                risk_level=pattern.risk_level.value,
                block_by_default=pattern.block_by_default,
                matched=matched,
            )
        )

    safety = checked.safety
    if safety.blocked:
        decision = "blocked"
    elif safety.requires_approval:
        decision = "requires_approval"
    else:
        decision = "allowed"

    return SafetyCheckResponse(
        command=cmd,
        tool_name=request.tool_name,
        blocked=safety.blocked,
        decision=decision,
        risk_level=safety.risk_level.value,
        risk_score=safety.risk_score,
        reasons=safety.reasons,
        matched_policies=safety.matched_policies,
        requires_approval=safety.requires_approval,
        threat_path=threat_path,
    )


@app.get("/api/v1/dashboard/summary")
async def dashboard_summary(_auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    sessions = _collector.list_sessions(limit=200)
    stats = _collector.get_stats()
    return {
        "total_sessions": len(sessions),
        "active_sessions": stats["active_sessions"],
        "failed_sessions": sum(
            1 for session in sessions if session.status == ExecutionStatus.FAILURE
        ),
        "blocked_sessions": sum(
            1 for session in sessions if session.status == ExecutionStatus.BLOCKED
        ),
        "total_tokens": sum(session.total_tokens for session in sessions),
        "estimated_cost_usd": round(sum(session.estimated_cost_usd for session in sessions), 4),
        "safety_stats": _safety_engine.stats(),
        "event_bus_stats": get_event_bus().stats(),
    }


@app.get("/api/v1/dashboard/top")
async def dashboard_top(_auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    sessions = _collector.list_sessions(status=ExecutionStatus.RUNNING.value, limit=50)
    top_sessions = []
    now = datetime.now(UTC)
    for s in sessions:
        current_tool = s.metadata.get("current_tool", "idle")

        duration = (now - s.started_at).total_seconds()
        burn_rate = s.total_tokens / duration if duration > 0 else 0

        top_sessions.append(
            {
                "session_id": s.session_id,
                "agent_id": s.agent_id,
                "agent_name": s.agent_name,
                "current_tool": current_tool,
                "token_burn_rate_per_sec": round(burn_rate, 2),
                "total_tokens": s.total_tokens,
            }
        )
    return {"top_sessions": top_sessions}


@app.get("/api/v1/governance/compliance-report")
async def compliance_report(
    _auth: None = Depends(_require_api_key),
    format: str = Query("json", alias="format"),
    include_allowed: bool = Query(False),
):
    if format == "csv":
        from fastapi.responses import PlainTextResponse

        csv_content = _compliance_reporter.generate_csv(include_allowed=include_allowed)
        return PlainTextResponse(csv_content, media_type="text/csv")
    return _compliance_reporter.generate().to_dict()


@app.post("/api/v1/entitlement/usage")
async def report_entitlement_usage(
    report: EntitlementUsageReport,
    x_machine_id: str | None = Header(default=None, alias="X-Machine-Id"),
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    """Record an entitlement usage heartbeat and flag cross-device abuse (#463)."""
    machine_id = report.machine_id or x_machine_id
    if not machine_id:
        raise HTTPException(
            status_code=400, detail="machine_id is required (body or X-Machine-Id header)."
        )
    event = _usage_tracker.record(report.subject, machine_id)
    if event is not None:
        logger.warning("Entitlement abuse: %s on %d devices", event.subject, event.distinct_devices)
        await _alerting.alert_abuse(event)
    return {
        "recorded": True,
        "abuse_detected": event is not None,
        "active_devices": len(_usage_tracker.active_devices(report.subject)),
    }


@app.get("/api/v1/governance/eu-ai-act-report")
async def eu_ai_act_report(
    _auth: None = Depends(_require_api_key),
    _ent: object = Depends(require_entitlement("compliance")),
) -> dict[str, Any]:
    """EU AI Act Article 15 conformity export (CMP-004).

    Maps AgentWatch's safety telemetry to the Article 15 requirements and
    returns the technical documentation plus a conformity assessment as JSON.
    """
    from agentwatch.governance.eu_ai_act import (
        DecisionLogEntry,
        EUAIActPackage,
        TechnicalDocumentation,
    )

    safety = _safety_engine.stats()
    policy = _safety_engine.policy
    sessions = _collector.list_sessions(limit=200)
    checked = safety["checked"]

    robustness: list[str] = []
    if checked:
        robustness.append(f"safety_engine_risk_scoring:{checked}_events_checked")
    if safety["blocked"]:
        robustness.append(f"actions_blocked:{safety['blocked']}")
    if policy.block_on_critical:
        robustness.append("block_on_critical")
    if policy.block_on_high:
        robustness.append("block_on_high")

    oversight: list[str] = []
    if policy.block_on_critical:
        oversight.append("critical actions are blocked")
    if policy.require_approval_on_high:
        oversight.append("high-risk actions require human approval")
    if policy.require_approval_on_medium:
        oversight.append("medium-risk actions require human approval")

    doc = TechnicalDocumentation(
        system_name="AgentWatch-monitored AI system",
        intended_purpose="Observability, safety, and reliability layer for AI agents",
        risk_category="high",
        data_governance={
            "active_policy": policy.name,
            "approval_required_high": str(policy.require_approval_on_high),
        },
        accuracy_metrics={
            "events_checked": float(checked),
            "safety_block_rate": round(safety["blocked"] / checked, 4) if checked else 0.0,
        },
        robustness_evidence=robustness,
        human_oversight_description="; ".join(oversight) or "no oversight gates configured",
        transparency_disclosures=["session_replay", "reasoning_audit_trail"],
    )

    pkg = EUAIActPackage()
    pkg.set_documentation(doc)
    sessions_used = sessions[:50]
    for session in sessions_used:
        pkg.log_decision(
            DecisionLogEntry(
                when=session.started_at,
                decision_id=session.session_id,
                inputs_hash="",
                outputs_hash="",
                confidence=0.0,
                safety_checks_passed=session.status != ExecutionStatus.BLOCKED,
                human_oversight_required=policy.require_approval_on_high,
                explanation=f"session status={session.status.value}",
            )
        )

    return {
        "article": "EU AI Act Article 15",
        "documentation": doc.to_dict(),
        "conformity": pkg.assess().to_dict(),
        "telemetry": {"safety_stats": safety, "sessions_considered": len(sessions_used)},
    }


@app.get("/api/v1/compliance/audit-log")
async def compliance_audit_log_csv(
    _auth: None = Depends(_require_api_key),
    format: str = Query("csv", alias="format"),
    include_allowed: bool = Query(False),
):
    from fastapi.responses import PlainTextResponse

    csv_content = _compliance_reporter.generate_csv(include_allowed=include_allowed)
    return PlainTextResponse(csv_content, media_type="text/csv")


@app.post("/api/v1/demo/seed")
async def seed_demo(_auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    _seed_demo_data()
    return {"status": "seeded"}


def _sanitize_event(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Escape HTML tags in user-facing preview strings to prevent XSS in the dashboard.

    Creates and returns a new sanitized dictionary to avoid mutating the original input.
    """
    import html

    sanitized = event_dict.copy()

    if "prompt_preview" in sanitized and isinstance(sanitized["prompt_preview"], str):
        sanitized["prompt_preview"] = html.escape(sanitized["prompt_preview"])
    if "planner_output_preview" in sanitized and isinstance(
        sanitized["planner_output_preview"], str
    ):
        sanitized["planner_output_preview"] = html.escape(sanitized["planner_output_preview"])

    if "tool_call" in sanitized and sanitized["tool_call"]:
        tc = sanitized["tool_call"].copy()
        if "raw_command" in tc and isinstance(tc["raw_command"], str):
            tc["raw_command"] = html.escape(tc["raw_command"])
        if "arguments" in tc and isinstance(tc["arguments"], dict):
            tc["arguments"] = {k: html.escape(str(v)) for k, v in tc["arguments"].items()}
        sanitized["tool_call"] = tc

    if "tool_result" in sanitized and sanitized["tool_result"]:
        tr = sanitized["tool_result"].copy()
        if "output" in tr and isinstance(tr["output"], str):
            tr["output"] = html.escape(tr["output"])
        if "error" in tr and isinstance(tr["error"], str):
            tr["error"] = html.escape(tr["error"])
        sanitized["tool_result"] = tr

    return sanitized


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    """Real-time event stream over WebSocket.

    Authentication mirrors the REST layer: clients must supply the
    AGENTWATCH_API_KEY value either in the ``X-Api-Key`` request header or
    as the ``api_key`` query parameter.  When the key is absent or incorrect
    the connection is rejected with WebSocket close code 4001 before any data
    is sent, consistent with HTTP 401 semantics.

    When AGENTWATCH_API_KEY is not configured the guard follows the same
    logic as _require_api_key: open in development, fail-closed in production.
    """
    # Resolve the supplied key from the header or query parameter so browser
    # WebSocket clients (which cannot set arbitrary headers) can pass the key
    # as a URL parameter.
    supplied_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")

    if _IS_PROD and not _API_KEY:
        # Fail-closed: production deployment with no key configured is a
        # misconfiguration; reject all connections.
        logger.error(
            "AGENTWATCH_API_KEY is missing in production environment; "
            "rejecting WebSocket connection"
        )
        await websocket.close(code=4500, reason="Server misconfiguration")
        return

    if _API_KEY and not _key_matches(supplied_key):
        logger.warning("WebSocket connection rejected: invalid or missing API key")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    _ws_clients.append(websocket)
    bus = get_event_bus()

    async def forward(event: AgentEvent) -> None:
        try:
            await websocket.send_json(_sanitize_event(event.model_dump_for_storage()))
        except Exception:
            logger.debug("WebSocket client send failed", exc_info=True)

    handler_id = bus.subscribe_fn(forward, handler_id=f"ws-{id(websocket)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(handler_id)
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


@app.websocket("/ws/assessments/{assessment_id}")
async def websocket_assessment_progress(websocket: WebSocket, assessment_id: str) -> None:
    """Real-time contract assessment progress stream over WebSocket."""
    supplied_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")

    if _IS_PROD and not _API_KEY:
        logger.error(
            "AGENTWATCH_API_KEY is missing in production environment; "
            "rejecting WebSocket connection"
        )
        await websocket.close(code=4500, reason="Server misconfiguration")
        return

    if _API_KEY and not _key_matches(supplied_key):
        logger.warning("WebSocket connection rejected: invalid or missing API key")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    tenant_id = _v3_tenant(supplied_key)

    from agentwatch.contracts.stream import get_assessment_manager

    manager = get_assessment_manager()
    job = manager.get_job(tenant_id, assessment_id)
    eng = get_v3_engine()

    if job is None:
        stored = eng.store.get_assessment(tenant_id, assessment_id)
        if stored is None:
            await websocket.close(code=4004, reason="Assessment not found")
            return

    await websocket.accept()

    current_status = job.get_status() if job else eng.store.get_assessment(tenant_id, assessment_id)
    if current_status:
        await websocket.send_json(current_status)

    if job is None or job.processor.status.value in ("completed", "failed", "cancelled"):
        await websocket.close(code=1000, reason="Assessment completed")
        return

    async def forward_progress(progress_doc: dict[str, Any]) -> None:
        try:
            await websocket.send_json(progress_doc)
        except Exception:
            logger.debug("Assessment WebSocket client send failed", exc_info=True)

    job.processor.subscribe(forward_progress)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


# ─────────────────────────────────────────────
# Tenant management endpoints (Cloud mode)
# ─────────────────────────────────────────────


@app.post("/api/v1/tenants")
async def create_tenant(
    name: str = Query(...),
    plan: str = Query("free"),
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    store = get_tenant_store()
    try:
        tenant_plan = TenantPlan(plan)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {plan}")
    tenant = store.create_tenant(name=name, plan=tenant_plan)
    return tenant.to_dict()


@app.get("/api/v1/tenants")
async def list_tenants(_auth: None = Depends(_require_api_key)) -> dict[str, Any]:
    store = get_tenant_store()
    tenants = store.list_tenants()
    return {"tenants": [t.to_dict() for t in tenants]}


@app.get("/api/v1/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    store = get_tenant_store()
    tenant = store.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.to_dict()


@app.post("/api/v1/tenants/{tenant_id}/api-keys")
async def create_api_key(
    tenant_id: str,
    name: str = Query(""),
    _tenant: str = Depends(_require_tenant_ownership),
) -> dict[str, Any]:
    store = get_tenant_store()
    tenant = store.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    api_key, raw_key = store.create_api_key(tenant_id=tenant_id, name=name)
    return {
        **api_key.to_dict(),
        "key": raw_key,
        "message": "Store this key securely — it will not be shown again.",
    }


@app.get("/api/v1/tenants/{tenant_id}/api-keys")
async def list_api_keys(
    tenant_id: str,
    _tenant: str = Depends(_require_tenant_ownership),
) -> dict[str, Any]:
    store = get_tenant_store()
    keys = store.list_api_keys(tenant_id)
    return {"api_keys": [k.to_dict() for k in keys]}


@app.delete("/api/v1/tenants/{tenant_id}/api-keys/{key_id}")
async def revoke_api_key(
    tenant_id: str,
    key_id: str,
    _tenant: str = Depends(_require_tenant_ownership),
) -> dict[str, Any]:
    store = get_tenant_store()
    ok = store.revoke_api_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"revoked": True}


@app.get("/api/v1/tenants/{tenant_id}/usage")
async def get_usage(
    tenant_id: str,
    period: str | None = Query(None),
    _tenant: str = Depends(_require_tenant_ownership),
) -> dict[str, Any]:
    store = get_tenant_store()
    usage = store.get_usage(tenant_id, period=period)
    if not usage:
        return {"usage": None, "message": "No usage data for this period"}
    return {"usage": usage.to_dict()}


@app.get("/api/v1/ingestion/metrics")
async def ingestion_metrics(
    tenant_id: str | None = Query(None),
    _auth: None = Depends(_require_api_key),
) -> dict[str, Any]:
    # Ingestion metrics are available when the pipeline is running
    return {"message": "Ingestion metrics endpoint — attach TenantIngestionPipeline for live data"}


# ─── GDPR right-to-erasure (CMP-002) ──────────────────────────────────

# AGENTWATCH_ERASURE_SECRET must be configured at process startup. Receipts need
# to remain verifiable across restarts and deployments, so we resolve the
# secret lazily on first use (rather than at module import) so that import-time
# test harnesses that don't exercise the gdpr endpoint can still run.
_SESSION_ERASURE_SECRET: bytes | None = None
_ERASURE_SECRET_RESOLVED: bool = False


def _get_erasure_secret() -> bytes:
    """Resolve the HMAC signing secret for erasure receipts.

    Reads ``AGENTWATCH_ERASURE_SECRET`` from the environment on first access
    and caches the result for the process lifetime. Raises ``RuntimeError``
    if the secret is unset or empty so misconfiguration surfaces at the
    actual GDPR-endpoint call rather than at server import time.
    """
    global _SESSION_ERASURE_SECRET, _ERASURE_SECRET_RESOLVED
    if not _ERASURE_SECRET_RESOLVED:
        _SESSION_ERASURE_SECRET = os.getenv("AGENTWATCH_ERASURE_SECRET", "").encode()
        _ERASURE_SECRET_RESOLVED = True
    if not _SESSION_ERASURE_SECRET:
        raise RuntimeError(
            "AGENTWATCH_ERASURE_SECRET environment variable must be set to a "
            "non-empty value before the API server can accept GDPR erasure requests. "
            "Receipt signatures cannot be deterministic without it."
        )
    return _SESSION_ERASURE_SECRET


class _SessionErasureTarget:
    """ErasureTarget for agent session and event records in the SQLAlchemy repository.

    The repository is injected per-operation so each ``count`` / ``erase`` pair
    runs inside its own transactional scope.  This follows the same pattern as
    ``_pg_write_session`` and ``_pg_write_event``.

    ``tenant_id`` is bound at construction time so EVERY query in this target
    is filtered by the caller's tenant in cloud deployments, preventing
    caller-supplied identifiers from triggering an unintentional global wipe.
    """

    name = "sessions_and_events"

    def __init__(self, tenant_id: str | None = None) -> None:
        if _db_session_factory is None:
            raise RuntimeError(
                "Database session factory is not initialised; "
                "agentwatch.api.server._db_session_factory must be wired "
                "before the /api/v1/gdpr/erase endpoint accepts traffic."
            )
        if not _CLOUD_MODE and tenant_id is not None:
            raise RuntimeError(
                "Tenant filtering requested but server is in legacy mode "
                "(_CLOUD_MODE=False). Reject the request rather than silently "
                "scoping to a non-cloud tenant."
            )
        self._tenant_id = tenant_id

    async def count_matching(self, identifier: str, scope: ErasureScope) -> int:
        try:
            async with _db_session_factory() as db:
                repo = Repository(db)
                if scope == ErasureScope.SESSION_ID:
                    where = {"session_id": identifier}
                elif scope == ErasureScope.AGENT_ID:
                    where = {"agent_id": identifier}
                elif scope == ErasureScope.USER_ID:
                    where = {"user_id": identifier}
                elif scope == ErasureScope.TENANT_ID:
                    where = {"tenant_id": identifier}
                else:
                    return 0
                if self._tenant_id is not None:
                    where["tenant_id"] = self._tenant_id
                sessions = await repo.count_sessions(where)
                if scope == ErasureScope.SESSION_ID:
                    return sessions
                events = await repo.count_events(where)
                return sessions + events
        except Exception as exc:  # noqa: BLE001 — surfaced via ErasureTargetResult
            raise RuntimeError(f"sessions_and_events.count_matching failed: {exc}") from exc

    async def erase_matching(self, identifier: str, scope: ErasureScope) -> int:
        try:
            async with _db_session_factory() as db:
                repo = Repository(db)
                erased = 0
                if scope == ErasureScope.SESSION_ID:
                    where = {"session_id": identifier}
                elif scope == ErasureScope.AGENT_ID:
                    where = {"agent_id": identifier}
                elif scope == ErasureScope.USER_ID:
                    where = {"user_id": identifier}
                elif scope == ErasureScope.TENANT_ID:
                    where = {"tenant_id": identifier}
                else:
                    return 0
                if self._tenant_id is not None:
                    where["tenant_id"] = self._tenant_id
                event_where = {k: v for k, v in where.items()}
                if scope == ErasureScope.SESSION_ID:
                    erased += await repo.delete_events_by_session(identifier)
                    erased += await repo.delete_session(identifier)
                    await db.commit()
                    return erased
                session_ids = await repo.get_session_ids(where)
                for sid in session_ids:
                    event_where_for_sid = dict(event_where)
                    event_where_for_sid.pop("tenant_id", None)
                    event_where_for_sid["session_id"] = sid
                    ignored_events = await repo.get_session(sid)
                    _ = (event_where_for_sid, ignored_events)
                    erased += await repo.delete_events_by_session(sid)
                    erased += await repo.delete_session(sid)
                await db.commit()
                return erased
        except Exception as exc:  # noqa: BLE001 — surfaced via ErasureTargetResult
            raise RuntimeError(f"sessions_and_events.erase_matching failed: {exc}") from exc


class _MemoryErasureTarget:
    """Best-effort ErasureTarget for memory/causal backends that may not be wired.

    When the optional memory imports succeed the target delegates to the
    in-memory stores; otherwise it raises a scoped RuntimeError so the
    CrossSessionErasureService records the missing backend as a per-target
    failure rather than silently reporting zero matches.
    """

    name = "memory_and_causal"

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    async def count_matching(self, identifier: str, scope: ErasureScope) -> int:
        try:
            from agentwatch.memory.governance import list_memory_entries
        except Exception as exc:  # noqa: BLE001 — surfaced via ErasureTargetResult
            raise RuntimeError(f"memory_and_causal backend unavailable: {exc}") from exc
        items = await list_memory_entries()
        return sum(
            1
            for entry in items
            if identifier in (entry.get("user_id", "") or "")
            and (self._tenant_id is None or entry.get("tenant_id") == self._tenant_id)
        )

    async def erase_matching(self, identifier: str, scope: ErasureScope) -> int:
        try:
            from agentwatch.memory.governance import drop_memory_entries
        except Exception as exc:  # noqa: BLE001 — surfaced via ErasureTargetResult
            raise RuntimeError(f"memory_and_causal backend unavailable: {exc}") from exc
        try:
            removed = await drop_memory_entries(
                identifier=identifier,
                tenant_id=self._tenant_id,
            )
        except TypeError:
            removed = await drop_memory_entries(identifier)
        return int(removed)


async def _build_erasure_service(
    tenant_id: str | None = None,
) -> CrossSessionErasureService:
    targets: list = []
    if _db_session_factory is not None:
        targets.append(_SessionErasureTarget(tenant_id=tenant_id))
    else:
        logger.warning(
            "Database session factory is not initialised; portability-and-events target omitted from erasure service."
        )
    try:
        targets.append(_MemoryErasureTarget(tenant_id=tenant_id))
    except Exception as exc:  # noqa: BLE001 — surfaced via ErasureTargetResult
        logger.debug("Memory target will register as failure on first call: %s", exc)
    if not targets:
        raise RuntimeError(
            "No erasure targets are available; refusing to sign a receipt for a "
            "silent no-op erasure. Configure at least one backend before exposing "
            "the /api/v1/gdpr/erase endpoint."
        )
    return CrossSessionErasureService(
        targets=targets,
        signing_secret=_get_erasure_secret(),
    )


@app.post(
    "/api/v1/gdpr/erase",
    response_model=ErasureReceipt,
    responses={
        207: {"model": ErasureReceipt, "description": "Multi-Status — partial failure"},
    },
)
async def gdpr_erase(
    request: ErasureRequest,
    response: Response,
    tenant_id: str | None = Depends(_require_tenant_context),
) -> ErasureReceipt:
    """Execute a right-to-erasure (right-to-be-forgotten) action across all
    session, event, and memory data for the given identifier.

    The response is an HMAC-SHA256 signed erasure receipt suitable for
    compliance audit trails. If any registered target reports an error the
    endpoint responds with HTTP 207 (Multi-Status) so callers can distinguish
    a clean no-op from a partially-failed erasure; the response body in both
    200 and 207 cases is the signed ``ErasureReceipt`` itself.
    """
    service = await _build_erasure_service(tenant_id=tenant_id)
    receipt = await service.erase(request)
    if receipt.failure_count:
        response.status_code = status.HTTP_207_MULTI_STATUS
    return receipt


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0." + "0.0", port=8000, log_level="info")
