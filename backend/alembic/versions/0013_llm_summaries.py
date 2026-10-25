"""optional transcript summaries"""
from alembic import op
import sqlalchemy as sa

revision = "0013_llm_summaries"
down_revision = "0012_processing_transcription"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("videos", sa.Column("summary_status", sa.String(20), nullable=False, server_default="disabled"))
    op.add_column("videos", sa.Column("summary_data", sa.JSON(), nullable=True))
    op.add_column("videos", sa.Column("summary_error", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column("summary_model", sa.String(200), nullable=True))
    op.create_table("summary_cache", sa.Column("key", sa.String(64), primary_key=True), sa.Column("result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("summary_cache")
    for col in ("summary_model", "summary_error", "summary_data", "summary_status"):
        op.drop_column("videos", col)
