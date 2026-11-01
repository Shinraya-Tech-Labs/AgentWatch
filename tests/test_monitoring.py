"""Tests for Prometheus monitoring metrics and health scoring."""

from __future__ import annotations

from panoptis.monitoring.metrics import (
    calculate_health_score,
    record_api_latency,
    record_db_query,
    record_failure,
    update_db_connections,
    update_health_score,
)


class TestHealthScore:
    def test_healthy_system(self):
        score = calculate_health_score(0.0, 0.2, 0.5)
        assert score == 100

    def test_high_failure_rate_degrades_score(self):
        score = calculate_health_score(0.5, 0.0, 0.0)
        assert score < 100
        assert score >= 0

    def test_high_latency_degrades_score(self):
        score = calculate_health_score(0.0, 2.0, 0.0)
        assert score < 100

    def test_high_db_utilization_degrades_score(self):
        score = calculate_health_score(0.0, 0.0, 0.95)
        assert score < 100

    def test_critical_system_bottoms_at_zero(self):
        score = calculate_health_score(1.0, 10.0, 1.0)
        assert score == 0

    def test_score_within_bounds(self):
        for failure_rate in (0.0, 0.05, 0.2, 1.0):
            score = calculate_health_score(failure_rate, 0.5, 0.5)
            assert 0 <= score <= 100


class TestMetricRecording:
    def test_record_failure_does_not_raise(self):
        record_failure("/api/v1/events", 500, "connection error")

    def test_record_api_latency_does_not_raise(self):
        record_api_latency("/api/v1/sessions", 0.042)

    def test_record_db_query_does_not_raise(self):
        record_db_query("select", 0.005)

    def test_update_db_connections_does_not_raise(self):
        update_db_connections(10)

    def test_update_health_score_does_not_raise(self):
        update_health_score(0.02, 0.1, 0.3)

    def test_record_failure_with_empty_message(self):
        record_failure("/health", 404)

    def test_record_api_latency_zero(self):
        record_api_latency("/metrics", 0.0)
