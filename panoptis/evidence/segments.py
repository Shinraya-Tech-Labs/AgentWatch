"""Hash-sealed evidence segments.

Observations are appended without coordination. Periodically a sealer closes a
segment: it computes a Merkle root over the segment's observations (ordered by
``obs_id``) and chains the segment to its predecessor.

Leaf version 1 covers an observation's id, payload hash, idempotency key and tenant.
Leaf version 2 (every segment sealed by this version) covers the whole envelope: also
its kind, sensor, timestamps, clock, content type, declared ids, redaction manifest and
sampling, so changing any of them breaks verification. The version is bound into the
segment hash, so a segment cannot be downgraded to the weaker check.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from panoptis.evidence.canonical import sha256_hex
from panoptis.evidence.model import RawObservation

GENESIS = "0" * 64
LEAF_VERSION = 2

# the stored columns a version-2 leaf covers (everything but the payload body and the
# segment assignment; the body is covered through payload_sha256)
ENVELOPE_FIELDS = (
    "obs_id",
    "tenant_id",
    "sensor_type",
    "sensor_version",
    "sensor_instance",
    "source_kind",
    "source_seq",
    "idempotency_key",
    "observed_at",
    "received_at",
    "clock",
    "content_type",
    "payload_sha256",
    "declared_ids",
    "redaction",
    "sampling",
)


def leaf_v2(row: Mapping[Any, Any]) -> str:
    """Version-2 leaf: SHA-256 over the canonical JSON of the stored envelope."""
    env = {k: row.get(k) for k in ENVELOPE_FIELDS}
    return sha256_hex(
        "leaf2|" + json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def leaf_hash(obs: RawObservation) -> str:
    return sha256_hex(
        f"leaf|{obs.obs_id}|{obs.payload_sha256}|{obs.idempotency_key}|{obs.tenant_id}"
    )


def merkle_root(leaves: Sequence[str]) -> str:
    if not leaves:
        return sha256_hex("empty")
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(f"node|{level[i]}|{level[i + 1]}") for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: Sequence[str], index: int) -> list[tuple[str, str]]:
    """Inclusion proof: list of (sibling_hash, side) where side is 'L' or 'R'."""
    proof: list[tuple[str, str]] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sibling = idx ^ 1
        proof.append((level[sibling], "L" if sibling < idx else "R"))
        level = [sha256_hex(f"node|{level[i]}|{level[i + 1]}") for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def verify_proof(leaf: str, proof: Sequence[tuple[str, str]], root: str) -> bool:
    acc = leaf
    for sibling, side in proof:
        acc = (
            sha256_hex(f"node|{sibling}|{acc}")
            if side == "L"
            else sha256_hex(f"node|{acc}|{sibling}")
        )
    return acc == root


@dataclass(frozen=True, slots=True)
class Segment:
    segment_id: str
    tenant_id: str
    seq: int
    first_obs: str
    last_obs: str
    n_obs: int
    merkle_root: str
    prev_segment_hash: str
    segment_hash: str
    sealed_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "tenant_id": self.tenant_id,
            "seq": self.seq,
            "first_obs": self.first_obs,
            "last_obs": self.last_obs,
            "n_obs": self.n_obs,
            "merkle_root": self.merkle_root,
            "prev_segment_hash": self.prev_segment_hash,
            "segment_hash": self.segment_hash,
            "sealed_at": self.sealed_at.isoformat(),
        }


def segment_hash(
    tenant_id: str, seq: int, root: str, prev: str, n_obs: int, leaf_version: int = 1
) -> str:
    if leaf_version == 1:
        return sha256_hex(f"segment|{tenant_id}|{seq}|{root}|{prev}|{n_obs}")
    return sha256_hex(f"segment{leaf_version}|{tenant_id}|{seq}|{root}|{prev}|{n_obs}")


@dataclass
class VerifyReport:
    ok: bool
    segments_checked: int
    observations_checked: int
    unsealed_observations: int
    errors: list[str]
    warnings: list[str] | None = None
    head: dict[str, Any] | None = None  # {"seq", "segment_hash"} of the last segment
    purged_segments: int = 0
    leaf_versions: dict[str, int] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "segments_checked": self.segments_checked,
            "observations_checked": self.observations_checked,
            "unsealed_observations": self.unsealed_observations,
            "errors": self.errors,
            "warnings": self.warnings or [],
            "head": self.head,
            "purged_segments": self.purged_segments,
            "leaf_versions": self.leaf_versions or {},
        }
