"""Tests for Metric First-Order Temporal Logic (MFOTL) Engine (Issue #734 / Level 3).

Verifies:
1. First-Order syntax & AST parser (predicates, terms, comparisons, metric intervals, quantifiers).
2. Strong Kleene 3-valued valuation matrices.
3. Open-world missingness bounds on quantifiers (preservation of UNKNOWN vs collapsing to closed-world FALSE).
4. Combinatoric witness slicing (minimal event IDs, artifact IDs, variable bindings).
5. Join budget complexity bounds and RESOURCE_EXCEEDED fail-closed protection.
6. Differential testing against reference closed-world monitor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agentwatch.contracts.mfotl import (
    Always,
    And,
    Comparison,
    Const,
    Eventually,
    Exists,
    FieldAccess,
    Forall,
    Historically,
    Implies,
    MFOTLBudget,
    MFOTLParseError,
    Once,
    OpenWorldMFOTLMonitor,
    Predicate,
    ReferenceMFOTLMonitor,
    ResourceExceededError,
    Since,
    TraceDatabase,
    TriValued,
    Until,
    Var,
    kleene_and,
    kleene_implies,
    kleene_not,
    kleene_or,
    parse_mfotl,
)
from agentwatch.contracts.schema import EvidenceVerdict
from agentwatch.events.model import (
    ArtifactRef,
    ComputationalEvent,
    EventKind,
    EventStatus,
    TemporalInfo,
)


def _make_event(
    event_id: str,
    operation: str,
    dt_seconds: float,
    attributes: dict | None = None,
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
) -> ComputationalEvent:
    base_time = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    event_time = base_time + timedelta(seconds=dt_seconds)
    return ComputationalEvent(
        event_id=event_id,
        tenant_id="test_tenant",
        interp_id="test_interp",
        normalizer="test_norm",
        derived_from=(f"obs_{event_id}",),
        kind=EventKind.TOOL_INVOCATION,
        operation=operation,
        time=TemporalInfo(start=event_time, uncertainty_ms=0.0),
        status=EventStatus.OK,
        attributes_json=json.dumps(attributes or {}),
        inputs=tuple(ArtifactRef(artifact_id=d, role="input") for d in inputs),
        outputs=tuple(ArtifactRef(artifact_id=d, role="output") for d in outputs),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Strong Kleene 3-Valued Truth Tables
# ═══════════════════════════════════════════════════════════════════════════════

def test_kleene_logic_truth_tables():
    # Negation
    assert kleene_not(TriValued.TRUE) == TriValued.FALSE
    assert kleene_not(TriValued.FALSE) == TriValued.TRUE
    assert kleene_not(TriValued.UNKNOWN) == TriValued.UNKNOWN

    # Conjunction
    assert kleene_and(TriValued.TRUE, TriValued.TRUE) == TriValued.TRUE
    assert kleene_and(TriValued.TRUE, TriValued.FALSE) == TriValued.FALSE
    assert kleene_and(TriValued.FALSE, TriValued.TRUE) == TriValued.FALSE
    assert kleene_and(TriValued.FALSE, TriValued.UNKNOWN) == TriValued.FALSE  # FALSE dominates
    assert kleene_and(TriValued.UNKNOWN, TriValued.FALSE) == TriValued.FALSE
    assert kleene_and(TriValued.TRUE, TriValued.UNKNOWN) == TriValued.UNKNOWN
    assert kleene_and(TriValued.UNKNOWN, TriValued.UNKNOWN) == TriValued.UNKNOWN

    # Disjunction
    assert kleene_or(TriValued.TRUE, TriValued.FALSE) == TriValued.TRUE  # TRUE dominates
    assert kleene_or(TriValued.TRUE, TriValued.UNKNOWN) == TriValued.TRUE
    assert kleene_or(TriValued.UNKNOWN, TriValued.TRUE) == TriValued.TRUE
    assert kleene_or(TriValued.FALSE, TriValued.FALSE) == TriValued.FALSE
    assert kleene_or(TriValued.FALSE, TriValued.UNKNOWN) == TriValued.UNKNOWN

    # Implication (A implies B)
    assert kleene_implies(TriValued.FALSE, TriValued.FALSE) == TriValued.TRUE
    assert kleene_implies(TriValued.FALSE, TriValued.UNKNOWN) == TriValued.TRUE  # Ex falso quodlibet
    assert kleene_implies(TriValued.TRUE, TriValued.TRUE) == TriValued.TRUE
    assert kleene_implies(TriValued.TRUE, TriValued.FALSE) == TriValued.FALSE
    assert kleene_implies(TriValued.TRUE, TriValued.UNKNOWN) == TriValued.UNKNOWN
    assert kleene_implies(TriValued.UNKNOWN, TriValued.TRUE) == TriValued.TRUE


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Syntax & AST Parser Tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_parser_basic_predicates_and_terms():
    ast = parse_mfotl("Transfer(x, 500)")
    assert isinstance(ast, Predicate)
    assert ast.name == "Transfer"
    assert len(ast.args) == 2
    assert isinstance(ast.args[0], Var) and ast.args[0].name == "x"
    assert isinstance(ast.args[1], Const) and ast.args[1].value == 500

    ast2 = parse_mfotl("x.amount > 100")
    assert isinstance(ast2, Comparison)
    assert ast2.op == ">"
    assert isinstance(ast2.left, FieldAccess)
    assert ast2.left.field_name == "amount"
    assert isinstance(ast2.right, Const) and ast2.right.value == 100


def test_parser_quantifiers_and_implication():
    formula_str = "forall x. Transfer(x) implies exists y. Approval(y) and y.limit >= x.amount"
    ast = parse_mfotl(formula_str)

    assert isinstance(ast, Forall)
    assert ast.var == "x"
    sub = ast.formula
    assert isinstance(sub, Implies)
    assert isinstance(sub.antecedent, Predicate)
    assert sub.antecedent.name == "Transfer"

    consequent = sub.consequent
    assert isinstance(consequent, Exists)
    assert consequent.var == "y"
    and_node = consequent.formula
    assert isinstance(and_node, And)
    assert isinstance(and_node.left, Predicate)
    assert and_node.left.name == "Approval"
    assert isinstance(and_node.right, Comparison)
    assert and_node.right.op == ">="


def test_parser_metric_temporal_operators():
    # Past modalities
    ast1 = parse_mfotl("Once[0, 60] Auth(a)")
    assert isinstance(ast1, Once)
    assert ast1.interval.lower == 0.0
    assert ast1.interval.upper == 60.0
    assert isinstance(ast1.inner, Predicate)

    ast2 = parse_mfotl("Historically[0, 100.5] Safe(s)")
    assert isinstance(ast2, Historically)
    assert ast2.interval.lower == 0.0
    assert ast2.interval.upper == 100.5

    # Future modalities
    ast3 = parse_mfotl("Eventually[0, inf] Complete(c)")
    assert isinstance(ast3, Eventually)
    assert ast3.interval.lower == 0.0
    assert ast3.interval.upper == float("inf")

    ast4 = parse_mfotl("Always[10, 50] Invariant(i)")
    assert isinstance(ast4, Always)
    assert ast4.interval.lower == 10.0
    assert ast4.interval.upper == 50.0

    # Binary temporal: Since and Until
    ast5 = parse_mfotl("Valid(v) Since[0, 30] Start(s)")
    assert isinstance(ast5, Since)
    assert isinstance(ast5.left, Predicate) and ast5.left.name == "Valid"
    assert isinstance(ast5.right, Predicate) and ast5.right.name == "Start"

    ast6 = parse_mfotl("Lock(l) Until[0, 15] Release(r)")
    assert isinstance(ast6, Until)
    assert isinstance(ast6.left, Predicate) and ast6.left.name == "Lock"
    assert isinstance(ast6.right, Predicate) and ast6.right.name == "Release"


def test_parser_syntax_errors():
    with pytest.raises(MFOTLParseError):
        parse_mfotl("forall . Transfer(x)")  # missing var

    with pytest.raises(MFOTLParseError):
        parse_mfotl("Once[0 Transfer(x)")  # unclosed bracket

    with pytest.raises(MFOTLParseError):
        parse_mfotl("Transfer(x) and")  # trailing and


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Open-World Quantifier Semantics (Preserving UNKNOWN vs Closed World)
# ═══════════════════════════════════════════════════════════════════════════════

def test_open_world_existential_preservation():
    # Incomplete trace without matching approval
    events = [
        _make_event("e1", "Transfer", 10.0, {"amount": 250}),
    ]
    # Under open-world (coverage_complete=False)
    db_open = TraceDatabase(events, coverage_complete=False)
    monitor_open = OpenWorldMFOTLMonitor(db_open)

    formula = parse_mfotl("exists y. Approval(y)")
    res = monitor_open.evaluate(formula)

    # In open-world, lack of observed approval must remain UNKNOWN, not FALSE!
    assert res.value == TriValued.UNKNOWN
    assert res.value.to_verdict() == EvidenceVerdict.INCONCLUSIVE
    assert "open-world" in res.explanation

    # Under closed-world (coverage_complete=True)
    db_closed = TraceDatabase(events, coverage_complete=True)
    monitor_closed = OpenWorldMFOTLMonitor(db_closed)
    res_closed = monitor_closed.evaluate(formula)
    assert res_closed.value == TriValued.FALSE
    assert res_closed.value.to_verdict() == EvidenceVerdict.REFUTED


def test_open_world_existential_satisfaction():
    # Trace with matching approval
    events = [
        _make_event("e1", "Transfer", 10.0, {"amount": 250}),
        _make_event("e2", "Approval", 12.0, {"limit": 500}, inputs=("art_hash_1",)),
    ]
    db = TraceDatabase(events, coverage_complete=False)
    monitor = OpenWorldMFOTLMonitor(db)

    formula = parse_mfotl("exists y. Approval(y)")
    res = monitor.evaluate(formula)

    assert res.value == TriValued.TRUE
    assert "e2" in res.event_ids
    assert "art_hash_1" in res.artifact_ids


def test_open_world_universal_preservation_and_refutation():
    # All observed events are clean, but open-world means unobserved violations could exist
    events = [
        _make_event("e1", "Check", 10.0, {"valid": True}),
        _make_event("e2", "Check", 20.0, {"valid": True}),
    ]
    db_open = TraceDatabase(events, coverage_complete=False)
    monitor_open = OpenWorldMFOTLMonitor(db_open)

    formula = parse_mfotl("forall x. x.valid == true")
    res = monitor_open.evaluate(formula)
    assert res.value == TriValued.UNKNOWN

    # Closed-world confirms TRUE
    db_closed = TraceDatabase(events, coverage_complete=True)
    monitor_closed = OpenWorldMFOTLMonitor(db_closed)
    assert monitor_closed.evaluate(formula).value == TriValued.TRUE

    # A single counter-example refutes immediately even in open-world!
    events_with_violation = [
        _make_event("e1", "Check", 10.0, {"valid": True}),
        _make_event("e2", "Check", 20.0, {"valid": False}),
    ]
    db_violation = TraceDatabase(events_with_violation, coverage_complete=False)
    monitor_violation = OpenWorldMFOTLMonitor(db_violation)
    res_violation = monitor_violation.evaluate(formula)
    assert res_violation.value == TriValued.FALSE
    assert "e2" in res_violation.event_ids  # Minimal counter-example


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Metric Temporal Logic Verification & Witness Slicing
# ═══════════════════════════════════════════════════════════════════════════════

def test_metric_temporal_once_and_historically():
    events = [
        _make_event("e1", "Auth", 10.0, {"user": "alice"}),
        _make_event("e2", "Transfer", 25.0, {"amount": 100}),
    ]
    db = TraceDatabase(events, coverage_complete=True)
    monitor = OpenWorldMFOTLMonitor(db)

    # At step 1 (Transfer at 25s), did Auth happen within [0, 20] seconds?
    # Delta = 25 - 10 = 15s, which is within [0, 20]
    formula = parse_mfotl("Once[0, 20] Auth(a)")
    res = monitor.evaluate_at(formula, 1)
    assert res.value == TriValued.TRUE
    assert "e1" in res.event_ids

    # Did Auth happen within [0, 5] seconds? Delta=15s > 5s -> False
    formula_tight = parse_mfotl("Once[0, 5] Auth(a)")
    res_tight = monitor.evaluate_at(formula_tight, 1)
    assert res_tight.value == TriValued.FALSE


def test_metric_temporal_since():
    events = [
        _make_event("e0", "Login", 0.0),
        _make_event("e1", "ActiveSession", 10.0),
        _make_event("e2", "ActiveSession", 20.0),
        _make_event("e3", "Transfer", 30.0),
    ]
    db = TraceDatabase(events, coverage_complete=True)
    monitor = OpenWorldMFOTLMonitor(db)

    # ActiveSession has held Since Login within 40 seconds
    formula = parse_mfotl("ActiveSession(s) Since[0, 40] Login(l)")
    res = monitor.evaluate_at(formula, 2)
    assert res.value == TriValued.TRUE


def test_witness_slicing_minimal_extraction():
    events = [
        _make_event("e1", "Audit", 5.0, {"level": 1}),
        _make_event("e2", "Transfer", 10.0, {"amount": 100}),
        _make_event("e3", "Transfer", 20.0, {"amount": 500}),
        _make_event("e4", "Approval", 22.0, {"limit": 1000}),
    ]
    db = TraceDatabase(events, coverage_complete=True)
    monitor = OpenWorldMFOTLMonitor(db)

    # Implication satisfied: e4 approvals limit >= 500
    formula = parse_mfotl("forall x. Transfer(x) implies exists y. Approval(y) and y.limit >= x.amount")
    res = monitor.evaluate(formula)
    assert res.value == TriValued.TRUE
    # Slice should contain the relevant events
    assert "e2" in res.event_ids or "e3" in res.event_ids


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Complexity & Resource Bounding (Fail-Closed RESOURCE_EXCEEDED)
# ═══════════════════════════════════════════════════════════════════════════════

def test_resource_budget_fail_closed():
    # Build trace of 50 events
    events = [_make_event(f"e{i}", "Step", float(i)) for i in range(50)]
    db = TraceDatabase(events, coverage_complete=True)

    # Set extremely low join budget (e.g. 5 join operations)
    tight_budget = MFOTLBudget(max_join_tuples=5, fail_closed=True)
    monitor = OpenWorldMFOTLMonitor(db, budget=tight_budget)

    formula = parse_mfotl("forall x. exists y. Step(y)")
    res = monitor.evaluate(formula)

    # Must fail closed with UNKNOWN and resource_exceeded flag set!
    assert res.value == TriValued.UNKNOWN
    assert res.resource_exceeded is True
    assert "Join cardinality exceeded" in res.explanation


def test_resource_budget_raise_exception():
    events = [_make_event(f"e{i}", "Step", float(i)) for i in range(50)]
    db = TraceDatabase(events, coverage_complete=True)

    budget = MFOTLBudget(max_join_tuples=5, fail_closed=False)
    monitor = OpenWorldMFOTLMonitor(db, budget=budget)

    formula = parse_mfotl("forall x. exists y. Step(y)")
    with pytest.raises(ResourceExceededError):
        monitor.evaluate(formula)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Differential Verification Against Reference Monitor
# ═══════════════════════════════════════════════════════════════════════════════

def test_differential_verification_on_complete_traces():
    events = [
        _make_event("e1", "Auth", 0.0, {"role": "admin"}),
        _make_event("e2", "Action", 5.0, {"type": "read"}),
        _make_event("e3", "Action", 10.0, {"type": "write"}),
        _make_event("e4", "Audit", 12.0, {"status": "ok"}),
    ]
    # Complete trace (closed world)
    db = TraceDatabase(events, coverage_complete=True)
    open_monitor = OpenWorldMFOTLMonitor(db)
    ref_monitor = ReferenceMFOTLMonitor(db)

    test_formulas = [
        "exists x. Auth(x)",
        "forall x. Action(x) implies Once[0, 15] Auth(a)",
        "exists x. x.role == \"admin\"",
        "not (exists x. x.role == \"superadmin\")",
        "Historically[0, 20] not (Action(a) and a.type == \"delete\")",
    ]

    for f_str in test_formulas:
        formula = parse_mfotl(f_str)
        open_res = open_monitor.evaluate(formula)
        ref_res = ref_monitor.evaluate(formula)

        # On complete traces, OpenWorld monitor must strictly match Reference monitor!
        assert (open_res.value == TriValued.TRUE) == ref_res, (
            f"Differential mismatch on '{f_str}': open={open_res.value}, ref={ref_res}"
        )
