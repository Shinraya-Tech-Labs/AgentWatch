"""AgentWatch v3 evidence plane.

Raw observations are immutable evidence. Nothing in this package imports the rest of
AgentWatch, so sensors running inside observed applications stay dependency-light.
"""

from panoptis.evidence.canonical import canonical_json, sha256_hex
from panoptis.evidence.ids import new_ulid
from panoptis.evidence.model import (
    ClockInfo,
    ObservationDraft,
    RawObservation,
    RedactionManifest,
    SamplingInfo,
    SensorRef,
)

__all__ = [
    "ClockInfo",
    "ObservationDraft",
    "RawObservation",
    "RedactionManifest",
    "SamplingInfo",
    "SensorRef",
    "canonical_json",
    "new_ulid",
    "sha256_hex",
]
