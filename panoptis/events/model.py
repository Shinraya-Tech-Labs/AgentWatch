"""The v3 computational event algebra.

A :class:`ComputationalEvent` is *normalized structure* derived from one or more raw
observations by a versioned normalizer. It is not evidence (that is
:class:`~panoptis.evidence.model.RawObservation`) and not analysis (that lives in the
graph and interpretation layers).

Missing facts stay missing: ``actor``, ``run_key``, timestamps and parents are
``None``/empty when the source did not provide them, and the gap is listed in
``missing``. Normalizers must never invent them.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from panoptis.evidence.canonical import canonical_json

EVENT_NAMESPACE = uuid.UUID("6f0c2a4e-3c1d-5a8e-9b7f-0d2e4c6a8b10")


class EventKind(StrEnum):
    MODEL_INVOCATION = "MODEL_INVOCATION"
    TOOL_INVOCATION = "TOOL_INVOCATION"
    RETRIEVAL = "RETRIEVAL"
    MEMORY_ACCESS = "MEMORY_ACCESS"
    MESSAGE = "MESSAGE"
    DELEGATION = "DELEGATION"
    TRANSFORMATION = "TRANSFORMATION"
    STATE_MUTATION = "STATE_MUTATION"
    EXTERNAL_IO = "EXTERNAL_IO"
    EXTERNAL_INPUT = "EXTERNAL_INPUT"
    SYNCHRONIZATION = "SYNCHRONIZATION"
    OPERATION = "OPERATION"  # a named unit of work (planner step, chain, request handler)
    LIFECYCLE = "LIFECYCLE"
    FAILURE = "FAILURE"
    RECOVERY = "RECOVERY"
    UNKNOWN = "UNKNOWN"


class EventStatus(StrEnum):
    OK = "OK"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class EffectKind(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    CREATE = "CREATE"
    DELETE = "DELETE"
    TRANSFORM = "TRANSFORM"
    SEND = "SEND"
    RECEIVE = "RECEIVE"


@dataclass(frozen=True, slots=True, order=True)
class EntityRef:
    """Reference to a persistent thing by canonical key, e.g. ``tool:web_search``."""

    kind: str
    key: str

    @property
    def canonical(self) -> str:
        return f"{self.kind}:{self.key}"

    @classmethod
    def parse(cls, value: str) -> EntityRef:
        kind, _, key = value.partition(":")
        return cls(kind, key)


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A value consumed or produced by an event.

    ``artifact_id`` is the CONTENT identity (HMAC of the canonical value): equal bytes, equal
    id. It says nothing about which event produced the value (ADR-0017). Information identity
    comes from the producing event and output slot, and may be declared by the sensor:

    * ``instance`` (outputs): an opaque instance id the sensor declared for this produced value.
    * ``sources`` (inputs): references to the produced values this input is, or is built from,
      as declared by the sensor (``<key_space>:<source_id>/o<slot>``, or a declared instance
      id). Runtime object identity observed by the native SDK is declared this way.
    """

    artifact_id: str
    role: str  # input | output | prompt | completion | arguments | result | document | message
    label: str | None = None
    instance: str | None = None
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "role": self.role,
            "label": self.label,
        }
        if self.instance:
            d["instance"] = self.instance
        if self.sources:
            d["sources"] = list(self.sources)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactRef:
        return cls(
            d["artifact_id"],
            d["role"],
            d.get("label"),
            d.get("instance"),
            tuple(d.get("sources") or ()),
        )


@dataclass(frozen=True, slots=True)
class Effect:
    kind: EffectKind
    target: str  # entity canonical key or artifact id
    target_type: str = "entity"  # entity | artifact


@dataclass(frozen=True, slots=True)
class TemporalInfo:
    """Occurrence interval.

    ``basis``: ``source`` (timestamps supplied by the observed system), ``sensor``
    (stamped at capture), or ``missing`` (no occurrence time known — ``start`` is None).
    ``end`` is None for point events and for intervals whose end was never observed.
    """

    start: datetime | None
    end: datetime | None = None
    basis: str = "sensor"
    uncertainty_ms: float | None = None
    ordering_key: str = ""  # sensor instance + sequence, for stable ordering within a source

    @property
    def duration_ms(self) -> float | None:
        if self.start and self.end:
            return (self.end - self.start).total_seconds() * 1000.0
        return None


@dataclass(frozen=True, slots=True)
class DeclaredLink:
    """A relationship *declared by the source*, expressed in the source's id space.

    Example: an OTel span declaring ``parent_span_id`` becomes
    ``DeclaredLink("parent", "otel.span", "<trace>/<span>")``. The graph layer resolves
    it to an event; if no event carries that source id, it stays unresolved (never guessed).
    """

    relation: str  # parent | responds_to | follows | caused_by (source-asserted)
    key_space: str
    value: str


@dataclass(frozen=True, slots=True)
class Confidence:
    value: float
    basis: str
    calibrated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "basis": self.basis, "calibrated": self.calibrated}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    obs_id: str | None
    level: str  # info | warning | error
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "obs_id": self.obs_id,
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ComputationalEvent:
    event_id: str
    tenant_id: str
    interp_id: str
    normalizer: str
    derived_from: tuple[str, ...]
    kind: EventKind
    operation: str
    time: TemporalInfo
    status: EventStatus = EventStatus.UNKNOWN
    actor: EntityRef | None = None
    object: EntityRef | None = None
    facets: tuple[str, ...] = ()
    inputs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = ()
    effects: tuple[Effect, ...] = ()
    source_ids: tuple[tuple[str, str], ...] = ()  # (key_space, value) identifying this event
    parents: tuple[DeclaredLink, ...] = ()
    run_key: tuple[str, str] | None = None  # declared run grouping (key_space, value)
    error_json: str | None = None
    resources_json: str = "{}"
    attributes_json: str = "{}"
    confidence_observation: Confidence = field(default_factory=lambda: Confidence(1.0, "declared"))
    confidence_attribution: Confidence = field(default_factory=lambda: Confidence(1.0, "declared"))
    missing: tuple[str, ...] = ()

    @property
    def attributes(self) -> dict[str, Any]:
        return json.loads(self.attributes_json)

    @property
    def resources(self) -> dict[str, Any]:
        return json.loads(self.resources_json)

    @property
    def error(self) -> dict[str, Any] | None:
        return json.loads(self.error_json) if self.error_json else None

    @property
    def label(self) -> str:
        return f"{self.kind.value}:{self.operation}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "interp_id": self.interp_id,
            "normalizer": self.normalizer,
            "derived_from": list(self.derived_from),
            "kind": self.kind.value,
            "operation": self.operation,
            "status": self.status.value,
            "actor": self.actor.canonical if self.actor else None,
            "object": self.object.canonical if self.object else None,
            "facets": list(self.facets),
            "inputs": [a.to_dict() for a in self.inputs],
            "outputs": [a.to_dict() for a in self.outputs],
            "effects": [
                {"kind": e.kind.value, "target": e.target, "target_type": e.target_type}
                for e in self.effects
            ],
            "time": {
                "start": self.time.start.isoformat() if self.time.start else None,
                "end": self.time.end.isoformat() if self.time.end else None,
                "basis": self.time.basis,
                "uncertainty_ms": self.time.uncertainty_ms,
                "duration_ms": self.time.duration_ms,
                "ordering_key": self.time.ordering_key,
            },
            "source_ids": [list(s) for s in self.source_ids],
            "parents": [
                {"relation": p.relation, "key_space": p.key_space, "value": p.value}
                for p in self.parents
            ],
            "run_key": list(self.run_key) if self.run_key else None,
            "error": self.error,
            "resources": self.resources,
            "attributes": self.attributes,
            "confidence": {
                "observation": self.confidence_observation.to_dict(),
                "attribution": self.confidence_attribution.to_dict(),
            },
            "missing": list(self.missing),
        }


def make_event_id(
    normalizer: str, obs_ids: list[str] | tuple[str, ...], local_index: int = 0
) -> str:
    """Deterministic event id, stable across normalizer *versions* for the same derivation."""
    basis = f"{normalizer}|{','.join(sorted(obs_ids))}|{local_index}"
    return str(uuid.uuid5(EVENT_NAMESPACE, basis))


def dumps(value: Any) -> str:
    return canonical_json(value if value is not None else {})
