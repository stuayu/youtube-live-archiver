"""Add per-channel fallback storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_fallback_storage"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 uses current Base.metadata.create_all(), so fresh installs can already
    # contain this column. Existing installations need it added here.
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("channels")}
    if "fallback_storage_id" not in columns:
        bind = op.get_bind()
        # SQLite can add a REFERENCES column directly, but cannot ALTER ADD
        # CONSTRAINT as a separate operation.
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("channels") as batch:
                batch.add_column(sa.Column("fallback_storage_id", sa.Integer(), nullable=True))
                batch.create_foreign_key("fk_channels_fallback_storage_id_storages", "storages", ["fallback_storage_id"], ["id"])
        else:
            op.add_column("channels", sa.Column("fallback_storage_id", sa.Integer(), nullable=True))
            op.create_foreign_key("fk_channels_fallback_storage_id_storages", "channels", "storages",
                                  ["fallback_storage_id"], ["id"])


def downgrade():
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("channels")}
    if "fallback_storage_id" in columns:
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("channels") as batch:
                batch.drop_constraint("fk_channels_fallback_storage_id_storages", type_="foreignkey")
                batch.drop_column("fallback_storage_id")
            return
        constraints = inspect(op.get_bind()).get_foreign_keys("channels")
        for constraint in constraints:
            if "fallback_storage_id" in (constraint.get("constrained_columns") or []) and constraint.get("name"):
                op.drop_constraint(constraint["name"], "channels", type_="foreignkey")
        op.drop_column("channels", "fallback_storage_id")
