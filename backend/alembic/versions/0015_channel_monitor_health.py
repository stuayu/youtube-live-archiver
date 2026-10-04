"""channel monitor health and channel audit events"""
from alembic import op
import sqlalchemy as sa

revision = "0015_channel_monitor_health"
down_revision = "0014_recording_rules"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("channels", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("channels", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("channels", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("channels", sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("channels", sa.Column("baseline_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE channels SET baseline_completed_at = created_at")
    op.add_column("audit_logs", sa.Column("channel_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_audit_logs_channel_id_channels", "audit_logs", "channels", ["channel_id"], ["id"])
    op.create_index("ix_audit_logs_channel_id", "audit_logs", ["channel_id"])


def downgrade():
    op.drop_index("ix_audit_logs_channel_id", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_channel_id_channels", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "channel_id")
    op.drop_column("channels", "baseline_completed_at")
    op.drop_column("channels", "consecutive_failures")
    op.drop_column("channels", "last_error")
    op.drop_column("channels", "last_success_at")
    op.drop_column("channels", "last_checked_at")
