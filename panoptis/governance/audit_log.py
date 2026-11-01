"""
CMP-005 — Tamper-evident audit log for RBAC and policy changes (issue #395).

Every administrative change (role assignment, user add, team-policy update) is
appended as a record in a hash chain: each record's ``record_hash`` is computed
over its own fields *and* the previous record's hash. Altering any past record
breaks every hash after it, so :meth:`AuditLog.verify` detects tampering even
though the log is held in memory.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

# Hash that seeds the chain — the "previous hash" of the first record.
GENESIS_HASH = "0" * 64


def _digest(
    seq: int,
    timestamp: str,
    actor: str | None,
    action: str,
    target: str,
    details: dict[str, Any],
    prev_hash: str,
) -> str:
    """Return the SHA-256 chain hash for a record's fields."""
    canonical = json.dumps(
        {
            "seq": seq,
            "timestamp": timestamp,
            "actor": actor,
            "action": action,
            "target": target,
            "details": details,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditRecord:
    """A single tamper-evident entry in the audit chain."""

    seq: int
    timestamp: str
    actor: str | None
    action: str
    target: str
    details: dict[str, Any]
    prev_hash: str
    record_hash: str

    def copy(self) -> AuditRecord:
        """Return a detached copy so callers can't mutate the stored record."""
        return AuditRecord(
            seq=self.seq,
            timestamp=self.timestamp,
            actor=self.actor,
            action=self.action,
            target=self.target,
            details=dict(self.details),
            prev_hash=self.prev_hash,
            record_hash=self.record_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "details": dict(self.details),
            "prev_hash": self.prev_hash,
            "record_hash": self.record_hash,
        }


class AuditLog:
    """An append-only, hash-chained audit log.

    Reads return detached copies and writes are guarded by a lock, so the
    chain can only ever be extended through :meth:`append` — callers cannot
    corrupt stored records, even from multiple threads.
    """

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = threading.RLock()

    @property
    def head_hash(self) -> str:
        """Hash of the most recent record (or the genesis hash when empty)."""
        with self._lock:
            return self._records[-1].record_hash if self._records else GENESIS_HASH

    def append(
        self,
        action: str,
        target: str,
        *,
        actor: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Append a new record to the chain and return a copy of it."""
        details = dict(details or {})
        with self._lock:
            seq = len(self._records)
            timestamp = datetime.now(UTC).isoformat()
            prev_hash = self._records[-1].record_hash if self._records else GENESIS_HASH
            record = AuditRecord(
                seq=seq,
                timestamp=timestamp,
                actor=actor,
                action=action,
                target=target,
                details=details,
                prev_hash=prev_hash,
                record_hash=_digest(seq, timestamp, actor, action, target, details, prev_hash),
            )
            self._records.append(record)
            return record.copy()

    def records(self) -> list[AuditRecord]:
        """Return detached copies of the records in chronological order."""
        with self._lock:
            return [record.copy() for record in self._records]

    def verify(self) -> bool:
        """Return whether the chain is intact (no record altered or reordered)."""
        with self._lock:
            return verify_chain(self._records)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


def verify_chain(records: list[AuditRecord]) -> bool:
    """Return whether an ordered record list forms an intact hash chain."""
    prev_hash = GENESIS_HASH
    for expected_seq, record in enumerate(records):
        if record.seq != expected_seq:
            return False
        if record.prev_hash != prev_hash:
            return False
        expected = _digest(
            record.seq,
            record.timestamp,
            record.actor,
            record.action,
            record.target,
            record.details,
            record.prev_hash,
        )
        if expected != record.record_hash:
            return False
        prev_hash = record.record_hash
    return True


@runtime_checkable
class AuditStore(Protocol):
    """Durable backing store for a hash-chained audit log."""

    async def read_head(self) -> tuple[int, str]:
        """Return (record_count, head_hash) for the next append."""
        ...

    async def write(self, record: AuditRecord) -> None:
        """Append a formed record to storage."""
        ...

    async def read_all(self) -> list[AuditRecord]:
        """Return all records in ascending seq order."""
        ...


class InMemoryAuditStore:
    """List-backed AuditStore for tests and single-process use (not durable)."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = threading.RLock()

    async def read_head(self) -> tuple[int, str]:
        with self._lock:
            head = self._records[-1].record_hash if self._records else GENESIS_HASH
            return len(self._records), head

    async def write(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record.copy())

    async def read_all(self) -> list[AuditRecord]:
        with self._lock:
            return [record.copy() for record in self._records]


class PersistentAuditLog:
    """Append-only, hash-chained audit log backed by an AuditStore.

    Reads the previous hash from storage on each append, so the chain outlives
    the process and instances sharing one store extend a single chain.
    """

    def __init__(self, store: AuditStore) -> None:
        self._store = store

    async def head_hash(self) -> str:
        """Hash of the most recent persisted record (or genesis when empty)."""
        _, head = await self._store.read_head()
        return head

    async def append(
        self,
        action: str,
        target: str,
        *,
        actor: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Append a new record to the persisted chain and return a copy of it."""
        details = dict(details or {})
        seq, prev_hash = await self._store.read_head()
        timestamp = datetime.now(UTC).isoformat()
        record = AuditRecord(
            seq=seq,
            timestamp=timestamp,
            actor=actor,
            action=action,
            target=target,
            details=details,
            prev_hash=prev_hash,
            record_hash=_digest(seq, timestamp, actor, action, target, details, prev_hash),
        )
        await self._store.write(record)
        return record.copy()

    async def records(self) -> list[AuditRecord]:
        """Return detached copies of the persisted records in chronological order."""
        return await self._store.read_all()

    async def verify(self) -> bool:
        """Return whether the persisted chain is intact (nothing altered/reordered)."""
        return verify_chain(await self._store.read_all())

    async def count(self) -> int:
        count, _ = await self._store.read_head()
        return count


__all__ = [
    "GENESIS_HASH",
    "AuditRecord",
    "AuditLog",
    "AuditStore",
    "InMemoryAuditStore",
    "PersistentAuditLog",
    "verify_chain",
]
