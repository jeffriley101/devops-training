"""Disposable migration checks; a stored preference grants no authority."""
from datetime import date

from alembic import command
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import classroom_models as cm, models as m
from app.operator_schema_compatibility import assert_classroom_unchanged, classroom_snapshot, installed_revision
from tests.test_weekly2_foundation_migration import db, config, revision, seed_history, NOW

OLD, NEW = "w25weekly005", "c25class001"


def test_chart_sharing_migration_round_trip_and_private_history(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    with Session(db) as session:
        session.add(m.PracticeChart(id=1, profile_id=1, practice_date=date(2026, 10, 19),
                                    minutes=30, instrument="Trumpet"))
        session.commit()
    command.upgrade(config(), NEW)
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == NEW
        assert classroom_snapshot(connection, NEW)["classroom_chart_shares"] == []
    with Session(db) as session:
        assert session.scalars(select(cm.ClassroomChartShare)).all() == []
        assert session.get(m.PracticeChart, 1).minutes == 30
        assert session.get(cm.ClassroomReportingPeriod, 1).scope_version == "classroom-boundary-v1"
    command.downgrade(config(), OLD)
    assert revision(db) == OLD
    assert "classroom_chart_shares" not in inspect(db).get_table_names()
    command.upgrade(config(), NEW)
    assert revision(db) == NEW


def test_shares_are_class_specific_and_populated_downgrade_refuses(db):
    command.upgrade(config(), NEW)
    seed_history(db)
    with Session(db) as session:
        session.add(cm.ClassroomClass(id=2, program_id=1, display_name="Second synthetic Class"))
        session.add(m.PracticeChart(id=1, profile_id=1, practice_date=date(2026, 10, 19),
                                    minutes=30, instrument="Trumpet"))
        session.commit()
        session.add_all([cm.ClassroomChartShare(chart_id=1, class_id=1),
                         cm.ClassroomChartShare(chart_id=1, class_id=2)])
        session.commit()
        assert len(session.scalars(select(cm.ClassroomChartShare)).all()) == 2
    with pytest.raises(IntegrityError), Session(db) as session:
        session.add(cm.ClassroomChartShare(chart_id=1, class_id=1))
        session.commit()
    with pytest.raises(RuntimeError, match="classroom_chart_shares_in_use"):
        command.downgrade(config(), OLD)
    assert revision(db) == NEW
    with Session(db) as session:
        assert len(session.scalars(select(cm.ClassroomChartShare)).all()) == 2
        assert session.get(m.PracticeChart, 1).minutes == 30
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == NEW
        assert len(classroom_snapshot(connection, NEW)["classroom_chart_shares"]) == 2
    with pytest.raises(RuntimeError, match="unexpected_classroom_row_change"), db.begin() as connection:
        before = classroom_snapshot(connection, NEW)
        connection.execute(text("DELETE FROM classroom_chart_shares WHERE class_id = 1"))
        assert_classroom_unchanged(connection, NEW, before, RuntimeError)
    with db.connect() as connection:
        assert len(classroom_snapshot(connection, NEW)["classroom_chart_shares"]) == 2


def test_operator_refuses_changed_sharing_schema(db):
    command.upgrade(config(), NEW)
    with db.begin() as connection:
        connection.execute(text("ALTER TABLE classroom_chart_shares DROP COLUMN selected_at"))
    with db.connect() as connection:
        with pytest.raises(RuntimeError, match="classroom_chart_shares: columns"):
            installed_revision(connection, RuntimeError)


def test_recipient_history_blocks_downgrade(db):
    command.upgrade(config(), NEW)
    seed_history(db)
    with Session(db) as session:
        session.add(m.PracticeChart(id=1, profile_id=1, practice_date=date(2026, 10, 19),
                                    minutes=30, instrument="Trumpet"))
        session.add(cm.ClassroomTeachingAssignment(id=1, program_id=1, class_id=1, verifier_id=1,
            starts_at=NOW, granted_by_verifier_id=1))
        session.flush()
        session.add(cm.ClassroomChartDirectorShare(chart_id=1, class_id=1, assignment_id=1,
            membership_period_id=1, entitlement_id=1, account_declared_at=NOW,
            class_activation_at=NOW, selected_at=NOW))
        session.commit()
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == NEW
        assert len(classroom_snapshot(connection, NEW)["classroom_chart_director_shares"]) == 1
    with pytest.raises(RuntimeError, match="classroom_chart_shares_in_use"):
        command.downgrade(config(), OLD)
    assert revision(db) == NEW
