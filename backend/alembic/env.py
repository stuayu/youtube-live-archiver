from logging.config import fileConfig
from alembic import context
from app.db import Base
from app import models  # noqa
from app.database import resolve_database_url
from sqlalchemy import text

config = context.config
# ConfigParser treats percent signs as interpolation markers. SQLAlchemy URLs
# escape reserved password characters as %XX, so preserve those escapes here.
try:
    database_url = resolve_database_url()
except RuntimeError as exc:
    print(str(exc), flush=True)
    raise SystemExit(1)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
if config.config_file_name: fileConfig(config.config_file_name)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    from sqlalchemy import engine_from_config, pool
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(7182391408123)"))
        # End SQLAlchemy's implicit transaction while retaining the session lock;
        # Alembic must own a fresh transaction so each migration commits.
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction(): context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(7182391408123)"))

run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
