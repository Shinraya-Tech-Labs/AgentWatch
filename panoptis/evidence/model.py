"""Evidence records.

``ObservationDraft`` is what a sensor produces. ``RawObservation`` is what the evidence
store holds: an immutable, hashed record of exactly what was received (after edge
redaction, which is recorded in a manifest rather than applied later).

Immutability is structural: the dataclass is frozen, the payload is held as canonical
JSON text and every accessor returns a fresh copy, and identifier maps are tuples.
No code path can mutate a stored observation in place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from panoptis.evidence.canonical import canonical_json, sha256_hex

# Content types understood by normalizers. Payloads are always JSON-representable.
JSON = "application/json"


@dataclass(frozen=True, slots=True)
class SensorRef:
    """Which sensor produced an observation."""

    sensor_type: str
    sensor_version: str
    instance_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "sensor_type": self.sensor_type,
            "sensor_version": self.sensor_version,
            "instance_id": self.instance_id,
        }


@dataclass(frozen=True, slots=True)
class ClockInfo:
    """Where ``observed_at`` came from.

    ``source`` is ``"source"`` when the observed system supplied the timestamp,
    ``"sensor"`` when the sensor stamped it at capture time, and ``"missing"`` when no
    occurrence time is known (``observed_at`` is then ``None``).
    """

    source: str = "sensor"
    clock_id: str | None = None
    precision_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "clock_id": self.clock_id, "precision_ms": self.precision_ms}


@dataclass(frozen=True, slots=True)
class SamplingInfo:
    """Records a sampling decision so statistics can reweight."""

    policy: str
    rate: float

    def to_dict(self) -> dict[str, Any]:
        return {"policy": self.policy, "rate": self.rate}


@dataclass(frozen=True, slots=True)
class RedactionManifest:
    """What edge redaction removed. The original values are never stored."""

    detector: str
    detector_version: str
    redactions: tuple[tuple[str, str, int], ...] = ()  # (json_path, category, count)

    @property
    def total(self) -> int:
        return sum(c for _, _, c in self.redactions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "detector_version": self.detector_version,
            "redactions": [list(r) for r in self.redactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RedactionManifest | None:
        if not data:
            return None
        return cls(
            detector=data["detector"],
            detector_version=data["detector_version"],
            redactions=tuple((p, c, int(n)) for p, c, n in data.get("redactions", [])),
        )


@dataclass
class ObservationDraft:
    """A sensor-side observation before ingestion.

    ``declared_ids`` holds identifiers *declared by the source* (trace/span ids,
    run/parent-run ids, tool-use ids, ...). Sensors must never invent them.
    """

    sensor: SensorRef
    source_kind: str
    payload: Any
    source_seq: int | None = None
    observed_at: datetime | None = None
    clock: ClockInfo = field(default_factory=ClockInfo)
    declared_ids: dict[str, str] = field(default_factory=dict)
    content_type: str = JSON
    sampling: SamplingInfo | None = None
    tenant_id: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor": self.sensor.to_dict(),
            "source_kind": self.source_kind,
            "payload": self.payload,
            "source_seq": self.source_seq,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "clock": self.clock.to_dict(),
            "declared_ids": dict(self.declared_ids),
            "content_type": self.content_type,
            "sampling": self.sampling.to_dict() if self.sampling else None,
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationDraft:
        s = data["sensor"]
        clock = data.get("clock") or {}
        sampling = data.get("sampling")
        observed = data.get("observed_at")
        return cls(
            sensor=SensorRef(s["sensor_type"], s["sensor_version"], s["instance_id"]),
            source_kind=data["source_kind"],
            payload=data.get("payload"),
            source_seq=data.get("source_seq"),
            observed_at=parse_ts(observed) if observed else None,
            clock=ClockInfo(
                source=clock.get("source", "sensor"),
                clock_id=clock.get("clock_id"),
                precision_ms=clock.get("precision_ms"),
            ),
            declared_ids={str(k): str(v) for k, v in (data.get("declared_ids") or {}).items()},
            content_type=data.get("content_type", JSON),
            sampling=SamplingInfo(sampling["policy"], float(sampling["rate"]))
            if sampling
            else None,
            tenant_id=data.get("tenant_id", "default"),
        )


def parse_ts(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def idempotency_key(sensor: SensorRef, source_seq: int | None, payload_sha256: str) -> str:
    """Stable key used to deduplicate re-sent observations."""
    basis = f"{sensor.sensor_type}|{sensor.instance_id}|"
    basis += f"seq:{source_seq}" if source_seq is not None else f"sha:{payload_sha256}"
    return sha256_hex(basis)


@dataclass(frozen=True, slots=True)
class RawObservation:
    """Immutable evidence record."""

    obs_id: str
    tenant_id: str
    sensor: SensorRef
    source_kind: str
    source_seq: int | None
    idempotency_key: str
    observed_at: datetime | None
    received_at: datetime
    clock: ClockInfo
    content_type: str
    payload_json: str
    payload_sha256: str
    declared_ids_items: tuple[tuple[str, str], ...]
    redaction: RedactionManifest | None = None
    sampling: SamplingInfo | None = None
    segment_id: str | None = None
    # "plain": payload_sha256 covers payload_json. "aes-gcm": stored encrypted; the hash covers
    # the stored ciphertext and payload_json is the decrypted text. "erased": the data key
    # was destroyed (crypto-shredding); payload_json is a placeholder.
    encoding: str = "plain"

    def payload(self) -> Any:
        """Return a fresh, independent copy of the payload."""
        return json.loads(self.payload_json)

    @property
    def declared_ids(self) -> dict[str, str]:
        """A copy of the declared identifiers."""
        return dict(self.declared_ids_items)

    def declared(self, key: str) -> str | None:
        for k, v in self.declared_ids_items:
            if k == key:
                return v
        return None

    def verify_hash(self) -> bool | None:
        """True/False for plain payloads; None when the hash covers stored ciphertext
        (verified by the store, see ``Store.verify``)."""
        if self.encoding != "plain":
            return None
        return sha256_hex(self.payload_json) == self.payload_sha256

    @property
    def erased(self) -> bool:
        return self.encoding == "erased"

    def to_dict(self, include_payload: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "obs_id": self.obs_id,
            "tenant_id": self.tenant_id,
            "sensor": self.sensor.to_dict(),
            "source_kind": self.source_kind,
            "source_seq": self.source_seq,
            "idempotency_key": self.idempotency_key,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "received_at": self.received_at.isoformat(),
            "clock": self.clock.to_dict(),
            "content_type": self.content_type,
            "payload_sha256": self.payload_sha256,
            "declared_ids": self.declared_ids,
            "redaction": self.redaction.to_dict() if self.redaction else None,
            "sampling": self.sampling.to_dict() if self.sampling else None,
            "segment_id": self.segment_id,
            "encoding": self.encoding,
        }
        if include_payload:
            data["payload"] = self.payload()
        return data

    @staticmethod
    def build(
        draft: ObservationDraft,
        *,
        obs_id: str,
        received_at: datetime,
        payload: Any,
        redaction: RedactionManifest | None,
    ) -> RawObservation:
        payload_json = canonical_json(payload)
        digest = sha256_hex(payload_json)
        return RawObservation(
            obs_id=obs_id,
            tenant_id=draft.tenant_id,
            sensor=draft.sensor,
            source_kind=draft.source_kind,
            source_seq=draft.source_seq,
            idempotency_key=idempotency_key(draft.sensor, draft.source_seq, digest),
            observed_at=draft.observed_at,
            received_at=received_at,
            clock=draft.clock if draft.observed_at else ClockInfo(source="missing"),
            content_type=draft.content_type,
            payload_json=payload_json,
            payload_sha256=digest,
            declared_ids_items=tuple(
                sorted((str(k), str(v)) for k, v in draft.declared_ids.items())
            ),
            redaction=redaction,
            sampling=draft.sampling,
        )
