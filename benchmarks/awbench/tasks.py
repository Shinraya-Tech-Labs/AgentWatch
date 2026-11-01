"""AWBench evaluation tasks. Each returns {"metrics": {...}, "n": ..., "notes": ...}.

Matching between AgentWatch events and ground-truth nodes uses only the opaque
``awbench_id`` span attribute. No AgentWatch module reads that attribute.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from panoptis.behaviour.drift import drift
from panoptis.compare.runs import compare, signature
from panoptis.lab.branch import counterfactual
from panoptis.lab.replay import replay
from panoptis.query.engine import ask
from panoptis.query.workspace import Workspace
from panoptis.runtime.engine import Engine

# information-flow relations (MATCHES_CONTENT is similarity, not flow; CANDIDATE_SOURCE is an
# ambiguous candidate, not flow — both excluded, as in AgentWatch's own default traversal)
FLOW_TYPES = ["PRODUCES", "CONSUMES", "DERIVES_FROM", "CONTAINS_ITEM", "TRANSFERS"]


def _produced_by(node: str) -> str | None:
    """Event that produced an information node: ``inst:<event>/o..`` (an output or an item of
    one). Constructed-input nodes (``/in``) belong to their consumer, reached as an event."""
    if node.startswith("inst:") and "/in" not in node:
        return node[5:].split("/", 1)[0]
    return None


LEAF = {
    "TOOL_INVOCATION",
    "MODEL_INVOCATION",
    "RETRIEVAL",
    "MEMORY_ACCESS",
    "MESSAGE",
    "DELEGATION",
    "STATE_MUTATION",
    "OPERATION",
    "EXTERNAL_IO",
}


def awb_id(e: dict[str, Any]) -> str | None:
    a = e["attributes"]
    return a.get("awbench_id") or (a.get("otel.attributes") or {}).get("awbench_id")


def id_map(ws: Workspace, run_id: str) -> dict[str, dict[str, Any]]:
    return {i: e for e in ws.events(run_id) if (i := awb_id(e))}


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def closure(edges: list[list[str]]) -> set[tuple[str, str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
    out: set[tuple[str, str]] = set()
    for start in list(adj):
        stack, seen = [start], set()
        while stack:
            n = stack.pop()
            for m in adj.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    out.add((start, m))
                    stack.append(m)
    return out


def evaluate_all(
    ws: Workspace, engine: Engine, records: list[Any], drift_n: int, *, include_drift: bool = True
) -> dict[str, Any]:
    native = [r for r in records if r.sensor == "native"]
    out = {
        "h1_normalization": h1(ws, records),
        "h2_structure": h2(ws, native),
        "h3_lineage": h3(ws, native),
        "h4_divergence": h4(ws, native),
        "h5_motifs": h5(ws, native),
    }
    if include_drift:
        out["h7_drift"] = h7(ws, native, drift_n)
    out.update(
        {
            "replay_fidelity": replay_fidelity(ws, engine, native),
            "counterfactual_quality": counterfactual_quality(engine, native),
            "causal_hypotheses": causal_hypotheses(engine, native),
            "explanation_faithfulness": faithfulness(ws, native),
        }
    )
    return out


def evaluate_held_out(ws: Workspace, engine: Engine, records: list[Any]) -> dict[str, Any]:
    """Tasks that apply to the held-out architecture (no drift or cross-source sets)."""
    native = [r for r in records if r.sensor == "native"]
    return {
        "h2_structure": h2(ws, native),
        "h3_lineage": h3(ws, native),
        "h4_divergence": h4(ws, native),
        "h5_motifs": h5(ws, native),
        "replay_fidelity": replay_fidelity(ws, engine, native),
        "counterfactual_quality": counterfactual_quality(engine, native),
        "explanation_faithfulness": faithfulness(ws, native),
    }


def _primary(records: list[Any]) -> int:
    """Lowest seed present: the seed used by the expensive lab/faithfulness tasks."""
    return min((r.seed for r in records), default=0)


def _baseline(records: list[Any], rec: Any) -> Any | None:
    return next(
        (
            r
            for r in records
            if r.arch == rec.arch
            and r.seed == rec.seed
            and r.scenario == "normal"
            and r.sensor == rec.sensor
        ),
        None,
    )


def h1(ws: Workspace, records: list[Any]) -> dict[str, Any]:
    total = unknown = 0
    for r in records:
        for e in ws.events(r.run_id):
            if e["kind"] == "LIFECYCLE":
                continue
            total += 1
            unknown += e["kind"] == "UNKNOWN"
    pairs: dict[str, list[float]] = {"otel": [], "otel_enriched": []}
    for r in records:
        if r.sensor not in pairs:
            continue
        nat = _baseline(
            [x for x in records if x.sensor == "native"],
            type("R", (), {"arch": r.arch, "seed": r.seed, "sensor": "native"})(),
        )
        if nat is None:
            continue
        a = {(i, e["kind"]) for i, e in id_map(ws, nat.run_id).items()}
        b = {(i, e["kind"]) for i, e in id_map(ws, r.run_id).items()}
        pairs[r.sensor].append(prf(len(a & b), len(b - a), len(a - b))[2])

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "metrics": {
            "kind_coverage": round(1 - unknown / total, 4) if total else None,
            "cross_source_kind_f1": mean(pairs["otel"]),
            "cross_source_kind_f1_enriched": mean(pairs["otel_enriched"]),
        },
        "n": {
            "events": total,
            "cross_source_pairs": len(pairs["otel"]),
            "enriched_pairs": len(pairs["otel_enriched"]),
        },
        "notes": "cross-source compares the same program instrumented natively vs via OpenTelemetry "
        "GenAI spans (OTel-only, thresholded) and vs OpenTelemetry plus AgentWatch's own extension "
        "attributes (ADR-0018; reported, not thresholded — the extension is AgentWatch's convention, "
        "so its score measures AgentWatch's mapping, not interoperability with OTel as specified)",
    }


def h2(ws: Workspace, records: list[Any]) -> dict[str, Any]:
    tp = fp = fn = 0
    btp = bfp = bfn = 0
    itp = ifp = ifn = 0
    ctp = cfp = cfn = 0
    for r in records:
        ids = id_map(ws, r.run_id)
        by_event = {e["event_id"]: i for i, e in ids.items()}
        gt_exec = {tuple(x) for x in r.gt["exec_edges"] if x[0] in ids and x[1] in ids}
        g = ws.graph(r.run_id)
        aw_exec = set()
        for rel in g.relations.values():
            if rel["type"] in ("CONTAINS", "DEPENDS_ON") and rel["basis"] == "DECLARED":
                for t in rel["tail"]:
                    for h in rel["head"]:
                        a, b = by_event.get(t[6:]), by_event.get(h[6:])
                        if a and b:
                            aw_exec.add((a, b))
        tp += len(gt_exec & aw_exec)
        fp += len(aw_exec - gt_exec)
        fn += len(gt_exec - aw_exec)
        # temporal-adjacency baseline (what v0.2 could reconstruct)
        ordered = sorted(ids.items(), key=lambda kv: kv[1]["time"]["start"] or "")
        base = {(ordered[k][0], ordered[k + 1][0]) for k in range(len(ordered) - 1)}
        btp += len(gt_exec & base)
        bfp += len(base - gt_exec)
        bfn += len(gt_exec - base)
        # information flow: GT producer→consumer must be reachable in the INFORMATION view
        gt_info = {tuple(x) for x in r.gt["data_edges"] if x[0] in ids and x[1] in ids}
        gt_info_closure = {p for p in closure(r.gt["data_edges"]) if p[0] in ids and p[1] in ids}

        # A's information reached B if B's event, or a value B produced, is an INFORMATION-view
        # descendant of A (B may declare no inputs while its output contains A's data).
        def reach(include_candidates: bool) -> set[tuple[str, str]]:
            out: set[tuple[str, str]] = set()
            for a, e in ids.items():
                desc = g.descendants(
                    f"event:{e['event_id']}",
                    views=["INFORMATION"],
                    types=[*FLOW_TYPES, "CANDIDATE_SOURCE"] if include_candidates else FLOW_TYPES,
                    skip_kinds=["entity"],
                    max_depth=12,
                    include_ambiguous=include_candidates,
                )
                for s in desc:
                    eid = s.node[6:] if s.node.startswith("event:") else _produced_by(s.node)
                    b = by_event.get(eid or "")
                    if b and b != a:
                        out.add((a, b))
            return out

        exec_pairs = {(x[0], x[1]) for x in r.gt["exec_edges"]}
        info_reach = reach(False)
        itp += len(gt_info & info_reach)
        ifn += len(gt_info - info_reach)
        ifp += len(info_reach - gt_info_closure - exec_pairs)
        cand_reach = reach(True)
        ctp += len(gt_info & cand_reach)
        cfn += len(gt_info - cand_reach)
        cfp += len(cand_reach - gt_info_closure - exec_pairs)
    p, rcl, f = prf(tp, fp, fn)
    bp, br, bf = prf(btp, bfp, bfn)
    ip, ir, _ = prf(itp, ifp, ifn)
    cp, cr, _ = prf(ctp, cfp, cfn)
    return {
        "metrics": {
            "execution_precision": p,
            "execution_recall": rcl,
            "execution_f1": f,
            "baseline_temporal_f1": bf,
            "information_recall": ir,
            "information_precision": ip,
            # not thresholded: the same measure if ambiguous candidates were followed as flow
            "information_recall_with_candidates": cr,
            "information_precision_with_candidates": cp,
        },
        "n": {"runs": len(records), "gt_exec_edges": tp + fn, "gt_info_edges": itp + ifn},
        "notes": "execution edges from DECLARED relations only; information precision counts reachable pairs not in the transitive closure of true data flow",
    }


def h3(ws: Workspace, records: list[Any]) -> dict[str, Any]:
    from panoptis.provenance.lineage import lineage

    tp = fp = fn = 0
    for r in records:
        final = r.gt.get("final_output")
        ids = id_map(ws, r.run_id)
        if not final or final not in ids:
            continue
        truth = {a for a, b in closure(r.gt["data_edges"]) if b == final and a in ids}
        outputs = ids[final]["outputs"]
        if not outputs:
            continue
        # provenance is asked of the produced artifact ("where did report.md come from?"):
        # the exact value the final operation produced
        res = lineage(ws, f"inst:{ids[final]['event_id']}/o{len(outputs) - 1}", run_id=r.run_id)
        by_event = {e["event_id"]: i for i, e in ids.items()}
        found: set[str] = set()

        def walk(n: dict[str, Any]) -> None:
            if n["node"].startswith("event:") and (i := by_event.get(n["node"][6:])) and i != final:
                found.add(i)
            for p in n["parents"]:
                walk(p)

        walk(res["root"])
        # lineage of an event includes its structural container only through information links; compare leaf ops
        leaf_found = {i for i in found if ids[i]["kind"] not in ("OPERATION", "LIFECYCLE")}
        tp += len(truth & leaf_found)
        fp += len(leaf_found - truth)
        fn += len(truth - leaf_found)
    p, rcl, f = prf(tp, fp, fn)
    return {
        "metrics": {
            "lineage_membership_precision": p,
            "lineage_membership_recall": rcl,
            "lineage_membership_f1": f,
        },
        "n": {"runs": len(records)},
        "notes": "does lineage(final output) contain exactly the operations whose data truly flowed into it",
    }


def h4(ws: Workspace, records: list[Any]) -> dict[str, Any]:
    hit = upstream = n = base_hit = 0
    per_scenario: dict[str, list[int]] = defaultdict(list)
    for r in records:
        root = r.gt.get("root_cause")
        if r.scenario == "normal" or not root:
            continue
        base = _baseline(records, r)
        if base is None:
            continue
        n += 1
        res = compare(ws, base.run_id, r.run_id)
        d = res["earliest_divergence"] or {}
        ids_b = {e["event_id"]: i for i, e in id_map(ws, r.run_id).items()}
        ids_a = {e["event_id"]: i for i, e in id_map(ws, base.run_id).items()}
        reported = {
            ids_b.get((d.get("b_event") or {}).get("event_id", "")),
            ids_a.get((d.get("a_event") or {}).get("event_id", "")),
        } - {None}
        ok = root in reported
        hit += ok
        per_scenario[r.scenario].append(int(ok))
        # root at or upstream of the reported divergence (in the perturbed run)
        up = ok
        if not ok and d.get("b_event"):
            g = ws.graph(r.run_id)
            anc = {
                ids_b.get(s.node[6:])
                for s in g.ancestors(f"event:{d['b_event']['event_id']}", max_depth=30)
                if s.node.startswith("event:")
            }
            up = root in anc
        upstream += up
        # v0.2-style baseline: first index-aligned difference by (signature, status, outputs)
        ea, eb = ws.events(base.run_id), ws.events(r.run_id)
        first = None
        for x, y in zip(ea, eb, strict=False):
            if (signature(x), x["status"], [o["artifact_id"] for o in x["outputs"]]) != (
                signature(y),
                y["status"],
                [o["artifact_id"] for o in y["outputs"]],
            ):
                first = {awb_id(x), awb_id(y)}
                break
        base_hit += bool(first and root in first)
    return {
        "metrics": {
            "top1_localization": round(hit / n, 4) if n else None,
            "root_at_or_upstream": round(upstream / n, 4) if n else None,
            "baseline_index_aligned_top1": round(base_hit / n, 4) if n else None,
        },
        "n": {"perturbed_runs": n},
        "per_scenario_top1": {
            k: round(sum(v) / len(v), 3) for k, v in sorted(per_scenario.items())
        },
    }


ORIGIN_KINDS = ("RETRIEVAL", "EXTERNAL_INPUT")


def true_bottleneck(gt: dict[str, Any]) -> bool:
    """M006 from the ground-truth data flow, by the motif's definition: some intermediate
    operation lies on every true path from the origins (retrievals / external inputs; every
    stub retrieval returns >= 2 documents) to the final output. M006 is structural, not an
    injected fault, so it is derived here rather than declared per scenario."""
    final = gt.get("final_output")
    edges = gt["data_edges"]
    kinds = {n: v["kind"] for n, v in gt["nodes"].items()}
    anc = {a for a, b in closure(edges) if b == final}
    origins = {n for n in anc if kinds.get(n) in ORIGIN_KINDS}
    # M006 needs >= 2 distinct origin values: every stub retrieval returns >= 2 documents, an
    # external input is one value (correction 2026-09-27, see REGISTRY changelog)
    n_values = sum(2 if kinds.get(n) == "RETRIEVAL" else 1 for n in origins)
    if not final or n_values < 2:
        return False
    inc: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        inc[b].append(a)

    def reached(banned: str) -> set[str]:
        seen, stack = set(), [final]
        while stack:
            for p in inc.get(stack.pop(), ()):
                if p != banned and p not in seen:
                    seen.add(p)
                    stack.append(p)
        return seen

    return any(not (reached(x) & origins) for x in anc - origins)


def h5(ws: Workspace, records: list[Any]) -> dict[str, Any]:
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # tp, fp, fn
    for r in records:
        expected = set(r.gt.get("expected_motifs", [])) - {"M006"}
        if true_bottleneck(r.gt):
            expected.add("M006")
        detected = {m["motif_id"] for m in ws.derived("motif_instance", r.run_id)}
        for m in expected | detected:
            s = stats[m]
            if m in expected and m in detected:
                s[0] += 1
            elif m in detected:
                s[1] += 1
            else:
                s[2] += 1
    tp = sum(v[0] for v in stats.values())
    fp = sum(v[1] for v in stats.values())
    fn = sum(v[2] for v in stats.values())
    p, rcl, _ = prf(tp, fp, fn)
    per = {
        m: dict(zip(("precision", "recall", "f1"), prf(*v), strict=True))
        | {"tp": v[0], "fp": v[1], "fn": v[2]}
        for m, v in sorted(stats.items())
    }
    return {
        "metrics": {"micro_precision": p, "micro_recall": rcl},
        "n": {"runs": len(records)},
        "per_motif": per,
        "notes": "expected motifs come from the injected scenario (M006: derived from ground-truth data flow); motifs not expected but detected count as false positives",
    }


def h7(ws: Workspace, records: list[Any], drift_n: int) -> dict[str, Any]:
    profiles = {p["run_id"]: p for p in ws.derived("behaviour_profile")}

    def group(scenario: str, seeds: range) -> list[dict[str, Any]]:
        return [
            profiles[r.run_id]
            for r in records
            if r.arch == "tool_loop"
            and r.scenario == scenario
            and r.seed in seeds
            and r.run_id in profiles
        ]

    base = group("normal", range(0, drift_n))
    cand = group("model_substitution", range(0, drift_n))
    ctrl = group("normal", range(drift_n, 2 * drift_n))
    res = drift(base, cand)
    ctl = drift(base, ctrl)
    return {
        "metrics": {
            "detects_model_substitution": bool(res.get("behaviour_changed")),
            "control_flagged_features_max": len(ctl.get("drifted_features", [])),
            "candidate_flagged_features": len(res.get("drifted_features", [])),
        },
        "n": {"baseline": len(base), "candidate": len(cand), "control": len(ctl and ctrl)},
        "flagged": {
            "model_substitution": res.get("drifted_features"),
            "control": ctl.get("drifted_features"),
        },
        "notes": "tool_loop normal vs model_substitution; control = normal vs normal (different seeds)",
    }


def replay_fidelity(ws: Workspace, engine: Engine, records: list[Any]) -> dict[str, Any]:
    primary = _primary(records)
    l1 = [
        replay(ws, r.run_id, "L1", record=False)["consistent_with_stored_interpretation"]
        for r in records
        if r.seed == primary
    ]
    l2 = []
    for r in records:
        if r.seed == primary and r.scenario in ("normal", "tool_timeout", "bad_retrieval"):
            res = replay(Workspace(engine), r.run_id, "L2", record=False)
            l2.append(res["reproduction_confidence"]["value"])
    return {
        "metrics": {
            "l1_consistency": round(sum(l1) / len(l1), 4) if l1 else None,
            "l2_mean_reproduction": round(sum(l2) / len(l2), 4) if l2 else None,
        },
        "n": {"l1": len(l1), "l2": len(l2)},
    }


def _final_artifact(ws: Workspace, run_id: str) -> str | None:
    outs = [
        a["artifact_id"]
        for e in ws.events(run_id)
        if "artifact_creation" in e["facets"]
        for a in e["outputs"]
    ]
    return outs[-1] if outs else None


def _retrieval_output(ws: Workspace, run_id: str, awbench: str | None = None) -> Any:
    """The retrieval with the given awbench id (the injected root cause), else the first."""
    evs = ws.events(run_id, kind="RETRIEVAL")
    ev = next((e for e in evs if awbench and awb_id(e) == awbench), evs[0])
    out = next(o for o in ev["outputs"] if o["role"] == "documents")
    return ev, ws.store.artifact(ws.tenant_id, out["artifact_id"])["content"]


def counterfactual_quality(engine: Engine, records: list[Any]) -> dict[str, Any]:
    primary = _primary(records)
    correct = n = 0
    details = []
    for r in records:
        if r.scenario != "corrupted_retrieval" or r.seed != primary:
            continue
        base = _baseline(records, r)
        ws = Workspace(engine)
        root = r.gt.get("root_cause")
        _, clean_docs = _retrieval_output(ws, base.run_id, root)
        bad_ev, _ = _retrieval_output(ws, r.run_id, root)
        res = counterfactual(ws, r.run_id, bad_ev["event_id"], clean_docs, level="L3")
        ws2 = Workspace(engine)
        branch_run = (
            ws2.store.experiment(res["experiment_id"])["branch_run"]
            if res.get("experiment_id")
            else None
        )
        ok = bool(branch_run) and _final_artifact(ws2, branch_run) == _final_artifact(
            ws2, base.run_id
        )
        n += 1
        correct += ok
        details.append(
            {"arch": r.arch, "label": res["estimate"]["label"], "matches_actual_clean_run": ok}
        )
    return {
        "metrics": {"simulated_outcome_accuracy": round(correct / n, 4) if n else None},
        "n": {"counterfactuals": n},
        "details": details,
        "notes": "substitute the clean retrieval result into a corrupted run; the simulated final report must equal the actually observed clean report",
    }


def causal_hypotheses(engine: Engine, records: list[Any]) -> dict[str, Any]:
    primary = _primary(records)
    from panoptis.causality.hypotheses import add_evidence, get, propose

    true_ok = ctrl_ok = n = 0
    for r in records:
        if r.scenario != "bad_retrieval" or r.seed != primary:
            continue
        base = _baseline(records, r)
        ws = Workspace(engine)
        root = r.gt.get("root_cause")
        _, clean_docs = _retrieval_output(ws, base.run_id, root)
        bad_ev, _ = _retrieval_output(ws, r.run_id, root)
        n += 1
        h = propose(
            ws,
            cause=f"event:{bad_ev['event_id']}",
            effect="final_output",
            statement="the retrieval result determines the report",
            proposer="benchmark",
        )
        cf = counterfactual(ws, r.run_id, bad_ev["event_id"], clean_docs, level="L3")
        changed = bool(cf["estimate"].get("final_outputs_changed"))
        add_evidence(
            ws,
            h["hypothesis_id"],
            kind="INTERVENTION",
            direction="supports" if changed else "contradicts",
            summary="substituted retrieval result",
            experiment_id=cf["experiment_id"],
            reproduction_confidence=cf["estimate"]["uncertainty"]["reproduction_confidence"],
        )
        true_ok += get(ws, h["hypothesis_id"])["status"] == "SUPPORTED"
        # control: re-substitute a tool/model call with its own recorded value (a null intervention)
        ws = Workspace(engine)
        ctrl_ev = next(
            (
                e
                for e in ws.events(r.run_id)
                if e["kind"] in ("TOOL_INVOCATION", "MODEL_INVOCATION")
                and e["attributes"].get("call_key")
            ),
            None,
        )
        if ctrl_ev is None:
            continue
        own = ws.store.artifact(ws.tenant_id, next(o for o in ctrl_ev["outputs"])["artifact_id"])[
            "content"
        ]
        hc = propose(
            ws,
            cause=f"event:{ctrl_ev['event_id']}",
            effect="final_output",
            statement="control",
            proposer="benchmark",
        )
        cfc = counterfactual(ws, r.run_id, ctrl_ev["event_id"], own, level="L3")
        changed_c = bool(cfc["estimate"].get("final_outputs_changed"))
        add_evidence(
            ws,
            hc["hypothesis_id"],
            kind="INTERVENTION",
            direction="supports" if changed_c else "contradicts",
            summary="null substitution",
            experiment_id=cfc["experiment_id"],
            reproduction_confidence=cfc["estimate"]["uncertainty"]["reproduction_confidence"],
        )
        ctrl_ok += get(ws, hc["hypothesis_id"])["status"] != "SUPPORTED"
    return {
        "metrics": {
            "true_hypothesis_supported": round(true_ok / n, 4) if n else None,
            "control_not_supported": round(ctrl_ok / n, 4) if n else None,
        },
        "n": {"scenarios": n},
        "notes": "hypotheses become SUPPORTED only through recorded interventions",
    }


NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?")


def faithfulness(ws: Workspace, records: list[Any]) -> dict[str, Any]:
    primary = _primary(records)
    import json

    resolves = grounded = total_nums = n = 0
    for r in records:
        if r.scenario == "normal" or r.seed != primary:
            continue
        base = _baseline(records, r)
        ans = ask(ws, f"why did {r.run_id} take longer than {base.run_id}?")
        if not ans["answered"]:
            continue
        n += 1
        ok = True
        for ev in ans["evidence"]:
            try:
                ws.resolve_run(ev)
            except LookupError:
                try:
                    ws.event(ev)
                except LookupError:
                    ok = False
        resolves += ok
        blob = json.dumps(ans["result"], default=str)
        for line in ans["answer"]:
            for num in NUM.findall(line):
                total_nums += 1
                val = num.lstrip("+")
                grounded += (
                    val in blob
                    or val.rstrip("0").rstrip(".") in blob
                    or _pct_grounded(num, line, ans["result"])
                )
    return {
        "metrics": {
            "evidence_resolves": round(resolves / n, 4) if n else None,
            "numbers_grounded": round(grounded / total_nums, 4) if total_nums else None,
        },
        "n": {"answers": n, "numbers": total_nums},
        "notes": "every cited id must resolve; every number in the answer must appear in (or be a percentage of) the structured result",
    }


def _pct_grounded(num: str, line: str, result: dict[str, Any]) -> bool:
    if f"{num}%" not in line:
        return False
    import json

    blob = json.dumps(result, default=str)
    return any(abs(float(x) * 100 - float(num)) < 0.6 for x in re.findall(r"0\.\d+", blob))
