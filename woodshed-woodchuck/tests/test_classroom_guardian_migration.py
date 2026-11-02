"""Actual additive guardian schema, retained evidence and strict operator checks."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.operator_classroom_guardian_contract import C34
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from app.classroom_guardian_models import ClassroomGuardianAuthorization as Grant
from test_classroom_s1 import adult_pin_hash
from test_classroom_progress_migration import migrated_db
from test_classroom_guardian_authorization import guardian_data, captured, fully_authorize
from tests.test_weekly2_foundation_migration import config, rows, schema_contract, revision


def test_empty_c34_upgrade_preserves_all_prior_objects_and_operator_snapshot(migrated_db):
    db = migrated_db
    command.upgrade(config(), 'c33identity001')
    before, structure = rows(db), schema_contract(db)
    command.upgrade(config(), 'c34guardian001')
    after, current = rows(db), schema_contract(db)
    assert set(after) - set(before) == set(C34)
    assert {key:value for key,value in after.items() if key not in C34} == before
    assert {key:value for key,value in current[0].items() if key not in C34} == structure[0]
    assert current[1] == structure[1]
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c34guardian001'
        assert set(C34) <= set(classroom_snapshot(connection, 'c34guardian001'))
        # C35 has its own full-head parity check. Qualify the historical C34
        # contract against only the schema available at that revision.
        ctx = MigrationContext.configure(connection, opts={'include_object': lambda obj,name,kind,reflected,compare:
            not (kind == 'table' and name == 'classroom_school_authorizations')
            and not (kind == 'column' and name == 'guardian_permission_id')
            and not (kind == 'foreign_key_constraint' and name == 'fk_classroom_chart_guardian_permission')})
        assert compare_metadata(ctx, Base.metadata) == []
        for old in ('c33identity001', 'c32enroll001', 'p31pta001'):
            with connection.begin_nested() as negative:
                connection.execute(text('UPDATE alembic_version SET version_num=:old'), {'old':old})
                with pytest.raises(RuntimeError, match='schema_revision_mismatch'):
                    installed_revision(connection, RuntimeError)
                negative.rollback()
    command.downgrade(config(), 'c33identity001')
    assert rows(db) == before and schema_contract(db) == structure
    command.upgrade(config(), 'c34guardian001')
    assert revision(db) == 'c34guardian001'


def test_qualified_evidence_cannot_be_downgraded_or_cross_scoped(guardian_data):
    refs = guardian_data
    command.downgrade(config(), 'c34guardian001')
    fully_authorize(refs)
    db = refs.factory.engine
    before = rows(db)
    with pytest.raises(RuntimeError, match='classroom_guardian_evidence_in_use'):
        command.downgrade(config(), 'c33identity001')
    assert revision(db) == 'c34guardian001' and rows(db) == before
    for sql in (
        "UPDATE classroom_guardian_authorizations SET purpose='school'",
        "UPDATE classroom_guardian_authorizations SET class_id=999999",
        "UPDATE classroom_guardian_authorizations SET consent_id=999999",
        "UPDATE classroom_guardian_authorizations SET verification_id=999999",
        "UPDATE classroom_guardian_authorizations SET expires_at=accepted_at",
        "UPDATE classroom_guardian_authorizations SET guardian_attested_at=expires_at",
        "UPDATE classroom_guardian_authorizations SET field_contract=NULL WHERE purpose='reporting'",
        "UPDATE classroom_guardian_authorizations SET participation_id=NULL WHERE purpose='reporting'",
        "UPDATE classroom_guardian_authorizations SET withdrawn_at=accepted_at",
        "UPDATE classroom_guardian_recipients SET assignment_id=999999",
        "UPDATE classroom_guardian_recipients SET profile_id=999999",
        "UPDATE classroom_guardian_audits SET class_id=999999",
        "UPDATE classroom_guardian_audits SET actor_consent_id=999999",
    ):
        with pytest.raises(IntegrityError), db.begin() as connection:
            connection.execute(text(sql))
    assert rows(db) == before
    with db.connect() as connection:
        baseline = classroom_snapshot(connection, 'c34guardian001')
        with connection.begin_nested() as negative:
            connection.execute(text("UPDATE classroom_guardian_authorizations SET notice_digest='changed'"))
            with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'):
                assert_classroom_unchanged(connection, 'c34guardian001', baseline, RuntimeError)
            negative.rollback()
    # Guardian records never copy the parent address, nickname, raw KWS result,
    # credential, practice measurements or notes into the new evidence schema.
    for name in C34:
        assert not {'email','parent_email','name','note','payload','pin','session_hash','minutes'} & {
            col['name'] for col in inspect(db).get_columns(name)}
