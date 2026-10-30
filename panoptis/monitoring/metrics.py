"""System Health Monitoring & Metrics

Prometheus-based metrics collection for comprehensive system health monitoring
and alerting on agent failures, API latency, and system health.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Prometheus metrics
agent_failures = Counter(
    "agent_failures_total",
    "Total agent failures",
    labelnames=["agent_id"],
)
api_latency = Histogram(
    "api_latency_seconds",
    "API request latency in seconds",
    labelnames=["endpoint"],
)
system_health = Gauge(
    "system_health",
    "System health score 0-100",
)
db_connections_active = Gauge(
    "db_connections_active",
    "Number of active database connections",
)
db_query_duration = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    labelnames=["query_type"],
)


def record_failure(endpoint: str, status_code: int, error_msg: str = "") -> None:
    """Record API failure metric.

    Args:
        endpoint: The API endpoint that failed
        status_code: HTTP status code (4xx or 5xx)
        error_msg: Optional error message for debugging
    """
    # Use endpoint as agent_id for tracking
    agent_failures.labels(agent_id=endpoint).inc()


def record_api_latency(endpoint: str, latency_sec: float) -> None:
    """Record API request latency metric.

    Args:
        endpoint: The API endpoint path
        latency_sec: Request latency in seconds
    """
    api_latency.labels(endpoint=endpoint).observe(latency_sec)


def record_db_query(query_type: str, duration_sec: float) -> None:
    """Record database query metric.

    Args:
        query_type: The type of database query (e.g., 'select', 'insert', 'update')
        duration_sec: Query duration in seconds
    """
    db_query_duration.labels(query_type=query_type).observe(duration_sec)


def update_db_connections(count: int) -> None:
    """Update the number of active database connections.

    Args:
        count: Number of active connections
    """
    db_connections_active.set(count)


def calculate_health_score(
    failure_rate: float,
    p99_latency_sec: float,
    db_connection_utilization: float,
) -> int:
    """Calculate system health score based on key metrics.

    Args:
        failure_rate: Agent failure rate (0-1)
        p99_latency_sec: 99th percentile API latency in seconds
        db_connection_utilization: Database connection pool utilization (0-1)

    Returns:
        Health score 0-100 (100 = healthy, 0 = critical)
    """
    score = 100

    if failure_rate > 0.1:
        score -= int(failure_rate * 200)
    if p99_latency_sec > 1.0:
        score -= min(40, int((p99_latency_sec - 1.0) * 20))
    if db_connection_utilization > 0.8:
        score -= min(30, int((db_connection_utilization - 0.8) * 150))

    return max(0, score)


def update_health_score(
    failure_rate: float,
    p99_latency_sec: float = 0.0,
    db_connection_utilization: float = 0.0,
) -> None:
    """Update the system health score gauge.

    Args:
        failure_rate: Agent failure rate (0-1)
        p99_latency_sec: 99th percentile API latency in seconds
        db_connection_utilization: Database connection pool utilization (0-1)
    """
    score = calculate_health_score(
        failure_rate,
        p99_latency_sec,
        db_connection_utilization,
    )
    system_health.set(score)
