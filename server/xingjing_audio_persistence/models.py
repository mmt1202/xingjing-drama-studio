"""PostgreSQL schema metadata for the M07 persistence boundary.

The metadata is deliberately independent from the legacy ArcReel metadata.  M07 is
not mounted in that application yet, and importing it here must not make an
incomplete runtime silently use the legacy SQLite default.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

metadata = MetaData()

_scope_columns = ("tenant_id", "workspace_id", "project_id")
_identifier = lambda name, nullable=False: Column(name, String(128), nullable=nullable)  # noqa: E731
_scope_types = lambda: [Column(name, String(128), nullable=False) for name in _scope_columns]  # noqa: E731
_timestamp = lambda: Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())  # noqa: E731

audio_tracks = Table(
    "xingjing_audio_tracks",
    metadata,
    _identifier("id"),
    *_scope_types(),
    Column("track_kind", String(24), nullable=False),
    Column("title", String(240), nullable=False),
    Column("state", String(24), nullable=False, server_default=text("'draft'")),
    Column("current_revision", Integer, nullable=False, server_default=text("0")),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("created_by", String(128), nullable=False),
    _timestamp(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
    PrimaryKeyConstraint("id", name="pk_xingjing_audio_tracks"),
    UniqueConstraint("id", *_scope_columns, name="uq_xingjing_audio_track_scope"),
    CheckConstraint("track_kind IN ('dialogue', 'voiceover', 'bgm', 'sfx', 'mix')", name="ck_xj_audio_track_kind"),
    CheckConstraint("state IN ('draft', 'ready', 'archived')", name="ck_xj_audio_track_state"),
    CheckConstraint("current_revision >= 0", name="ck_xj_audio_track_revision"),
    CheckConstraint("version >= 1", name="ck_xj_audio_track_version"),
)

audio_track_versions = Table(
    "xingjing_audio_track_versions",
    metadata,
    _identifier("id"),
    Column("track_id", String(128), nullable=False),
    *_scope_types(),
    Column("revision", Integer, nullable=False),
    Column("cue_payload", JSONB, nullable=False),
    Column("object_key", String(1024), nullable=True),
    Column("object_sha256", String(64), nullable=True),
    Column("object_size_bytes", BigInteger, nullable=True),
    Column("mime_type", String(128), nullable=True),
    Column("source_task_id", String(128), nullable=True),
    Column("created_by", String(128), nullable=False),
    _timestamp(),
    PrimaryKeyConstraint("id", name="pk_xingjing_audio_track_versions"),
    UniqueConstraint("id", *_scope_columns, name="uq_xingjing_audio_version_scope"),
    UniqueConstraint("track_id", "revision", name="uq_xingjing_audio_track_revision"),
    ForeignKeyConstraint(
        ["track_id", *_scope_columns],
        ["xingjing_audio_tracks.id", *[f"xingjing_audio_tracks.{name}" for name in _scope_columns]],
        name="fk_xj_audio_version_track_scope",
        ondelete="RESTRICT",
    ),
    CheckConstraint("revision >= 1", name="ck_xj_audio_version_revision"),
    CheckConstraint("object_size_bytes IS NULL OR object_size_bytes >= 0", name="ck_xj_audio_object_size"),
    CheckConstraint(
        "(object_key IS NULL AND object_sha256 IS NULL AND object_size_bytes IS NULL AND mime_type IS NULL) "
        "OR (object_key IS NOT NULL AND object_sha256 IS NOT NULL AND object_size_bytes IS NOT NULL AND mime_type IS NOT NULL)",
        name="ck_xj_audio_object_reference",
    ),
)

subtitle_tracks = Table(
    "xingjing_subtitle_tracks",
    metadata,
    _identifier("id"),
    *_scope_types(),
    Column("language", String(35), nullable=False),
    Column("format", String(12), nullable=False),
    Column("title", String(240), nullable=False),
    Column("state", String(24), nullable=False, server_default=text("'draft'")),
    Column("current_revision", Integer, nullable=False, server_default=text("0")),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("created_by", String(128), nullable=False),
    _timestamp(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
    PrimaryKeyConstraint("id", name="pk_xingjing_subtitle_tracks"),
    UniqueConstraint("id", *_scope_columns, name="uq_xingjing_subtitle_track_scope"),
    CheckConstraint("format IN ('srt', 'ass', 'vtt', 'native')", name="ck_xj_subtitle_format"),
    CheckConstraint("state IN ('draft', 'ready', 'archived')", name="ck_xj_subtitle_track_state"),
    CheckConstraint("current_revision >= 0", name="ck_xj_subtitle_track_revision"),
    CheckConstraint("version >= 1", name="ck_xj_subtitle_track_version"),
)

subtitle_track_versions = Table(
    "xingjing_subtitle_track_versions",
    metadata,
    _identifier("id"),
    Column("track_id", String(128), nullable=False),
    *_scope_types(),
    Column("revision", Integer, nullable=False),
    Column("cues", JSONB, nullable=False),
    Column("object_key", String(1024), nullable=True),
    Column("object_sha256", String(64), nullable=True),
    Column("object_size_bytes", BigInteger, nullable=True),
    Column("mime_type", String(128), nullable=True),
    Column("source_task_id", String(128), nullable=True),
    Column("created_by", String(128), nullable=False),
    _timestamp(),
    PrimaryKeyConstraint("id", name="pk_xingjing_subtitle_track_versions"),
    UniqueConstraint("id", *_scope_columns, name="uq_xingjing_subtitle_version_scope"),
    UniqueConstraint("track_id", "revision", name="uq_xingjing_subtitle_track_revision"),
    ForeignKeyConstraint(
        ["track_id", *_scope_columns],
        ["xingjing_subtitle_tracks.id", *[f"xingjing_subtitle_tracks.{name}" for name in _scope_columns]],
        name="fk_xj_subtitle_version_track_scope",
        ondelete="RESTRICT",
    ),
    CheckConstraint("revision >= 1", name="ck_xj_subtitle_version_revision"),
    CheckConstraint("object_size_bytes IS NULL OR object_size_bytes >= 0", name="ck_xj_subtitle_object_size"),
    CheckConstraint(
        "(object_key IS NULL AND object_sha256 IS NULL AND object_size_bytes IS NULL AND mime_type IS NULL) "
        "OR (object_key IS NOT NULL AND object_sha256 IS NOT NULL AND object_size_bytes IS NOT NULL AND mime_type IS NOT NULL)",
        name="ck_xj_subtitle_object_reference",
    ),
)

lip_sync_versions = Table(
    "xingjing_lip_sync_versions",
    metadata,
    _identifier("id"),
    *_scope_types(),
    Column("audio_version_id", String(128), nullable=False),
    Column("subtitle_version_id", String(128), nullable=True),
    Column("input_video_key", String(1024), nullable=False),
    Column("output_video_key", String(1024), nullable=True),
    Column("output_sha256", String(64), nullable=True),
    Column("output_size_bytes", BigInteger, nullable=True),
    Column("mime_type", String(128), nullable=True),
    Column("state", String(24), nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("is_selected", Boolean, nullable=False, server_default=text("false")),
    Column("selected_by", String(128), nullable=True),
    Column("selected_at", DateTime(timezone=True), nullable=True),
    Column("calibration", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("fallback_of_id", String(128), nullable=True),
    Column("source_task_id", String(128), nullable=True),
    Column("failure_metadata", JSONB, nullable=True),
    Column("created_by", String(128), nullable=False),
    _timestamp(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
    PrimaryKeyConstraint("id", name="pk_xingjing_lip_sync_versions"),
    UniqueConstraint("id", *_scope_columns, name="uq_xingjing_lip_sync_scope"),
    ForeignKeyConstraint(
        ["audio_version_id", *_scope_columns],
        ["xingjing_audio_track_versions.id", *[f"xingjing_audio_track_versions.{name}" for name in _scope_columns]],
        name="fk_xj_lip_sync_audio_scope",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["subtitle_version_id", *_scope_columns],
        ["xingjing_subtitle_track_versions.id", *[f"xingjing_subtitle_track_versions.{name}" for name in _scope_columns]],
        name="fk_xj_lip_sync_subtitle_scope",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["fallback_of_id", *_scope_columns],
        ["xingjing_lip_sync_versions.id", *[f"xingjing_lip_sync_versions.{name}" for name in _scope_columns]],
        name="fk_xj_lip_sync_fallback_scope",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["source_task_id", *_scope_columns],
        ["xingjing_audio_generation_tasks.id", *[f"xingjing_audio_generation_tasks.{name}" for name in _scope_columns]],
        name="fk_xj_lip_sync_source_task_scope",
        ondelete="RESTRICT",
    ),
    CheckConstraint("state IN ('pending', 'ready', 'failed', 'cancelled', 'fallback')", name="ck_xj_lip_sync_state"),
    CheckConstraint("version >= 1", name="ck_xj_lip_sync_version"),
    CheckConstraint(
        "(output_video_key IS NULL AND output_sha256 IS NULL AND output_size_bytes IS NULL AND mime_type IS NULL) "
        "OR (output_video_key IS NOT NULL AND output_sha256 IS NOT NULL AND output_size_bytes IS NOT NULL AND mime_type IS NOT NULL)",
        name="ck_xj_lip_sync_output_reference",
    ),
    CheckConstraint("output_size_bytes IS NULL OR output_size_bytes > 0", name="ck_xj_lip_sync_output_size"),
)

generation_tasks = Table(
    "xingjing_audio_generation_tasks",
    metadata,
    _identifier("id"),
    *_scope_types(),
    Column("task_kind", String(32), nullable=False),
    Column("resource_type", String(32), nullable=False),
    Column("resource_id", String(128), nullable=False),
    Column("status", String(24), nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("idempotency_key", String(255), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("provider_name", String(120), nullable=True),
    Column("provider_task_id", String(255), nullable=True),
    Column("lease_owner", String(255), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False, server_default=text("1")),
    Column("failure_metadata", JSONB, nullable=True),
    Column("cancellation_metadata", JSONB, nullable=True),
    Column("fallback_metadata", JSONB, nullable=True),
    Column("result_metadata", JSONB, nullable=True),
    Column("created_by", String(128), nullable=False),
    _timestamp(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
    Column("timeout_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("id", name="pk_xingjing_audio_generation_tasks"),
    UniqueConstraint("id", *_scope_columns, name="uq_xingjing_audio_task_scope"),
    UniqueConstraint(*_scope_columns, "idempotency_key", name="uq_xj_audio_task_idempotency"),
    CheckConstraint("task_kind IN ('audio', 'subtitle', 'lip_sync', 'mix')", name="ck_xj_audio_task_kind"),
    CheckConstraint(
        "status IN ('pending', 'queued', 'running', 'cancelling', 'retrying', 'succeeded', 'failed', 'cancelled', 'timed_out')",
        name="ck_xj_audio_task_status",
    ),
    CheckConstraint("version >= 1 AND attempt_count >= 0 AND max_attempts >= 1", name="ck_xj_audio_task_counters"),
    CheckConstraint(
        "(lease_owner IS NULL AND lease_expires_at IS NULL) OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
        name="ck_xj_audio_task_lease_pair",
    ),
)

audio_billing_holds = Table(
    "xingjing_audio_billing_holds",
    metadata,
    *_scope_types(),
    Column("task_id", String(128), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("estimated_minor", BigInteger, nullable=False),
    Column("actual_minor", BigInteger, nullable=False, server_default=text("0")),
    Column("released_minor", BigInteger, nullable=False, server_default=text("0")),
    Column("pricing_version", String(128), nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'active'")),
    Column("terminal_event_id", String(128), nullable=True),
    _timestamp(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
    PrimaryKeyConstraint(*_scope_columns, "task_id", name="pk_xj_audio_billing_hold"),
    ForeignKeyConstraint(
        ["task_id", *_scope_columns],
        ["xingjing_audio_generation_tasks.id", *[f"xingjing_audio_generation_tasks.{name}" for name in _scope_columns]],
        name="fk_xj_audio_billing_hold_task",
        ondelete="RESTRICT",
    ),
    CheckConstraint("estimated_minor > 0", name="ck_xj_audio_billing_estimated"),
    CheckConstraint("actual_minor >= 0 AND released_minor >= 0", name="ck_xj_audio_billing_amounts"),
    CheckConstraint("actual_minor + released_minor <= estimated_minor", name="ck_xj_audio_billing_conservation"),
    CheckConstraint("status IN ('active', 'settled', 'released')", name="ck_xj_audio_billing_status"),
)

audio_billing_journals = Table(
    "xingjing_audio_billing_journals",
    metadata,
    Column("event_id", String(128), nullable=False),
    *_scope_types(),
    Column("task_id", String(128), nullable=False),
    Column("action", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    Column("postings", JSONB, nullable=False),
    Column("reference", String(255), nullable=False),
    _timestamp(),
    PrimaryKeyConstraint("event_id", name="pk_xj_audio_billing_journal"),
    CheckConstraint("action IN ('freeze', 'settle', 'release')", name="ck_xj_audio_billing_journal_action"),
    CheckConstraint("amount_minor >= 0", name="ck_xj_audio_billing_journal_amount"),
)

idempotency_records = Table(
    "xingjing_audio_idempotency_records",
    metadata,
    *_scope_types(),
    Column("operation", String(80), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("resource_type", String(48), nullable=False),
    Column("resource_id", String(128), nullable=True),
    Column("response_metadata", JSONB, nullable=True),
    _timestamp(),
    PrimaryKeyConstraint(*_scope_columns, "operation", "idempotency_key", name="pk_xj_audio_idempotency"),
)

audit_events = Table(
    "xingjing_audio_audit_events",
    metadata,
    _identifier("id"),
    *_scope_types(),
    Column("request_id", String(128), nullable=False),
    Column("operation", String(80), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("object_type", String(48), nullable=False),
    Column("object_id", String(128), nullable=False),
    Column("result", String(24), nullable=False),
    Column("before_summary", JSONB, nullable=True),
    Column("after_summary", JSONB, nullable=True),
    _timestamp(),
    PrimaryKeyConstraint("id", name="pk_xingjing_audio_audit_events"),
    CheckConstraint("result IN ('succeeded', 'rejected', 'failed')", name="ck_xj_audio_audit_result"),
)

Index("ix_xj_audio_track_scope_updated", audio_tracks.c.tenant_id, audio_tracks.c.workspace_id, audio_tracks.c.project_id, audio_tracks.c.updated_at)
Index("ix_xj_subtitle_track_scope_updated", subtitle_tracks.c.tenant_id, subtitle_tracks.c.workspace_id, subtitle_tracks.c.project_id, subtitle_tracks.c.updated_at)
Index("ix_xj_audio_task_scope_status", generation_tasks.c.tenant_id, generation_tasks.c.workspace_id, generation_tasks.c.project_id, generation_tasks.c.status, generation_tasks.c.created_at)
Index("ix_xj_audio_task_provider", generation_tasks.c.provider_name, generation_tasks.c.provider_task_id, unique=True, postgresql_where=generation_tasks.c.provider_task_id.is_not(None))
Index("ix_xj_audio_billing_project_time", audio_billing_journals.c.tenant_id, audio_billing_journals.c.workspace_id, audio_billing_journals.c.project_id, audio_billing_journals.c.created_at)
Index("ix_xj_lip_sync_scope_input", lip_sync_versions.c.tenant_id, lip_sync_versions.c.workspace_id, lip_sync_versions.c.project_id, lip_sync_versions.c.input_video_key, lip_sync_versions.c.updated_at)
Index("uq_xj_lip_sync_selected_input", lip_sync_versions.c.tenant_id, lip_sync_versions.c.workspace_id, lip_sync_versions.c.project_id, lip_sync_versions.c.input_video_key, unique=True, postgresql_where=lip_sync_versions.c.is_selected.is_(True))
Index("ix_xj_audio_audit_request", audit_events.c.tenant_id, audit_events.c.request_id)
