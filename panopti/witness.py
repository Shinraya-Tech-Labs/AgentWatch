"""Panopti Court: Cryptographic Execution Witness & Merkle Audit Proofs for AI Agents."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger("panopti.court")


def canonical_json_bytes(data: Any) -> bytes:
    """Serialize data to deterministic, canonical JSON bytes for hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_hex(data: bytes | str) -> str:
    """Compute standard SHA-256 hex digest."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class WitnessEntry:
    """Cryptographic receipt for an individual agent action or decision."""

    step: int
    timestamp: float
    actor: str
    action: str
    payload_hash: str
    parent_hash: str
    entry_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WitnessChain:
    """Cryptographic hash chain sealing an autonomous agent's execution trajectory."""

    GENESIS_HASH = "0" * 64

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._entries: list[WitnessEntry] = []
        self._last_hash: str = self.GENESIS_HASH

    @property
    def root_hash(self) -> str:
        """Current cryptographic root hash of the execution chain."""
        return self._last_hash

    @property
    def length(self) -> int:
        return len(self._entries)

    def record(
        self,
        actor: str,
        action: str,
        payload: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> WitnessEntry:
        """Record and seal a new agent event into the tamper-evident witness chain."""
        step = len(self._entries) + 1
        ts = timestamp if timestamp is not None else time.time()
        p_bytes = canonical_json_bytes(payload or {})
        payload_hash = sha256_hex(p_bytes)

        # Hash: step + timestamp + actor + action + payload_hash + parent_hash
        header = f"{step}:{ts}:{actor}:{action}:{payload_hash}:{self._last_hash}"
        entry_hash = sha256_hex(header)

        entry = WitnessEntry(
            step=step,
            timestamp=ts,
            actor=actor,
            action=action,
            payload_hash=payload_hash,
            parent_hash=self._last_hash,
            entry_hash=entry_hash,
        )

        self._entries.append(entry)
        self._last_hash = entry_hash
        return entry

    def export_proof(self) -> dict[str, Any]:
        """Export the complete cryptographic witness receipt for verification."""
        return {
            "version": "1.0",
            "session_id": self.session_id,
            "root_hash": self.root_hash,
            "entry_count": len(self._entries),
            "entries": [e.to_dict() for e in self._entries],
        }

    @classmethod
    def verify_proof(cls, proof: dict[str, Any]) -> tuple[bool, str | None]:
        """Mathematically verify that a witness proof has not been altered or tampered with.

        Returns:
            (valid: bool, error_message: str | None)
        """
        entries_data = proof.get("entries", [])
        if not entries_data:
            return True, None

        expected_parent = cls.GENESIS_HASH

        for idx, entry_dict in enumerate(entries_data):
            step = entry_dict.get("step")
            ts = entry_dict.get("timestamp")
            actor = entry_dict.get("actor")
            action = entry_dict.get("action")
            payload_hash = entry_dict.get("payload_hash")
            parent_hash = entry_dict.get("parent_hash")
            claimed_hash = entry_dict.get("entry_hash")

            if parent_hash != expected_parent:
                return (
                    False,
                    f"Chain break at step {step}: expected parent {expected_parent[:12]}..., got {parent_hash[:12]}...",
                )

            header = f"{step}:{ts}:{actor}:{action}:{payload_hash}:{parent_hash}"
            computed_hash = sha256_hex(header)

            if computed_hash != claimed_hash:
                return (
                    False,
                    f"Hash mismatch at step {step}: computed {computed_hash[:12]}..., claimed {claimed_hash[:12]}...",
                )

            expected_parent = claimed_hash

        claimed_root = proof.get("root_hash")
        if claimed_root != expected_parent:
            return (
                False,
                f"Root hash mismatch: expected {expected_parent[:12]}..., claimed {claimed_root[:12]}...",
            )

        return True, None
