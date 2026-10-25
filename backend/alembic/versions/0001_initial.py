"""Initial schema, intentionally frozen at revision 0001."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("storages",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("root_path", sa.String(1000), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("minimum_free_bytes", sa.BigInteger(), nullable=False), sa.Column("warning_free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("channels",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("youtube_channel_id", sa.String(100), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False), sa.Column("name", sa.String(300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("poll_interval", sa.String(20), nullable=False),
        sa.Column("storage_id", sa.Integer(), sa.ForeignKey("storages.id")), sa.Column("retention_days", sa.Integer()),
        sa.Column("deletion_grace_days", sa.Integer(), nullable=False), sa.Column("quality", sa.String(100), nullable=False),
        sa.Column("cookie_profile", sa.String(200)), sa.Column("auto_protect_private", sa.Boolean(), nullable=False),
        sa.Column("auto_protect_deleted", sa.Boolean(), nullable=False), sa.Column("auto_protect_edited", sa.Boolean(), nullable=False),
        sa.Column("edit_detection_mode", sa.String(20), nullable=False), sa.Column("always_protect", sa.Boolean(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True)))
    op.create_index("ix_channels_youtube_channel_id", "channels", ["youtube_channel_id"], unique=True)
    op.create_table("videos",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("youtube_video_id", sa.String(32), nullable=False),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("storage_id", sa.Integer(), sa.ForeignKey("storages.id")), sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True)), sa.Column("recording_finished_at", sa.DateTime(timezone=True)),
        sa.Column("original_duration", sa.Float()), sa.Column("remote_status", sa.String(40), nullable=False),
        sa.Column("retention_days", sa.Integer()), sa.Column("retention_expires_at", sa.DateTime(timezone=True)),
        sa.Column("protected", sa.Boolean(), nullable=False), sa.Column("protection_reason", sa.String(60)),
        sa.Column("protected_at", sa.DateTime(timezone=True)), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("deletion_candidate_at", sa.DateTime(timezone=True)), sa.Column("deletion_after", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)), sa.Column("file_path", sa.String(2000)),
        sa.Column("file_size", sa.BigInteger(), nullable=False), sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_videos_youtube_video_id", "videos", ["youtube_video_id"], unique=True)
    op.create_index("ix_videos_channel_id", "videos", ["channel_id"])
    op.create_index("ix_videos_status", "videos", ["status"])
    op.create_index("ix_videos_protected", "videos", ["protected"])
    op.create_table("remote_checks", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False), sa.Column("remote_status", sa.String(40), nullable=False),
        sa.Column("remote_duration", sa.Float()), sa.Column("edit_detected", sa.Boolean()), sa.Column("error", sa.Text()), sa.Column("raw_metadata", sa.JSON()))
    op.create_index("ix_remote_checks_video_id", "remote_checks", ["video_id"])
    op.create_table("fingerprints", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id"), nullable=False), sa.Column("position", sa.Float(), nullable=False),
        sa.Column("video_hash", sa.String(256)), sa.Column("audio_hash", sa.String(256)), sa.UniqueConstraint("video_id", "position"))
    op.create_index("ix_fingerprints_video_id", "fingerprints", ["video_id"])
    op.create_table("audit_logs", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id")), sa.Column("action", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(200)), sa.Column("details", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_logs_video_id", "audit_logs", ["video_id"])


def downgrade():
    op.drop_index("ix_audit_logs_video_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_fingerprints_video_id", table_name="fingerprints")
    op.drop_table("fingerprints")
    op.drop_index("ix_remote_checks_video_id", table_name="remote_checks")
    op.drop_table("remote_checks")
    op.drop_index("ix_videos_protected", table_name="videos")
    op.drop_index("ix_videos_status", table_name="videos")
    op.drop_index("ix_videos_channel_id", table_name="videos")
    op.drop_index("ix_videos_youtube_video_id", table_name="videos")
    op.drop_table("videos")
    op.drop_index("ix_channels_youtube_channel_id", table_name="channels")
    op.drop_table("channels")
    op.drop_table("storages")
