"""Tests for the Hyperedge Relation Store (Issue #736).

Covers:
- Typed hyperedge storage (n-ary tail[] and head[], views, basis, confidence, evidence)
- ADR-0005: Three views, CAUSAL relation evidence class enforcement
- Indexed forward and reverse adjacency lookups
- Bidirectional lineage engine: backward origin, forward dependents, impact radius
- Cycle handling, repeated vertices, bounded depth, and deterministic results
- Tenant and run isolation
- Concurrent / idempotent appends
- Run sealing and rejection of mutations to sealed runs
- Schema upgrade compatibility
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from agentwatch.graph.model import (
    Basis,
    EvidenceClass,
    RelType,
    View,
    make_relation,
)
from agentwatch.storage import schema
from agentwatch.storage.store import SealedRunError, Store


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{(tmp_path / 'hyperedge_test.db').as_posix()}"
    s = Store(url)
    yield s
    s.close()


def test_typed_hyperedge_storage_and_retrieval(store):
    """Test n-ary tail and head hyperedge persistence and retrieval."""
    tenant = "tenant-test"
    interp = "interp-test"
    run_id = "run-001"

    # Multi-tail and multi-head hyperedge: 3 inputs aggregated into 2 outputs
    rel = make_relation(
        View.INFORMATION,
        RelType.DERIVES_FROM,
        tail=["artifact:art-01", "artifact:art-02", "artifact:art-03"],
        head=["artifact:out-A", "artifact:out-B"],
        basis=Basis.DECLARED,
        confidence=0.95,
        run_id=run_id,
        derived_by="test-aggregator",
        evidence=["obs-101", "obs-102"],
        attributes={"strategy": "ensemble", "strength": "STRONG"},
    )

    inserted = store.put_relations(tenant, interp, [rel])
    assert inserted == 1

    # Retrieve by ID
    stored = store.get_relation(interp, rel["rel_id"], tenant_id=tenant)
    assert stored is not None
    assert stored["rel_id"] == rel["rel_id"]
    assert stored["view"] == "INFORMATION"
    assert stored["type"] == "DERIVES_FROM"
    assert stored["tail"] == ["artifact:art-01", "artifact:art-02", "artifact:art-03"]
    assert stored["head"] == ["artifact:out-A", "artifact:out-B"]
    assert stored["confidence"] == 0.95
    assert stored["basis"] == "DECLARED"
    assert stored["attributes"]["strategy"] == "ensemble"

    # List relations
    all_rels = store.relations(interp, tenant_id=tenant, run_id=run_id)
    assert len(all_rels) == 1
    assert all_rels[0]["rel_id"] == rel["rel_id"]


def test_three_views_and_causal_evidence_enforcement(store):
    """Test ADR-0005 rules across the three views and causal evidence class requirement."""
    tenant = "tenant-test"
    interp = "interp-test"

    # 1. EXECUTION view
    exec_rel = make_relation(
        View.EXECUTION,
        RelType.CONTAINS,
        tail=["event:e-parent"],
        head=["event:e-child"],
        basis=Basis.DECLARED,
        run_id="run-exec",
        derived_by="normalizer",
    )
    store.put_relations(tenant, interp, [exec_rel])

    # 2. INFORMATION view
    info_rel = make_relation(
        View.INFORMATION,
        RelType.PRODUCES,
        tail=["event:e-child"],
        head=["artifact:art-1"],
        basis=Basis.DECLARED,
        run_id="run-exec",
        derived_by="normalizer",
    )
    store.put_relations(tenant, interp, [info_rel])

    # 3. CAUSAL view with required evidence class
    causal_rel = make_relation(
        View.CAUSAL,
        RelType.CAUSES,
        tail=["event:e-cause"],
        head=["event:e-effect"],
        basis=Basis.INTERVENTIONAL,
        evidence_class=EvidenceClass.INTERVENTIONAL,
        confidence=1.0,
        run_id="run-exec",
        derived_by="causal-engine",
    )
    store.put_relations(tenant, interp, [causal_rel])

    # 4. CAUSAL relation without evidence class must fail
    with pytest.raises(ValueError, match="CAUSAL relations require an evidence class"):
        make_relation(
            View.CAUSAL,
            RelType.CAUSES,
            tail=["event:e-1"],
            head=["event:e-2"],
            basis=Basis.HEURISTIC,
            run_id="run-exec",
            derived_by="bad-engine",
            evidence_class=None,
        )

    # 5. Causal relation type in non-causal view must fail
    with pytest.raises(ValueError, match="belongs to the CAUSAL view"):
        make_relation(
            View.EXECUTION,
            RelType.CAUSES,
            tail=["event:e-1"],
            head=["event:e-2"],
            basis=Basis.DECLARED,
            run_id="run-exec",
            derived_by="bad-engine",
        )

    # Verify filtering by view
    exec_rels = store.relations(interp, tenant_id=tenant, view="EXECUTION")
    assert len(exec_rels) == 1
    assert exec_rels[0]["type"] == "CONTAINS"

    info_rels = store.relations(interp, tenant_id=tenant, view="INFORMATION")
    assert len(info_rels) == 1
    assert info_rels[0]["type"] == "PRODUCES"

    causal_rels = store.relations(interp, tenant_id=tenant, view="CAUSAL")
    assert len(causal_rels) == 1
    assert causal_rels[0]["type"] == "CAUSES"
    assert causal_rels[0]["evidence_class"] == "INTERVENTIONAL"


def test_indexed_forward_and_reverse_adjacency(store):
    """Test indexed forward (tail-to-head) and reverse (head-to-tail) adjacency queries."""
    tenant = "tenant-adj"
    interp = "interp-adj"
    run_id = "run-adj"

    # Construct hyperedges:
    # R1: {A, B} -> {C, D}
    # R2: {C} -> {E}
    r1 = make_relation(
        View.INFORMATION,
        RelType.TRANSFERS,
        tail=["artifact:A", "artifact:B"],
        head=["artifact:C", "artifact:D"],
        basis=Basis.DECLARED,
        run_id=run_id,
        derived_by="pipeline",
    )
    r2 = make_relation(
        View.INFORMATION,
        RelType.DERIVES_FROM,
        tail=["artifact:C"],
        head=["artifact:E"],
        basis=Basis.DECLARED,
        run_id=run_id,
        derived_by="pipeline",
    )
    store.put_relations(tenant, interp, [r1, r2])

    # Forward from A: should find R1 and heads {C, D}
    fwd_a = store.adjacent_relations(interp, "artifact:A", direction="forward", tenant_id=tenant)
    assert len(fwd_a) == 1
    rel_doc, heads = fwd_a[0]
    assert rel_doc["rel_id"] == r1["rel_id"]
    assert set(heads) == {"artifact:C", "artifact:D"}

    # Forward from C: should find R2 and heads {E}
    fwd_c = store.adjacent_relations(interp, "artifact:C", direction="forward", tenant_id=tenant)
    assert len(fwd_c) == 1
    rel_doc, heads = fwd_c[0]
    assert rel_doc["rel_id"] == r2["rel_id"]
    assert heads == ["artifact:E"]

    # Reverse from E: should find R2 and tails {C}
    rev_e = store.adjacent_relations(interp, "artifact:E", direction="reverse", tenant_id=tenant)
    assert len(rev_e) == 1
    rel_doc, tails = rev_e[0]
    assert rel_doc["rel_id"] == r2["rel_id"]
    assert tails == ["artifact:C"]

    # Reverse from D: should find R1 and tails {A, B}
    rev_d = store.adjacent_relations(interp, "artifact:D", direction="reverse", tenant_id=tenant)
    assert len(rev_d) == 1
    rel_doc, tails = rev_d[0]
    assert rel_doc["rel_id"] == r1["rel_id"]
    assert set(tails) == {"artifact:A", "artifact:B"}


def test_bidirectional_lineage_and_impact_radius(store):
    """Test multi-hop backward origin lineage and forward impact-radius queries."""
    tenant = "tenant-lin"
    interp = "interp-lin"
    run_id = "run-lin"

    # Lineage chain: In -> e1 -> art1 -> e2 -> Out
    # rel1: In -> e1
    # rel2: e1 -> art1
    # rel3: art1 -> e2
    # rel4: e2 -> Out
    rels = [
        make_relation(View.INFORMATION, RelType.CONSUMES, ["artifact:In"], ["event:e1"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
        make_relation(View.INFORMATION, RelType.PRODUCES, ["event:e1"], ["artifact:art1"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
        make_relation(View.INFORMATION, RelType.CONSUMES, ["artifact:art1"], ["event:e2"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
        make_relation(View.INFORMATION, RelType.PRODUCES, ["event:e2"], ["artifact:Out"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
    ]
    store.put_relations(tenant, interp, rels)

    # 1. Backward lineage from Out
    steps_back = store.storage_traverse(interp, "artifact:Out", direction="reverse", tenant_id=tenant)
    nodes_back = [s.node for s in steps_back]
    assert nodes_back == ["event:e2", "artifact:art1", "event:e1", "artifact:In"]
    depths = [s.depth for s in steps_back]
    assert depths == [1, 2, 3, 4]

    # 2. Forward dependents from In
    steps_fwd = store.storage_traverse(interp, "artifact:In", direction="forward", tenant_id=tenant)
    nodes_fwd = [s.node for s in steps_fwd]
    assert nodes_fwd == ["event:e1", "artifact:art1", "event:e2", "artifact:Out"]

    # 3. Impact radius from In
    radius = store.impact_radius(interp, "artifact:In", tenant_id=tenant)
    assert radius["root"] == "artifact:In"
    assert radius["radius"] == 4
    assert radius["reachable_count"] == 4
    assert set(radius["reachable_nodes"]) == {"event:e1", "artifact:art1", "event:e2", "artifact:Out"}


def test_cycles_repeated_vertices_and_bounded_depth(store):
    """Test cycle prevention, repeated vertices handling, and depth bounding."""
    tenant = "tenant-cyc"
    interp = "interp-cyc"
    run_id = "run-cyc"

    # Cycle: N1 -> N2 -> N3 -> N1
    rels = [
        make_relation(View.EXECUTION, RelType.DEPENDS_ON, ["node:N1"], ["node:N2"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
        make_relation(View.EXECUTION, RelType.DEPENDS_ON, ["node:N2"], ["node:N3"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
        make_relation(View.EXECUTION, RelType.DEPENDS_ON, ["node:N3"], ["node:N1"], basis=Basis.DECLARED, run_id=run_id, derived_by="test"),
    ]
    store.put_relations(tenant, interp, rels)

    # Traversal should terminate cleanly without infinite loop
    steps = store.storage_traverse(interp, "node:N1", direction="forward", tenant_id=tenant, max_depth=10)
    visited = [s.node for s in steps]
    assert visited == ["node:N2", "node:N3"]  # N1 already in seen set

    # Test depth limit
    steps_depth_1 = store.storage_traverse(interp, "node:N1", direction="forward", tenant_id=tenant, max_depth=1)
    assert len(steps_depth_1) == 1
    assert steps_depth_1[0].node == "node:N2"


def test_tenant_and_run_isolation(store):
    """Test that relations are partitioned by tenant_id and run_id."""
    interp = "shared-interp"

    # Tenant A
    rel_a = make_relation(
        View.EXECUTION,
        RelType.DEPENDS_ON,
        ["node:A1"],
        ["node:A2"],
        basis=Basis.DECLARED,
        run_id="run-A",
        derived_by="test",
    )
    store.put_relations("tenant-A", interp, [rel_a])

    # Tenant B
    rel_b = make_relation(
        View.EXECUTION,
        RelType.DEPENDS_ON,
        ["node:B1"],
        ["node:B2"],
        basis=Basis.DECLARED,
        run_id="run-B",
        derived_by="test",
    )
    store.put_relations("tenant-B", interp, [rel_b])

    # Tenant A cannot see Tenant B's relations
    assert store.get_relation(interp, rel_b["rel_id"], tenant_id="tenant-A") is None
    rels_a = store.relations(interp, tenant_id="tenant-A")
    assert len(rels_a) == 1
    assert rels_a[0]["rel_id"] == rel_a["rel_id"]

    # Adjacency for node:B1 under Tenant A yields nothing
    fwd_b_from_a = store.adjacent_relations(interp, "node:B1", direction="forward", tenant_id="tenant-A")
    assert len(fwd_b_from_a) == 0

    # But yields result under Tenant B
    fwd_b_from_b = store.adjacent_relations(interp, "node:B1", direction="forward", tenant_id="tenant-B")
    assert len(fwd_b_from_b) == 1


def test_concurrent_idempotent_appends(store):
    """Test idempotent relation insertions without duplicating rows or raising PK conflicts."""
    tenant = "tenant-idem"
    interp = "interp-idem"

    rel = make_relation(
        View.INFORMATION,
        RelType.PRODUCES,
        ["event:e-idem"],
        ["artifact:art-idem"],
        basis=Basis.DECLARED,
        run_id="run-idem",
        derived_by="test",
    )

    # First insert
    count1 = store.put_relations(tenant, interp, [rel])
    assert count1 == 1

    # Second insert with same relation (idempotent / concurrent retry)
    count2 = store.put_relations(tenant, interp, [rel])
    assert count2 == 0

    # Still exactly 1 relation in store
    assert len(store.relations(interp, tenant_id=tenant)) == 1


def test_run_sealing_and_mutation_rejection(store):
    """Test that sealed runs cannot be mutated or deleted through relation APIs."""
    tenant = "tenant-seal"
    interp = "interp-seal"
    run_id = "run-sealed-1"

    rel1 = make_relation(
        View.EXECUTION,
        RelType.CONTAINS,
        ["event:parent"],
        ["event:child"],
        basis=Basis.DECLARED,
        run_id=run_id,
        derived_by="test",
    )
    store.put_relations(tenant, interp, [rel1])
    assert not store.is_run_sealed(tenant, interp, run_id)

    # Seal the run
    store.seal_run(tenant, interp, run_id)
    assert store.is_run_sealed(tenant, interp, run_id)

    # Attempting to add new relations to the sealed run must fail
    rel2 = make_relation(
        View.EXECUTION,
        RelType.DEPENDS_ON,
        ["event:child"],
        ["event:child2"],
        basis=Basis.DECLARED,
        run_id=run_id,
        derived_by="test",
    )
    with pytest.raises(SealedRunError, match="sealed"):
        store.put_relations(tenant, interp, [rel2])

    # Attempting to delete relations of the sealed run must fail
    with pytest.raises(SealedRunError, match="sealed"):
        store.delete_relations_for_run(tenant, interp, run_id)

    # Relations still intact
    assert len(store.relations(interp, tenant_id=tenant, run_id=run_id)) == 1


def test_schema_upgrade_v2_to_v3(tmp_path):
    """Test that existing v2 stores are upgraded in place to v3 with sealed_runs table and new indexes."""
    url = f"sqlite:///{(tmp_path / 'v2_upgrade.db').as_posix()}"
    store = Store(url)
    assert store.is_run_sealed("default", "interp-1", "run-1") is False

    # Simulate older v2 database by dropping aw3_sealed_runs and updating schema_version to 2
    with store.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS aw3_sealed_runs"))
        conn.execute(text("UPDATE aw3_meta SET value = '2' WHERE key = 'schema_version'"))
    store.close()

    # Reopen store: Store.init() should automatically upgrade schema to v3
    upgraded = Store(url)
    with upgraded.engine.connect() as conn:
        ver = conn.execute(text("SELECT value FROM aw3_meta WHERE key = 'schema_version'")).scalar()
        assert ver == str(schema.SCHEMA_VERSION)
        assert int(ver) >= 3

    # aw3_sealed_runs works properly on the upgraded store
    upgraded.seal_run("default", "interp-1", "run-1")
    assert upgraded.is_run_sealed("default", "interp-1", "run-1") is True
    upgraded.close()
