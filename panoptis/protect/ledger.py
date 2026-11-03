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

import hashlib
import itertools
import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from panoptis.evidence.model import ClockInfo, ObservationDraft, SensorRef
from panoptis.storage import schema as s
from panoptis.storage.store import Store, panoptis_home

logger = logging.getLogger(__name__)

SENSOR_TYPE = "panoptis.protect"
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
        seal_every: int = 200,
        seal_interval_s: float = 30.0,
    ) -> None:
        self.store = store if isinstance(store, Store) else Store(store)
        self.tenant_id = tenant_id
        # Records are integrity-protected once sealed; seal after this many records or
        # seconds (per tenant), so the unprotected window stays short. 0 disables.
        self.seal_every = seal_every
        self.seal_interval_s = seal_interval_s
        self._pending: dict[str, int] = {}
        self._last_seal: dict[str, float] = {}
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
        self._maybe_seal(draft.tenant_id)
        stored = res.accepted or res.duplicates
        return stored[0] if stored else None

    def _maybe_seal(self, tenant: str) -> None:
        if not self.seal_every:
            return
        now = time.monotonic()
        with self._lock:
            n = self._pending.get(tenant, 0) + 1
            self._pending[tenant] = n
            due = (
                n >= self.seal_every
                or now - self._last_seal.setdefault(tenant, now) >= self.seal_interval_s
            )
            if not due:
                return
            self._pending[tenant] = 0
            self._last_seal[tenant] = now
        try:
            self.store.seal_all(tenant)
        except Exception as exc:  # noqa: BLE001 - another process sealed concurrently; next time
            logger.debug("auto-seal of %s deferred: %s", tenant, exc)

    # ── read ───────────────────────────────────────────────────────────────────────
    def query(
        self,
        kinds: tuple[str, ...] | list[str] = ALL_KINDS,
        *,
        after: str | None = None,
        limit: int = 200,
        newest_first: bool = True,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Records of the given kinds, newest first by default."""
        tenant = tenant_id or self.tenant_id
        t = s.observations
        q = select(t.c.obs_id).where(t.c.tenant_id == tenant, t.c.source_kind.in_(list(kinds)))
        if after:
            q = q.where(t.c.obs_id > after)
        q = q.order_by(t.c.obs_id.desc() if newest_first else t.c.obs_id).limit(limit)
        with self.store.engine.connect() as conn:
            ids = [r[0] for r in conn.execute(q)]
        if not ids:
            return []
        obs = {o.obs_id: o for o in self.store.observations(tenant, obs_ids=ids)}
        return [self._row(obs[i]) for i in ids if i in obs]

    def find(
        self,
        key: str,
        value: str,
        kinds: tuple[str, ...] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        tenant = tenant_id or self.tenant_id
        ids = self.store.find_by_declared_id(tenant, key, value)
        if not ids:
            return []
        rows = [self._row(o) for o in self.store.observations(tenant, obs_ids=ids)]
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
    def anchor_path(self, tenant_id: str | None = None) -> Path:
        """Where the last verified head is kept, outside the store (truncation check)."""
        tenant = tenant_id or self.tenant_id
        base = Path(os.environ.get("AGENTWATCH_EVIDENCE_ANCHORS") or panoptis_home() / "anchors")
        store_id = hashlib.sha256(str(self.store.engine.url).encode()).hexdigest()[:16]
        return base / f"{store_id}-{tenant}.json"

    def verify(
        self,
        *,
        seal: bool = True,
        tenant_id: str | None = None,
        expected_head: dict[str, Any] | None = None,
        anchor: bool = True,
    ) -> dict[str, Any]:
        """Seal pending records, then verify the tenant's whole chain.

        With ``anchor``, the head seen by the last successful verification (kept in
        :meth:`anchor_path`) must still be in the chain, so a chain cut back to an earlier
        segment fails; the anchor then moves to the new head.
        """
        tenant = tenant_id or self.tenant_id
        sealed = self.store.seal_all(tenant) if seal else []
        path = self.anchor_path(tenant)
        if expected_head is None and anchor and path.is_file():
            try:
                expected_head = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                expected_head = None
        report = self.store.verify(tenant, expected_head=expected_head)
        if report.ok and anchor and report.head:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({**report.head, "tenant_id": tenant}), encoding="utf-8")
                os.replace(tmp, path)
            except OSError as exc:
                logger.warning("could not store the evidence anchor %s: %s", path, exc)
        return {
            **report.to_dict(),  # segments_checked, observations_checked, unsealed_observations, ...
            "ok": report.ok,
            "tenant_id": tenant,
            "sealed_now": len(sealed),
            "segments": report.segments_checked,
            "observations": report.observations_checked,
            "unsealed": report.unsealed_observations,
            "purged_segments": report.purged_segments,
            "head": report.head,
            "expected_head": expected_head,
            "errors": list(report.errors)[:50],
            "warnings": list(report.warnings or [])[:20],
            "verified_at": datetime.now(UTC).isoformat(),
        }

    def proof(self, obs_id: str, tenant_id: str = "default") -> dict[str, Any] | None:
        return self.store.inclusion_proof(obs_id, tenant_id=tenant_id)
