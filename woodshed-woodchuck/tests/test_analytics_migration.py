"""Only the new table changes, including a populated local migration round trip."""
from datetime import date, datetime, timezone
from importlib import import_module
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.models import AnalyticsEvent, WoodchuckProfile

OLD = "t0p1q2r3s4t5"
NEW = "u1q2r3s4t5u6"


def snapshot(engine):
    with engine.connect() as connection:
        schema = dict(connection.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'analytics_events%' AND name NOT LIKE 'ix_analytics_events%'"
        )).all())
        data = {name: connection.execute(text(f'SELECT * FROM "{name}"')).all()
                for name in inspect(connection).get_table_names()
                if name not in {"alembic_version", "analytics_events"}}
        return schema, data


def test_additive_upgrade_downgrade_preserves_every_existing_table(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(WoodchuckProfile.__table__.insert().values(id=1, woodchuck_id="WC-HISTORY",
            display_name="Unchanged", pin_hash="private", instrument="Flute", level="Beginner", goal="Practice"))
    before = snapshot(engine)
    command.upgrade(config, NEW)
    assert snapshot(engine) == before
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare_to:
                name == "analytics_events" if kind == "table" else True,
        }), Base.metadata)
        assert differences == []
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == NEW
    values = dict(profile_id=1, event_type="arcade_entered", activity_date=date(2026, 9, 15),
                  occurred_at=datetime(2026, 9, 15, 18, tzinfo=timezone.utc))
    with engine.begin() as connection:
        connection.execute(AnalyticsEvent.__table__.insert().values(**values))
    for invalid in (values, {**values, "event_type": "arbitrary"}):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(AnalyticsEvent.__table__.insert().values(**invalid))
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        with pytest.raises(IntegrityError):
            connection.execute(AnalyticsEvent.__table__.insert().values(**{**values, "profile_id": 999}))
        connection.rollback()
        connection.execute(text("DELETE FROM woodchuck_profiles WHERE id = 1"))
        assert connection.scalar(text("SELECT COUNT(*) FROM analytics_events")) == 0
        connection.rollback()  # Keep historical account and event for the downgrade check.
    command.downgrade(config, OLD)
    assert snapshot(engine) == before
    assert "analytics_events" not in inspect(engine).get_table_names()
    command.upgrade(config, NEW)
    assert snapshot(engine) == before
    engine.dispose()


def test_postgres_migration_compiles_to_only_additive_ddl():
    migration = import_module(f"migrations.versions.{NEW}_add_analytics_events")
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE analytics_events" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "ON DELETE CASCADE" in sql
    assert "ALTER TABLE" not in sql and "UPDATE " not in sql and "DROP " not in sql
