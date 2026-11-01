"""Durable tenant and API-key store (TO-006).

Same interface as :class:`panoptis.api.tenant_auth.TenantStore`, backed by SQL (SQLite or
PostgreSQL through SQLAlchemy). Tenants, API-key metadata, revocations, usage and an
append-only audit trail survive restarts and are shared by every API replica that points
at the same database.

Security properties:

* Only the PBKDF2 ``salt:hash`` of a key is stored; the raw key is returned once, at
  creation, and never persisted or logged.
* ``key_prefix`` is unique (lookup index); a collision on creation is retried.
* Revocation and suspension are single transactions and are recorded in the audit table.
* A database failure while *validating* a key denies it (fail closed, logged); a failure
  while *writing* raises, so a caller never believes a change was saved when it was not.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from panoptis.models.tenant import (
    PLAN_DEFAULTS,
    ApiKey,
    Tenant,
    TenantConfig,
    TenantPlan,
    TenantStatus,
    UsageRecord,
    _hash_api_key,
    _verify_api_key,
    generate_api_key,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
md = MetaData()

tenants = Table(
    "aw_tenants",
    md,
    Column("tenant_id", String(36), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("plan", String(32), nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("config", JSON, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
api_keys = Table(
    "aw_api_keys",
    md,
    Column("key_id", String(36), primary_key=True),
    Column("tenant_id", String(36), nullable=False, index=True),
    Column("key_prefix", String(32), nullable=False),
    Column("key_hash", String(256), nullable=False),  # PBKDF2 salt:hash, never the raw key
    Column("name", String(256), nullable=False, default=""),
    Column("scopes", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("revoked_at", DateTime(timezone=True)),
    UniqueConstraint("key_prefix", name="uq_aw_api_keys_prefix"),
)
usage = Table(
    "aw_tenant_usage",
    md,
    Column("tenant_id", String(36), primary_key=True),
    Column("period", String(7), primary_key=True),  # YYYY-MM
    Column("tokens_used", Integer, nullable=False, default=0),
    Column("requests_count", Integer, nullable=False, default=0),
    Column("usd_cost", Float, nullable=False, default=0.0),
    Column("sessions_count", Integer, nullable=False, default=0),
    Column("events_count", Integer, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
daily = Table(
    "aw_tenant_daily_events",
    md,
    Column("tenant_id", String(36), primary_key=True),
    Column("day", String(10), primary_key=True),  # YYYY-MM-DD
    Column("events", Integer, nullable=False, default=0),
)
audit = Table(
    "aw_tenant_audit",
    md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", DateTime(timezone=True), nullable=False, index=True),
    Column("action", String(64), nullable=False),
    Column("tenant_id", String(36), index=True),
    Column("key_id", String(36)),
    Column("details", Text),
)
schema_meta = Table(
    "aw_tenant_schema",
    md,
    Column("key", String(32), primary_key=True),
    Column("value", String(64), nullable=False),
)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything stored here is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _config_dict(c: TenantConfig) -> dict[str, Any]:
    return {
        k: getattr(c, k)
        for k in (
            "max_sessions",
            "max_events_per_day",
            "max_tokens_per_month",
            "max_usd_per_month",
            "ingestion_rate_limit",
            "retention_days",
        )
    }


class SqlTenantStore:
    """Database-backed tenant/key store with the TenantStore interface."""

    #: last_used_at is written at most this often per key (validation is a hot path)
    LAST_USED_GRANULARITY = timedelta(seconds=60)

    def __init__(self, url: str) -> None:
        self.url = url
        kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        self.engine = create_engine(url, **kwargs)
        self._lock = threading.Lock()
        self.migrate()

    # ── schema ─────────────────────────────────────────────────────────────────────
    def migrate(self) -> None:
        """Create or upgrade the tables (idempotent); refuses a newer schema."""
        md.create_all(self.engine)
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema_meta.c.value).where(schema_meta.c.key == "version")
            ).first()
            if row is None:
                conn.execute(insert(schema_meta).values(key="version", value=str(SCHEMA_VERSION)))
            elif int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"tenant store schema {row[0]} is newer than this code ({SCHEMA_VERSION})"
                )

    def close(self) -> None:
        self.engine.dispose()

    def _audit(
        self,
        conn: Any,
        action: str,
        tenant_id: str | None,
        key_id: str | None = None,
        **details: Any,
    ) -> None:
        conn.execute(
            insert(audit).values(
                at=datetime.now(UTC),
                action=action,
                tenant_id=tenant_id,
                key_id=key_id,
                details=json.dumps(details, default=str),
            )
        )

    def audit_log(self, tenant_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = select(audit).order_by(audit.c.id.desc()).limit(limit)
        if tenant_id:
            q = q.where(audit.c.tenant_id == tenant_id)
        with self.engine.connect() as conn:
            return [
                {
                    "at": _aware(r.at).isoformat(),
                    "action": r.action,
                    "tenant_id": r.tenant_id,
                    "key_id": r.key_id,
                    "details": json.loads(r.details or "{}"),
                }
                for r in conn.execute(q)
            ]

    # ── tenants ────────────────────────────────────────────────────────────────────
    @staticmethod
    def _tenant(r: Any) -> Tenant:
        cfg = TenantConfig(**r.config)
        return Tenant(
            tenant_id=r.tenant_id,
            name=r.name,
            plan=TenantPlan(r.plan),
            status=TenantStatus(r.status),
            config=cfg,
            created_at=_aware(r.created_at),
            metadata=dict(r.metadata or {}),
        )

    def create_tenant(
        self, name: str, plan: TenantPlan = TenantPlan.FREE, metadata: dict[str, Any] | None = None
    ) -> Tenant:
        t = Tenant(
            tenant_id=str(uuid.uuid4()),
            name=name,
            plan=plan,
            config=PLAN_DEFAULTS.get(plan, TenantConfig()),
            metadata=metadata or {},
        )
        with self.engine.begin() as conn:
            conn.execute(
                insert(tenants).values(
                    tenant_id=t.tenant_id,
                    name=t.name,
                    plan=t.plan.value,
                    status=t.status.value,
                    config=_config_dict(t.config),
                    metadata=t.metadata,
                    created_at=t.created_at,
                )
            )
            self._audit(conn, "tenant.create", t.tenant_id, name=name, plan=plan.value)
        logger.info("Created tenant: %s (%s, plan=%s)", name, t.tenant_id, plan.value)
        return t

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(tenants).where(tenants.c.tenant_id == tenant_id)).first()
        return self._tenant(r) if r else None

    def list_tenants(self, status: TenantStatus | None = None) -> list[Tenant]:
        q = select(tenants).order_by(tenants.c.created_at)
        if status:
            q = q.where(tenants.c.status == status.value)
        with self.engine.connect() as conn:
            return [self._tenant(r) for r in conn.execute(q)]

    def update_tenant(self, tenant_id: str, **kwargs: Any) -> Tenant | None:
        allowed = {"name", "plan", "status", "metadata", "config"}
        values: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k in ("plan", "status"):
                values[k] = v.value if hasattr(v, "value") else str(v)
            elif k == "config":
                values[k] = _config_dict(v) if isinstance(v, TenantConfig) else dict(v)
            else:
                values[k] = v
        with self.engine.begin() as conn:
            if values:
                n = conn.execute(
                    update(tenants).where(tenants.c.tenant_id == tenant_id).values(**values)
                ).rowcount
                if not n:
                    return None
                self._audit(conn, "tenant.update", tenant_id, fields=sorted(values))
        return self.get_tenant(tenant_id)

    def suspend_tenant(self, tenant_id: str) -> bool:
        now = datetime.now(UTC)
        with (
            self.engine.begin() as conn
        ):  # one transaction: no window with a suspended tenant's key still valid
            n = conn.execute(
                update(tenants)
                .where(tenants.c.tenant_id == tenant_id)
                .values(status=TenantStatus.SUSPENDED.value)
            ).rowcount
            if not n:
                return False
            revoked = conn.execute(
                update(api_keys)
                .where(api_keys.c.tenant_id == tenant_id, api_keys.c.revoked.is_(False))
                .values(revoked=True, revoked_at=now)
            ).rowcount
            self._audit(conn, "tenant.suspend", tenant_id, keys_revoked=revoked)
        logger.info("Suspended tenant: %s", tenant_id)
        return True

    # ── API keys ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _key(r: Any) -> ApiKey:
        return ApiKey(
            key_id=r.key_id,
            tenant_id=r.tenant_id,
            key_hash=r.key_hash,
            name=r.name,
            scopes=list(r.scopes or ["*"]),
            created_at=_aware(r.created_at),
            last_used_at=_aware(r.last_used_at),
            expires_at=_aware(r.expires_at),
            revoked=bool(r.revoked),
        )

    def create_api_key(
        self,
        tenant_id: str,
        name: str = "",
        scopes: list[str] | None = None,
        *,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a key. Returns (ApiKey, raw_key); the raw key is never stored or logged."""
        if self.get_tenant(tenant_id) is None:
            raise KeyError(f"unknown tenant {tenant_id}")
        for _ in range(5):
            raw = generate_api_key(tenant_id)
            k = ApiKey(
                key_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                key_hash=_hash_api_key(raw),
                name=name,
                scopes=scopes or ["*"],
                expires_at=expires_at,
            )
            try:
                with self.engine.begin() as conn:
                    conn.execute(
                        insert(api_keys).values(
                            key_id=k.key_id,
                            tenant_id=tenant_id,
                            key_prefix=raw[:20],
                            key_hash=k.key_hash,
                            name=name,
                            scopes=k.scopes,
                            created_at=k.created_at,
                            expires_at=expires_at,
                            revoked=False,
                        )
                    )
                    self._audit(conn, "key.create", tenant_id, k.key_id, name=name, scopes=k.scopes)
            except IntegrityError:
                continue  # prefix collision: generate another key
            logger.info("Created API key '%s' for tenant %s", name, tenant_id)
            return k, raw
        raise RuntimeError("could not generate a unique API key prefix")

    def validate_api_key(self, raw_key: str) -> ApiKey | None:
        """The key's record if valid (known, unrevoked, unexpired, hash matches), else None.

        Any database error denies the key (fail closed) and is logged.
        """
        if not raw_key:
            return None
        try:
            with self.engine.connect() as conn:
                r = conn.execute(
                    select(api_keys).where(api_keys.c.key_prefix == raw_key[:20])
                ).first()
                if r is None or r.revoked:
                    return None
                t = conn.execute(
                    select(tenants.c.status).where(tenants.c.tenant_id == r.tenant_id)
                ).first()
            if t is None or t[0] != TenantStatus.ACTIVE.value:
                return None
            k = self._key(r)
            now = datetime.now(UTC)
            if k.expires_at and now > k.expires_at:
                return None
            if not _verify_api_key(raw_key, k.key_hash):
                return None
            if k.last_used_at is None or now - k.last_used_at > self.LAST_USED_GRANULARITY:
                with self.engine.begin() as conn:
                    conn.execute(
                        update(api_keys)
                        .where(api_keys.c.key_id == k.key_id)
                        .values(last_used_at=now)
                    )
                k.last_used_at = now
            return k
        except SQLAlchemyError:
            logger.error(
                "tenant store unavailable while validating an API key; denying it", exc_info=True
            )
            return None

    def revoke_api_key(self, key_id: str) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(
                select(api_keys.c.tenant_id).where(api_keys.c.key_id == key_id)
            ).first()
            if r is None:
                return False
            conn.execute(
                update(api_keys)
                .where(api_keys.c.key_id == key_id)
                .values(revoked=True, revoked_at=datetime.now(UTC))
            )
            self._audit(conn, "key.revoke", r[0], key_id)
        logger.info("Revoked API key %s for tenant %s", key_id, r[0])
        return True

    def list_api_keys(self, tenant_id: str) -> list[ApiKey]:
        with self.engine.connect() as conn:
            return [
                self._key(r)
                for r in conn.execute(
                    select(api_keys)
                    .where(api_keys.c.tenant_id == tenant_id)
                    .order_by(api_keys.c.created_at)
                )
            ]

    # ── usage ──────────────────────────────────────────────────────────────────────
    def record_usage(
        self, tenant_id: str, tokens: int = 0, usd_cost: float = 0.0, events: int = 0
    ) -> UsageRecord:
        now = datetime.now(UTC)
        period, day = now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")
        with self._lock, self.engine.begin() as conn:
            r = conn.execute(
                select(usage).where(usage.c.tenant_id == tenant_id, usage.c.period == period)
            ).first()
            if r is None:
                conn.execute(
                    insert(usage).values(
                        tenant_id=tenant_id,
                        period=period,
                        tokens_used=tokens,
                        requests_count=1,
                        usd_cost=usd_cost,
                        events_count=events,
                        sessions_count=0,
                        updated_at=now,
                    )
                )
            else:
                conn.execute(
                    update(usage)
                    .where(usage.c.tenant_id == tenant_id, usage.c.period == period)
                    .values(
                        tokens_used=usage.c.tokens_used + tokens,
                        requests_count=usage.c.requests_count + 1,
                        usd_cost=usage.c.usd_cost + usd_cost,
                        events_count=usage.c.events_count + events,
                        updated_at=now,
                    )
                )
            d = conn.execute(
                select(daily.c.events).where(daily.c.tenant_id == tenant_id, daily.c.day == day)
            ).first()
            if d is None:
                conn.execute(insert(daily).values(tenant_id=tenant_id, day=day, events=events))
            else:
                conn.execute(
                    update(daily)
                    .where(daily.c.tenant_id == tenant_id, daily.c.day == day)
                    .values(events=daily.c.events + events)
                )
        got = self.get_usage(tenant_id, period)
        assert got is not None
        return got

    def get_usage(self, tenant_id: str, period: str | None = None) -> UsageRecord | None:
        period = period or datetime.now(UTC).strftime("%Y-%m")
        with self.engine.connect() as conn:
            r = conn.execute(
                select(usage).where(usage.c.tenant_id == tenant_id, usage.c.period == period)
            ).first()
        if r is None:
            return None
        return UsageRecord(
            tenant_id=r.tenant_id,
            period=r.period,
            tokens_used=r.tokens_used,
            requests_count=r.requests_count,
            usd_cost=r.usd_cost,
            sessions_count=r.sessions_count,
            events_count=r.events_count,
            updated_at=_aware(r.updated_at),
        )

    def check_usage_limits(self, tenant_id: str) -> tuple[bool, str]:
        t = self.get_tenant(tenant_id)
        if not t:
            return False, "Tenant not found"
        if t.status != TenantStatus.ACTIVE:
            return False, f"Tenant is {t.status.value}"
        u = self.get_usage(tenant_id)
        if u:
            if u.tokens_used >= t.config.max_tokens_per_month:
                return False, "Monthly token limit exceeded"
            if u.usd_cost >= t.config.max_usd_per_month:
                return False, "Monthly cost limit exceeded"
        with self.engine.connect() as conn:
            d = conn.execute(
                select(func.coalesce(daily.c.events, 0)).where(
                    daily.c.tenant_id == tenant_id,
                    daily.c.day == datetime.now(UTC).strftime("%Y-%m-%d"),
                )
            ).first()
        if d and d[0] >= t.config.max_events_per_day:
            return False, "Daily event limit exceeded"
        return True, "OK"
