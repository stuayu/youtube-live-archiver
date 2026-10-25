import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.config import settings
from app.db import Base
from app import models  # noqa: F401


pytestmark = pytest.mark.integration


def run_alembic(command: str):
    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "alembic", *command.split()], cwd=backend_dir,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_database_migration_round_trip_and_metadata_drift():
    run_alembic("downgrade base")
    run_alembic("upgrade head")
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": False})
        differences = compare_metadata(context, Base.metadata)
    engine.dispose()
    unexpected = [difference for difference in differences
                  if "ix_transcript_segments_search" not in repr(difference)]
    assert unexpected == []
