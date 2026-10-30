"""v3 relational schema (SQLAlchemy Core), portable across SQLite and PostgreSQL.

Evidence tables are protected by database triggers: UPDATE is always rejected and
DELETE is only possible for rows in a segment that has an explicit purge authorization
(retention), which is itself recorded.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

SCHEMA_VERSION = 2  # 2: data keys + erasure log (crypto-shredding)
metadata = MetaData()

# ── Evidence ──────────────────────────────────────────────────────────────
observations = Table(
    "aw3_observations",
    metadata,
    Column("obs_id", String(26), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("sensor_type", String(64), nullable=False),
    Column("sensor_version", String(32), nullable=False),
    Column("sensor_instance", String(128), nullable=False),
    Column("source_kind", String(128), nullable=False),
    Column("source_seq", BigInteger),
    Column("idempotency_key", String(64), nullable=False),
    Column("observed_at", String(40)),
    Column("received_at", String(40), nullable=False),
    Column("clock", Text, nullable=False),
    Column("content_type", String(128), nullable=False),
    Column("payload_json", Text),  # NULL when stored in the blob store
    Column("payload_blob", String(64)),
    Column("payload_sha256", String(64), nullable=False),
    Column("declared_ids", Text, nullable=False),
    Column("redaction", Text),
    Column("sampling", Text),
    Column("segment_id", String(64)),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_aw3_obs_idem"),
)
Index("ix_aw3_obs_tenant_seg", observations.c.tenant_id, observations.c.segment_id)
Index("ix_aw3_obs_kind", observations.c.tenant_id, observations.c.source_kind)

declared_ids = Table(
    "aw3_declared_ids",
    metadata,
    Column("obs_id", String(26), primary_key=True),
    Column("key", String(64), primary_key=True),
    Column("value", String(256), nullable=False),
    Column("tenant_id", String(64), nullable=False),
)
Index("ix_aw3_declared_lookup", declared_ids.c.tenant_id, declared_ids.c.key, declared_ids.c.value)

segments = Table(
    "aw3_segments",
    metadata,
    Column("segment_id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("first_obs", String(26), nullable=False),
    Column("last_obs", String(26), nullable=False),
    Column("n_obs", Integer, nullable=False),
    Column("merkle_root", String(64), nullable=False),
    Column("prev_segment_hash", String(64), nullable=False),
    Column("segment_hash", String(64), nullable=False),
    Column("sealed_at", String(40), nullable=False),
    Column("purged_at", String(40)),
    UniqueConstraint("tenant_id", "seq", name="uq_aw3_seg_seq"),
)

purge_authorizations = Table(
    "aw3_purge_authorizations",
    metadata,
    Column("segment_id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("reason", Text, nullable=False),
    Column("authorized_at", String(40), nullable=False),
    Column("actor", String(256)),
)

tenant_keys = Table(
    "aw3_tenant_keys",
    metadata,
    Column("tenant_id", String(64), primary_key=True),
    Column("artifact_key_hex", String(128), nullable=False),
    Column("created_at", String(40), nullable=False),
)

data_keys = Table(
    "aw3_data_keys",
    metadata,
    Column("key_id", String(32), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("subject", String(256), nullable=False),  # '' = tenant default key
    Column("key_hex", String(64)),  # NULL once destroyed (crypto-shredding)
    Column("created_at", String(40), nullable=False),
    Column("destroyed_at", String(40)),
    Column("reason", Text),
    UniqueConstraint("tenant_id", "subject", name="uq_aw3_data_key_subject"),
)

erasures = Table(
    "aw3_erasures",
    metadata,
    Column("erasure_id", String(26), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("subject", String(256), nullable=False),
    Column("key_id", String(32)),
    Column("reason", Text, nullable=False),
    Column("actor", String(256)),
    Column("erased_at", String(40), nullable=False),
    Column("observations_affected", Integer, nullable=False),
    Column("doc", Text, nullable=False),
)

# ── Interpretation ────────────────────────────────────────────────────────
interpretations = Table(
    "aw3_interpretations",
    metadata,
    Column("interp_id", String(128), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("pipeline", Text, nullable=False),  # JSON: component versions
    Column("config_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("status", String(16), nullable=False),  # active | superseded
    Column("processed_through", String(26)),  # last obs_id processed
    Column("stats", Text),
)

events = Table(
    "aw3_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("interp_id", String(128), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("run_id", String(36)),
    Column("kind", String(32), nullable=False),
    Column("operation", String(256), nullable=False),
    Column("actor", String(256)),
    Column("object", String(256)),
    Column("status", String(16), nullable=False),
    Column("t_start", String(40)),
    Column("t_end", String(40)),
    Column("ordering_key", String(160)),
    Column("doc", Text, nullable=False),
)
Index("ix_aw3_events_run", events.c.interp_id, events.c.run_id)
Index("ix_aw3_events_kind", events.c.interp_id, events.c.kind)

event_sources = Table(
    "aw3_event_sources",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("interp_id", String(128), primary_key=True),
    Column("obs_id", String(26), primary_key=True),
)
Index("ix_aw3_evsrc_obs", event_sources.c.obs_id)

diagnostics = Table(
    "aw3_diagnostics",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("interp_id", String(128), nullable=False),
    Column("tenant_id", String(64), nullable=False),
    Column("obs_id", String(26)),
    Column("level", String(16), nullable=False),
    Column("code", String(64), nullable=False),
    Column("message", Text, nullable=False),
)

artifacts = Table(
    "aw3_artifacts",
    metadata,
    Column("tenant_id", String(64), primary_key=True),
    Column("artifact_id", String(64), primary_key=True),
    Column("media_type", String(128), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("preview", Text),
    Column("content_json", Text),
    Column("content_blob", String(64)),
)

entities = Table(
    "aw3_entities",
    metadata,
    Column("entity_id", String(36), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("interp_id", String(128), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("canonical_key", String(512), nullable=False),
    Column("doc", Text, nullable=False),
)
Index("ix_aw3_entities_key", entities.c.interp_id, entities.c.canonical_key)

runs = Table(
    "aw3_runs",
    metadata,
    Column("run_id", String(36), primary_key=True),
    Column("interp_id", String(128), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("name", String(256)),
    Column("started_at", String(40)),
    Column("ended_at", String(40)),
    Column("status", String(16)),
    Column("system_version", String(64)),
    Column("doc", Text, nullable=False),
)
Index("ix_aw3_runs_started", runs.c.interp_id, runs.c.started_at)

relations = Table(
    "aw3_relations",
    metadata,
    Column("rel_id", String(36), primary_key=True),
    Column("interp_id", String(128), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("run_id", String(36)),
    Column("view", String(16), nullable=False),
    Column("type", String(32), nullable=False),
    Column("basis", String(24), nullable=False),
    Column("evidence_class", String(24)),
    Column("confidence", Float, nullable=False),
    Column("doc", Text, nullable=False),
)
Index("ix_aw3_rel_run", relations.c.interp_id, relations.c.run_id, relations.c.view)

relation_members = Table(
    "aw3_relation_members",
    metadata,
    Column("rel_id", String(36), primary_key=True),
    Column("interp_id", String(128), primary_key=True),
    Column("role", String(8), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("node", String(600), nullable=False),
)
Index("ix_aw3_relmem_node", relation_members.c.interp_id, relation_members.c.node)

# ── Derived analysis (versioned by interp + analyzer) ─────────────────────
derived = Table(
    "aw3_derived",
    metadata,
    Column("record_id", String(64), primary_key=True),
    Column("interp_id", String(128), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column(
        "record_type", String(48), nullable=False
    ),  # motif_instance | profile | state_estimate | ...
    Column("scope", String(256), nullable=False),  # run id, system version, ...
    Column("analyzer", String(128), nullable=False),
    Column("maturity", String(16), nullable=False),
    Column("doc", Text, nullable=False),
)
Index("ix_aw3_derived_scope", derived.c.interp_id, derived.c.record_type, derived.c.scope)

# ── Experiments (append-only records; not derived from evidence) ──────────
experiments = Table(
    "aw3_experiments",
    metadata,
    Column("record_id", String(64), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column(
        "record_type", String(48), nullable=False
    ),  # hypothesis | hypothesis_evidence | branch | replay | counterfactual
    Column("subject", String(256)),
    Column("created_at", String(40), nullable=False),
    Column("doc", Text, nullable=False),
)
Index("ix_aw3_exp_type", experiments.c.tenant_id, experiments.c.record_type, experiments.c.subject)

meta_table = Table(
    "aw3_meta",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)


SQLITE_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS aw3_obs_no_update BEFORE UPDATE ON aw3_observations
       WHEN NOT (OLD.segment_id IS NULL AND NEW.segment_id IS NOT NULL
                 AND NEW.payload_sha256 = OLD.payload_sha256 AND NEW.obs_id = OLD.obs_id
                 AND NEW.idempotency_key = OLD.idempotency_key
                 AND COALESCE(NEW.payload_json, '') = COALESCE(OLD.payload_json, '')
                 AND COALESCE(NEW.payload_blob, '') = COALESCE(OLD.payload_blob, '')
                 AND NEW.declared_ids = OLD.declared_ids AND NEW.tenant_id = OLD.tenant_id
                 AND NEW.source_kind = OLD.source_kind)
       BEGIN SELECT RAISE(ABORT, 'aw3_observations is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS aw3_obs_no_delete BEFORE DELETE ON aw3_observations
       WHEN OLD.segment_id IS NULL OR NOT EXISTS
            (SELECT 1 FROM aw3_purge_authorizations p WHERE p.segment_id = OLD.segment_id)
       BEGIN SELECT RAISE(ABORT, 'aw3_observations rows can only be removed by authorized segment purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS aw3_seg_no_update BEFORE UPDATE ON aw3_segments
       WHEN NOT (NEW.segment_hash = OLD.segment_hash AND NEW.merkle_root = OLD.merkle_root
                 AND NEW.prev_segment_hash = OLD.prev_segment_hash AND OLD.purged_at IS NULL)
       BEGIN SELECT RAISE(ABORT, 'aw3_segments is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS aw3_seg_no_delete BEFORE DELETE ON aw3_segments
       BEGIN SELECT RAISE(ABORT, 'aw3_segments is append-only'); END""",
]

POSTGRES_TRIGGERS = [
    """CREATE OR REPLACE FUNCTION aw3_obs_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'UPDATE' THEN
        IF OLD.segment_id IS NULL AND NEW.segment_id IS NOT NULL
           AND NEW.payload_sha256 = OLD.payload_sha256 AND NEW.obs_id = OLD.obs_id
           AND NEW.idempotency_key = OLD.idempotency_key
           AND COALESCE(NEW.payload_json, '') = COALESCE(OLD.payload_json, '')
           AND COALESCE(NEW.payload_blob, '') = COALESCE(OLD.payload_blob, '')
           AND NEW.declared_ids = OLD.declared_ids AND NEW.tenant_id = OLD.tenant_id
           AND NEW.source_kind = OLD.source_kind THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION 'aw3_observations is append-only';
      END IF;
      IF OLD.segment_id IS NOT NULL AND EXISTS
         (SELECT 1 FROM aw3_purge_authorizations p WHERE p.segment_id = OLD.segment_id) THEN
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'aw3_observations rows can only be removed by authorized segment purge';
    END; $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS aw3_obs_guard_t ON aw3_observations",
    """CREATE TRIGGER aw3_obs_guard_t BEFORE UPDATE OR DELETE ON aw3_observations
       FOR EACH ROW EXECUTE FUNCTION aw3_obs_guard()""",
    """CREATE OR REPLACE FUNCTION aw3_seg_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'UPDATE' AND NEW.segment_hash = OLD.segment_hash AND NEW.merkle_root = OLD.merkle_root
         AND NEW.prev_segment_hash = OLD.prev_segment_hash AND OLD.purged_at IS NULL THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'aw3_segments is append-only';
    END; $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS aw3_seg_guard_t ON aw3_segments",
    """CREATE TRIGGER aw3_seg_guard_t BEFORE UPDATE OR DELETE ON aw3_segments
       FOR EACH ROW EXECUTE FUNCTION aw3_seg_guard()""",
]
