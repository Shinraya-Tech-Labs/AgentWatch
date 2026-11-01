"""
AgentWatch Storage Layer
SQLAlchemy models, database schema, and repository pattern
for PostgreSQL persistence.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from panoptis.governance.audit_log import AuditRecord

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

logger = logging.getLogger(__name__)

# JSONB on PostgreSQL (unchanged), JSON elsewhere: the schema also runs on SQLite
# (local development, tests).
JSONB = JSON().with_variant(_PG_JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────
# ORM Models
# ─────────────────────────────────────────────


class SessionRecord(Base):
    __tablename__ = "agent_sessions"

    session_id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True, nullable=True)  # Cloud multi-tenancy
    agent_id = Column(String(128), nullable=False, index=True)
    agent_name = Column(String(256))
    framework = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="running", index=True)
    goal = Column(Text)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    ended_at = Column(DateTime(timezone=True))
    total_events = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    final_confidence = Column(Float)
    session_metadata = Column(JSONB, default=dict)

    events = relationship("EventRecord", back_populates="session", lazy="dynamic")
    checkpoints = relationship("CheckpointRecord", back_populates="session", lazy="dynamic")

    __table_args__ = (
        Index("ix_sessions_started_at", "started_at"),
        Index("ix_sessions_framework_status", "framework", "status"),
        Index("ix_sessions_tenant_id", "tenant_id"),
    )


class EventRecord(Base):
    __tablename__ = "agent_events"

    event_id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True, nullable=True)  # Cloud multi-tenancy
    session_id = Column(
        String(36),
        ForeignKey("agent_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    agent_id = Column(String(128), nullable=False)
    framework = Column(String(64), nullable=False)
    event_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="running")
    step_number = Column(Integer, default=0)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_ms = Column(Float)
    goal = Column(Text)
    task_id = Column(String(36), index=True)
    parent_event_id = Column(String(36))
    trace_id = Column(String(64))

    # Tool data
    tool_name = Column(String(128))
    tool_raw_command = Column(Text)
    tool_arguments = Column(JSONB)
    tool_output = Column(Text)
    tool_error = Column(Text)

    # Safety data
    risk_level = Column(String(16))
    risk_score = Column(Float)
    safety_blocked = Column(Boolean, default=False)
    safety_reasons = Column(JSONB)

    # Token usage
    prompt_tokens = Column(Integer)
    completion_tokens = Column(Integer)
    total_tokens = Column(Integer)
    estimated_cost_usd = Column(Float)

    # Confidence
    confidence_score = Column(Float)
    anomaly_flags = Column(JSONB)

    # Generic
    prompt_preview = Column(Text)
    planner_output_preview = Column(Text)
    event_metadata = Column(JSONB, default=dict)
    tags = Column(JSONB, default=list)

    session = relationship("SessionRecord", back_populates="events")

    __table_args__ = (
        Index("ix_events_session_type", "session_id", "event_type"),
        Index("ix_events_session_step", "session_id", "step_number"),
        Index("ix_events_risk_level", "risk_level"),
        Index("ix_events_safety_blocked", "safety_blocked"),
    )


class CheckpointRecord(Base):
    __tablename__ = "checkpoints"

    checkpoint_id = Column(String(36), primary_key=True)
    session_id = Column(
        String(36),
        ForeignKey("agent_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    step_number = Column(Integer, nullable=False)
    checkpoint_type = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    snapshot_path = Column(String(512))
    git_commit_ref = Column(String(40))
    git_stash_ref = Column(String(40))
    working_dir = Column(String(512))
    checkpoint_metadata = Column(JSONB, default=dict)

    session = relationship("SessionRecord", back_populates="checkpoints")


class MemoryEntryRecord(Base):
    __tablename__ = "memory_entries"

    entry_id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True, nullable=True)  # Cloud multi-tenancy
    agent_id = Column(String(128), nullable=False, index=True)
    memory_type = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=False)
    summary = Column(Text)
    importance = Column(String(16), default="medium")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    last_accessed = Column(DateTime(timezone=True))
    access_count = Column(Integer, default=0)
    session_id = Column(String(36), index=True)
    task_id = Column(String(36))
    tags = Column(JSONB, default=list)
    entry_metadata = Column(JSONB, default=dict)
    superseded_by = Column(String(36))
    decay_factor = Column(Float, default=1.0)
    # NOTE: pgvector extension adds: embedding vector(384)

    __table_args__ = (
        Index("ix_memory_agent_type", "agent_id", "memory_type"),
        Index("ix_memory_importance", "importance"),
    )


class PluginRecord(Base):
    __tablename__ = "plugins"

    plugin_id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    version = Column(String(32), nullable=False)
    author = Column(String(128), nullable=False)
    description = Column(Text)
    status = Column(String(32), default="unverified", index=True)
    trust_level = Column(Integer, default=0)
    checksum_sha256 = Column(String(64))
    signature = Column(Text)
    permissions = Column(JSONB, default=dict)
    execution_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    registered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    plugin_metadata = Column(JSONB, default=dict)


class TaskRecord(Base):
    __tablename__ = "task_nodes"

    task_id = Column(String(36), primary_key=True)
    session_id = Column(String(36), ForeignKey("agent_sessions.session_id"), index=True)
    parent_task_id = Column(String(36), index=True)
    assigned_agent_id = Column(String(128))
    title = Column(String(512), nullable=False)
    description = Column(Text)
    status = Column(String(32), default="pending", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    dependencies = Column(JSONB, default=list)
    outputs = Column(JSONB, default=dict)
    task_metadata = Column(JSONB, default=dict)


class AuditLogRecord(Base):
    """Append-only, hash-chained audit record.

    Mirrors ``governance.audit_log.AuditRecord``. ``timestamp`` is the exact
    ISO-8601 string that was hashed, so a round-trip reproduces the digest.
    """

    __tablename__ = "audit_log"

    seq = Column(Integer, primary_key=True, autoincrement=False)
    timestamp = Column(String(40), nullable=False)
    actor = Column(String(256))
    action = Column(String(128), nullable=False)
    target = Column(String(512), nullable=False)
    details = Column(JSONB, default=dict)
    prev_hash = Column(String(64), nullable=False)
    record_hash = Column(String(64), nullable=False, unique=True)


# ─────────────────────────────────────────────
# Repository
# ─────────────────────────────────────────────


class Repository:
    """
    Data access layer for AgentWatch storage.
    Uses SQLAlchemy async sessions.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert_session(self, session_data: dict[str, Any]) -> None:
        existing = await self._session.get(SessionRecord, session_data["session_id"])
        if existing:
            for k, v in session_data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        else:
            record = SessionRecord(
                **{k: v for k, v in session_data.items() if hasattr(SessionRecord, k)}
            )
            self._session.add(record)
        await self._session.flush()

    async def insert_event(self, event_data: dict[str, Any]) -> None:
        """Insert an event; idempotent on ``event_id`` (a retried write is not an error)."""
        event_id = event_data.get("event_id")
        if event_id and await self._session.get(EventRecord, event_id) is not None:
            return
        record = EventRecord(**{k: v for k, v in event_data.items() if hasattr(EventRecord, k)})
        self._session.add(record)
        await self._session.flush()

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await self._session.get(SessionRecord, session_id)

    async def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[EventRecord]:
        from sqlalchemy import select

        q = select(EventRecord).where(EventRecord.session_id == session_id)
        if event_type:
            q = q.where(EventRecord.event_type == event_type)
        q = q.order_by(EventRecord.step_number).limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def get_recent_sessions(
        self,
        limit: int = 50,
        framework: str | None = None,
        status: str | None = None,
    ) -> list[SessionRecord]:
        from sqlalchemy import select

        q = select(SessionRecord).order_by(SessionRecord.started_at.desc())
        if framework:
            q = q.where(SessionRecord.framework == framework)
        if status:
            q = q.where(SessionRecord.status == status)
        q = q.limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def insert_checkpoint(self, cp_data: dict[str, Any]) -> None:
        record = CheckpointRecord(
            **{k: v for k, v in cp_data.items() if hasattr(CheckpointRecord, k)}
        )
        self._session.add(record)
        await self._session.flush()

    async def get_blocked_events(self, limit: int = 100) -> list[EventRecord]:
        from sqlalchemy import select

        q = (
            select(EventRecord)
            .where(EventRecord.safety_blocked)
            .order_by(EventRecord.timestamp.desc())
            .limit(limit)
        )
        result = await self._session.execute(q)
        return list(result.scalars())

    async def get_sessions_older_than(self, cutoff: datetime) -> list[str]:
        """Find IDs of sessions that started before the cutoff time.

        Args:
            cutoff (datetime): The threshold date/time.

        Returns:
            list[str]: A list of session IDs older than the cutoff.
        """
        from sqlalchemy import select

        q = select(SessionRecord.session_id).where(SessionRecord.started_at < cutoff)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def prune_sessions(self, session_ids: list[str]) -> int:
        """Delete specific sessions and their dependent records from the database.

        Args:
            session_ids (list[str]): The IDs of the sessions to delete.

        Returns:
            int: The number of sessions deleted.
        """
        if not session_ids:
            return 0

        from sqlalchemy import delete

        # Remove dependent task rows first (no ON DELETE CASCADE on task_nodes.session_id)
        await self._session.execute(
            delete(TaskRecord).where(TaskRecord.session_id.in_(session_ids))
        )

        d = delete(SessionRecord).where(SessionRecord.session_id.in_(session_ids))
        result = await self._session.execute(d)
        await self._session.flush()
        return result.rowcount or 0

    async def erase_user_data(
        self, user_id: str, *, scope: str = "all", tenant_id: str | None = None
    ) -> int:
        """
        Erase a subject's persisted rows and return the row count.

        ``user_id`` is the agent identity that owns the data. Deletes run on the
        caller's session (uncommitted), so wrapping the call in
        ``session.begin()`` makes the erasure atomic. ``scope`` is ``all``,
        ``sessions`` (sessions + events + tasks), or ``memories``.
        """
        valid_scopes = {"all", "sessions", "memories"}
        if scope not in valid_scopes:
            raise ValueError(
                f"unknown erasure scope {scope!r}; expected one of {sorted(valid_scopes)}"
            )

        from sqlalchemy import delete, select

        deleted = 0

        if scope in ("all", "sessions"):
            session_q = select(SessionRecord.session_id).where(SessionRecord.agent_id == user_id)
            event_d = delete(EventRecord).where(EventRecord.agent_id == user_id)
            session_d = delete(SessionRecord).where(SessionRecord.agent_id == user_id)
            if tenant_id is not None:
                session_q = session_q.where(SessionRecord.tenant_id == tenant_id)
                event_d = event_d.where(EventRecord.tenant_id == tenant_id)
                session_d = session_d.where(SessionRecord.tenant_id == tenant_id)

            session_ids = list((await self._session.execute(session_q)).scalars())
            if session_ids:
                # task_nodes has no ON DELETE CASCADE, so clear it before sessions.
                await self._session.execute(
                    delete(TaskRecord).where(TaskRecord.session_id.in_(session_ids))
                )
            # Delete events explicitly; a bulk session delete skips the FK cascade.
            deleted += (await self._session.execute(event_d)).rowcount
            deleted += (await self._session.execute(session_d)).rowcount

        if scope in ("all", "memories"):
            memory_d = delete(MemoryEntryRecord).where(MemoryEntryRecord.agent_id == user_id)
            if tenant_id is not None:
                memory_d = memory_d.where(MemoryEntryRecord.tenant_id == tenant_id)
            deleted += (await self._session.execute(memory_d)).rowcount

        await self._session.flush()
        return deleted

    @staticmethod
    def _session_filters(where: dict[str, str]) -> list[Any] | None:
        """SQL filters on sessions for an erasure/count query, or None if a key is unknown.

        ``user_id`` is matched against the session's recorded metadata. Any other key that is
        not a column makes the whole query match nothing: silently dropping it would widen
        the match (e.g. to every session of the tenant).
        """
        filters: list[Any] = []
        for k, v in where.items():
            if k == "user_id":
                filters.append(SessionRecord.session_metadata["user_id"].as_string() == v)
                continue
            col = getattr(SessionRecord, k, None)
            if col is None:
                logger.warning("unknown session filter %r: matching nothing", k)
                return None
            filters.append(col == v)
        return filters or None

    async def get_session_ids(self, where: dict[str, str]) -> list[str]:
        """Retrieve session IDs matching a filter (e.g. agent_id=, user_id=, tenant_id=)."""
        from sqlalchemy import select

        filters = self._session_filters(where)
        if filters is None:
            return []
        q = select(SessionRecord.session_id).where(*filters)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def count_sessions(self, where: dict[str, str]) -> int:
        """Count sessions matching a filter."""
        from sqlalchemy import func, select

        filters = self._session_filters(where)
        if filters is None:
            return 0
        q = select(func.count()).select_from(SessionRecord).where(*filters)
        result = await self._session.execute(q)
        return result.scalar_one()

    async def count_events(self, where: dict[str, str]) -> int:
        """Count events matching a filter; ``user_id`` counts the events of that user's sessions."""
        from sqlalchemy import func, select

        if "user_id" in where:
            filters = self._session_filters(where)
            if filters is None:
                return 0
            sessions = select(SessionRecord.session_id).where(*filters)
            q = (
                select(func.count())
                .select_from(EventRecord)
                .where(EventRecord.session_id.in_(sessions))
            )
            return (await self._session.execute(q)).scalar_one()
        efilters: list[Any] = []
        for k, v in where.items():
            col = getattr(EventRecord, k, None)
            if col is None:
                logger.warning("unknown event filter %r: matching nothing", k)
                return 0
            efilters.append(col == v)
        if not efilters:
            return 0
        q = select(func.count()).select_from(EventRecord).where(*efilters)
        result = await self._session.execute(q)
        return result.scalar_one()

    async def delete_session(self, session_id: str) -> int:
        """Delete a single session (and its task dependencies). Returns 1 if deleted."""
        return await self.prune_sessions([session_id])

    async def erase_session(self, session_id: str, *, tenant_id: str | None) -> int:
        """Erase one session and everything recorded under it, only if ``tenant_id`` owns it.

        The tenant constraint is part of every DELETE (through an owned-session subquery),
        so there is no fetch-then-delete window and a session id owned by another tenant
        is not touched. Events are deleted when they belong to the owned session and to the
        same tenant (or carry no tenant, i.e. were recorded before tenancy). Returns the
        number of rows removed; 0 for an unknown session and for another tenant's session
        alike, so the result does not reveal who owns an id.
        """
        from sqlalchemy import delete, or_, select

        owned = select(SessionRecord.session_id).where(SessionRecord.session_id == session_id)
        if tenant_id is not None:
            owned = owned.where(SessionRecord.tenant_id == tenant_id)
        owned_ids = owned.scalar_subquery()
        removed = 0
        event_filter = [EventRecord.session_id == owned_ids]
        if tenant_id is not None:
            event_filter.append(
                or_(EventRecord.tenant_id == tenant_id, EventRecord.tenant_id.is_(None))
            )
        for stmt in (
            delete(EventRecord).where(*event_filter),
            delete(TaskRecord).where(TaskRecord.session_id == owned_ids),
            delete(CheckpointRecord).where(CheckpointRecord.session_id == owned_ids),
        ):
            removed += (await self._session.execute(stmt)).rowcount or 0
        final = delete(SessionRecord).where(SessionRecord.session_id == session_id)
        if tenant_id is not None:
            final = final.where(SessionRecord.tenant_id == tenant_id)
        removed += (await self._session.execute(final)).rowcount or 0
        await self._session.flush()
        return removed

    async def delete_events_by_session(self, session_id: str) -> int:
        """Delete events tied to a session and return the number removed."""
        from sqlalchemy import delete

        d = delete(EventRecord).where(EventRecord.session_id == session_id)
        result = await self._session.execute(d)
        await self._session.flush()
        return result.rowcount


class SqlAlchemyAuditStore:
    """AuditStore over the ``audit_log`` table.

    Writes flush on the caller's session; wrap ``read_head`` + ``write`` in
    ``session.begin()`` to append atomically.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_head(self) -> tuple[int, str]:
        from sqlalchemy import func, select

        from panoptis.governance.audit_log import GENESIS_HASH

        count = (
            await self._session.execute(select(func.count()).select_from(AuditLogRecord))
        ).scalar_one()
        head = (
            await self._session.execute(
                select(AuditLogRecord.record_hash).order_by(AuditLogRecord.seq.desc()).limit(1)
            )
        ).scalar_one_or_none()
        return count, head or GENESIS_HASH

    async def write(self, record: AuditRecord) -> None:
        self._session.add(
            AuditLogRecord(
                seq=record.seq,
                timestamp=record.timestamp,
                actor=record.actor,
                action=record.action,
                target=record.target,
                details=dict(record.details),
                prev_hash=record.prev_hash,
                record_hash=record.record_hash,
            )
        )
        await self._session.flush()

    async def read_all(self) -> list[AuditRecord]:
        from sqlalchemy import select

        from panoptis.governance.audit_log import AuditRecord

        rows = (
            await self._session.execute(select(AuditLogRecord).order_by(AuditLogRecord.seq))
        ).scalars()
        return [
            AuditRecord(
                seq=r.seq,
                timestamp=r.timestamp,
                actor=r.actor,
                action=r.action,
                target=r.target,
                details=dict(r.details or {}),
                prev_hash=r.prev_hash,
                record_hash=r.record_hash,
            )
            for r in rows
        ]


class TenantRepository:
    """Wrapper around Repository that enforces tenant_id isolation.

    In cloud mode, every query is scoped to the provided tenant_id,
    preventing cross-tenant data access.  In legacy (single-tenant) mode,
    tenant_id filtering is skipped.
    """

    def __init__(self, repo: Repository, tenant_id: str | None = None):
        self._repo = repo
        self._tenant_id = tenant_id

    @property
    def _session(self) -> AsyncSession:
        return self._repo._session  # noqa: SLF001

    def _scope(self, q: Any) -> Any:
        """Add tenant_id filter to a SQLAlchemy select query if in cloud mode."""
        if self._tenant_id:
            # Check if the query's entity has a tenant_id column
            from sqlalchemy import inspect as sa_inspect

            for entity in q.column_descriptions:
                mapper = sa_inspect(entity["entity"])
                if hasattr(mapper.c, "tenant_id"):
                    return q.where(mapper.c.tenant_id == self._tenant_id)
        return q

    async def upsert_session(self, session_data: dict[str, Any]) -> None:
        if self._tenant_id:
            # Always enforce tenant_id — overwrite any mismatched value
            session_data["tenant_id"] = self._tenant_id
        await self._repo.upsert_session(session_data)

    async def insert_event(self, event_data: dict[str, Any]) -> None:
        if self._tenant_id:
            # Always enforce tenant_id — overwrite any mismatched value
            event_data["tenant_id"] = self._tenant_id
        await self._repo.insert_event(event_data)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        record = await self._repo.get_session(session_id)
        if record and self._tenant_id and record.tenant_id != self._tenant_id:
            return None
        return record

    async def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[EventRecord]:
        if self._tenant_id:
            from sqlalchemy import select

            q = select(EventRecord).where(
                EventRecord.session_id == session_id,
                EventRecord.tenant_id == self._tenant_id,
            )
            if event_type:
                q = q.where(EventRecord.event_type == event_type)
            q = q.order_by(EventRecord.step_number).limit(limit)
            result = await self._session.execute(q)
            return list(result.scalars())
        return await self._repo.get_events(session_id, event_type, limit)

    async def get_recent_sessions(
        self,
        limit: int = 50,
        framework: str | None = None,
        status: str | None = None,
    ) -> list[SessionRecord]:
        if self._tenant_id:
            from sqlalchemy import select

            q = (
                select(SessionRecord)
                .where(SessionRecord.tenant_id == self._tenant_id)
                .order_by(SessionRecord.started_at.desc())
            )
            if framework:
                q = q.where(SessionRecord.framework == framework)
            if status:
                q = q.where(SessionRecord.status == status)
            q = q.limit(limit)
            result = await self._session.execute(q)
            return list(result.scalars())
        return await self._repo.get_recent_sessions(limit, framework, status)

    async def get_blocked_events(self, limit: int = 100) -> list[EventRecord]:
        if self._tenant_id:
            from sqlalchemy import select

            q = (
                select(EventRecord)
                .where(
                    EventRecord.safety_blocked,
                    EventRecord.tenant_id == self._tenant_id,
                )
                .order_by(EventRecord.timestamp.desc())
                .limit(limit)
            )
            result = await self._session.execute(q)
            return list(result.scalars())
        return await self._repo.get_blocked_events(limit)

    async def get_sessions_older_than(self, cutoff: datetime) -> list[str]:
        session_ids = await self._repo.get_sessions_older_than(cutoff)
        if not self._tenant_id or not session_ids:
            return session_ids
        # Filter to only this tenant's sessions
        from sqlalchemy import select

        q = select(SessionRecord.session_id).where(
            SessionRecord.session_id.in_(session_ids),
            SessionRecord.tenant_id == self._tenant_id,
        )
        result = await self._session.execute(q)
        return list(result.scalars())

    async def prune_sessions(self, session_ids: list[str]) -> int:
        if not session_ids:
            return 0
        # Verify tenant ownership before pruning
        if self._tenant_id:
            owned_ids = await self.get_sessions_older_than(datetime.max.replace(tzinfo=UTC))
            # Intersect requested ids with owned ids
            owned_set = set(owned_ids)
            session_ids = [sid for sid in session_ids if sid in owned_set]
        return await self._repo.prune_sessions(session_ids)

    async def erase_user_data(self, user_id: str, *, scope: str = "all") -> int:
        return await self._repo.erase_user_data(user_id, scope=scope, tenant_id=self._tenant_id)


# ─────────────────────────────────────────────
# Database initialization
# ─────────────────────────────────────────────


async def init_db(database_url: str) -> async_sessionmaker:
    """Initialize database, create tables, return session factory."""
    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Enable pgvector extension if using PostgreSQL
        if "postgresql" in database_url:
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                # Add embedding column if not exists
                await conn.execute(
                    text(
                        """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='memory_entries' AND column_name='embedding'
                        ) THEN
                            ALTER TABLE memory_entries ADD COLUMN embedding vector(384);
                            CREATE INDEX ON memory_entries USING ivfflat (embedding vector_cosine_ops);
                        END IF;
                    END $$;
                """
                    )
                )
            except Exception as exc:
                # pgvector not installed — fall back to in-memory embeddings
                import logging

                logging.getLogger(__name__).warning(
                    "pgvector not available: %s. Memory retrieval uses in-process fallback.",
                    exc,
                )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


def get_database_url(
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """Build the asyncpg database URL from explicit arguments or environment variables.

    Explicit arguments take priority over environment variables, which take
    priority over non-sensitive defaults (host, port, database name, user).
    The password has no default and must be supplied via an argument or the
    DB_PASSWORD / PGPASSWORD environment variable; if it is absent the
    function raises RuntimeError rather than silently connecting with a
    well-known credential.

    Args:
        host: Database host. Defaults to DB_HOST env var or 'localhost'.
        port: Database port. Defaults to DB_PORT env var or 5432.
        database: Database name. Defaults to DB_NAME env var or 'panoptis'.
        user: Database user. Defaults to DB_USER env var or 'panoptis'.
        password: Database password. Defaults to DB_PASSWORD or PGPASSWORD
            env var. No hardcoded fallback.

    Returns:
        A postgresql+asyncpg:// connection string.

    Raises:
        RuntimeError: When no password is available from arguments or env.
    """
    import os as _os

    resolved_host = host or _os.getenv("DB_HOST", "localhost")
    resolved_port = port or int(_os.getenv("DB_PORT", "5432"))
    resolved_database = database or _os.getenv("DB_NAME", "panoptis")
    resolved_user = user or _os.getenv("DB_USER", "panoptis")
    resolved_password = password or _os.getenv("DB_PASSWORD") or _os.getenv("PGPASSWORD")

    if not resolved_password:
        raise RuntimeError(
            "Database password is not configured. "
            "Set the DB_PASSWORD environment variable before starting AgentWatch."
        )

    return (
        f"postgresql+asyncpg://{resolved_user}:{resolved_password}"
        f"@{resolved_host}:{resolved_port}/{resolved_database}"
    )
