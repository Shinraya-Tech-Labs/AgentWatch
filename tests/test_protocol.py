"""Phase 10 — Protocol Play tests (PRT-001..004)."""

from __future__ import annotations

import pytest

from panoptis.protocol.badge import check
from panoptis.protocol.benchmark import AnonymizedBenchmark
from panoptis.protocol.mcp_server import AgentWatchMCPServer
from panoptis.protocol.schema_v1 import (
    REASONING_TRACE_VERSION,
    reasoning_trace_schema,
    validate_trace,
)

# ─────────────────────────────────────────────
# PRT-001 — Open trace schema
# ─────────────────────────────────────────────


def test_schema_returns_jsonschema_document():
    schema = reasoning_trace_schema()
    assert schema["title"] == "ReasoningTrace"
    assert schema["$schema"].startswith("https://json-schema.org/")


def test_validate_trace_passes_on_well_formed():
    trace = {
        "version": REASONING_TRACE_VERSION,
        "trace_id": "t1",
        "agent": {"id": "a1", "name": "claude"},
        "spans": [
            {
                "span_id": "s1",
                "kind": "reasoning",
                "name": "plan",
                "start_time": "2026-05-26T00:00:00Z",
            }
        ],
    }
    ok, errors = validate_trace(trace)
    assert ok, errors


def test_validate_trace_rejects_invalid_kind():
    trace = {
        "version": REASONING_TRACE_VERSION,
        "trace_id": "t1",
        "agent": {"id": "a1", "name": "n"},
        "spans": [
            {
                "span_id": "s1",
                "kind": "not_a_kind",
                "name": "x",
                "start_time": "2026-05-26T00:00:00Z",
            }
        ],
    }
    ok, errors = validate_trace(trace)
    assert not ok
    assert any("invalid kind" in e for e in errors)


def test_validate_trace_rejects_missing_fields():
    ok, errors = validate_trace({"version": REASONING_TRACE_VERSION})
    assert not ok
    assert any("missing required field 'trace_id'" in e for e in errors)


# ─────────────────────────────────────────────
# PRT-002 — Badge
# ─────────────────────────────────────────────


def test_badge_passes_with_full_coverage():
    sample = {
        "version": REASONING_TRACE_VERSION,
        "trace_id": "t1",
        "agent": {"id": "a", "name": "n"},
        "spans": [
            {
                "span_id": "s1",
                "kind": "reasoning",
                "name": "x",
                "start_time": "2026-05-26T00:00:00Z",
            },
            {
                "span_id": "s2",
                "kind": "tool_call",
                "name": "y",
                "start_time": "2026-05-26T00:00:01Z",
            },
        ],
    }
    result = check("my-framework", [sample])
    assert result.passed is True
    assert "✓" in result.badge_text


def test_badge_fails_when_missing_required_kind():
    sample = {
        "version": REASONING_TRACE_VERSION,
        "trace_id": "t1",
        "agent": {"id": "a", "name": "n"},
        "spans": [
            {
                "span_id": "s1",
                "kind": "reasoning",
                "name": "x",
                "start_time": "2026-05-26T00:00:00Z",
            }
        ],
    }
    result = check("my-framework", [sample])
    assert result.passed is False
    assert any("missing span kinds" in n for n in result.notes)


# ─────────────────────────────────────────────
# PRT-003 — Anonymized benchmark
# ─────────────────────────────────────────────


def test_anonymized_benchmark_requires_k_anonymity():
    b = AnonymizedBenchmark(k_anonymity_threshold=3)
    # Only 2 contributors report the same pattern — should NOT appear in report
    b.submit("c1", "tool_error", "rm -rf failed")
    b.submit("c2", "tool_error", "rm -rf failed")
    rep = b.report()
    assert (
        all(p.fingerprint != b._patterns[next(iter(b._patterns))].fingerprint for p in rep.patterns)
        or len(rep.patterns) == 0
    )


def test_anonymized_benchmark_aggregates_with_enough_contributors():
    b = AnonymizedBenchmark(k_anonymity_threshold=3)
    for c in ("c1", "c2", "c3", "c4"):
        b.submit(c, "timeout", "model_call_timeout")
    rep = b.report()
    assert rep.n_contributors == 4
    assert any(p.category == "timeout" for p in rep.patterns)


# ─────────────────────────────────────────────
# PRT-004 — MCP server
# ─────────────────────────────────────────────


def test_mcp_server_lists_default_tools():
    srv = AgentWatchMCPServer()
    catalog = srv.tool_catalog()
    names = {t["name"] for t in catalog}
    assert "panoptis_confidence_history" in names
    assert "panoptis_memory_query" in names
    assert "panoptis_session_replay" in names
    assert "panoptis_safety_status" in names


def test_mcp_server_dispatches_safety_status():
    srv = AgentWatchMCPServer()
    response = srv.dispatch("panoptis_safety_status")
    assert response.ok
    assert "status" in response.result


def test_mcp_server_uses_custom_provider():
    srv = AgentWatchMCPServer()
    srv.confidence_provider = lambda sid: [0.9, 0.85, 0.7]
    response = srv.dispatch("panoptis_confidence_history", {"session_id": "S"})
    assert response.ok
    assert response.result == [0.9, 0.85, 0.7]


def test_mcp_server_unknown_tool():
    srv = AgentWatchMCPServer()
    response = srv.dispatch("nonexistent")
    assert not response.ok
    assert "unknown tool" in (response.error or "")


@pytest.mark.asyncio
async def test_mcp_server_build_fastmcp():
    pytest.importorskip("mcp")
    srv = AgentWatchMCPServer()
    fastmcp = srv.build_fastmcp()

    tools = await fastmcp.list_tools()
    tool_names = {t.name for t in tools}
    assert "get_confidence_report" in tool_names
    assert "memory_query" in tool_names
    assert "get_session_replay" in tool_names
    assert "list_active_sessions" in tool_names
    assert "get_safety_events" in tool_names
    assert "get_cost_report" in tool_names
