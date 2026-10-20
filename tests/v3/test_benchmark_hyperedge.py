"""Reproducible benchmark for Hyperedge Relation Store (Issue #736).

Measures recursive provenance query latency over a store populated with > 100,000 events/relations,
evaluating the sub-2ms acceptance target.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time

import pytest
from sqlalchemy import func, insert, select

from agentwatch.graph.model import Basis, RelType, View, make_relation
from agentwatch.storage import schema as s
from agentwatch.storage.store import Store


def test_hyperedge_store_100k_events_benchmark(tmp_path):
    """Benchmark: > 100,000 events/relations with 50-step recursive provenance queries."""
    db_path = tmp_path / "bench_100k.db"
    url = f"sqlite:///{db_path.as_posix()}"
    store = Store(url)

    tenant_id = "bench-tenant"
    interp_id = "bench-interp"
    run_id = "bench-run-100k"

    total_events = 100_000
    chain_length = 60  # includes at least 50 steps

    print(f"\n[BENCHMARK] Generating {total_events:,} events and relations...")
    t0 = time.perf_counter()

    # Pre-generate batch data
    # 1. A 60-step deep linear derivation chain: step_0 -> step_1 -> ... -> step_59
    # 2. Remaining relations to exceed 100,000 items
    rel_rows = []
    mem_rows = []

    # Deep chain
    for i in range(chain_length - 1):
        rel_id = f"rel-chain-{i:05d}"
        tail_node = f"event:step-{i}"
        head_node = f"event:step-{i+1}"
        rel_doc = {
            "rel_id": rel_id,
            "view": "INFORMATION",
            "type": "PRODUCES" if i % 2 == 0 else "CONSUMES",
            "tail": [tail_node],
            "head": [head_node],
            "basis": "DECLARED",
            "evidence_class": None,
            "confidence": 1.0,
            "run_id": run_id,
            "derived_by": "bench",
            "attributes": {"step": i},
        }
        rel_rows.append(
            {
                "rel_id": rel_id,
                "interp_id": interp_id,
                "tenant_id": tenant_id,
                "run_id": run_id,
                "view": "INFORMATION",
                "type": rel_doc["type"],
                "basis": "DECLARED",
                "evidence_class": None,
                "confidence": 1.0,
                "doc": json.dumps(rel_doc),
            }
        )
        mem_rows.append({"rel_id": rel_id, "interp_id": interp_id, "role": "tail", "ordinal": 0, "node": tail_node})
        mem_rows.append({"rel_id": rel_id, "interp_id": interp_id, "role": "head", "ordinal": 0, "node": head_node})

    # Filler relations to reach > 100,000 items
    batch_size = 5000
    with store.engine.begin() as conn:
        # Insert chain first
        conn.execute(insert(s.relations), rel_rows)
        conn.execute(insert(s.relation_members), mem_rows)

        # Insert remainder in fast bulk chunks
        remaining = total_events - len(rel_rows)
        curr_id = len(rel_rows)
        while remaining > 0:
            chunk_n = min(remaining, batch_size)
            chunk_rels = []
            chunk_mems = []
            for j in range(chunk_n):
                idx = curr_id + j
                rid = f"rel-bulk-{idx:07d}"
                t_node = f"event:bulk-src-{idx}"
                h_node = f"event:bulk-dst-{idx}"
                doc = {
                    "rel_id": rid,
                    "view": "EXECUTION",
                    "type": "CONTAINS",
                    "tail": [t_node],
                    "head": [h_node],
                    "basis": "DECLARED",
                    "evidence_class": None,
                    "confidence": 1.0,
                    "run_id": run_id,
                    "derived_by": "bulk-gen",
                }
                chunk_rels.append(
                    {
                        "rel_id": rid,
                        "interp_id": interp_id,
                        "tenant_id": tenant_id,
                        "run_id": run_id,
                        "view": "EXECUTION",
                        "type": "CONTAINS",
                        "basis": "DECLARED",
                        "evidence_class": None,
                        "confidence": 1.0,
                        "doc": json.dumps(doc),
                    }
                )
                chunk_mems.append({"rel_id": rid, "interp_id": interp_id, "role": "tail", "ordinal": 0, "node": t_node})
                chunk_mems.append({"rel_id": rid, "interp_id": interp_id, "role": "head", "ordinal": 0, "node": h_node})

            conn.execute(insert(s.relations), chunk_rels)
            conn.execute(insert(s.relation_members), chunk_mems)
            remaining -= chunk_n
            curr_id += chunk_n

    pop_time = time.perf_counter() - t0
    print(f"[BENCHMARK] Populated {total_events:,} relations in {pop_time:.2f}s ({total_events / pop_time:,.0f} rel/s)")

    # Verify counts in database
    with store.engine.connect() as conn:
        rel_count = conn.execute(select(func.count()).select_from(s.relations)).scalar_one()
        mem_count = conn.execute(select(func.count()).select_from(s.relation_members)).scalar_one()

    assert rel_count >= total_events
    print(f"[BENCHMARK] Verified store size: {rel_count:,} relations, {mem_count:,} relation members.")

    # Target query: 50-step recursive backward provenance query from step-50 to step-0
    start_target = "event:step-50"
    num_samples = 30
    latencies_ms = []

    # Warmup
    warmup_steps = store.storage_traverse(
        interp_id, start_target, direction="reverse", tenant_id=tenant_id, max_depth=50
    )
    assert len(warmup_steps) == 50
    assert warmup_steps[-1].node == "event:step-0"

    # Measured runs
    for _ in range(num_samples):
        t_start = time.perf_counter()
        steps = store.storage_traverse(
            interp_id, start_target, direction="reverse", tenant_id=tenant_id, max_depth=50
        )
        t_end = time.perf_counter()
        latencies_ms.append((t_end - t_start) * 1000.0)
        assert len(steps) == 50

    latencies_ms.sort()
    mean_lat = statistics.mean(latencies_ms)
    median_lat = statistics.median(latencies_ms)
    p95_lat = latencies_ms[int(len(latencies_ms) * 0.95)]
    min_lat = min(latencies_ms)
    max_lat = max(latencies_ms)

    print("\n" + "=" * 60)
    print("HYPEREDGE RELATION STORE BENCHMARK RESULTS (Issue #736)")
    print("=" * 60)
    print(f"Environment: Python {sys.version.split()[0]} on {platform.system()} ({platform.machine()})")
    print(f"Backend: SQLite 3 (WAL mode) via SQLAlchemy Core")
    print(f"Dataset Size: {rel_count:,} relations ({mem_count:,} members)")
    print(f"Query: 50-step recursive backward provenance traversal ({start_target} -> event:step-0)")
    print(f"Sample Count: {num_samples} iterations")
    print(f"Latency (mean):   {mean_lat:.3f} ms")
    print(f"Latency (median): {median_lat:.3f} ms")
    print(f"Latency (p95):    {p95_lat:.3f} ms")
    print(f"Latency (min):    {min_lat:.3f} ms")
    print(f"Latency (max):    {max_lat:.3f} ms")
    print(f"Target sub-2ms met: {'YES' if mean_lat < 2.0 else 'NO'}")
    print("=" * 60 + "\n")

    store.close()
