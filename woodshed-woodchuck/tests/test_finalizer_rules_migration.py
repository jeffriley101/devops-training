"""A new finalizer marker must never attest to rules used by older writers."""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_add_finalizer_rules_version_keeps_old_provenance_unknown():
    migration = importlib.import_module(
        "migrations.versions.d17contest001_add_finalizer_rules_version"
    )
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE contest_weeks ("
                "id INTEGER PRIMARY KEY, status TEXT NOT NULL, "
                "practice_scoring_mode TEXT)"
            ))
            connection.execute(text(
                "INSERT INTO contest_weeks (id, status, practice_scoring_mode) "
                "VALUES (1, 'finalized', 'legacy_minutes'), "
                "(2, 'finalized', 'precise_seconds'), (3, 'open', NULL)"
            ))
            before = list(connection.execute(text(
                "SELECT id, status, practice_scoring_mode FROM contest_weeks ORDER BY id"
            )))

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            column = next(column for column in inspect(connection).get_columns("contest_weeks")
                          if column["name"] == "finalizer_rules_version")
            assert column["nullable"]
            assert column["default"] is None
            assert list(connection.execute(text(
                "SELECT id, status, practice_scoring_mode FROM contest_weeks ORDER BY id"
            ))) == before
            assert list(connection.scalars(text(
                "SELECT finalizer_rules_version FROM contest_weeks ORDER BY id"
            ))) == [None, None, None]

            # Updating an old open row, or inserting through an old writer that
            # does not know the new column, cannot invent rule provenance.
            connection.execute(text("UPDATE contest_weeks SET status = 'finalized' WHERE id = 3"))
            connection.execute(text(
                "INSERT INTO contest_weeks (id, status, practice_scoring_mode) "
                "VALUES (4, 'finalized', 'precise_seconds')"
            ))
            assert list(connection.scalars(text(
                "SELECT finalizer_rules_version FROM contest_weeks ORDER BY id"
            ))) == [None, None, None, None]
    finally:
        engine.dispose()
