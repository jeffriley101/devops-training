"""Real C34->C35 migration, including preserved older chart choices."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.models import PracticeChart
from app import classroom_reporting as boundary, classroom_chart_sharing as sharing
from app.classroom_models import ClassroomClassState
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from test_classroom_progress_migration import migrated_db, seeded_sources
from test_classroom_school_authorization import school_data, verify, adult_pin_hash, captured, guardian_data
from test_classroom_school_chart_privacy import proposed_chart_data
from test_classroom_guardian_authorization import student
from test_classroom_s2 import student as ordinary_student
from tests.test_weekly2_foundation_migration import config, rows, schema_contract, revision


def test_c35_adds_only_school_receipt_and_nullable_chart_reference_preserving_old_choices(migrated_db, monkeypatch, adult_pin_hash):
    db = migrated_db
    command.upgrade(config(), 'c34guardian001')
    factory, refs = seeded_sources(db, monkeypatch, adult_pin_hash)
    with factory() as session:
        context = boundary._context(session, 1, refs.class_id, 1, student=ordinary_student(session))
        anchor, member, entitlement, rule, activation, now = context
        chart = PracticeChart(profile_id=1, practice_date=now.date(), created_at=now,
            minutes=17, instrument='Trumpet', note='RETAINED_OLD_INDIVIDUAL_CHART')
        session.add(chart); session.flush()
        old = Table('classroom_chart_director_shares', MetaData(), autoload_with=session.connection())
        session.execute(old.insert().values(chart_id=chart.id, class_id=refs.class_id,
            assignment_id=refs.assignment_id, membership_period_id=member.id, invitation_id=None,
            entitlement_id=entitlement.id, account_declared_at=rule.declared_at,
            class_activation_at=activation, source_audit_id=None, selected_at=now, withdrawn_at=None))
        session.commit()
    before, schema = rows(db), schema_contract(db)
    command.upgrade(config(), 'c35school001')
    after = rows(db)
    assert set(after)-set(before) == {'classroom_school_authorizations'}
    assert {name: value for name,value in after.items() if name not in {'classroom_school_authorizations','classroom_chart_director_shares'}} == {
        name:value for name,value in before.items() if name != 'classroom_chart_director_shares'}
    with db.connect() as connection:
        assert connection.scalar(text('SELECT guardian_permission_id FROM classroom_chart_director_shares')) is None
        assert connection.scalar(text('SELECT count(*) FROM classroom_chart_director_shares')) == 1
        assert installed_revision(connection, RuntimeError) == 'c35school001'
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert 'classroom_school_authorizations' in classroom_snapshot(connection, 'c35school001')
        for old_stamp in ('c34guardian001', 'c33identity001', 'p31pta001'):
            with connection.begin_nested() as negative:
                connection.execute(text('UPDATE alembic_version SET version_num=:stamp'), {'stamp':old_stamp})
                with pytest.raises(RuntimeError, match='schema_revision_mismatch'): installed_revision(connection, RuntimeError)
                negative.rollback()
    command.downgrade(config(), 'c34guardian001')
    restored = schema_contract(db)
    # SQLite batch rebuilds retain every FK definition but reflection order is
    # not stable. Preserve names, endpoints and delete actions exactly.
    def canonical(structure):
        import copy, json
        tables, triggers = copy.deepcopy(structure)
        for table in tables.values():
            table['foreign_keys'] = sorted(table['foreign_keys'], key=lambda value: json.dumps(value, sort_keys=True))
        return tables, triggers
    assert rows(db) == before and canonical(restored) == canonical(schema)


def test_school_evidence_refuses_downgrade_and_operator_snapshots_detect_changes(school_data):
    refs = school_data; db = refs.factory.engine
    with refs.factory() as session: verify(session, refs); session.commit()
    before = rows(db)
    with pytest.raises(RuntimeError, match='school_evidence_in_use'): command.downgrade(config(), 'c34guardian001')
    assert revision(db) == 'c35school001' and rows(db) == before
    with db.connect() as connection:
        snapshot = classroom_snapshot(connection, 'c35school001')
        with connection.begin_nested() as negative:
            connection.execute(text("UPDATE classroom_school_authorizations SET verification_notes='tampered'"))
            with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'):
                assert_classroom_unchanged(connection, 'c35school001', snapshot, RuntimeError)
            negative.rollback()
    for sql in ("UPDATE classroom_school_authorizations SET authority_kind='email_domain'",
        'UPDATE classroom_school_authorizations SET approver_id=verified_by_id',
        'UPDATE classroom_school_authorizations SET contacted_at=expires_at',
        'UPDATE classroom_school_authorizations SET approved_at=expires_at',
        'UPDATE classroom_school_authorizations SET revoked_at=verified_at',
        'UPDATE classroom_school_authorizations SET program_id=999999'):
        with pytest.raises(IntegrityError), db.begin() as connection: connection.execute(text(sql))


def test_exact_parent_chart_receipt_refuses_downgrade_even_without_school_rows(proposed_chart_data):
    refs = proposed_chart_data; db = refs.factory.engine
    with refs.factory() as session:
        sharing.set_sharing(session, student=student(session), chart_id=refs.chart_id,
            assignment_ids=[refs.private_assignment_id]); session.commit()
    before = rows(db)
    with pytest.raises(RuntimeError, match='school_evidence_in_use'): command.downgrade(config(), 'c34guardian001')
    assert revision(db) == 'c35school001' and rows(db) == before
    with pytest.raises(IntegrityError), db.begin() as connection:
        connection.execute(text('UPDATE classroom_chart_director_shares SET guardian_permission_id=999999'))
