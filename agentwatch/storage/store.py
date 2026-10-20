"""v3 store: evidence (append-only) + versioned interpretations, on SQLite or PostgreSQL.

One implementation serves both backends through SQLAlchemy Core. All state lives in
the database (and the blob directory), never in process memory, so any number of API or
worker processes can share a store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, delete, event, func, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentwatch.evidence import crypto
from agentwatch.evidence.blobs import BlobStore, FsBlobStore, MemoryBlobStore
from agentwatch.evidence.canonical import canonical_json, sha256_hex
from agentwatch.evidence.ids import new_ulid
from agentwatch.evidence.model import (
    ClockInfo,
    ObservationDraft,
    RawObservation,
    RedactionManifest,
    SamplingInfo,
    SensorRef,
    parse_ts,
)
from agentwatch.evidence.redaction import DEFAULT_POLICY, PayloadPolicy, redact_payload
from agentwatch.evidence.segments import (
    GENESIS,
    Segment,
    VerifyReport,
    merkle_proof,
    merkle_root,
    segment_hash,
)
from agentwatch.graph.model import (
    EVIDENCE_RANK,
    STRENGTH_RANK,
    EvidenceClass,
    RelType,
    View,
)
from agentwatch.graph.traverse import Step
from agentwatch.storage import schema as s

INLINE_PAYLOAD_LIMIT = 64 * 1024


class ImmutableEvidenceError(RuntimeError):
    """Raised when something attempts to modify stored evidence."""


class SealedRunError(PermissionError):
    """Raised when an operation attempts to mutate or delete data for a sealed run."""


@dataclass
class AppendResult:
    accepted: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)
    redacted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": len(self.accepted),
            "duplicates": len(self.duplicates),
            "rejected": [{"index": i, "reason": r} for i, r in self.rejected],
            "redacted_observations": self.redacted,
            "obs_ids": self.accepted,
        }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


logger = logging.getLogger(__name__)


def agentwatch_home() -> Path:
    """AGENTWATCH_HOME, or ~/.agentwatch when unset or empty (an empty value exported from a
    template must not mean the current directory)."""
    return Path(os.environ.get("AGENTWATCH_HOME") or Path.home() / ".agentwatch")


def default_store_url() -> str:
    url = os.environ.get("AGENTWATCH_STORE")
    if url:
        return url
    return f"sqlite:///{(agentwatch_home() / 'agentwatch.db').as_posix()}"


def default_blob_store(url: str, blob_dir: str | Path | None = None) -> BlobStore:
    """Where payloads larger than INLINE_PAYLOAD_LIMIT live. They must persist as long as the
    observations that reference them, so memory is used only for an in-memory SQLite store.

    Order: explicit ``blob_dir`` > AGENTWATCH_BLOB_DIR > next to a SQLite file >
    $AGENTWATCH_HOME/blobs (a server database: every API/worker replica must share it)."""
    if blob_dir is not None:
        return FsBlobStore(blob_dir)
    if os.environ.get("AGENTWATCH_BLOB_DIR"):
        return FsBlobStore(os.environ["AGENTWATCH_BLOB_DIR"])
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if not path or ":memory:" in url:
            return MemoryBlobStore()
        return FsBlobStore(Path(path).parent / "blobs")
    root = agentwatch_home() / "blobs"
    logger.warning(
        "large payloads of this %s store are kept in %s; set AGENTWATCH_BLOB_DIR to a directory "
        "every API/worker replica shares",
        url.split(":", 1)[0],
        root,
    )
    return FsBlobStore(root)


class Store:
    def __init__(
        self,
        url: str | None = None,
        *,
        blob_dir: str | Path | None = None,
        blob_store: BlobStore | None = None,
        encrypt_payloads: bool | None = None,
    ) -> None:
        self.url = url or default_store_url()
        if encrypt_payloads is None:
            encrypt_payloads = os.environ.get("AGENTWATCH_ENCRYPT_PAYLOADS", "").lower() in (
                "1",
                "true",
                "yes",
            )
        if encrypt_payloads and not crypto.available():
            raise crypto.CryptoUnavailableError(
                "AGENTWATCH_ENCRYPT_PAYLOADS requires the 'cryptography' package"
            )
        self.encrypt_payloads = bool(encrypt_payloads)
        self.is_sqlite = self.url.startswith("sqlite")
        kwargs: dict[str, Any] = {"future": True}
        if self.is_sqlite:
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            path = self.url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        else:
            kwargs["pool_pre_ping"] = True
        self.engine: Engine = create_engine(self.url, **kwargs)
        if self.is_sqlite:

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        self.blobs: BlobStore = (
            blob_store if blob_store is not None else default_blob_store(self.url, blob_dir)
        )
        self.init()

    # ── lifecycle ──────────────────────────────────────────────────────────
    def init(self) -> None:
        s.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            for stmt in s.SQLITE_TRIGGERS if self.is_sqlite else s.POSTGRES_TRIGGERS:
                conn.execute(text(stmt))
            existing = conn.execute(
                select(s.meta_table.c.value).where(s.meta_table.c.key == "schema_version")
            ).scalar()
            if existing is None:
                conn.execute(
                    insert(s.meta_table).values(key="schema_version", value=str(s.SCHEMA_VERSION))
                )
            elif int(existing) < s.SCHEMA_VERSION:  # additive upgrades only (new tables)
                conn.execute(
                    update(s.meta_table)
                    .where(s.meta_table.c.key == "schema_version")
                    .values(value=str(s.SCHEMA_VERSION))
                )
            elif int(existing) > s.SCHEMA_VERSION:
                raise RuntimeError(
                    f"store schema {existing} is newer than this AgentWatch ({s.SCHEMA_VERSION})"
                )

    def close(self) -> None:
        self.engine.dispose()

    # ── tenant keys ────────────────────────────────────────────────────────
    def artifact_key(self, tenant_id: str) -> bytes:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(s.tenant_keys.c.artifact_key_hex).where(
                    s.tenant_keys.c.tenant_id == tenant_id
                )
            ).scalar()
            if row:
                return bytes.fromhex(row)
            key = secrets.token_hex(32)
            try:
                conn.execute(
                    insert(s.tenant_keys).values(
                        tenant_id=tenant_id, artifact_key_hex=key, created_at=_now()
                    )
                )
            except IntegrityError:  # pragma: no cover - concurrent creation
                pass
        with self.engine.connect() as conn:
            return bytes.fromhex(
                conn.execute(
                    select(s.tenant_keys.c.artifact_key_hex).where(
                        s.tenant_keys.c.tenant_id == tenant_id
                    )
                ).scalar_one()
            )

    # ── data keys (crypto-shredding) ───────────────────────────────────────
    def data_key(
        self, tenant_id: str, subject: str | None, *, create: bool = True
    ) -> tuple[str, bytes | None] | None:
        """(key_id, key) for a subject ('' = tenant default). key is None once destroyed."""
        subj = subject or ""
        with self.engine.begin() as conn:
            row = conn.execute(
                select(s.data_keys.c.key_id, s.data_keys.c.key_hex).where(
                    s.data_keys.c.tenant_id == tenant_id, s.data_keys.c.subject == subj
                )
            ).first()
            if row is not None:
                return row.key_id, bytes.fromhex(row.key_hex) if row.key_hex else None
            if not create:
                return None
            key_id = secrets.token_hex(8)
            try:
                conn.execute(
                    insert(s.data_keys).values(
                        key_id=key_id,
                        tenant_id=tenant_id,
                        subject=subj,
                        key_hex=crypto.new_key().hex(),
                        created_at=_now(),
                    )
                )
            except IntegrityError:  # pragma: no cover - concurrent creation
                pass
        return self.data_key(tenant_id, subject, create=False)

    def _key_by_id(self, key_id: str, cache: dict[str, bytes | None]) -> bytes | None:
        if key_id not in cache:
            with self.engine.connect() as conn:
                hexkey = conn.execute(
                    select(s.data_keys.c.key_hex).where(s.data_keys.c.key_id == key_id)
                ).scalar()
            cache[key_id] = bytes.fromhex(hexkey) if hexkey else None
        return cache[key_id]

    def erase_subject(
        self, tenant_id: str, subject: str, *, reason: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Crypto-shred a data subject: destroy its key, then purge every derived row that may
        hold its plaintext (events, relations, artifacts, analyses of all interpretations of
        the tenant, and experiments about affected runs). Raw observations are untouched, so
        the evidence chain still verifies; their payloads just become unreadable."""
        found = self.data_key(tenant_id, subject, create=False)
        obs_ids = self.find_by_declared_id(tenant_id, "subject_id", subject)
        with self.engine.begin() as conn:
            if found is not None:
                conn.execute(
                    update(s.data_keys)
                    .where(s.data_keys.c.key_id == found[0])
                    .values(key_hex=None, destroyed_at=_now(), reason=reason)
                )
            runs = self._runs_of(conn, obs_ids)
            interp_ids, artifact_blobs, removed_experiments = self._clear_derived(
                conn, tenant_id, runs
            )
            report = {
                "subject": subject,
                "key_destroyed": found is not None,
                "observations_affected": len(obs_ids),
                "runs_affected": sorted(runs),
                "derived_data_purged": {
                    "interpretations": len(interp_ids),
                    "experiments": removed_experiments,
                },
                "note": "raw observations are unchanged (ciphertext); rebuild derived data with Engine.process(force=True)",
            }
            report["blobs_deleted"] = self._delete_unreferenced_blobs(conn, artifact_blobs)
            conn.execute(
                insert(s.erasures).values(
                    erasure_id=new_ulid(),
                    tenant_id=tenant_id,
                    subject=subject,
                    key_id=found[0] if found else None,
                    reason=reason,
                    actor=actor,
                    erased_at=_now(),
                    observations_affected=len(obs_ids),
                    doc=canonical_json(report),
                )
            )
        return report

    def _runs_of(self, conn: Any, obs_ids: Sequence[str]) -> set[str]:
        """Runs (of any interpretation) with an event derived from one of ``obs_ids``."""
        runs: set[str] = set()
        for chunk in _chunks(list(obs_ids), 500):
            ev = [
                r[0]
                for r in conn.execute(
                    select(s.event_sources.c.event_id).where(s.event_sources.c.obs_id.in_(chunk))
                )
            ]
            for echunk in _chunks(ev, 500):
                runs.update(
                    r[0]
                    for r in conn.execute(
                        select(s.events.c.run_id).where(s.events.c.event_id.in_(echunk))
                    )
                    if r[0]
                )
        return runs

    def _clear_derived(
        self, conn: Any, tenant_id: str, runs: set[str]
    ) -> tuple[list[str], set[str], int]:
        """Delete every derived row of the tenant (all interpretations, artifacts) and the
        experiments about ``runs``: they may hold copies of payloads that are going away.
        Returns (interp_ids, artifact blob digests, experiments removed)."""
        interp_ids = [
            r[0]
            for r in conn.execute(
                select(s.interpretations.c.interp_id).where(
                    s.interpretations.c.tenant_id == tenant_id
                )
            )
        ]
        for table in (
            s.events,
            s.event_sources,
            s.diagnostics,
            s.entities,
            s.runs,
            s.relations,
            s.relation_members,
            s.derived,
        ):
            for chunk in _chunks(interp_ids, 200):
                conn.execute(delete(table).where(table.c.interp_id.in_(chunk)))
        conn.execute(
            update(s.interpretations)
            .where(s.interpretations.c.tenant_id == tenant_id)
            .values(processed_through=None)
        )
        artifact_blobs = {
            r[0]
            for r in conn.execute(
                select(s.artifacts.c.content_blob).where(
                    s.artifacts.c.tenant_id == tenant_id, s.artifacts.c.content_blob.is_not(None)
                )
            )
        }
        conn.execute(delete(s.artifacts).where(s.artifacts.c.tenant_id == tenant_id))
        removed_experiments = 0
        for chunk in _chunks(sorted(runs), 200):
            removed_experiments += int(
                conn.execute(
                    delete(s.experiments).where(
                        s.experiments.c.tenant_id == tenant_id,
                        s.experiments.c.subject.in_(chunk),
                    )
                ).rowcount
                or 0
            )
        return interp_ids, artifact_blobs, removed_experiments

    def _delete_unreferenced_blobs(self, conn: Any, digests: set[str]) -> int:
        """Delete the blob files among ``digests`` that no observation or artifact (of any
        tenant: blobs are content-addressed and shared) still references.

        Limitation: a payload byte-identical to a deleted one, ingested concurrently between
        this check and its row insert, would lose its blob; run retention when that cannot
        happen (e.g. not while replaying the same large payloads)."""
        deleted = 0
        for digest in sorted(digests):
            used = (
                conn.execute(
                    select(s.observations.c.obs_id)
                    .where(s.observations.c.payload_blob == digest)
                    .limit(1)
                ).first()
                or conn.execute(
                    select(s.artifacts.c.artifact_id)
                    .where(s.artifacts.c.content_blob == digest)
                    .limit(1)
                ).first()
            )
            if not used and self.blobs.delete(digest):
                deleted += 1
        return deleted

    def erasures(self, tenant_id: str = "default") -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                json.loads(r[0]) | {"erased_at": r[1], "reason": r[2]}
                for r in conn.execute(
                    select(s.erasures.c.doc, s.erasures.c.erased_at, s.erasures.c.reason)
                    .where(s.erasures.c.tenant_id == tenant_id)
                    .order_by(s.erasures.c.erasure_id)
                )
            ]

    # ── evidence: append ───────────────────────────────────────────────────
    def append(
        self, drafts: Sequence[ObservationDraft], policy: PayloadPolicy = DEFAULT_POLICY
    ) -> AppendResult:
        """Append observations. Idempotent on (tenant, idempotency_key)."""
        result = AppendResult()
        received = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        id_rows: list[dict[str, Any]] = []
        keys_seen: dict[tuple[str, str], str] = {}
        rows_seen: dict[tuple[str, str], str] = {}
        key_cache: dict[tuple[str, str], tuple[str, bytes | None]] = {}
        for i, draft in enumerate(drafts):
            problem = _validate_draft(draft)
            if problem:
                result.rejected.append((i, problem))
                continue
            try:
                payload, manifest = redact_payload(draft.payload, policy)
                obs = RawObservation.build(
                    draft,
                    obs_id=new_ulid(),
                    received_at=received,
                    payload=payload,
                    redaction=manifest,
                )
            except (TypeError, ValueError) as exc:
                result.rejected.append((i, f"unserializable payload: {exc}"))
                continue
            if manifest:
                result.redacted += 1
            k = (obs.tenant_id, obs.idempotency_key)
            if k in keys_seen:
                result.duplicates.append(keys_seen[k])
                continue
            keys_seen[k] = obs.obs_id
            row = self._obs_row(obs, key_cache)
            if (obs.tenant_id, row["idempotency_key"]) in rows_seen:
                result.duplicates.append(rows_seen[(obs.tenant_id, row["idempotency_key"])])
                continue
            rows_seen[(obs.tenant_id, row["idempotency_key"])] = obs.obs_id
            rows.append(row)
            id_rows.extend(
                {"obs_id": obs.obs_id, "key": key, "value": val[:256], "tenant_id": obs.tenant_id}
                for key, val in obs.declared_ids_items
            )
        if not rows:
            return result
        with self.engine.begin() as conn:
            existing: dict[tuple[str, str], str] = {}
            by_tenant: dict[str, list[str]] = {}
            for r in rows:
                by_tenant.setdefault(r["tenant_id"], []).append(r["idempotency_key"])
            for tenant, idems in by_tenant.items():
                for chunk in _chunks(idems, 500):
                    q = select(s.observations.c.idempotency_key, s.observations.c.obs_id).where(
                        s.observations.c.tenant_id == tenant,
                        s.observations.c.idempotency_key.in_(chunk),
                    )
                    for idem, oid in conn.execute(q):
                        existing[(tenant, idem)] = oid
            fresh = [r for r in rows if (r["tenant_id"], r["idempotency_key"]) not in existing]
            fresh_ids = {r["obs_id"] for r in fresh}
            result.duplicates.extend(existing.values())
            if fresh:
                conn.execute(insert(s.observations), fresh)
                ids = [r for r in id_rows if r["obs_id"] in fresh_ids]
                if ids:
                    conn.execute(insert(s.declared_ids), ids)
            result.accepted.extend(r["obs_id"] for r in fresh)
        return result

    def _obs_row(
        self,
        obs: RawObservation,
        key_cache: dict[tuple[str, str], tuple[str, bytes | None]] | None = None,
    ) -> dict[str, Any]:
        stored = obs.payload_json
        payload_sha256 = obs.payload_sha256
        idem = obs.idempotency_key
        if self.encrypt_payloads:
            subject = obs.declared("subject_id") or ""
            ck = (obs.tenant_id, subject)
            cache = key_cache if key_cache is not None else {}
            if ck not in cache:
                found = self.data_key(obs.tenant_id, subject)
                assert found is not None  # nosec B101 — data_key auto-creates key if missing
                cache[ck] = found
            key_id, key = cache[ck]
            if key is None:
                raise ValueError(
                    f"data key for subject {subject!r} was destroyed (erased subject); refusing to store new payloads"
                )
            stored = crypto.encrypt(obs.payload_json, key, key_id)
            # the chain hashes the ciphertext so it still verifies after the key is destroyed;
            # the dedup key is keyed by the subject key so erased payloads cannot be guessed from it
            payload_sha256 = sha256_hex(stored)
            idem = hmac.new(key, obs.idempotency_key.encode("utf-8"), hashlib.sha256).hexdigest()
        payload_json: str | None = stored
        payload_blob = None
        if len(stored.encode("utf-8")) > INLINE_PAYLOAD_LIMIT:
            payload_blob = self.blobs.put(stored.encode("utf-8"))
            payload_json = None
        return {
            "obs_id": obs.obs_id,
            "tenant_id": obs.tenant_id,
            "sensor_type": obs.sensor.sensor_type,
            "sensor_version": obs.sensor.sensor_version,
            "sensor_instance": obs.sensor.instance_id,
            "source_kind": obs.source_kind,
            "source_seq": obs.source_seq,
            "idempotency_key": idem,
            "observed_at": _iso(obs.observed_at),
            "received_at": _iso(obs.received_at),
            "clock": canonical_json(obs.clock.to_dict()),
            "content_type": obs.content_type,
            "payload_json": payload_json,
            "payload_blob": payload_blob,
            "payload_sha256": payload_sha256,
            "declared_ids": canonical_json(dict(obs.declared_ids_items)),
            "redaction": canonical_json(obs.redaction.to_dict()) if obs.redaction else None,
            "sampling": canonical_json(obs.sampling.to_dict()) if obs.sampling else None,
            "segment_id": None,
        }

    def _row_to_obs(
        self, r: Any, key_cache: dict[str, bytes | None] | None = None
    ) -> RawObservation:
        payload_json = r.payload_json
        if payload_json is None and r.payload_blob:
            payload_json = self.blobs.get(r.payload_blob).decode("utf-8")
        encoding = "plain"
        if crypto.is_encrypted(payload_json):
            key = self._key_by_id(
                crypto.key_id_of(payload_json), key_cache if key_cache is not None else {}
            )
            if key is None:
                encoding = "erased"
                payload_json = canonical_json(
                    {"$erased": True, "reason": "data key destroyed (subject erased)"}
                )
            else:
                encoding = "aes-gcm"
                payload_json = crypto.decrypt(payload_json, key)
        clock = json.loads(r.clock)
        sampling = json.loads(r.sampling) if r.sampling else None
        return RawObservation(
            obs_id=r.obs_id,
            tenant_id=r.tenant_id,
            sensor=SensorRef(r.sensor_type, r.sensor_version, r.sensor_instance),
            source_kind=r.source_kind,
            source_seq=r.source_seq,
            idempotency_key=r.idempotency_key,
            observed_at=parse_ts(r.observed_at) if r.observed_at else None,
            received_at=parse_ts(r.received_at),
            clock=ClockInfo(
                clock.get("source", "sensor"), clock.get("clock_id"), clock.get("precision_ms")
            ),
            content_type=r.content_type,
            payload_json=payload_json or "null",
            payload_sha256=r.payload_sha256,
            declared_ids_items=tuple(sorted(json.loads(r.declared_ids).items())),
            redaction=RedactionManifest.from_dict(json.loads(r.redaction) if r.redaction else None),
            sampling=SamplingInfo(sampling["policy"], sampling["rate"]) if sampling else None,
            segment_id=r.segment_id,
            encoding=encoding,
        )

    # ── evidence: read ─────────────────────────────────────────────────────
    def get_observation(self, obs_id: str) -> RawObservation | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.observations).where(s.observations.c.obs_id == obs_id)
            ).first()
        return self._row_to_obs(r) if r else None

    def observations(
        self,
        tenant_id: str = "default",
        *,
        after: str | None = None,
        through: str | None = None,
        source_kinds: Iterable[str] | None = None,
        obs_ids: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[RawObservation]:
        q = select(s.observations).where(s.observations.c.tenant_id == tenant_id)
        if after:
            q = q.where(s.observations.c.obs_id > after)
        if through:
            q = q.where(s.observations.c.obs_id <= through)
        if source_kinds is not None:
            q = q.where(s.observations.c.source_kind.in_(list(source_kinds)))
        if obs_ids is not None:
            q = q.where(s.observations.c.obs_id.in_(list(obs_ids)))
        q = q.order_by(s.observations.c.obs_id)
        if limit:
            q = q.limit(limit)
        cache: dict[str, bytes | None] = {}
        with self.engine.connect() as conn:
            rows = list(conn.execute(q))
        return [self._row_to_obs(r, cache) for r in rows]

    def count_observations(self, tenant_id: str = "default") -> int:
        with self.engine.connect() as conn:
            return int(
                conn.execute(
                    select(func.count())
                    .select_from(s.observations)
                    .where(s.observations.c.tenant_id == tenant_id)
                ).scalar_one()
            )

    def latest_obs_id(self, tenant_id: str = "default") -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(
                select(func.max(s.observations.c.obs_id)).where(
                    s.observations.c.tenant_id == tenant_id
                )
            ).scalar()

    def find_by_declared_id(self, tenant_id: str, key: str, value: str) -> list[str]:
        q = select(s.declared_ids.c.obs_id).where(
            s.declared_ids.c.tenant_id == tenant_id,
            s.declared_ids.c.key == key,
            s.declared_ids.c.value == value,
        )
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(q)]

    def tenants(self) -> list[str]:
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(select(s.observations.c.tenant_id).distinct())]

    # ── evidence: sealing & verification ───────────────────────────────────
    def seal(self, tenant_id: str = "default", max_obs: int = 10_000) -> Segment | None:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(
                    s.observations.c.obs_id,
                    s.observations.c.payload_sha256,
                    s.observations.c.idempotency_key,
                )
                .where(
                    s.observations.c.tenant_id == tenant_id, s.observations.c.segment_id.is_(None)
                )
                .order_by(s.observations.c.obs_id)
                .limit(max_obs)
            ).all()
            if not rows:
                return None
            last = conn.execute(
                select(s.segments.c.seq, s.segments.c.segment_hash)
                .where(s.segments.c.tenant_id == tenant_id)
                .order_by(s.segments.c.seq.desc())
                .limit(1)
            ).first()
            seq = (last.seq + 1) if last else 1
            prev = last.segment_hash if last else GENESIS
            leaves = [_leaf(tenant_id, r) for r in rows]
            root = merkle_root(leaves)
            seg = Segment(
                segment_id=f"{tenant_id}:{seq:08d}",
                tenant_id=tenant_id,
                seq=seq,
                first_obs=rows[0].obs_id,
                last_obs=rows[-1].obs_id,
                n_obs=len(rows),
                merkle_root=root,
                prev_segment_hash=prev,
                segment_hash=segment_hash(tenant_id, seq, root, prev, len(rows)),
                sealed_at=datetime.now(UTC),
            )
            conn.execute(
                insert(s.segments).values(
                    **{**seg.to_dict(), "sealed_at": seg.sealed_at.isoformat(), "purged_at": None}
                )
            )
            for chunk in _chunks([r.obs_id for r in rows], 500):
                conn.execute(
                    update(s.observations)
                    .where(s.observations.c.obs_id.in_(chunk))
                    .values(segment_id=seg.segment_id)
                )
        return seg

    def seal_all(self, tenant_id: str = "default") -> list[Segment]:
        out = []
        while (seg := self.seal(tenant_id)) is not None:
            out.append(seg)
        return out

    def segments(self, tenant_id: str = "default") -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(r._mapping)
                for r in conn.execute(
                    select(s.segments)
                    .where(s.segments.c.tenant_id == tenant_id)
                    .order_by(s.segments.c.seq)
                )
            ]

    def verify(self, tenant_id: str = "default") -> VerifyReport:
        errors: list[str] = []
        n_obs = 0
        prev = GENESIS
        segs = self.segments(tenant_id)
        with self.engine.connect() as conn:
            for seg in segs:
                expect = segment_hash(
                    tenant_id,
                    seg["seq"],
                    seg["merkle_root"],
                    seg["prev_segment_hash"],
                    seg["n_obs"],
                )
                if seg["prev_segment_hash"] != prev:
                    errors.append(f"segment {seg['segment_id']}: chain broken (prev hash mismatch)")
                if expect != seg["segment_hash"]:
                    errors.append(f"segment {seg['segment_id']}: segment hash mismatch")
                prev = seg["segment_hash"]
                if seg["purged_at"]:
                    continue  # rows removed by authorized retention purge; chain still verifies
                rows = conn.execute(
                    select(
                        s.observations.c.obs_id,
                        s.observations.c.payload_sha256,
                        s.observations.c.idempotency_key,
                        s.observations.c.payload_json,
                        s.observations.c.payload_blob,
                    )
                    .where(s.observations.c.segment_id == seg["segment_id"])
                    .order_by(s.observations.c.obs_id)
                ).all()
                n_obs += len(rows)
                if len(rows) != seg["n_obs"]:
                    errors.append(
                        f"segment {seg['segment_id']}: expected {seg['n_obs']} observations, found {len(rows)}"
                    )
                for r in rows:
                    body = (
                        r.payload_json
                        if r.payload_json is not None
                        else self.blobs.get(r.payload_blob).decode("utf-8")
                    )
                    from agentwatch.evidence.canonical import sha256_hex

                    if sha256_hex(body) != r.payload_sha256:
                        errors.append(f"observation {r.obs_id}: payload hash mismatch")
                if merkle_root([_leaf(tenant_id, r) for r in rows]) != seg["merkle_root"]:
                    errors.append(f"segment {seg['segment_id']}: merkle root mismatch")
            unsealed = int(
                conn.execute(
                    select(func.count())
                    .select_from(s.observations)
                    .where(
                        s.observations.c.tenant_id == tenant_id,
                        s.observations.c.segment_id.is_(None),
                    )
                ).scalar_one()
            )
        return VerifyReport(
            ok=not errors,
            segments_checked=len(segs),
            observations_checked=n_obs,
            unsealed_observations=unsealed,
            errors=errors,
        )

    def inclusion_proof(self, obs_id: str) -> dict[str, Any] | None:
        obs = self.get_observation(obs_id)
        if obs is None or obs.segment_id is None:
            return None
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    s.observations.c.obs_id,
                    s.observations.c.payload_sha256,
                    s.observations.c.idempotency_key,
                )
                .where(s.observations.c.segment_id == obs.segment_id)
                .order_by(s.observations.c.obs_id)
            ).all()
            seg = conn.execute(
                select(s.segments).where(s.segments.c.segment_id == obs.segment_id)
            ).first()
        leaves = [_leaf(obs.tenant_id, r) for r in rows]
        idx = [r.obs_id for r in rows].index(obs_id)
        return {
            "obs_id": obs_id,
            "segment_id": obs.segment_id,
            "leaf": leaves[idx],
            "proof": merkle_proof(leaves, idx),
            "merkle_root": seg.merkle_root if seg else None,
            "segment_hash": seg.segment_hash if seg else None,
        }

    # ── retention ──────────────────────────────────────────────────────────
    def purge_segment(self, segment_id: str, reason: str, actor: str | None = None) -> int:
        """Purge one sealed segment; see :meth:`purge_segments`. Returns observations removed."""
        return int(self.purge_segments([segment_id], reason, actor)["observations"])

    def purge_segments(
        self, segment_ids: Sequence[str], reason: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Remove sealed segments' observations under retention policy.

        The authorization is recorded and the segment row (with its hashes) is kept, so
        the chain still verifies and the purge is visible. Everything that may hold a copy
        of the purged payloads goes too: the blob files of large payloads, and the tenant's
        derived data (events, relations, artifacts and their blobs, analyses, experiments
        about affected runs). Rebuild the derived data afterwards with
        ``Engine.process(force=True)`` (``Engine.purge_segments`` does both).
        """
        removed = 0
        blobs: set[str] = set()
        runs: set[str] = set()
        tenants: set[str] = set()
        with self.engine.begin() as conn:
            for segment_id in segment_ids:
                seg = conn.execute(
                    select(s.segments).where(s.segments.c.segment_id == segment_id)
                ).first()
                if seg is None:
                    raise KeyError(segment_id)
                tenants.add(seg.tenant_id)
                conn.execute(
                    insert(s.purge_authorizations).values(
                        segment_id=segment_id,
                        tenant_id=seg.tenant_id,
                        reason=reason,
                        authorized_at=_now(),
                        actor=actor,
                    )
                )
                rows = conn.execute(
                    select(s.observations.c.obs_id, s.observations.c.payload_blob).where(
                        s.observations.c.segment_id == segment_id
                    )
                ).all()
                ids = [r[0] for r in rows]
                blobs.update(r[1] for r in rows if r[1])
                runs |= self._runs_of(conn, ids)
                for chunk in _chunks(ids, 500):
                    conn.execute(delete(s.declared_ids).where(s.declared_ids.c.obs_id.in_(chunk)))
                removed += int(
                    conn.execute(
                        delete(s.observations).where(s.observations.c.segment_id == segment_id)
                    ).rowcount
                    or 0
                )
                conn.execute(
                    update(s.segments)
                    .where(s.segments.c.segment_id == segment_id)
                    .values(purged_at=_now())
                )
            experiments = 0
            for tenant in sorted(tenants):
                _, artifact_blobs, n = self._clear_derived(conn, tenant, runs)
                blobs |= artifact_blobs
                experiments += n
            deleted = self._delete_unreferenced_blobs(conn, blobs)
        return {
            "segments": len(segment_ids),
            "observations": removed,
            "blobs_deleted": deleted,
            "experiments_removed": experiments,
            "tenants_to_rebuild": sorted(tenants),
        }

    def segments_sealed_before(self, tenant_id: str, cutoff: datetime) -> list[str]:
        return [
            seg["segment_id"]
            for seg in self.segments(tenant_id)
            if not seg["purged_at"] and parse_ts(seg["sealed_at"]) < cutoff
        ]

    # ── interpretations ────────────────────────────────────────────────────
    def active_interpretation(self, tenant_id: str = "default") -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.interpretations)
                .where(
                    s.interpretations.c.tenant_id == tenant_id,
                    s.interpretations.c.status == "active",
                )
                .order_by(s.interpretations.c.created_at.desc())
                .limit(1)
            ).first()
        return _interp(r) if r else None

    def interpretations(self, tenant_id: str = "default") -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                _interp(r)
                for r in conn.execute(
                    select(s.interpretations)
                    .where(s.interpretations.c.tenant_id == tenant_id)
                    .order_by(s.interpretations.c.created_at)
                )
            ]

    def get_interpretation(self, interp_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.interpretations).where(s.interpretations.c.interp_id == interp_id)
            ).first()
        return _interp(r) if r else None

    def activate_interpretation(
        self, tenant_id: str, interp_id: str, pipeline: dict[str, Any], config_hash: str
    ) -> None:
        with self.engine.begin() as conn:
            if not self.is_sqlite:  # concurrent processes activate the same interpretation id
                conn.execute(
                    text("SELECT pg_advisory_xact_lock(:k)"),
                    {"k": _lock_key(f"activate|{tenant_id}")},
                )
            conn.execute(
                update(s.interpretations)
                .where(
                    s.interpretations.c.tenant_id == tenant_id,
                    s.interpretations.c.interp_id != interp_id,
                    s.interpretations.c.status == "active",
                )
                .values(status="superseded")
            )
            exists = conn.execute(
                select(s.interpretations.c.interp_id).where(
                    s.interpretations.c.interp_id == interp_id
                )
            ).first()
            if exists:
                conn.execute(
                    update(s.interpretations)
                    .where(s.interpretations.c.interp_id == interp_id)
                    .values(status="active")
                )
            else:
                conn.execute(
                    insert(s.interpretations).values(
                        interp_id=interp_id,
                        tenant_id=tenant_id,
                        pipeline=canonical_json(pipeline),
                        config_hash=config_hash,
                        created_at=_now(),
                        status="active",
                        processed_through=None,
                        stats=None,
                    )
                )

    def write_interpretation(
        self,
        interp_id: str,
        tenant_id: str,
        *,
        events: Sequence[dict[str, Any]],
        diagnostics: Sequence[dict[str, Any]],
        artifacts: Sequence[dict[str, Any]],
        entities: Sequence[dict[str, Any]],
        runs: Sequence[dict[str, Any]],
        relations: Sequence[dict[str, Any]],
        processed_through: str | None,
        stats: dict[str, Any],
        derived: Sequence[dict[str, Any]] = (),
    ) -> None:
        """Replace all derived rows of ``interp_id`` atomically. Evidence is untouched."""
        with self.engine.begin() as conn:
            if not self.is_sqlite:  # serialize concurrent rebuilds of one interpretation
                conn.execute(
                    text("SELECT pg_advisory_xact_lock(:k)"),
                    {"k": int(sha256_hex(interp_id)[:15], 16)},
                )
            for table in (
                s.events,
                s.event_sources,
                s.diagnostics,
                s.entities,
                s.runs,
                s.relations,
                s.relation_members,
            ):
                conn.execute(delete(table).where(table.c.interp_id == interp_id))
            conn.execute(delete(s.derived).where(s.derived.c.interp_id == interp_id))
            self._insert_rows(
                conn,
                interp_id,
                tenant_id,
                events=events,
                diagnostics=diagnostics,
                artifacts=artifacts,
                entities=entities,
                runs=runs,
                relations=relations,
                derived=derived,
            )
            conn.execute(
                update(s.interpretations)
                .where(s.interpretations.c.interp_id == interp_id)
                .values(processed_through=processed_through, stats=canonical_json(stats))
            )

    def _insert_rows(
        self,
        conn: Any,
        interp_id: str,
        tenant_id: str,
        *,
        events: Sequence[dict[str, Any]],
        diagnostics: Sequence[dict[str, Any]],
        artifacts: Sequence[dict[str, Any]],
        entities: Sequence[dict[str, Any]],
        runs: Sequence[dict[str, Any]],
        relations: Sequence[dict[str, Any]],
        derived: Sequence[dict[str, Any]],
    ) -> None:
        if events:
            conn.execute(
                insert(s.events),
                [
                    {
                        "event_id": e["event_id"],
                        "interp_id": interp_id,
                        "tenant_id": tenant_id,
                        "run_id": e.get("run_id"),
                        "kind": e["kind"],
                        "operation": e["operation"][:256],
                        "actor": e.get("actor"),
                        "object": e.get("object"),
                        "status": e["status"],
                        "t_start": e["time"]["start"],
                        "t_end": e["time"]["end"],
                        "ordering_key": e["time"].get("ordering_key", "")[:160],
                        "doc": canonical_json(e),
                    }
                    for e in events
                ],
            )
            conn.execute(
                insert(s.event_sources),
                [
                    {"event_id": e["event_id"], "interp_id": interp_id, "obs_id": o}
                    for e in events
                    for o in sorted(set(e["derived_from"]))
                ],
            )
        if diagnostics:
            conn.execute(
                insert(s.diagnostics),
                [{"interp_id": interp_id, "tenant_id": tenant_id, **d} for d in diagnostics],
            )
        if artifacts:
            self._put_artifacts(conn, artifacts)
        if entities:
            conn.execute(
                insert(s.entities),
                [
                    {
                        "entity_id": en["entity_id"],
                        "tenant_id": tenant_id,
                        "interp_id": interp_id,
                        "kind": en["kind"],
                        "canonical_key": en["canonical_key"][:512],
                        "doc": canonical_json(en),
                    }
                    for en in entities
                ],
            )
        if runs:
            conn.execute(
                insert(s.runs),
                [
                    {
                        "run_id": r["run_id"],
                        "interp_id": interp_id,
                        "tenant_id": tenant_id,
                        "name": (r.get("name") or "")[:256],
                        "started_at": r.get("started_at"),
                        "ended_at": r.get("ended_at"),
                        "status": r.get("status"),
                        "system_version": r.get("system_version"),
                        "doc": canonical_json(r),
                    }
                    for r in runs
                ],
            )
        if relations:
            self._put_relations_conn(conn, tenant_id, interp_id, relations)
        if derived:
            conn.execute(
                insert(s.derived), [self._derived_row(interp_id, tenant_id, r) for r in derived]
            )

    def replace_partial(
        self,
        interp_id: str,
        tenant_id: str,
        *,
        run_ids: Sequence[str],
        obs_ids: Sequence[str],
        events: Sequence[dict[str, Any]],
        diagnostics: Sequence[dict[str, Any]],
        artifacts: Sequence[dict[str, Any]],
        entities: Sequence[dict[str, Any]],
        runs: Sequence[dict[str, Any]],
        relations: Sequence[dict[str, Any]],
        derived: Sequence[dict[str, Any]],
        processed_through: str | None,
    ) -> dict[str, int]:
        """Replace the derived rows of specific runs (and of events derived from ``obs_ids``)
        atomically; entities are replaced wholesale because they aggregate over all runs."""
        with self.engine.begin() as conn:
            if not self.is_sqlite:
                conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(interp_id)})
            stale_events: set[str] = set()
            for chunk in _chunks(list(obs_ids), 500):
                stale_events.update(
                    r[0]
                    for r in conn.execute(
                        select(s.event_sources.c.event_id).where(
                            s.event_sources.c.interp_id == interp_id,
                            s.event_sources.c.obs_id.in_(chunk),
                        )
                    )
                )
            for chunk in _chunks(list(run_ids), 500):
                stale_events.update(
                    r[0]
                    for r in conn.execute(
                        select(s.events.c.event_id).where(
                            s.events.c.interp_id == interp_id, s.events.c.run_id.in_(chunk)
                        )
                    )
                )
                rel_ids = [
                    r[0]
                    for r in conn.execute(
                        select(s.relations.c.rel_id).where(
                            s.relations.c.interp_id == interp_id, s.relations.c.run_id.in_(chunk)
                        )
                    )
                ]
                for rchunk in _chunks(rel_ids, 500):
                    conn.execute(
                        delete(s.relation_members).where(
                            s.relation_members.c.interp_id == interp_id,
                            s.relation_members.c.rel_id.in_(rchunk),
                        )
                    )
                    conn.execute(
                        delete(s.relations).where(
                            s.relations.c.interp_id == interp_id, s.relations.c.rel_id.in_(rchunk)
                        )
                    )
                conn.execute(
                    delete(s.runs).where(
                        s.runs.c.interp_id == interp_id, s.runs.c.run_id.in_(chunk)
                    )
                )
                conn.execute(
                    delete(s.derived).where(
                        s.derived.c.interp_id == interp_id, s.derived.c.scope.in_(chunk)
                    )
                )
            for chunk in _chunks(sorted(stale_events), 500):
                conn.execute(
                    delete(s.event_sources).where(
                        s.event_sources.c.interp_id == interp_id,
                        s.event_sources.c.event_id.in_(chunk),
                    )
                )
                conn.execute(
                    delete(s.events).where(
                        s.events.c.interp_id == interp_id, s.events.c.event_id.in_(chunk)
                    )
                )
            for chunk in _chunks(list(obs_ids), 500):
                conn.execute(
                    delete(s.diagnostics).where(
                        s.diagnostics.c.interp_id == interp_id, s.diagnostics.c.obs_id.in_(chunk)
                    )
                )
            conn.execute(delete(s.entities).where(s.entities.c.interp_id == interp_id))
            self._insert_rows(
                conn,
                interp_id,
                tenant_id,
                events=events,
                diagnostics=diagnostics,
                artifacts=artifacts,
                entities=entities,
                runs=runs,
                relations=relations,
                derived=derived,
            )
            counts = {
                name: int(
                    conn.execute(
                        select(func.count()).select_from(t).where(t.c.interp_id == interp_id)
                    ).scalar_one()
                )
                for name, t in (
                    ("events", s.events),
                    ("runs", s.runs),
                    ("relations", s.relations),
                    ("entities", s.entities),
                    ("diagnostics", s.diagnostics),
                    ("derived_records", s.derived),
                )
            }
            conn.execute(
                update(s.interpretations)
                .where(s.interpretations.c.interp_id == interp_id)
                .values(
                    processed_through=processed_through,
                    stats=canonical_json({**counts, "incremental": True}),
                )
            )
        return counts

    def _put_artifacts(self, conn: Any, artifacts: Sequence[dict[str, Any]]) -> None:
        by_tenant: dict[str, list[dict[str, Any]]] = {}
        for a in artifacts:
            by_tenant.setdefault(a["tenant_id"], []).append(a)
        for tenant, items in by_tenant.items():
            have: set[str] = set()
            for chunk in _chunks([a["artifact_id"] for a in items], 500):
                have.update(
                    r[0]
                    for r in conn.execute(
                        select(s.artifacts.c.artifact_id).where(
                            s.artifacts.c.tenant_id == tenant, s.artifacts.c.artifact_id.in_(chunk)
                        )
                    )
                )
            rows = []
            for a in items:
                if a["artifact_id"] in have:
                    continue
                have.add(a["artifact_id"])
                content = a["content_json"]
                blob = None
                if len(content.encode("utf-8")) > INLINE_PAYLOAD_LIMIT:
                    blob = self.blobs.put(content.encode("utf-8"))
                    content = None
                rows.append(
                    {
                        "tenant_id": tenant,
                        "artifact_id": a["artifact_id"],
                        "media_type": a["media_type"],
                        "size_bytes": a["size_bytes"],
                        "preview": a["preview"],
                        "content_json": content,
                        "content_blob": blob,
                    }
                )
            if rows:
                conn.execute(insert(s.artifacts), rows)

    def put_derived(
        self,
        interp_id: str,
        tenant_id: str,
        records: Sequence[dict[str, Any]],
        *,
        replace_types: Iterable[str] = (),
        scope: str | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            for rt in replace_types:
                q = delete(s.derived).where(
                    s.derived.c.interp_id == interp_id, s.derived.c.record_type == rt
                )
                if scope is not None:
                    q = q.where(s.derived.c.scope == scope)
                conn.execute(q)
            if records:
                conn.execute(
                    insert(s.derived), [self._derived_row(interp_id, tenant_id, r) for r in records]
                )

    @staticmethod
    def _derived_row(interp_id: str, tenant_id: str, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_id": r["record_id"],
            "interp_id": interp_id,
            "tenant_id": tenant_id,
            "record_type": r["record_type"],
            "scope": r["scope"][:256],
            "analyzer": r["analyzer"],
            "maturity": r["maturity"],
            "doc": canonical_json(r),
        }

    # ── interpretation reads ───────────────────────────────────────────────
    def events(
        self,
        interp_id: str,
        *,
        run_id: str | None = None,
        kind: str | None = None,
        event_ids: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        q = select(s.events.c.doc).where(s.events.c.interp_id == interp_id)
        if run_id is not None:
            q = q.where(s.events.c.run_id == run_id)
        if kind:
            q = q.where(s.events.c.kind == kind)
        if event_ids is not None:
            q = q.where(s.events.c.event_id.in_(list(event_ids)))
        q = q.order_by(s.events.c.t_start, s.events.c.ordering_key, s.events.c.event_id)
        if limit:
            q = q.limit(limit)
        with self.engine.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(q)]

    def event(self, interp_id: str, event_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.events.c.doc).where(
                    s.events.c.interp_id == interp_id, s.events.c.event_id == event_id
                )
            ).first()
        return json.loads(r[0]) if r else None

    def events_for_observation(self, obs_id: str) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            return [
                (r.event_id, r.interp_id)
                for r in conn.execute(
                    select(s.event_sources).where(s.event_sources.c.obs_id == obs_id)
                )
            ]

    def diagnostics(self, interp_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(r._mapping)
                for r in conn.execute(
                    select(s.diagnostics)
                    .where(s.diagnostics.c.interp_id == interp_id)
                    .order_by(s.diagnostics.c.id)
                )
            ]

    def artifact(
        self, tenant_id: str, artifact_id: str, *, with_content: bool = True
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.artifacts).where(
                    s.artifacts.c.tenant_id == tenant_id, s.artifacts.c.artifact_id == artifact_id
                )
            ).first()
        if r is None:
            return None
        d = {
            "artifact_id": r.artifact_id,
            "media_type": r.media_type,
            "size_bytes": r.size_bytes,
            "preview": r.preview,
        }
        if with_content:
            content = (
                r.content_json
                if r.content_json is not None
                else self.blobs.get(r.content_blob).decode("utf-8")
            )
            d["content"] = json.loads(content)
        return d

    def artifacts_by_prefix(self, tenant_id: str, prefix: str) -> list[str]:
        with self.engine.connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    select(s.artifacts.c.artifact_id)
                    .where(
                        s.artifacts.c.tenant_id == tenant_id,
                        s.artifacts.c.artifact_id.like(f"{prefix}%"),
                    )
                    .limit(20)
                )
            ]

    def entities(self, interp_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                json.loads(r[0])
                for r in conn.execute(
                    select(s.entities.c.doc)
                    .where(s.entities.c.interp_id == interp_id)
                    .order_by(s.entities.c.canonical_key)
                )
            ]

    def runs(self, interp_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        q = (
            select(s.runs.c.doc)
            .where(s.runs.c.interp_id == interp_id)
            .order_by(s.runs.c.started_at.desc())
        )
        if limit:
            q = q.limit(limit)
        with self.engine.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(q)]

    def run(self, interp_id: str, run_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.runs.c.doc).where(
                    s.runs.c.interp_id == interp_id, s.runs.c.run_id == run_id
                )
            ).first()
        return json.loads(r[0]) if r else None

    # ── hyperedge relation store (Issue #736) ──────────────────────────────
    def is_run_sealed(
        self, tenant_id: str, interp_id: str, run_id: str, *, conn: Any | None = None
    ) -> bool:
        """Check if relations for this run have been sealed."""
        q = select(s.sealed_runs.c.run_id).where(
            s.sealed_runs.c.tenant_id == tenant_id,
            s.sealed_runs.c.interp_id == interp_id,
            s.sealed_runs.c.run_id == run_id,
        )
        if conn is not None:
            return conn.execute(q).first() is not None
        with self.engine.connect() as c:
            return c.execute(q).first() is not None

    def seal_run(self, tenant_id: str, interp_id: str, run_id: str) -> None:
        """Seal a run and its relations. Sealed relations cannot be modified or deleted."""
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(s.sealed_runs.c.sealed_at).where(
                    s.sealed_runs.c.tenant_id == tenant_id,
                    s.sealed_runs.c.interp_id == interp_id,
                    s.sealed_runs.c.run_id == run_id,
                )
            ).scalar()
            if not existing:
                conn.execute(
                    insert(s.sealed_runs).values(
                        tenant_id=tenant_id,
                        interp_id=interp_id,
                        run_id=run_id,
                        sealed_at=_now(),
                    )
                )

    def put_relations(
        self,
        tenant_id: str,
        interp_id: str,
        relations: Sequence[dict[str, Any]],
        *,
        conn: Any | None = None,
    ) -> int:
        """Validate, persist, and index typed hyperedge relations idempotently."""
        if not relations:
            return 0

        # Validate typed hyperedges and ADR-0005 view constraints
        for rel in relations:
            tail = rel.get("tail")
            head = rel.get("head")
            if (
                not tail
                or not head
                or not isinstance(tail, (list, tuple))
                or not isinstance(head, (list, tuple))
            ):
                raise ValueError(
                    f"Relation {rel.get('rel_id')!r} must have non-empty tail and head member lists."
                )
            view = rel.get("view")
            valid_views = {View.EXECUTION.value, View.INFORMATION.value, View.CAUSAL.value}
            if view not in valid_views:
                raise ValueError(
                    f"Invalid relation view {view!r}; expected one of {sorted(valid_views)}"
                )
            if view == View.CAUSAL.value and not rel.get("evidence_class"):
                raise ValueError("CAUSAL relations require an evidence class")
            causal_types = {
                RelType.CAUSES.value,
                RelType.INFLUENCES.value,
                RelType.ENABLES.value,
                RelType.PREVENTS.value,
            }
            if view != View.CAUSAL.value and rel.get("type") in causal_types:
                raise ValueError(
                    f"{rel.get('type')} is a causal relation type and belongs to the CAUSAL view"
                )

        if conn is not None:
            return self._put_relations_conn(conn, tenant_id, interp_id, relations)
        with self.engine.begin() as c:
            return self._put_relations_conn(c, tenant_id, interp_id, relations)

    def _put_relations_conn(
        self, conn: Any, tenant_id: str, interp_id: str, relations: Sequence[dict[str, Any]]
    ) -> int:
        run_ids = {rel.get("run_id") for rel in relations if rel.get("run_id")}
        if run_ids:
            sealed = set(
                r[0]
                for r in conn.execute(
                    select(s.sealed_runs.c.run_id).where(
                        s.sealed_runs.c.tenant_id == tenant_id,
                        s.sealed_runs.c.interp_id == interp_id,
                        s.sealed_runs.c.run_id.in_(list(run_ids)),
                    )
                )
            )
            if sealed:
                raise SealedRunError(
                    f"Run(s) {sorted(sealed)} are sealed; cannot mutate or add relations."
                )

        rel_ids = [rel["rel_id"] for rel in relations]
        existing = set()
        for chunk in _chunks(rel_ids, 500):
            existing.update(
                r[0]
                for r in conn.execute(
                    select(s.relations.c.rel_id).where(
                        s.relations.c.interp_id == interp_id,
                        s.relations.c.rel_id.in_(chunk),
                    )
                )
            )

        to_insert = [r for r in relations if r["rel_id"] not in existing]
        if not to_insert:
            return 0

        conn.execute(
            insert(s.relations),
            [
                {
                    "rel_id": rel["rel_id"],
                    "interp_id": interp_id,
                    "tenant_id": tenant_id,
                    "run_id": rel.get("run_id"),
                    "view": rel["view"],
                    "type": rel["type"],
                    "basis": rel["basis"],
                    "evidence_class": rel.get("evidence_class"),
                    "confidence": float(rel.get("confidence", 1.0)),
                    "doc": canonical_json(rel),
                }
                for rel in to_insert
            ],
        )

        members = []
        for rel in to_insert:
            for role in ("tail", "head"):
                for i, node in enumerate(rel[role]):
                    members.append(
                        {
                            "rel_id": rel["rel_id"],
                            "interp_id": interp_id,
                            "role": role,
                            "ordinal": i,
                            "node": str(node)[:600],
                        }
                    )
        if members:
            for mchunk in _chunks(members, 1000):
                conn.execute(insert(s.relation_members), mchunk)
        return len(to_insert)

    def get_relation(
        self, interp_id: str, rel_id: str, *, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """Retrieve a stored relation by ID."""
        q = select(s.relations.c.doc).where(
            s.relations.c.interp_id == interp_id, s.relations.c.rel_id == rel_id
        )
        if tenant_id is not None:
            q = q.where(s.relations.c.tenant_id == tenant_id)
        with self.engine.connect() as conn:
            r = conn.execute(q).first()
        return json.loads(r[0]) if r else None

    def delete_relations_for_run(self, tenant_id: str, interp_id: str, run_id: str) -> int:
        """Delete all relations belonging to a run, checking for run sealing."""
        with self.engine.begin() as conn:
            if self.is_run_sealed(tenant_id, interp_id, run_id, conn=conn):
                raise SealedRunError(f"Run {run_id!r} is sealed; cannot delete relations.")
            rel_ids = [
                r[0]
                for r in conn.execute(
                    select(s.relations.c.rel_id).where(
                        s.relations.c.tenant_id == tenant_id,
                        s.relations.c.interp_id == interp_id,
                        s.relations.c.run_id == run_id,
                    )
                )
            ]
            if not rel_ids:
                return 0
            for rchunk in _chunks(rel_ids, 500):
                conn.execute(
                    delete(s.relation_members).where(
                        s.relation_members.c.interp_id == interp_id,
                        s.relation_members.c.rel_id.in_(rchunk),
                    )
                )
                conn.execute(
                    delete(s.relations).where(
                        s.relations.c.interp_id == interp_id,
                        s.relations.c.rel_id.in_(rchunk),
                    )
                )
            return len(rel_ids)

    def relations(
        self,
        interp_id: str,
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
        view: str | None = None,
        rtype: str | None = None,
        all_runs: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        q = select(s.relations.c.doc).where(s.relations.c.interp_id == interp_id)
        if tenant_id is not None:
            q = q.where(s.relations.c.tenant_id == tenant_id)
        if not all_runs:
            q = q.where(s.relations.c.run_id == run_id) if run_id is not None else q
        if view:
            q = q.where(s.relations.c.view == view)
        if rtype:
            q = q.where(s.relations.c.type == rtype)
        if limit:
            q = q.limit(limit)
        with self.engine.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(q)]

    def _check_relation_filters(
        self,
        rel: dict[str, Any],
        views: set[str] | None,
        types: set[str] | None,
        min_confidence: float,
        min_evidence: EvidenceClass | None,
        include_ambiguous: bool = False,
        min_strength: str | None = None,
    ) -> bool:
        attrs = rel.get("attributes") or {}
        if not include_ambiguous and (
            attrs.get("resolution") == "AMBIGUOUS" or attrs.get("strength") == "NONE"
        ):
            return False
        if min_strength is not None and "strength" in attrs:
            if STRENGTH_RANK.get(attrs["strength"], 0) < STRENGTH_RANK.get(min_strength, 0):
                return False
        if views and rel["view"] not in views:
            return False
        if types and rel["type"] not in types:
            return False
        if rel.get("confidence", 1.0) < min_confidence:
            return False
        if min_evidence is not None:
            ec = EvidenceClass(rel["evidence_class"]) if rel.get("evidence_class") else None
            if EVIDENCE_RANK.get(ec, 0) < EVIDENCE_RANK.get(min_evidence, 0):
                return False
        return True

    def adjacent_relations(
        self,
        interp_id: str,
        node: str,
        direction: str = "forward",
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
        views: Iterable[str] | None = None,
        types: Iterable[str] | None = None,
        min_confidence: float = 0.0,
        min_evidence: EvidenceClass | None = None,
        include_ambiguous: bool = False,
        min_strength: str | None = None,
        conn: Any | None = None,
    ) -> list[tuple[dict[str, Any], list[str]]]:
        """Indexed hyperedge adjacency lookup.
        forward: node is in tail -> yields head members
        reverse: node is in head -> yields tail members
        """
        if direction not in ("forward", "reverse"):
            raise ValueError(f"Direction must be 'forward' or 'reverse', got: {direction!r}")

        query_role = "tail" if direction == "forward" else "head"
        target_role = "head" if direction == "forward" else "tail"

        q = (
            select(s.relation_members.c.rel_id, s.relations.c.doc)
            .join(
                s.relations,
                (s.relations.c.rel_id == s.relation_members.c.rel_id)
                & (s.relations.c.interp_id == s.relation_members.c.interp_id),
            )
            .where(
                s.relation_members.c.interp_id == interp_id,
                s.relation_members.c.node == node,
                s.relation_members.c.role == query_role,
            )
        )
        if tenant_id is not None:
            q = q.where(s.relations.c.tenant_id == tenant_id)
        if run_id is not None:
            q = q.where(s.relations.c.run_id == run_id)
        if views:
            q = q.where(s.relations.c.view.in_(tuple(views)))
        if types:
            q = q.where(s.relations.c.type.in_(tuple(types)))
        if min_confidence > 0.0:
            q = q.where(s.relations.c.confidence >= min_confidence)

        vset = set(views) if views else None
        tset = set(types) if types else None

        def do_query(connection):
            results = []
            for row in connection.execute(q):
                rel_doc = json.loads(row[1])
                if not self._check_relation_filters(
                    rel_doc,
                    vset,
                    tset,
                    min_confidence,
                    min_evidence,
                    include_ambiguous,
                    min_strength,
                ):
                    continue
                adj_nodes = rel_doc.get(target_role, [])
                results.append((rel_doc, adj_nodes))
            return results

        if conn is not None:
            return do_query(conn)
        with self.engine.connect() as c:
            return do_query(c)

    def storage_traverse(
        self,
        interp_id: str,
        start_node: str,
        direction: str = "reverse",
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
        views: Iterable[str] | None = None,
        types: Iterable[str] | None = None,
        max_depth: int = 50,
        min_confidence: float = 0.0,
        min_evidence: EvidenceClass | None = None,
        include_ambiguous: bool = False,
        min_strength: str | None = None,
        skip_kinds: Iterable[str] = (),
        event_times: dict[str, float] | None = None,
    ) -> list[Step]:
        """Storage-backed bidirectional lineage traversal using indexed adjacency."""
        if direction not in ("forward", "reverse"):
            raise ValueError(f"Direction must be 'forward' or 'reverse', got: {direction!r}")

        query_role = "tail" if direction == "forward" else "head"
        target_role = "head" if direction == "forward" else "tail"

        skip = tuple(f"{k}:" for k in skip_kinds)
        seen = {start_node}
        out: list[Step] = []
        times = event_times or {}
        dir_factor = 1 if direction == "forward" else -1
        eps = 1e-6

        q: deque[tuple[str, int, float | None]] = deque([(start_node, 0, times.get(start_node))])

        m_curr = s.relation_members.alias("m_curr")
        m_next = s.relation_members.alias("m_next")
        r = s.relations

        needs_doc = (min_strength is not None) or (not include_ambiguous)

        cols = [
            m_next.c.node.label("target_node"),
            r.c.rel_id,
            r.c.type,
            r.c.evidence_class,
        ]
        if needs_doc:
            cols.append(r.c.doc)

        stmt = (
            select(*cols)
            .select_from(
                m_curr.join(
                    r,
                    (r.c.rel_id == m_curr.c.rel_id) & (r.c.interp_id == m_curr.c.interp_id),
                ).join(
                    m_next,
                    (m_next.c.rel_id == r.c.rel_id)
                    & (m_next.c.interp_id == r.c.interp_id)
                    & (m_next.c.role == target_role),
                )
            )
            .where(
                m_curr.c.interp_id == interp_id,
                m_curr.c.role == query_role,
                m_curr.c.node == bindparam("curr_node"),
            )
        )

        if tenant_id is not None:
            stmt = stmt.where(r.c.tenant_id == tenant_id)
        if run_id is not None:
            stmt = stmt.where(r.c.run_id == run_id)
        if views:
            stmt = stmt.where(r.c.view.in_(tuple(views)))
        if types:
            stmt = stmt.where(r.c.type.in_(tuple(types)))
        if min_confidence > 0.0:
            stmt = stmt.where(r.c.confidence >= min_confidence)

        doc_cache: dict[str, dict[str, Any]] = {}

        with self.engine.connect() as conn:
            while q:
                curr, depth, t = q.popleft()
                if depth >= max_depth:
                    continue

                rows = conn.execute(stmt, {"curr_node": curr}).all()
                if not rows:
                    continue

                valid_rows = []
                for row in rows:
                    if min_evidence is not None:
                        ec = EvidenceClass(row.evidence_class) if row.evidence_class else None
                        if EVIDENCE_RANK.get(ec, 0) < EVIDENCE_RANK.get(min_evidence, 0):
                            continue
                    if needs_doc:
                        rel_id = row.rel_id
                        if rel_id not in doc_cache:
                            doc_cache[rel_id] = json.loads(row.doc)
                        rel_doc = doc_cache[rel_id]
                        if not self._check_relation_filters(
                            rel_doc,
                            None,
                            None,
                            min_confidence,
                            min_evidence,
                            include_ambiguous,
                            min_strength,
                        ):
                            continue
                    valid_rows.append(row)

                valid_rows.sort(key=lambda r: (r.rel_id, r.type, r.target_node))

                for r_row in valid_rows:
                    nb = r_row.target_node
                    if nb in seen or (skip and nb.startswith(skip)):
                        continue
                    tn = times.get(nb)
                    if t is not None and tn is not None and (tn - t) * dir_factor < -eps:
                        continue
                    seen.add(nb)
                    step = Step(nb, depth + 1, r_row.rel_id, r_row.type, curr)
                    out.append(step)
                    q.append((nb, depth + 1, tn if tn is not None else t))

        return out

    def impact_radius(
        self,
        interp_id: str,
        start_node: str,
        *,
        tenant_id: str | None = None,
        run_id: str | None = None,
        max_depth: int = 50,
        views: Iterable[str] | None = None,
        types: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Compute the reachable downstream nodes and impact radius from start_node."""
        steps = self.storage_traverse(
            interp_id,
            start_node,
            direction="forward",
            tenant_id=tenant_id,
            run_id=run_id,
            views=views,
            types=types,
            max_depth=max_depth,
        )
        max_d = max((s.depth for s in steps), default=0)
        reachable = [s.node for s in steps]
        events = [n for n in reachable if n.startswith("event:")]
        artifacts = [n for n in reachable if n.startswith("artifact:")]
        return {
            "root": start_node,
            "radius": max_d,
            "reachable_count": len(reachable),
            "reachable_nodes": reachable,
            "affected_events": events,
            "affected_artifacts": artifacts,
            "steps": [s.to_dict() for s in steps],
        }

    def relations_touching(self, interp_id: str, node: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    select(s.relation_members.c.rel_id)
                    .where(
                        s.relation_members.c.interp_id == interp_id,
                        s.relation_members.c.node == node,
                    )
                    .distinct()
                )
            ]
            if not ids:
                return []
            return [
                json.loads(r[0])
                for r in conn.execute(
                    select(s.relations.c.doc).where(
                        s.relations.c.interp_id == interp_id, s.relations.c.rel_id.in_(ids)
                    )
                )
            ]

    def derived(
        self, interp_id: str, record_type: str, scope: str | None = None
    ) -> list[dict[str, Any]]:
        q = select(s.derived.c.doc).where(
            s.derived.c.interp_id == interp_id, s.derived.c.record_type == record_type
        )
        if scope is not None:
            q = q.where(s.derived.c.scope == scope)
        with self.engine.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(q)]

    # ── experiments (append-only) ──────────────────────────────────────────
    def put_experiment(
        self,
        tenant_id: str,
        record_type: str,
        doc: dict[str, Any],
        subject: str | None = None,
        record_id: str | None = None,
    ) -> str:
        rid = record_id or new_ulid()
        doc = {**doc, "record_id": rid, "record_type": record_type}
        with self.engine.begin() as conn:
            conn.execute(
                insert(s.experiments).values(
                    record_id=rid,
                    tenant_id=tenant_id,
                    record_type=record_type,
                    subject=subject,
                    created_at=_now(),
                    doc=canonical_json(doc),
                )
            )
        return rid

    def experiments(
        self, tenant_id: str, record_type: str, subject: str | None = None
    ) -> list[dict[str, Any]]:
        q = select(s.experiments.c.doc).where(
            s.experiments.c.tenant_id == tenant_id, s.experiments.c.record_type == record_type
        )
        if subject is not None:
            q = q.where(s.experiments.c.subject == subject)
        with self.engine.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(q.order_by(s.experiments.c.record_id))]

    def experiment(self, record_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(s.experiments.c.doc).where(s.experiments.c.record_id == record_id)
            ).first()
        return json.loads(r[0]) if r else None

    # ── guarded mutation entry points (always refuse) ──────────────────────
    def update_observation(self, *_: Any, **__: Any) -> None:
        raise ImmutableEvidenceError(
            "raw observations are immutable; create a new interpretation instead"
        )

    def delete_observation(self, *_: Any, **__: Any) -> None:
        raise ImmutableEvidenceError(
            "raw observations cannot be deleted individually; use retention purge of sealed segments"
        )

    def iter_all_observations(
        self, tenant_id: str = "default", batch: int = 5000
    ) -> Iterator[RawObservation]:
        after = None
        while True:
            chunk = self.observations(tenant_id, after=after, limit=batch)
            if not chunk:
                return
            yield from chunk
            after = chunk[-1].obs_id


def _lock_key(name: str) -> int:
    """Stable signed 63-bit key for pg_advisory_xact_lock."""
    return int(sha256_hex(name)[:15], 16)


def _leaf(tenant_id: str, r: Any) -> str:
    from agentwatch.evidence.canonical import sha256_hex

    return sha256_hex(f"leaf|{r.obs_id}|{r.payload_sha256}|{r.idempotency_key}|{tenant_id}")


def _interp(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    d["pipeline"] = json.loads(d["pipeline"])
    d["stats"] = json.loads(d["stats"]) if d.get("stats") else None
    return d


def _validate_draft(d: ObservationDraft) -> str | None:
    if not isinstance(d.sensor, SensorRef) or not d.sensor.sensor_type or not d.sensor.instance_id:
        return "sensor identity is required"
    if not d.source_kind or len(d.source_kind) > 128:
        return "source_kind is required (<=128 chars)"
    if d.observed_at is not None and d.observed_at.tzinfo is None:
        return "observed_at must be timezone-aware"
    if len(d.declared_ids) > 64:
        return "too many declared ids"
    if not d.tenant_id or len(d.tenant_id) > 64:
        return "tenant_id is required (<=64 chars)"
    return None


def _chunks(items: Sequence[Any], n: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _now() -> str:
    return datetime.now(UTC).isoformat()


# re-exported helpers
__all__ = ["AppendResult", "ImmutableEvidenceError", "Store", "default_store_url"]
