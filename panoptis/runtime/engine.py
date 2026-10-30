"""The v3 processing engine.

    evidence (immutable) ─► normalize ─► resolve entities ─► segment runs
                         ─► build EXECUTION + INFORMATION relations ─► analyzers

An interpretation is identified by the versions of every component in the pipeline.
Changing any component version yields a new interpretation; the previous one is kept
(``superseded``) and raw evidence is never touched. Processing is a deterministic
rebuild, so it is idempotent and any process sharing the store gets the same result.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from panoptis.analysis.base import AnalysisInput, Analyzer
from panoptis.entities.resolve import (
    RESOLVER,
    RESOLVER_VERSION,
    load_aliases,
    merge_entities,
    resolve_entities,
)
from panoptis.events.model import ComputationalEvent, Diagnostic
from panoptis.events.normalize import ArtifactContent, NormalizeContext, Normalizer
from panoptis.events.registry import all_normalizers
from panoptis.evidence.canonical import canonical_json, sha256_hex
from panoptis.evidence.model import ObservationDraft, RawObservation
from panoptis.evidence.redaction import DEFAULT_POLICY, PayloadPolicy
from panoptis.graph.build import EXEC_BUILDER, INFO_BUILDER, build_execution, build_information
from panoptis.runs.segment import build_source_index, segment
from panoptis.sensors.legacy.translator import LegacyTranslator, TranslationResult
from panoptis.storage.store import AppendResult, Store

logger = logging.getLogger(__name__)
PIPELINE_VERSION = "2"  # 2: zero-padded ordering keys


def _writes_seen_by_other_runs(
    events: Sequence[ComputationalEvent],
    run_of: dict[str, str | None],
    stored_memory: Sequence[dict[str, Any]],
) -> bool:
    """True when a newly built memory write shares its store and key with a stored read of
    another run that started after it: that read's source could change."""
    new_writes = [
        (e.object.canonical, e.attributes.get("key"), e.time.start, run_of.get(e.event_id))
        for e in events
        if e.kind.value == "MEMORY_ACCESS"
        and e.object is not None
        and (e.attributes.get("access") == "write" or "write" in e.facets)
    ]
    if not new_writes:
        return False
    for r in stored_memory:
        if not (r["attributes"].get("access") == "read" or "read" in r["facets"]):
            continue
        for obj, key, t, run in new_writes:
            if (
                r.get("object") == obj
                and r["attributes"].get("key") == key
                and r.get("run_id") != run
                and (t is None or (r["time"]["start"] or "") >= t.isoformat())
            ):
                return True
    return False


def default_analyzers() -> list[Analyzer]:
    from panoptis.analysis.registry import builtin_analyzers

    return builtin_analyzers()


class Engine:
    def __init__(
        self,
        store: Store | str | None = None,
        *,
        policy: PayloadPolicy = DEFAULT_POLICY,
        normalizers: Sequence[Normalizer] | None = None,
        analyzers: Sequence[Analyzer] | None = None,
        incremental: bool = True,
        entity_aliases: Mapping[str, str] | str | None = None,
    ) -> None:
        """``entity_aliases``: declared aliases (mapping or JSON file; default: the file named by
        AGENTWATCH_ENTITY_ALIASES). They are part of the interpretation identity."""
        self.store = store if isinstance(store, Store) else Store(store)
        self.entity_aliases = load_aliases(entity_aliases)
        self.policy = policy
        self.normalizers = list(normalizers) if normalizers is not None else all_normalizers()
        self.analyzers = list(analyzers) if analyzers is not None else default_analyzers()
        self.incremental = incremental
        self.last_mode: str | None = (
            None  # "full" | "incremental" | "skipped" (for tests and stats)
        )

    # ── ingestion ──────────────────────────────────────────────────────────
    def ingest(
        self, drafts: Sequence[ObservationDraft], policy: PayloadPolicy | None = None
    ) -> AppendResult:
        return self.store.append(drafts, policy or self.policy)

    def erase_subject(
        self, tenant_id: str, subject: str, *, reason: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Crypto-shred ``subject`` and rebuild derived data without its payloads."""
        report = self.store.erase_subject(tenant_id, subject, reason=reason, actor=actor)
        report["rebuild"] = self.process(tenant_id, force=True)
        return report

    def purge_segments(
        self, segment_ids: Sequence[str], *, reason: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Retention: purge sealed segments with every derived copy, then rebuild."""
        report = self.store.purge_segments(segment_ids, reason, actor)
        report["rebuild"] = {
            t: self.process(t, force=True) for t in report.pop("tenants_to_rebuild")
        }
        return report

    def ingest_legacy(
        self, events: Iterable[Any], tenant_id: str = "default"
    ) -> tuple[list[TranslationResult], AppendResult]:
        translator = LegacyTranslator(tenant_id=tenant_id)
        results = [translator.translate(e, tenant_id=tenant_id) for e in events]
        drafts = [r.draft for r in results if r.draft is not None]
        return results, self.ingest(drafts)

    # ── interpretation identity ────────────────────────────────────────────
    def pipeline(self) -> dict[str, Any]:
        return {
            "pipeline": PIPELINE_VERSION,
            "normalizers": {
                n.name: n.version for n in sorted(self.normalizers, key=lambda n: n.name)
            },
            "resolver": f"{RESOLVER}@{RESOLVER_VERSION}",
            **(
                {"entity_aliases": sha256_hex(canonical_json(self.entity_aliases))}
                if self.entity_aliases
                else {}
            ),
            "graph": [EXEC_BUILDER, INFO_BUILDER],
            "analyzers": {a.name: a.version for a in sorted(self.analyzers, key=lambda a: a.name)},
            "policy": {
                "capture": self.policy.capture,
                "redact_secrets": self.policy.redact_secrets,
                "redact_pii": self.policy.redact_pii,
            },
        }

    def interp_id_for(self, tenant_id: str) -> str:
        return f"{tenant_id}:{sha256_hex(canonical_json(self.pipeline()))[:16]}"

    def current_interp(self, tenant_id: str = "default", *, process: bool = True) -> str:
        interp_id = self.interp_id_for(tenant_id)
        if process:
            self.process(tenant_id)
        return interp_id

    def is_stale(self, tenant_id: str = "default") -> bool:
        interp = self.store.get_interpretation(self.interp_id_for(tenant_id))
        latest = self.store.latest_obs_id(tenant_id)
        return (
            interp is None
            or interp.get("processed_through") != latest
            or interp.get("status") != "active"
        )

    # ── processing ─────────────────────────────────────────────────────────
    def process(self, tenant_id: str = "default", *, force: bool = False) -> dict[str, Any]:
        interp_id = self.interp_id_for(tenant_id)
        if not force and not self.is_stale(tenant_id):
            interp = self.store.get_interpretation(interp_id) or {}
            return {"interp_id": interp_id, "skipped": True, **(interp.get("stats") or {})}
        if not force and self.incremental:
            incremental = self._process_incremental(tenant_id, interp_id)
            if incremental is not None:
                return incremental
        t0 = time.perf_counter()
        through = self.store.latest_obs_id(tenant_id)
        observations = list(self.store.iter_all_observations(tenant_id)) if through else []
        observations = [o for o in observations if through is None or o.obs_id <= through]
        self.store.activate_interpretation(
            tenant_id, interp_id, self.pipeline(), sha256_hex(canonical_json(self.pipeline()))
        )
        built = self.build(tenant_id, interp_id, observations)
        t_build = time.perf_counter()
        event_docs = []
        for ev in built["events"]:
            d = ev.to_dict()
            d["run_id"] = built["run_of"].get(ev.event_id)
            d["run_basis"] = built["run_basis"].get(ev.event_id)
            event_docs.append(d)
        derived = built["derived"]
        stats = {
            "observations": len(observations),
            "events": len(event_docs),
            "runs": len(built["runs"]),
            "entities": len(built["entities"]),
            "relations": len(built["relations"]),
            "artifacts": len(built["artifacts"]),
            "diagnostics": len(built["diagnostics"]),
            "derived_records": len(derived),
            "build_ms": round((t_build - t0) * 1000, 1),
        }
        self.store.write_interpretation(
            interp_id,
            tenant_id,
            events=event_docs,
            diagnostics=[{k: v for k, v in d.to_dict().items()} for d in built["diagnostics"]],
            artifacts=artifact_rows(built["artifacts"]),
            entities=built["entities"],
            runs=list(built["runs"].values()),
            relations=built["relations"],
            processed_through=through,
            stats=stats,
            derived=derived,
        )
        stats["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.last_mode = "full"
        return {"interp_id": interp_id, "skipped": False, "mode": "full", **stats}

    def _process_incremental(self, tenant_id: str, interp_id: str) -> dict[str, Any] | None:
        """Rebuild only the correlation groups touched by new observations.

        Returns None (the caller then does a full rebuild) whenever the result could differ
        from a full rebuild: an observation without a known correlation key, an erased or
        unnormalizable observation, events without a run, or declared links that do not
        resolve inside the rebuilt group.
        """
        interp = self.store.get_interpretation(interp_id)
        if not interp or interp.get("status") != "active" or not interp.get("processed_through"):
            return None
        t0 = time.perf_counter()
        new = self.store.observations(tenant_id, after=interp["processed_through"])
        if not new:
            return None
        keys: set[tuple[str, str]] = set()
        for obs in new:
            normalizer = next((n for n in self.normalizers if n.accepts(obs)), None)
            key = normalizer.correlation_key(obs) if normalizer and not obs.erased else None
            if key is None:
                return None
            keys.add(key)
        through = max(o.obs_id for o in new)
        group_ids: set[str] = set()
        for name, value in keys:
            group_ids.update(self.store.find_by_declared_id(tenant_id, name, value))
        group = [
            o
            for o in self.store.observations(tenant_id, obs_ids=sorted(group_ids))
            if o.obs_id <= through
        ]
        from panoptis.events.codec import event_from_dict

        group_obs = {o.obs_id for o in group}
        stored_memory = [
            e
            for e in self.store.events(interp_id, kind="MEMORY_ACCESS")
            if not set(e["derived_from"]) & group_obs
        ]
        memory_context = [
            event_from_dict(e)
            for e in stored_memory
            if e["attributes"].get("access") == "write" or "write" in e["facets"]
        ]
        built = self.build(
            tenant_id,
            interp_id,
            group,
            memory_context=memory_context,
            memory_context_runs={e["event_id"]: e.get("run_id") for e in stored_memory},
        )
        if _writes_seen_by_other_runs(built["events"], built["run_of"], stored_memory):
            return None  # a new write may change what reads of other runs returned
        if any(built["run_of"].get(e.event_id) is None for e in built["events"]):
            return None
        if any(run.get("unresolved_links") for run in built["runs"].values()):
            return None
        new_docs = []
        for ev in built["events"]:
            d = ev.to_dict()
            d["run_id"] = built["run_of"].get(ev.event_id)
            d["run_basis"] = built["run_basis"].get(ev.event_id)
            new_docs.append(d)
        # entities aggregate over every run. New runs only add to the stored aggregates; a run
        # whose events are being replaced needs a recompute (a minimum cannot be subtracted).
        affected_runs = set(built["runs"])
        replaces_stored = any(
            self.store.events(interp_id, run_id=r, limit=1) for r in affected_runs
        ) or any(i == interp_id for o in group_obs for _, i in self.store.events_for_observation(o))
        if not replaces_stored:
            entities = merge_entities(
                self.store.entities(interp_id),
                resolve_entities(built["events"], tenant_id, built["run_of"], self.entity_aliases),
            )
        else:
            kept = [
                e
                for e in self.store.events(interp_id)
                if e.get("run_id") not in affected_runs and not set(e["derived_from"]) & group_obs
            ]
            all_events = [event_from_dict(e) for e in kept] + list(built["events"])
            run_of = {e["event_id"]: e.get("run_id") for e in kept} | dict(built["run_of"])
            entities = resolve_entities(all_events, tenant_id, run_of, self.entity_aliases)
        counts = self.store.replace_partial(
            interp_id,
            tenant_id,
            run_ids=sorted(affected_runs),
            obs_ids=sorted(group_obs),
            events=new_docs,
            diagnostics=[d.to_dict() for d in built["diagnostics"]],
            artifacts=artifact_rows(built["artifacts"]),
            entities=entities,
            runs=list(built["runs"].values()),
            relations=built["relations"],
            derived=built["derived"],
            processed_through=through,
        )
        self.last_mode = "incremental"
        return {
            "interp_id": interp_id,
            "skipped": False,
            "mode": "incremental",
            "new_observations": len(new),
            "group_observations": len(group),
            "runs_rebuilt": len(affected_runs),
            **counts,
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def build(
        self,
        tenant_id: str,
        interp_id: str,
        observations: Sequence[RawObservation],
        *,
        memory_context: Sequence[ComputationalEvent] = (),
        memory_context_runs: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """Pure derivation from evidence (no store writes). Used by process() and replay.

        ``memory_context``: already-interpreted memory writes of runs outside ``observations``
        (incremental processing), so reads can be linked to writes of other runs exactly as a
        full rebuild links them."""
        ctx = NormalizeContext(
            tenant_id=tenant_id,
            interp_id=interp_id,
            artifact_key=self.store.artifact_key(tenant_id),
        )
        events: list[ComputationalEvent] = []
        diagnostics: list[Diagnostic] = []
        by_normalizer: dict[str, list[RawObservation]] = {n.name: [] for n in self.normalizers}
        for obs in observations:
            if obs.erased:
                diagnostics.append(
                    Diagnostic(
                        obs.obs_id,
                        "info",
                        "payload_erased",
                        "data subject erased (key destroyed); payload not interpreted",
                    )
                )
                continue
            target = next((n for n in self.normalizers if n.accepts(obs)), None)
            if target is None:
                diagnostics.append(
                    Diagnostic(
                        obs.obs_id,
                        "warning",
                        "no_normalizer",
                        f"no normalizer accepts source_kind {obs.source_kind!r}",
                    )
                )
                continue
            by_normalizer[target.name].append(obs)
        for n in self.normalizers:
            batch = by_normalizer.get(n.name) or []
            if not batch:
                continue
            try:
                res = n.normalize(batch, ctx)
            except Exception as exc:  # a broken normalizer must not take down the pipeline
                logger.exception("normalizer %s failed", n.name)
                diagnostics.extend(
                    Diagnostic(o.obs_id, "error", "normalizer_failed", f"{n.name}: {exc}")
                    for o in batch
                )
                continue
            events.extend(res.events)
            diagnostics.extend(res.diagnostics)
        # invariant: every observation is represented by an event or a diagnostic
        covered = {o for e in events for o in e.derived_from} | {
            d.obs_id for d in diagnostics if d.obs_id
        }
        for obs in observations:
            if obs.obs_id not in covered:
                diagnostics.append(
                    Diagnostic(
                        obs.obs_id,
                        "error",
                        "unaccounted_observation",
                        "normalizer neither mapped nor reported this observation",
                    )
                )
        events = _unique(events, diagnostics)
        seg = segment(events, tenant_id)
        index = build_source_index(events)
        entities = resolve_entities(events, tenant_id, seg.run_of, self.entity_aliases)

        def register(value: Any, role: str) -> str:
            return ctx.artifact(value, role).artifact_id

        relations = build_execution(events, seg.run_of, index) + build_information(
            events,
            seg.run_of,
            ctx.artifacts,
            register,
            index,
            memory_context=memory_context,
            memory_context_runs=memory_context_runs,
        )
        derived: list[dict[str, Any]] = []
        data = AnalysisInput(
            tenant_id, interp_id, events, seg.run_of, seg.runs, relations, ctx.artifacts
        )
        for analyzer in self.analyzers:
            try:
                derived.extend(analyzer.analyze(data))
            except Exception as exc:
                logger.exception("analyzer %s failed", analyzer.ref)
                diagnostics.append(
                    Diagnostic(None, "error", "analyzer_failed", f"{analyzer.ref}: {exc}")
                )
        return {
            "events": events,
            "diagnostics": diagnostics,
            "run_of": seg.run_of,
            "run_basis": seg.basis,
            "runs": seg.runs,
            "entities": entities,
            "relations": relations,
            "artifacts": ctx.artifacts,
            "derived": derived,
        }


def _unique(
    events: list[ComputationalEvent], diagnostics: list[Diagnostic]
) -> list[ComputationalEvent]:
    seen: dict[str, ComputationalEvent] = {}
    for ev in events:
        if ev.event_id in seen:
            diagnostics.append(
                Diagnostic(
                    ev.derived_from[0] if ev.derived_from else None,
                    "error",
                    "duplicate_event_id",
                    f"event id {ev.event_id} produced twice",
                )
            )
            continue
        seen[ev.event_id] = ev
    return list(seen.values())


def artifact_rows(artifacts: dict[str, ArtifactContent]) -> list[dict[str, Any]]:
    return [{s: getattr(a, s) for s in a.__slots__} for a in artifacts.values()]
