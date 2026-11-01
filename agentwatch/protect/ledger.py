"""Security evidence ledger: protection records in the v3 append-only Merkle store.

Every decision, output inspection, approval request/resolution, kill-switch change,
policy change and discovery scan is appended as an observation (``source_kind``
``protect.*``) to the same immutable store the rest of AgentWatch uses. That gives the
security trail the store's guarantees with no second mechanism:

* append-only, enforced by database triggers;
* secrets redacted at the edge before storage (with a manifest);
* Merkle-sealed segments, chain verification and per-record inclusion proofs;
* a shared medium between processes: an MCP gateway or an SDK-protected agent writes
  decisions and approval requests; the API server (dashboard, CLI) reads them and
  appends approvals and kill-switch commands that the agents pick up.

Records are never updated; state (is this request approved? is this agent suspended?)
is derived from the sequence of records.
"""

from __future__ import annotations

import itertools
import os
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from agentwatch.evidence.model import ClockInfo, ObservationDraft, SensorRef
from agentwatch.storage import schema as s
from agentwatch.storage.store import Store

SENSOR_TYPE = "agentwatch.protect"
SENSOR_VERSION = "1"

KIND_DECISION = "protect.decision"
KIND_OUTPUT = "protect.output"
KIND_EXECUTION = "protect.execution"  # the tool actually ran: outcome, duration
KIND_APPROVAL_REQUEST = "protect.approval.request"
KIND_APPROVAL_DECISION = "protect.approval.decision"
KIND_CONTROL = "protect.control"  # kill switch, agent/session suspension
KIND_POLICY = "protect.policy"
KIND_DISCOVERY = "protect.discovery"
KIND_AGENT = "protect.agent"  # agent/session registration and heartbeat
KIND_GATEWAY = "protect.gateway"  # MCP gateway lifecycle and protocol errors
ALL_KINDS = (
    KIND_DECISION,
    KIND_OUTPUT,
    KIND_EXECUTION,
    KIND_APPROVAL_REQUEST,
    KIND_APPROVAL_DECISION,
    KIND_CONTROL,
    KIND_POLICY,
    KIND_DISCOVERY,
    KIND_AGENT,
    KIND_GATEWAY,
)


class ProtectLedger:
    """Append and query protection records. Thread-safe; one per process is enough."""

    def __init__(
        self,
        store: Store | str | None = None,
        *,
        tenant_id: str = "default",
        instance_id: str | None = None,
    ) -> None:
        self.store = store if isinstance(store, Store) else Store(store)
        self.tenant_id = tenant_id
        self.sensor = SensorRef(
            SENSOR_TYPE, SENSOR_VERSION, instance_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    # ── write ──────────────────────────────────────────────────────────────────────
    def record(
        self,
        kind: str,
        payload: dict[str, Any],
        declared: dict[str, str | None] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str | None:
        ids = {k: str(v) for k, v in (declared or {}).items() if v is not None}
        draft = ObservationDraft(
            sensor=self.sensor,
            source_kind=kind,
            payload={**payload, "record_id": uuid.uuid4().hex},
            source_seq=next(self._seq),
            observed_at=datetime.now(UTC),
            clock=ClockInfo(),
            declared_ids=ids,
            tenant_id=tenant_id or self.tenant_id,
        )
        with self._lock:
            res = self.store.append([draft])
        if res.rejected:
            raise ValueError(f"protect ledger rejected a record: {res.rejected}")
        return (res.accepted or res.duplicates or [None])[0]

    # ── read ───────────────────────────────────────────────────────────────────────
    def query(
        self,
        kinds: tuple[str, ...] | list[str] = ALL_KINDS,
        *,
        after: str | None = None,
        limit: int = 200,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Records of the given kinds, newest first by default."""
        t = s.observations
        q = select(t.c.obs_id).where(
            t.c.tenant_id == self.tenant_id, t.c.source_kind.in_(list(kinds))
        )
        if after:
            q = q.where(t.c.obs_id > after)
        q = q.order_by(t.c.obs_id.desc() if newest_first else t.c.obs_id).limit(limit)
        with self.store.engine.connect() as conn:
            ids = [r[0] for r in conn.execute(q)]
        if not ids:
            return []
        obs = {o.obs_id: o for o in self.store.observations(self.tenant_id, obs_ids=ids)}
        return [self._row(obs[i]) for i in ids if i in obs]

    def find(
        self, key: str, value: str, kinds: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        ids = self.store.find_by_declared_id(self.tenant_id, key, value)
        if not ids:
            return []
        rows = [self._row(o) for o in self.store.observations(self.tenant_id, obs_ids=ids)]
        if kinds:
            rows = [r for r in rows if r["kind"] in kinds]
        return sorted(rows, key=lambda r: r["obs_id"])

    def latest_id(self) -> str | None:
        t = s.observations
        q = (
            select(t.c.obs_id)
            .where(t.c.tenant_id == self.tenant_id, t.c.source_kind.in_(list(ALL_KINDS)))
            .order_by(t.c.obs_id.desc())
            .limit(1)
        )
        with self.store.engine.connect() as conn:
            r = conn.execute(q).first()
        return r[0] if r else None

    @staticmethod
    def _row(o: Any) -> dict[str, Any]:
        return {
            "obs_id": o.obs_id,
            "kind": o.source_kind,
            "observed_at": o.observed_at.isoformat() if getattr(o, "observed_at", None) else None,
            "declared": dict(o.declared_ids),
            "payload": o.payload(),
            "redactions": o.redaction.total if o.redaction else 0,
            "sensor_instance": o.sensor.instance_id,
        }

    # ── integrity ──────────────────────────────────────────────────────────────────
    def verify(self, *, seal: bool = True) -> dict[str, Any]:
        """Seal pending records, then verify the whole Merkle chain of the tenant."""
        sealed = self.store.seal_all(self.tenant_id) if seal else []
        report = self.store.verify(self.tenant_id)
        return {
            "ok": report.ok,
            "sealed_now": len(sealed),
            "segments": len(self.store.segments(self.tenant_id)),
            "errors": list(report.errors)[:20],
            "verified_at": datetime.now(UTC).isoformat(),
        }

    def proof(self, obs_id: str) -> dict[str, Any] | None:
        return self.store.inclusion_proof(obs_id)
