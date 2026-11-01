"""Actual P31 -> c32 migration, preserved history, schema refusals and restore."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import classroom, classroom_enrollment as enrollment
from app.classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation
from app.db import Base
from app.operator_classroom_enrollment_contract import C32
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from test_classroom_s1 import actor, adult_pin_hash
from test_classroom_s2 import student
from test_classroom_progress_migration import migrated_db, seeded_sources
from tests.test_weekly2_foundation_migration import config, rows, schema_contract, revision


def install_with_history(db, monkeypatch, adult_pin_hash):
    command.upgrade(config(), 'p31pta001')
    factory, refs = seeded_sources(db, monkeypatch, adult_pin_hash)
    monkeypatch.setenv('CLASSROOM_ENROLLMENT_V2_ENABLED', '1')
    return factory, refs


def enroll(factory, refs):
    with factory() as session:
        offer = enrollment.issue_invitation(session, actor=actor(session, 2), program_id=1,
            class_id=refs.class_id, woodchuck_id='WC-SYNTHETIC')
        result = enrollment.respond(session, student=student(session), invitation_id=offer.id, choice='accept')
        session.commit()
        return offer.id, result


def test_actual_additive_migration_runtime_and_populated_downgrade_fence(migrated_db, monkeypatch, adult_pin_hash):
    db = migrated_db
    factory, refs = install_with_history(db, monkeypatch, adult_pin_hash)
    before, structure = rows(db), schema_contract(db)
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'p31pta001'
    command.upgrade(config(), 'c32enroll001')
    after, new_structure = rows(db), schema_contract(db)
    assert set(after) - set(before) == set(C32)
    assert all(after[name] == [] for name in C32)
    assert {k: v for k, v in after.items() if k not in C32} == before
    assert {k: v for k, v in new_structure[0].items() if k not in C32} == structure[0]
    assert new_structure[1] == structure[1]  # Every prior trigger/function unchanged.
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c32enroll001'
        assert set(C32) <= set(classroom_snapshot(connection, 'c32enroll001'))
        context = MigrationContext.configure(connection, opts={'include_object':
            lambda obj, name, kind, reflected, other: kind != 'table' or name in C32})
        assert compare_metadata(context, Base.metadata) == []
    command.downgrade(config(), 'p31pta001')
    assert rows(db) == before and schema_contract(db) == structure
    command.upgrade(config(), 'c32enroll001')
    ident, result = enroll(factory, refs)
    assert result.state == 'accepted'
    used = rows(db)
    # This student was already enrolled before the additive migration.
    assert {k: v for k, v in used.items() if k not in C32} == before
    with pytest.raises(RuntimeError, match='classroom_enrollment_evidence_in_use'):
        command.downgrade(config(), 'p31pta001')
    assert revision(db) == 'c32enroll001' and rows(db) == used
    for sql in [
        "UPDATE classroom_enrollment_invitations SET purpose='classroom-chart-sharing'",
        "UPDATE classroom_enrollment_invitations SET class_id=99999",
        "UPDATE classroom_enrollment_invitations SET accepted_period_id=NULL",
        "UPDATE classroom_enrollment_audit_events SET profile_id=99999",
        "UPDATE classroom_enrollment_audit_events SET actor_profile_id=NULL,actor_verifier_id=NULL",
    ]:
        with pytest.raises(IntegrityError), db.begin() as connection:
            connection.execute(text(sql))
    assert rows(db) == used
    with db.connect() as connection:
        baseline = classroom_snapshot(connection, 'c32enroll001')
        with connection.begin_nested() as negative:
            connection.execute(text("UPDATE classroom_enrollment_invitations SET issued_by_verifier_id=1"))
            with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'):
                assert_classroom_unchanged(connection, 'c32enroll001', baseline, RuntimeError)
            negative.rollback()
        for sql in [
            'DROP INDEX uq_classroom_enrollment_invitation_open',
            "UPDATE alembic_version SET version_num='p31pta001'",  # Negative false stamp only.
        ]:
            with connection.begin_nested() as negative:
                connection.execute(text(sql))
                with pytest.raises(RuntimeError): installed_revision(connection, RuntimeError)
                negative.rollback()
        if db.dialect.name == 'postgresql':
            quote = connection.dialect.identifier_preparer.quote
            for table, contract in C32.items():
                for name in contract['checks']:
                    with connection.begin_nested() as negative:
                        connection.execute(text(f'ALTER TABLE {quote(table)} DROP CONSTRAINT {quote(name)}'))
                        connection.execute(text(f'ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(name)} CHECK (TRUE)'))
                        with pytest.raises(RuntimeError, match='checks'):
                            installed_revision(connection, RuntimeError)
                        negative.rollback()
            with connection.begin_nested() as negative:
                connection.execute(text('ALTER TABLE classroom_enrollment_invitations DROP CONSTRAINT fk_classroom_enrollment_class'))
                connection.execute(text('ALTER TABLE classroom_enrollment_invitations ADD CONSTRAINT fk_classroom_enrollment_class '
                    'FOREIGN KEY (class_id,program_id) REFERENCES classroom_classes(id,program_id) ON DELETE CASCADE'))
                with pytest.raises(RuntimeError, match='foreign_keys'):
                    installed_revision(connection, RuntimeError)
                negative.rollback()
        assert installed_revision(connection, RuntimeError) == 'c32enroll001'


def test_postgres_native_archive_restore_preserves_enrollment_and_guards(migrated_db, monkeypatch, adult_pin_hash, tmp_path, record_property):
    if migrated_db.dialect.name != 'postgresql':
        # The SQLite parameter still exercises the equivalent local file restore.
        import shutil
        factory, refs = install_with_history(migrated_db, monkeypatch, adult_pin_hash)
        command.upgrade(config(), 'c32enroll001'); enroll(factory, refs)
        before = rows(migrated_db)
        migrated_db.dispose()
        archive = tmp_path / 'restored-enrollment.db'
        shutil.copyfile(migrated_db.url.database, archive)
        restored = create_engine('sqlite:///' + str(archive))
        event.listen(restored, 'connect', lambda connection, _: connection.execute('PRAGMA foreign_keys=ON'))
        with restored.connect() as connection:
            assert installed_revision(connection, RuntimeError) == 'c32enroll001'
        assert rows(restored) == before
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        ident = before['classroom_enrollment_invitations'][0]['id']
        with restored_factory() as session:
            assert enrollment.respond(session, student=student(session), invitation_id=ident, choice='accept').state == 'accepted'
        assert rows(restored) == before
        restored.dispose()
        return
    db = migrated_db
    factory, refs = install_with_history(db, monkeypatch, adult_pin_hash)
    command.upgrade(config(), 'c32enroll001'); enroll(factory, refs)
    before = rows(db)
    raw = make_url(os.environ['WW_CLASSROOM_TEST_POSTGRES_URL'])
    assert raw.host == '127.0.0.1' and raw.database.startswith('classroom_s1_') and not raw.query
    with db.connect() as connection: schema = connection.scalar(text('SELECT current_schema()'))
    target = 'classroom_s1_restore_' + uuid4().hex
    binary = Path('/usr/lib/postgresql/18/bin')
    archive = tmp_path / 'enrollment.dump'
    def run(*args):
        result = subprocess.run([str(v) for v in args], capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/home/geph', 'PGPASSFILE': '/dev/null'})
        assert result.returncode == 0, result.stderr.decode()[-2000:]
    address = ['-h', raw.host, '-p', str(raw.port), '-U', raw.username]
    run(binary / 'pg_dump', *address, '-d', raw.database, '--format=custom', '--schema=' + schema, '--file=' + str(archive))
    run(binary / 'createdb', *address, target)
    restored = None
    try:
        run(binary / 'pg_restore', *address, '-d', target, '--single-transaction', '--exit-on-error', archive)
        restored = create_engine(raw.set(database=target).update_query_dict({'options': '-csearch_path=' + schema}))
        with restored.connect() as connection:
            assert installed_revision(connection, RuntimeError) == 'c32enroll001'
        assert rows(restored) == before
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        ident = before['classroom_enrollment_invitations'][0]['id']
        with restored_factory() as session:
            assert enrollment.respond(session, student=student(session), invitation_id=ident, choice='accept').state == 'accepted'
        assert rows(restored) == before
        record_property('archive_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
        record_property('native_restore', 'PostgreSQL 18, complete fixture schema including data and guards')
    finally:
        if restored is not None: restored.dispose()
        run(binary / 'dropdb', *address, target)
