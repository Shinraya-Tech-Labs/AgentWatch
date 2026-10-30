"""Graph builders: events → EXECUTION and INFORMATION relations.

Nothing here creates CAUSAL relations. Declared parents that do not resolve to an
observed event are reported, not guessed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from panoptis.events.model import ComputationalEvent, EventKind, EventStatus
from panoptis.events.normalize import ArtifactContent
from panoptis.graph.model import (
    Basis,
    RelType,
    View,
    make_relation,
    node_entity,
    node_event,
)

EXEC_BUILDER = "graph.execution@1"
INFO_BUILDER = "graph.information@6"  # 6: information instances (ADR-0017)

SHINGLE = 5
MIN_SHINGLES = 3
CONTAINMENT_THRESHOLD = 0.6
MAX_LIST_ITEMS = 100
_WORD = re.compile(r"[\w']+", re.UNICODE)


def event_order(events: Sequence[ComputationalEvent]) -> list[ComputationalEvent]:
    def key(e: ComputationalEvent) -> tuple[Any, ...]:
        return (
            e.time.start.timestamp() if e.time.start else float("inf"),
            e.time.ordering_key,
            e.event_id,
        )

    return sorted(events, key=key)


def build_execution(
    events: Sequence[ComputationalEvent],
    run_of: dict[str, str | None],
    source_index: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    rels: list[dict[str, Any]] = []
    for ev in events:
        rid = run_of.get(ev.event_id)
        for link in ev.parents:
            target = source_index.get((link.key_space, link.value))
            if target is None or target == ev.event_id:
                continue
            if link.relation == "parent":
                rtype = RelType.CONTAINS
            elif link.relation == "responds_to":
                rtype = RelType.RESPONDS_TO
            else:
                rtype = RelType.DEPENDS_ON
            rels.append(
                make_relation(
                    View.EXECUTION,
                    rtype,
                    [node_event(target)],
                    [node_event(ev.event_id)],
                    basis=Basis.DECLARED,
                    run_id=rid,
                    evidence=list(ev.derived_from),
                    derived_by=EXEC_BUILDER,
                    attributes={"declared_relation": link.relation, "key_space": link.key_space},
                )
            )
        if ev.actor:
            rels.append(
                make_relation(
                    View.EXECUTION,
                    RelType.PERFORMED_BY,
                    [node_entity(ev.actor.canonical)],
                    [node_event(ev.event_id)],
                    basis=Basis.DECLARED,
                    confidence=ev.confidence_attribution.value,
                    run_id=rid,
                    evidence=list(ev.derived_from),
                    derived_by=EXEC_BUILDER,
                    attributes={"attribution_basis": ev.confidence_attribution.basis},
                )
            )
        if ev.object:
            rels.append(
                make_relation(
                    View.EXECUTION,
                    RelType.TARGETS,
                    [node_event(ev.event_id)],
                    [node_entity(ev.object.canonical)],
                    basis=Basis.DECLARED,
                    run_id=rid,
                    evidence=list(ev.derived_from),
                    derived_by=EXEC_BUILDER,
                )
            )
        if ev.kind == EventKind.DELEGATION and ev.actor and ev.object:
            rels.append(
                make_relation(
                    View.EXECUTION,
                    RelType.DELEGATES_TO,
                    [node_entity(ev.actor.canonical)],
                    [node_entity(ev.object.canonical)],
                    basis=Basis.DECLARED,
                    run_id=rid,
                    evidence=[node_event(ev.event_id)],
                    derived_by=EXEC_BUILDER,
                    attributes={"event_id": ev.event_id},
                )
            )
    rels.extend(_retries(events, run_of, source_index))
    return _dedupe(rels)


def _parent_of(ev: ComputationalEvent, source_index: dict[tuple[str, str], str]) -> str | None:
    for link in ev.parents:
        if link.relation == "parent":
            return source_index.get((link.key_space, link.value))
    return None


def _retries(
    events: Sequence[ComputationalEvent],
    run_of: dict[str, str | None],
    source_index: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    """HEURISTIC: a failed call followed by a call with the same kind, operation and
    object under the same parent is labelled a retry (confidence 0.7)."""
    rels = []
    last: dict[tuple[Any, ...], ComputationalEvent] = {}
    for ev in event_order(events):
        if ev.kind not in (
            EventKind.TOOL_INVOCATION,
            EventKind.MODEL_INVOCATION,
            EventKind.RETRIEVAL,
            EventKind.EXTERNAL_IO,
        ):
            continue
        key = (
            run_of.get(ev.event_id),
            _parent_of(ev, source_index),
            ev.kind,
            ev.operation,
            ev.object.canonical if ev.object else None,
        )
        prev = last.get(key)
        if prev is not None and prev.status in (EventStatus.ERROR, EventStatus.TIMEOUT):
            rels.append(
                make_relation(
                    View.EXECUTION,
                    RelType.RETRIES,
                    [node_event(prev.event_id)],
                    [node_event(ev.event_id)],
                    basis=Basis.HEURISTIC,
                    confidence=0.7,
                    run_id=run_of.get(ev.event_id),
                    evidence=[node_event(prev.event_id), node_event(ev.event_id)],
                    derived_by=EXEC_BUILDER,
                    attributes={
                        "rule": "same kind/operation/object under same parent after failure"
                    },
                )
            )
        last[key] = ev
    return rels


def text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return " ".join(parts)


def shingles(text: str) -> frozenset[str]:
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) < SHINGLE:
        return frozenset()
    return frozenset(" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1))


def build_information(
    events: Sequence[ComputationalEvent],
    run_of: dict[str, str | None],
    artifacts: dict[str, ArtifactContent],
    register: Callable[[Any, str], str],
    source_index: dict[tuple[str, str], str] | None = None,
    *,
    memory_context: Sequence[ComputationalEvent] = (),
    memory_context_runs: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """INFORMATION relations over information instances (ADR-0017; see graph.information).

    ``register(value, role)`` returns the content id of a value (registering its content)."""
    from panoptis.graph.information import build_information_instances
    from panoptis.runs.segment import build_source_index

    cache: dict[str, frozenset[str]] = {}

    def tokens_of(content_id: str) -> frozenset[str]:
        if content_id not in cache:
            content = artifacts.get(content_id)
            sh = shingles(text_of(json.loads(content.content_json))) if content else frozenset()
            cache[content_id] = sh if len(sh) >= MIN_SHINGLES else frozenset()
        return cache[content_id]

    rels = build_information_instances(
        events,
        run_of,
        artifacts,
        register,
        source_index if source_index is not None else build_source_index(events),
        derived_by=INFO_BUILDER,
        tokens_of=tokens_of,
        containment_threshold=CONTAINMENT_THRESHOLD,
        max_list_items=MAX_LIST_ITEMS,
        shingle_size=SHINGLE,
        memory_context=memory_context,
        memory_context_runs=memory_context_runs or {},
    )
    return _dedupe(rels)


def _dedupe(rels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for r in rels:
        seen.setdefault(r["rel_id"], r)
    return list(seen.values())
