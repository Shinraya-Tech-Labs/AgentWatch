"""Tests and Latency Benchmark for Distributed Hyperedge Relation Store (Issue #736 / ADR-0005).

Verifies:
1. Hyperedge data algebra across EXECUTION, INFORMATION, CAUSAL views.
2. Partitioned sharding by (tenant_id, run_id).
3. Bi-directional lineage query engine (backward origin, forward impact, derivation depth).
4. Lock-free append-only writes & immutable run sealing.
5. Benchmark validating sub-2ms query latency on 50-step recursive queries over 100,000+ relations.
"""

from __future__ import annotations

import concurrent.futures

import pytest

from agentwatch.storage.hyperedge import (
    Hyperedge,
    HyperedgeRelationStore,
    HyperedgeView,
    SealedRunError,
)


def _create_hyperedge(
    rel_id: str,
    tenant_id: str,
    run_id: str,
    view: str,
    tail: tuple[str, ...],
    head: tuple[str, ...],
    rel_type: str = "FLOWS_TO",
) -> Hyperedge:
    return Hyperedge(
        rel_id=rel_id,
        tenant_id=tenant_id,
        run_id=run_id,
        interp_id="interp_v3",
        view=view,
        rel_type=rel_type,
        tail=tail,
        head=head,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Hyperedge Data Algebra & 3-View Partitioning
# ═══════════════════════════════════════════════════════════════════════════════

def test_hyperedge_views_and_n_ary_vertices():
    store = HyperedgeRelationStore()

    # N-ary hyperedge connecting 2 inputs (tail) to 3 outputs (head) in INFORMATION view
    edge_info = _create_hyperedge(
        rel_id="rel_info_1",
        tenant_id="tenant_a",
        run_id="run_100",
        view=HyperedgeView.INFORMATION,
        tail=("art_doc_1", "art_prompt_1"),
        head=("art_summary_1", "art_summary_2", "art_embedding_1"),
        rel_type="SYNTHESIZED_FROM",
    )
    store.append(edge_info)

    # EXECUTION hyperedge
    edge_exec = _create_hyperedge(
        rel_id="rel_exec_1",
        tenant_id="tenant_a",
        run_id="run_100",
        view=HyperedgeView.EXECUTION,
        tail=("event_agent_call",),
        head=("event_tool_exec_1", "event_tool_exec_2"),
        rel_type="INVOKED",
    )
    store.append(edge_exec)

    # CAUSAL hyperedge
    edge_causal = _create_hyperedge(
        rel_id="rel_causal_1",
        tenant_id="tenant_a",
        run_id="run_100",
        view=HyperedgeView.CAUSAL,
        tail=("event_user_intent", "art_doc_1"),
        head=("event_decision_transfer",),
        rel_type="CAUSED",
    )
    store.append(edge_causal)

    shard = store.get_shard("tenant_a", "run_100")
    assert shard is not None
    assert len(shard.edges) == 3
    assert len(shard.view_edges[HyperedgeView.INFORMATION]) == 1
    assert len(shard.view_edges[HyperedgeView.EXECUTION]) == 1
    assert len(shard.view_edges[HyperedgeView.CAUSAL]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Partitioned Sharding by (tenant_id, run_id)
# ═══════════════════════════════════════════════════════════════════════════════

def test_partitioned_tenant_and_run_sharding():
    store = HyperedgeRelationStore()

    store.append(_create_hyperedge("e1", "tenant_alpha", "run_1", HyperedgeView.EXECUTION, ("a",), ("b",)))
    store.append(_create_hyperedge("e2", "tenant_alpha", "run_2", HyperedgeView.EXECUTION, ("c",), ("d",)))
    store.append(_create_hyperedge("e3", "tenant_beta", "run_1", HyperedgeView.EXECUTION, ("x",), ("y",)))

    shard_a1 = store.get_shard("tenant_alpha", "run_1")
    shard_a2 = store.get_shard("tenant_alpha", "run_2")
    shard_b1 = store.get_shard("tenant_beta", "run_1")

    assert shard_a1 is not None and len(shard_a1.edges) == 1
    assert shard_a2 is not None and len(shard_a2.edges) == 1
    assert shard_b1 is not None and len(shard_b1.edges) == 1
    assert "e1" in shard_a1.edges
    assert "e2" in shard_a2.edges
    assert "e3" in shard_b1.edges


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Bi-Directional Lineage Traversal & Derivation Depth
# ═══════════════════════════════════════════════════════════════════════════════

def test_bidirectional_lineage_and_impact():
    store = HyperedgeRelationStore()
    tenant = "tenant_lineage"
    run = "run_chain"

    # Linear chain with a branch:
    # root_input -> step_1 -> step_2 -> step_3 -> final_output
    # branch_input -> step_2
    edges = [
        _create_hyperedge("rel_1", tenant, run, HyperedgeView.INFORMATION, ("root_input",), ("step_1",)),
        _create_hyperedge("rel_2", tenant, run, HyperedgeView.INFORMATION, ("step_1", "branch_input"), ("step_2",)),
        _create_hyperedge("rel_3", tenant, run, HyperedgeView.INFORMATION, ("step_2",), ("step_3",)),
        _create_hyperedge("rel_4", tenant, run, HyperedgeView.INFORMATION, ("step_3",), ("final_output",)),
        _create_hyperedge("rel_other", tenant, run, HyperedgeView.EXECUTION, ("exec_node",), ("final_output",)),
    ]
    store.append_batch(tenant, run, edges)

    # 1. Backward origin query for final_output filtered to INFORMATION view
    lineage = store.backward_origin(tenant, run, "final_output", max_depth=10, views={HyperedgeView.INFORMATION})
    assert "root_input" in lineage.root_nodes
    assert "branch_input" in lineage.root_nodes
    assert "exec_node" not in lineage.root_nodes  # filtered out by view
    assert lineage.max_depth == 4

    # 2. Forward impact blast radius from branch_input
    impact = store.forward_impact_radius(tenant, run, "branch_input", max_depth=10)
    assert "step_2" in impact.affected_nodes
    assert "step_3" in impact.affected_nodes
    assert "final_output" in impact.affected_nodes
    assert "root_input" not in impact.affected_nodes

    # 3. Derivation depth calculation
    depth = store.derivation_depth(tenant, run, "final_output", views={HyperedgeView.INFORMATION})
    assert depth == 4


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Concurrency & Immutable Relation Sealing
# ═══════════════════════════════════════════════════════════════════════════════

def test_concurrent_appends_and_run_sealing():
    store = HyperedgeRelationStore()
    tenant = "tenant_conc"
    run = "run_conc"

    def worker(worker_id: int):
        for i in range(100):
            edge = _create_hyperedge(
                rel_id=f"rel_{worker_id}_{i}",
                tenant_id=tenant,
                run_id=run,
                view=HyperedgeView.EXECUTION,
                tail=(f"node_{worker_id}_{i}",),
                head=(f"node_{worker_id}_{i+1}",),
            )
            store.append(edge)

    # Concurrently append from 5 threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, w) for w in range(5)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    shard = store.get_shard(tenant, run)
    assert shard is not None
    assert len(shard.edges) == 500
    assert not store.is_run_sealed(tenant, run)

    # Seal the run
    store.seal_run(tenant, run)
    assert store.is_run_sealed(tenant, run)

    # Subsequent append must be rejected with SealedRunError
    with pytest.raises(SealedRunError):
        store.append(_create_hyperedge("late_rel", tenant, run, HyperedgeView.EXECUTION, ("a",), ("b",)))

    with pytest.raises(SealedRunError):
        store.append_batch(tenant, run, [_create_hyperedge("late_rel_batch", tenant, run, HyperedgeView.EXECUTION, ("a",), ("b",))])


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Latency Benchmark: Sub-2ms on 50-Step Recursive Query over 100,000+ Relations
# ═══════════════════════════════════════════════════════════════════════════════

def test_sub_2ms_benchmark_over_100k_relations():
    """Validates Issue #736 Acceptance Criteria:

    Benchmark validating sub-2ms query latency on 50-step recursive provenance queries
    over runs containing >100,000 events/relations.
    """
    store = HyperedgeRelationStore()
    tenant = "bench_tenant"
    run = "bench_run_100k"

    total_relations = 100_000
    batch_size = 10_000

    # Build deep 50-step backbone chain:
    # backbone_0 -> backbone_1 -> ... -> backbone_50
    backbone_edges = [
        _create_hyperedge(
            rel_id=f"bb_rel_{i}",
            tenant_id=tenant,
            run_id=run,
            view=HyperedgeView.INFORMATION,
            tail=(f"backbone_{i}",),
            head=(f"backbone_{i+1}",),
        )
        for i in range(50)
    ]

    # Pre-generate remaining bulk relations up to 100,000 relations
    bulk_needed = total_relations - len(backbone_edges)

    for b_start in range(0, bulk_needed, batch_size):
        b_count = min(batch_size, bulk_needed - b_start)
        batch = [
            _create_hyperedge(
                rel_id=f"bulk_rel_{b_start + j}",
                tenant_id=tenant,
                run_id=run,
                view=HyperedgeView.EXECUTION if j % 2 == 0 else HyperedgeView.INFORMATION,
                tail=(f"node_{b_start + j}",),
                head=(f"node_{b_start + j + 1}",),
            )
            for j in range(b_count)
        ]
        store.append_batch(tenant, run, batch)

    # Append backbone edges
    store.append_batch(tenant, run, backbone_edges)

    shard = store.get_shard(tenant, run)
    assert shard is not None
    assert len(shard.edges) >= 100_000

    # Execute 50-step recursive backward origin query from backbone_50
    # Warmup
    _ = store.backward_origin(tenant, run, "backbone_50", max_depth=50)

    # Measurement over multiple runs
    latencies: list[float] = []
    for _ in range(10):
        res = store.backward_origin(tenant, run, "backbone_50", max_depth=50)
        assert res.max_depth == 50
        assert "backbone_0" in res.root_nodes
        latencies.append(res.execution_time_ms)

    avg_latency_ms = sum(latencies) / len(latencies)
    min_latency_ms = min(latencies)

    # Must be strictly under 2.0ms per acceptance criteria
    assert avg_latency_ms < 2.0, f"Average latency {avg_latency_ms:.3f}ms exceeded 2.0ms threshold!"
    assert min_latency_ms < 2.0, f"Minimum latency {min_latency_ms:.3f}ms exceeded 2.0ms threshold!"
