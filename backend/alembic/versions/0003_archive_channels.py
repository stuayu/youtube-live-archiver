"""Allow soft deleting channels while preserving their video history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_archive_channels"
down_revision = "0002_fallback_storage"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("channels")}
    if "archived" not in columns:
        op.add_column("channels", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("channels")}
    if "archived" in columns:
        op.drop_column("channels", "archived")
