"""
MEM-008 — Memory Governance.

Retention policies, deletion schedules, and GDPR right-to-erasure.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from panoptis.governance.gdpr import ErasureStore, PartialErasureError


@dataclass
class RetentionPolicy:
    name: str
    applies_to: str  # "all" | "type:episodic" | "user:X" | "project:Y"
    retain_for: timedelta
    require_consent: bool = False


@dataclass
class ErasureRequest:
    user_id: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    scope: str = "all"  # all | sessions | memories | exports


@dataclass
class ErasureReceipt:
    request: ErasureRequest
    completed_at: datetime
    items_deleted: int


class MemoryGovernance:
    """Apply policies and process erasure requests."""

    def __init__(self) -> None:
        self.policies: list[RetentionPolicy] = []

    def add_policy(self, policy: RetentionPolicy) -> None:
        self.policies.append(policy)

    def policy_for(self, memory: dict[str, Any]) -> RetentionPolicy | None:
        type_str = f"type:{memory.get('type')}"
        user_str = f"user:{memory.get('user_id')}"
        project_str = f"project:{memory.get('project_id')}"
        for p in self.policies:
            if p.applies_to in ("all", type_str, user_str, project_str):
                return p
        return None

    def apply_retention(
        self,
        memories: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return the subset of memories that survive policy enforcement."""
        now = now or datetime.now(UTC)
        kept: list[dict[str, Any]] = []
        for m in memories:
            policy = self.policy_for(m)
            if policy is None:
                kept.append(m)
                continue
            ts = m.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    ts = None
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if now - ts > policy.retain_for:
                    continue
            kept.append(m)
        return kept

    def erase(
        self,
        request: ErasureRequest,
        memories: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], ErasureReceipt]:
        """Process a GDPR Article 17 erasure request over an in-memory list."""
        keep = [m for m in memories if m.get("user_id") != request.user_id]
        deleted = len(memories) - len(keep)
        receipt = ErasureReceipt(
            request=request,
            completed_at=datetime.now(UTC),
            items_deleted=deleted,
        )
        return keep, receipt

    async def erase_persisted(
        self,
        request: ErasureRequest,
        store: ErasureStore,
    ) -> ErasureReceipt:
        """Process an erasure request against the persistence layer."""
        deleted = await store.erase_user_data(request.user_id, scope=request.scope)
        return ErasureReceipt(
            request=request,
            completed_at=datetime.now(UTC),
            items_deleted=deleted,
        )


# ── memory stores reachable for right-to-erasure ─────────────────────────────────
#
# Memory engines keep their entries in process (MemoryStore). Every MemoryEngine registers
# its store here under its tenant, so the GDPR erasure endpoint of the same process can
# find and delete a subject's memories. Stores are held weakly: a discarded engine drops
# out. If no store is registered, the process holds no memory records at all.

_STORES: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_STORES_LOCK = threading.Lock()
SCOPES = ("user_id", "agent_id", "session_id", "tenant_id")


def register_memory_store(store: Any, *, tenant_id: str = "default") -> None:
    """Make ``store`` (a :class:`~panoptis.memory.engine.MemoryStore`) erasable for ``tenant_id``."""
    with _STORES_LOCK:
        _STORES[store] = tenant_id


def memory_stores_registered() -> int:
    with _STORES_LOCK:
        return len(_STORES)


def _stores(tenant_id: str | None) -> list[tuple[Any, str]]:
    with _STORES_LOCK:
        return [(s, t) for s, t in list(_STORES.items()) if tenant_id is None or t == tenant_id]


def _matches(entry: Any, identifier: str, scope: str, tenant: str) -> bool:
    if scope == "user_id":
        return str((entry.metadata or {}).get("user_id")) == identifier
    if scope == "agent_id":
        return entry.agent_id == identifier
    if scope == "session_id":
        return entry.session_id == identifier
    if scope == "tenant_id":
        return tenant == identifier
    raise ValueError(f"unknown erasure scope {scope!r}; expected one of {SCOPES}")


async def list_memory_entries(
    identifier: str | None = None, *, scope: str = "user_id", tenant_id: str | None = None
) -> list[dict[str, Any]]:
    """Memory entries of the registered stores (of ``tenant_id``), optionally only those
    matching ``identifier`` in ``scope`` (exact match)."""
    out = []
    for store, tenant in _stores(tenant_id):
        for e in store.all_entries():
            if identifier is None or _matches(e, identifier, scope, tenant):
                out.append(
                    {
                        **e.to_dict(),
                        "tenant_id": tenant,
                        "user_id": (e.metadata or {}).get("user_id"),
                    }
                )
    return out


async def drop_memory_entries(
    identifier: str, *, scope: str = "user_id", tenant_id: str | None = None
) -> int:
    """Delete every memory entry matching ``identifier`` in ``scope`` (exact match) in the
    stores of ``tenant_id`` registered in this process. Returns the number deleted, and raises
    :class:`PartialErasureError` if any store could not delete a match: the other stores are still
    processed. Stores in other processes are not reachable from here. Only memory is touched:
    the evidence ledger is append-only and keeps its own erasure path (crypto-shredding)."""
    if not identifier or not identifier.strip():
        raise ValueError("an erasure needs a non-empty identifier")
    removed, failures = 0, []
    for store, tenant in _stores(tenant_id):
        try:
            for e in [e for e in store.all_entries() if _matches(e, identifier, scope, tenant)]:
                if store.remove(e.entry_id):
                    removed += 1
                else:
                    failures.append(f"entry {e.entry_id} was not removed")
        except Exception as exc:  # noqa: BLE001 - reported below; other stores must still run
            failures.append(f"{type(store).__name__}: {exc}")
    if failures:
        raise PartialErasureError("; ".join(failures[:5]), erased=removed)
    return removed


__all__ = [
    "register_memory_store",
    "memory_stores_registered",
    "list_memory_entries",
    "drop_memory_entries",
    "RetentionPolicy",
    "ErasureRequest",
    "ErasureReceipt",
    "ErasureStore",
    "MemoryGovernance",
]
