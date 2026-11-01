"""Normalizer framework: RawObservation → ComputationalEvent.

Normalizers are pure and versioned. They receive every observation of the source kinds
they accept (sorted in source order) and return events, the artifact contents those
events reference, and diagnostics. Every observation passed in must end up either in
some event's ``derived_from`` or in a diagnostic — never silently dropped.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from panoptis.events.model import (
    ArtifactRef,
    ComputationalEvent,
    Confidence,
    DeclaredLink,
    Diagnostic,
    Effect,
    EntityRef,
    EventKind,
    EventStatus,
    TemporalInfo,
    dumps,
    make_event_id,
)
from panoptis.evidence.canonical import canonical_json
from panoptis.evidence.model import RawObservation


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    artifact_id: str
    tenant_id: str
    media_type: str
    content_json: str  # canonical JSON of the value
    size_bytes: int
    preview: str


@dataclass
class NormalizeContext:
    tenant_id: str
    interp_id: str
    artifact_key: bytes  # per-tenant HMAC key for artifact ids
    artifacts: dict[str, ArtifactContent] = field(default_factory=dict)

    def artifact(
        self,
        value: Any,
        role: str,
        label: str | None = None,
        media_type: str | None = None,
        *,
        instance: str | None = None,
        sources: tuple[str, ...] = (),
    ) -> ArtifactRef:
        """Register an artifact value and return a reference to it."""
        content_json = canonical_json(value)
        digest = hmac.new(
            self.artifact_key, content_json.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if digest not in self.artifacts:
            text = value if isinstance(value, str) else content_json
            self.artifacts[digest] = ArtifactContent(
                artifact_id=digest,
                tenant_id=self.tenant_id,
                media_type=media_type
                or ("text/plain" if isinstance(value, str) else "application/json"),
                content_json=content_json,
                size_bytes=len(content_json.encode("utf-8")),
                preview=text[:240],
            )
        return ArtifactRef(
            artifact_id=digest, role=role, label=label, instance=instance, sources=sources
        )


def ordering_seq(seq: int | None) -> str:
    """A source sequence number that sorts correctly as text. Ordering keys are compared as
    strings (in Python and in SQL); with coarse clocks (15.6 ms on Windows before Python
    3.13) many events share a timestamp, and an unpadded ':10' would sort before ':9'."""
    return f"{seq:012d}" if seq is not None else ""


@dataclass
class NormalizeResult:
    events: list[ComputationalEvent] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def extend(self, other: NormalizeResult) -> None:
        self.events.extend(other.events)
        self.diagnostics.extend(other.diagnostics)


class Normalizer:
    """Base class. Subclasses set ``name``, ``version`` and ``source_kinds``."""

    name: ClassVar[str] = "base"
    version: ClassVar[str] = "0"
    source_kinds: ClassVar[frozenset[str]] = frozenset()
    maturity: ClassVar[str] = "EXPERIMENTAL"

    def accepts(self, obs: RawObservation) -> bool:
        return obs.source_kind in self.source_kinds

    def correlation_key(self, obs: RawObservation) -> tuple[str, str] | None:
        """A declared id shared by every observation needed to interpret ``obs`` (and by
        nothing belonging to another run). ``None`` means unknown, which forces a full
        rebuild instead of an incremental one."""
        return None

    @staticmethod
    def _declared_key(obs: RawObservation, name: str) -> tuple[str, str] | None:
        value = obs.declared(name)
        return (name, value) if value else None

    def normalize(
        self, observations: Sequence[RawObservation], ctx: NormalizeContext
    ) -> NormalizeResult:
        raise NotImplementedError  # abstract


def source_order(observations: Iterable[RawObservation]) -> list[RawObservation]:
    """Order by sensor instance and sequence when declared, else by time then id."""

    def key(o: RawObservation) -> tuple[Any, ...]:
        ts = o.observed_at.timestamp() if o.observed_at else o.received_at.timestamp()
        return (
            ts,
            o.sensor.instance_id,
            o.source_seq if o.source_seq is not None else -1,
            o.obs_id,
        )

    return sorted(observations, key=key)


def temporal(start: RawObservation | None, end: RawObservation | None = None) -> TemporalInfo:
    """Temporal info from start/end observations; missing times remain missing."""
    anchor = start or end
    assert anchor is not None  # nosec B101 — invariant: caller must pass start or end
    basis = anchor.clock.source if anchor.observed_at else "missing"
    s = start.observed_at if start else None
    e = end.observed_at if end else None
    seq = ordering_seq(anchor.source_seq)
    return TemporalInfo(
        start=s,
        end=e,
        basis=basis,
        uncertainty_ms=anchor.clock.precision_ms,
        ordering_key=f"{anchor.sensor.instance_id}:{seq}",
    )


class EventBuilder:
    """Helper that assembles a frozen event and computes ``missing``."""

    def __init__(
        self,
        normalizer: Normalizer,
        ctx: NormalizeContext,
        derived_from: Sequence[RawObservation],
        index: int = 0,
    ) -> None:
        self.normalizer = normalizer
        self.ctx = ctx
        self.obs = list(derived_from)
        self.index = index
        self.kind: EventKind = EventKind.UNKNOWN
        self.operation = "unknown"
        self.status = EventStatus.UNKNOWN
        self.actor: EntityRef | None = None
        self.object: EntityRef | None = None
        self.facets: list[str] = []
        self.inputs: list[ArtifactRef] = []
        self.outputs: list[ArtifactRef] = []
        self.effects: list[Effect] = []
        self.source_ids: list[tuple[str, str]] = []
        self.parents: list[DeclaredLink] = []
        self.run_key: tuple[str, str] | None = None
        self.error: dict[str, Any] | None = None
        self.resources: dict[str, Any] = {}
        self.attributes: dict[str, Any] = {}
        self.time: TemporalInfo | None = None
        self.obs_confidence = Confidence(1.0, "declared")
        self.attr_confidence = Confidence(1.0, "declared")
        self.extra_missing: list[str] = []

    def input(
        self,
        value: Any,
        role: str = "input",
        label: str | None = None,
        *,
        sources: tuple[str, ...] | list[str] = (),
    ) -> ArtifactRef:
        ref = self.ctx.artifact(value, role, label, sources=tuple(sources))
        self.inputs.append(ref)
        return ref

    def reference(self, sources: Sequence[str], role: str = "reference") -> ArtifactRef:
        """An input declared only by reference (the sensor names the produced value it used
        but did not capture its content). ``artifact_id`` is empty: there is no content."""
        ref = ArtifactRef(artifact_id="", role=role, sources=tuple(sources))
        self.inputs.append(ref)
        return ref

    def output(
        self,
        value: Any,
        role: str = "output",
        label: str | None = None,
        *,
        instance: str | None = None,
    ) -> ArtifactRef:
        ref = self.ctx.artifact(value, role, label, instance=instance)
        self.outputs.append(ref)
        return ref

    def build(self) -> ComputationalEvent:
        time = self.time or temporal(self.obs[0] if self.obs else None)
        missing = list(self.extra_missing)
        if self.actor is None:
            missing.append("actor")
        if time.start is None:
            missing.append("start_time")
        if time.end is None and self.kind not in (
            EventKind.EXTERNAL_INPUT,
            EventKind.FAILURE,
            EventKind.MESSAGE,
        ):
            missing.append("end_time")
        if not self.parents:
            missing.append("parent")
        if self.run_key is None:
            missing.append("run")
        obs_ids = [o.obs_id for o in self.obs]
        return ComputationalEvent(
            event_id=make_event_id(self.normalizer.name, obs_ids, self.index),
            tenant_id=self.ctx.tenant_id,
            interp_id=self.ctx.interp_id,
            normalizer=f"{self.normalizer.name}@{self.normalizer.version}",
            derived_from=tuple(obs_ids),
            kind=self.kind,
            operation=self.operation,
            time=time,
            status=self.status,
            actor=self.actor,
            object=self.object,
            facets=tuple(sorted(set(self.facets))),
            inputs=tuple(self.inputs),
            outputs=tuple(self.outputs),
            effects=tuple(self.effects),
            source_ids=tuple(self.source_ids),
            parents=tuple(self.parents),
            run_key=self.run_key,
            error_json=dumps(self.error) if self.error else None,
            resources_json=dumps(self.resources),
            attributes_json=dumps(self.attributes),
            confidence_observation=self.obs_confidence,
            confidence_attribution=self.attr_confidence,
            missing=tuple(sorted(set(missing))),
        )


def status_from(value: Any) -> EventStatus:
    if value is None:
        return EventStatus.UNKNOWN
    v = str(value).lower()
    if v in {"ok", "success", "succeeded", "completed", "status_code_ok", "1"}:
        return EventStatus.OK
    if v in {"error", "failure", "failed", "status_code_error", "2", "blocked"}:
        return EventStatus.ERROR
    if v in {"timeout", "timed_out"}:
        return EventStatus.TIMEOUT
    if v in {"cancelled", "canceled"}:
        return EventStatus.CANCELLED
    return EventStatus.UNKNOWN


def ts_or_none(value: Any) -> datetime | None:
    from panoptis.evidence.model import parse_ts

    if not value:
        return None
    try:
        return parse_ts(value)
    except (TypeError, ValueError):
        return None


NormalizerFactory = Callable[[], Normalizer]
