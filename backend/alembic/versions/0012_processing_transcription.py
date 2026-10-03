"""processing queue and transcripts

Revision ID: 0012_processing_transcription
Revises: 0011_live_playback
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_processing_transcription"
down_revision = "0011_live_playback"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("videos", sa.Column("transcription_status", sa.String(20), nullable=False, server_default="pending"))
    op.add_column("videos", sa.Column("transcription_error", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column("transcription_model", sa.String(200), nullable=True))
    op.add_column("videos", sa.Column("transcription_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("transcription_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("processing_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("payload", sa.JSON(), nullable=False), sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("video_id", "kind", name="uq_processing_jobs_video_kind"))
    for col in ("video_id", "kind", "status", "next_attempt_at"):
        op.create_index(f"ix_processing_jobs_{col}", "processing_jobs", [col])
    op.create_table("transcript_segments", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start", sa.Float(), nullable=False), sa.Column("end", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("raw", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("video_id", "start", "end", "raw", name="uq_transcript_segment_position"))
    op.create_index("ix_transcript_segments_video_id", "transcript_segments", ["video_id"])
    op.create_index("ix_transcript_segments_raw", "transcript_segments", ["raw"])
    op.execute("CREATE INDEX ix_transcript_segments_search ON transcript_segments USING gin (to_tsvector('simple', text))")
    op.create_table("app_settings", sa.Column("key", sa.String(100), primary_key=True), sa.Column("value", sa.JSON(), nullable=False))

def downgrade():
    op.drop_table("app_settings")
    op.execute("DROP INDEX ix_transcript_segments_search")
    op.drop_index("ix_transcript_segments_raw", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_video_id", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    for col in ("next_attempt_at", "status", "kind", "video_id"):
        op.drop_index(f"ix_processing_jobs_{col}", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    for col in ("transcription_finished_at", "transcription_started_at", "transcription_model", "transcription_error", "transcription_status"):
        op.drop_column("videos", col)
