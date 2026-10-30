"""
Tests for MEM-008 — natural-language causal-graph traversal.

Mirrors the style of the causal-graph and nlquery tests in test_memory.py:
build a small causal graph, ask plain-English questions, and assert that the
question compiles into the correct traversal (upstream vs downstream) and
surfaces the right nodes.
"""

from __future__ import annotations

from panoptis.memory.causal_graph import CausalGraph, CausalNode, EdgeKind
from panoptis.memory.graph_query import (
    GraphQueryResult,
    TraversalDirection,
    parse,
    query,
)


def _sample_graph() -> CausalGraph:
    """The canonical 'why Postgres' scenario from the issue description."""
    g = CausalGraph()
    ctx = CausalNode(
        node_id="c1",
        kind="context",
        text="app needs ACID transactions and JSON support",
    )
    constraint = CausalNode(node_id="x1", kind="constraint", text="team already knows Postgres")
    decision = CausalNode(node_id="d1", kind="decision", text="choose Postgres over MySQL")
    outcome = CausalNode(node_id="o1", kind="outcome", text="migrations ran smoothly in production")
    for n in (ctx, constraint, decision, outcome):
        g.add_node(n)
    g.add_edge("c1", "d1", EdgeKind.CAUSED_BY)
    g.add_edge("x1", "d1", EdgeKind.CONSTRAINED_BY)
    g.add_edge("d1", "o1", EdgeKind.PRODUCED)
    return g


def test_parse_detects_upstream_for_why():
    q = parse("Why did we choose Postgres over MySQL in the previous session?")
    assert q.direction is TraversalDirection.UPSTREAM
    assert "postgres" in q.keywords


def test_parse_detects_downstream_for_outcome():
    q = parse("What happened as a result of choosing Postgres?")
    assert q.direction is TraversalDirection.DOWNSTREAM


def test_parse_detects_edge_filter():
    q = parse("What constraints shaped the Postgres decision?")
    assert q.edge_filter is EdgeKind.CONSTRAINED_BY


def test_why_question_traverses_upstream_to_causes():
    g = _sample_graph()
    result = query("Why did we choose Postgres over MySQL in the previous session?", g)
    assert isinstance(result, GraphQueryResult)
    assert result.direction is TraversalDirection.UPSTREAM
    assert result.entry_node is not None
    assert result.entry_node.node_id == "d1"
    assert result.answered
    path_ids = {step.node.node_id for step in result.path}
    # The causes of the decision are the context and the constraint.
    assert {"c1", "x1"} <= path_ids


def test_what_happened_question_traverses_downstream_to_outcome():
    g = _sample_graph()
    result = query("What happened as a result of choosing Postgres?", g)
    assert result.direction is TraversalDirection.DOWNSTREAM
    assert result.answered
    assert result.entry_node is not None
    assert result.entry_node.node_id == "d1"
    assert any(step.node.node_id == "o1" for step in result.path)


def test_edge_filter_narrows_to_constraint():
    g = _sample_graph()
    result = query("What constraints shaped the Postgres decision?", g)
    # The constraint filter must narrow to exactly the constraint node — no
    # other upstream nodes (e.g. the context node c1) may leak through.
    assert {step.node.node_id for step in result.path} == {"x1"}


def test_irrelevant_question_is_not_answered():
    g = _sample_graph()
    result = query(
        "What is the airspeed velocity of an unladen swallow?",
        g,
        min_match=0.15,
    )
    assert not result.answered
    assert result.entry_node is None
    assert result.path == []


def test_empty_graph_returns_unanswered():
    g = CausalGraph()
    result = query("Why did we choose Postgres?", g)
    assert not result.answered
    assert result.path == []


def test_result_to_dict_is_serializable():
    g = _sample_graph()
    result = query("Why did we choose Postgres over MySQL?", g)
    payload = result.to_dict()
    assert payload["direction"] == "upstream"
    assert payload["answered"] is True
    assert payload["entry_node"]["node_id"] == "d1"
    assert isinstance(payload["path"], list)
    assert payload["path"] and "node_id" in payload["path"][0]


def test_summary_renders_path():
    g = _sample_graph()
    result = query("Why did we choose Postgres over MySQL?", g)
    summary = result.summary()
    assert "Postgres" in summary
    # Upstream rendering uses the left arrow.
    assert "←" in summary
