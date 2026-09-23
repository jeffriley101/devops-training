"""Release 2 tester migration round trip on a disposable SQLite database."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db import Base


OLD = "a13screen004"
NEW = "b14tester001"


def test_tester_enrollment_upgrade_and_downgrade_are_scoped(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'tester-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO woodchuck_profiles "
            "(id,woodchuck_id,display_name,pin_hash,instrument,level,goal,status,session_version,"
            "deletion_failed_attempts,plunge_best_score,created_at,updated_at) "
            "VALUES (1,'WC-MIGRATE','Existing','private','Flute','Beginner','Practice','active',0,0,0,"
            "'2026-09-16 12:00:00','2026-09-16 12:00:00')"
        ))
    command.upgrade(config, NEW)
    inspector = inspect(engine)
    assert "tester_enrollments" in inspector.get_table_names()
    pending_columns = {column["name"] for column in inspector.get_columns("child_pending_consents")}
    assert {"cohort_key", "cohort_claimed_at"} <= pending_columns
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tester_enrollments (profile_id,cohort_key,joined_at) "
            "VALUES (1,'PILOT-D1','2026-09-16 12:00:00')"
        ))
    command.downgrade(config, OLD)
    inspector = inspect(engine)
    assert "tester_enrollments" not in inspector.get_table_names()
    pending_columns = {column["name"] for column in inspector.get_columns("child_pending_consents")}
    assert "cohort_key" not in pending_columns and "cohort_claimed_at" not in pending_columns
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM woodchuck_profiles")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == OLD
    command.upgrade(config, NEW)
    assert inspect(engine).has_table("tester_enrollments")
    engine.dispose()
