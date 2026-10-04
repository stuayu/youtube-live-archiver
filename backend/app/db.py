from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings


class Base(DeclarativeBase):
    pass


_connect_args = {}
if settings.database_url.startswith(("postgresql://", "postgresql+")):
    _connect_args["options"] = f"-c idle_in_transaction_session_timeout={max(0, settings.db_idle_in_transaction_timeout_ms)}"
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # A dependency may have yielded while a failed request left a transaction open.
        # Always close even if rollback itself discovers a broken connection.
        try:
            if db.in_transaction():
                db.rollback()
        finally:
            db.close()
