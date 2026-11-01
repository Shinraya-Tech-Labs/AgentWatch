"""Hash-sealed evidence segments.

Observations are appended without coordination. Periodically a sealer closes a
segment: it computes a Merkle root over the segment's observations (ordered by
``obs_id``) and chains the segment to its predecessor. Any later change to a sealed
observation's payload, id or idempotency key breaks verification.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from panoptis.evidence.canonical import sha256_hex
from panoptis.evidence.model import RawObservation

GENESIS = "0" * 64


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


def segment_hash(tenant_id: str, seq: int, root: str, prev: str, n_obs: int) -> str:
    return sha256_hex(f"segment|{tenant_id}|{seq}|{root}|{prev}|{n_obs}")


@dataclass
class VerifyReport:
    ok: bool
    segments_checked: int
    observations_checked: int
    unsealed_observations: int
    errors: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "segments_checked": self.segments_checked,
            "observations_checked": self.observations_checked,
            "unsealed_observations": self.unsealed_observations,
            "errors": self.errors,
        }
