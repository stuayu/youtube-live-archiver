"""automatic recording rules"""
from alembic import op
import sqlalchemy as sa

revision = "0014_recording_rules"
down_revision = "0013_llm_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("edit_detection_mode", sa.String(20), nullable=True))
    op.create_table(
        "recording_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("target", sa.String(20), nullable=False, server_default="both"),
        sa.Column("match_mode", sa.String(20), nullable=False, server_default="keyword"),
        sa.Column("is_exclusion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("storage_id", sa.Integer(), sa.ForeignKey("storages.id"), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("quality", sa.String(500), nullable=True),
        sa.Column("edit_detection_mode", sa.String(20), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recording_rules_channel_id", "recording_rules", ["channel_id"])
    op.create_index("ix_recording_rules_enabled", "recording_rules", ["enabled"])


def downgrade():
    op.drop_index("ix_recording_rules_enabled", table_name="recording_rules")
    op.drop_index("ix_recording_rules_channel_id", table_name="recording_rules")
    op.drop_table("recording_rules")
    op.drop_column("videos", "edit_detection_mode")
