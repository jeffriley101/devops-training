"""P31 -> C32 -> C33 additive migration and privacy-minimal storage contracts."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.classroom_identity_models import ClassroomRosterIdentity as Identity, ClassroomRosterIdentityAudit as Audit
from app.db import Base
from app.operator_classroom_identity_contract import C33
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from test_classroom_s1 import adult_pin_hash
from test_classroom_progress_migration import migrated_db
from test_classroom_enrollment_v2_migration import install_with_history, enroll
from tests.test_weekly2_foundation_migration import config, rows, schema_contract, revision
from test_classroom_s2 import NOW


def installed_history(db, monkeypatch, adult_pin_hash):
    factory, refs = install_with_history(db, monkeypatch, adult_pin_hash)
    command.upgrade(config(), 'c32enroll001')
    enroll(factory, refs)
    return factory, refs


def seed_identity(factory, refs):
    with factory() as session:
        identity = Identity(program_id=1, profile_id=1, school_name='Sýnthetic 李 Student', version=1, updated_at=NOW)
        session.add(identity); session.flush()
        session.add(Audit(identity_id=identity.id, program_id=1, profile_id=1, class_id=refs.class_id,
            actor_verifier_id=2, version=1, action='created', occurred_at=NOW))
        session.commit()
        return identity.id


def test_actual_additive_chain_constraints_and_populated_downgrade_fence(migrated_db, monkeypatch, adult_pin_hash):
    db = migrated_db
    factory, refs = installed_history(db, monkeypatch, adult_pin_hash)
    before, structure = rows(db), schema_contract(db)
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c32enroll001'
    command.upgrade(config(), 'c33identity001')
    after, new_structure = rows(db), schema_contract(db)
    assert set(after) - set(before) == set(C33)
    assert all(after[name] == [] for name in C33)
    assert {name: values for name, values in after.items() if name not in C33} == before
    assert {name: values for name, values in new_structure[0].items() if name not in C33} == structure[0]
    assert new_structure[1] == structure[1]
    with db.connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c33identity001'
        assert set(C33) <= set(classroom_snapshot(connection, 'c33identity001'))
        context = MigrationContext.configure(connection, opts={'include_object':
            lambda obj, name, kind, reflected, other: kind != 'table' or name in C33})
        assert compare_metadata(context, Base.metadata) == []
    command.downgrade(config(), 'c32enroll001')
    assert rows(db) == before and schema_contract(db) == structure
    command.upgrade(config(), 'c33identity001')
    ident = seed_identity(factory, refs)
    with factory() as session:
        row = session.get(Identity, ident)
        row.school_name, row.version = 'Synthetic Corrected Student', 2
        session.add(Audit(identity_id=ident, program_id=1, profile_id=1, class_id=refs.class_id,
            actor_verifier_id=2, version=2, action='corrected', occurred_at=NOW))
        session.commit()
    used = rows(db)
    assert {name: values for name, values in used.items() if name not in C33} == before
    assert not any(key in {'school_name', 'old_name', 'new_name', 'email', 'name_hash'}
        for row in used['classroom_roster_identity_audits'] for key in row)
    assert [col['name'] for col in inspect(db).get_columns('classroom_roster_identity_audits')] == [
        'id', 'identity_id', 'program_id', 'profile_id', 'class_id', 'actor_verifier_id', 'version', 'action', 'occurred_at']
    with pytest.raises(RuntimeError, match='classroom_identity_evidence_in_use'):
        command.downgrade(config(), 'c32enroll001')
    assert revision(db) == 'c33identity001' and rows(db) == used
    for sql in [
        "UPDATE classroom_roster_identities SET school_name=' '",
        "UPDATE classroom_roster_identities SET school_name=' Leading'",
        "UPDATE classroom_roster_identities SET school_name='" + ('X' * 151) + "'",
        'UPDATE classroom_roster_identities SET version=0',
        'UPDATE classroom_roster_identities SET profile_id=999999',
        'UPDATE classroom_roster_identity_audits SET class_id=999999',
        'UPDATE classroom_roster_identity_audits SET program_id=999999',
        'UPDATE classroom_roster_identity_audits SET profile_id=999999',
        'UPDATE classroom_roster_identity_audits SET actor_verifier_id=999999',
        "UPDATE classroom_roster_identity_audits SET action='created' WHERE version=2",
        "UPDATE classroom_roster_identity_audits SET action='corrected' WHERE version=1",
        'UPDATE classroom_roster_identity_audits SET version=0',
        'DELETE FROM classroom_roster_identities',
    ]:
        with pytest.raises((IntegrityError, DataError)), db.begin() as connection:
            connection.execute(text(sql))
    assert rows(db) == used
    with db.connect() as connection:
        baseline = classroom_snapshot(connection, 'c33identity001')
        with connection.begin_nested() as negative:
            connection.execute(text("UPDATE classroom_roster_identities SET school_name='Synthetic Unauthorized Mutation'"))
            with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'):
                assert_classroom_unchanged(connection, 'c33identity001', baseline, RuntimeError)
            negative.rollback()
        for old in ['c32enroll001', 'p31pta001']:
            with connection.begin_nested() as negative:
                connection.execute(text('UPDATE alembic_version SET version_num=:revision'), {'revision': old})
                with pytest.raises(RuntimeError, match='schema_revision_mismatch'):
                    installed_revision(connection, RuntimeError)
                negative.rollback()
        if db.dialect.name == 'postgresql':
            quote = connection.dialect.identifier_preparer.quote
            for table, contract in C33.items():
                for constraint in contract['checks']:
                    with connection.begin_nested() as negative:
                        connection.execute(text(f'ALTER TABLE {quote(table)} DROP CONSTRAINT {quote(constraint)}'))
                        connection.execute(text(f'ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(constraint)} CHECK (TRUE)'))
                        with pytest.raises(RuntimeError, match='checks'):
                            installed_revision(connection, RuntimeError)
                        negative.rollback()
            with connection.begin_nested() as negative:
                connection.execute(text('ALTER TABLE classroom_roster_identity_audits DROP CONSTRAINT fk_classroom_identity_audit_class'))
                connection.execute(text('ALTER TABLE classroom_roster_identity_audits ADD CONSTRAINT fk_classroom_identity_audit_class '
                    'FOREIGN KEY (class_id,program_id) REFERENCES classroom_classes(id,program_id) ON DELETE CASCADE'))
                with pytest.raises(RuntimeError, match='foreign_keys'):
                    installed_revision(connection, RuntimeError)
                negative.rollback()
        assert installed_revision(connection, RuntimeError) == 'c33identity001'


def test_native_private_identity_archive_restoration(migrated_db, monkeypatch, adult_pin_hash, tmp_path, record_property):
    db = migrated_db
    factory, refs = installed_history(db, monkeypatch, adult_pin_hash)
    command.upgrade(config(), 'c33identity001')
    seed_identity(factory, refs)
    before = rows(db)
    if db.dialect.name == 'sqlite':
        db.dispose()
        archive = tmp_path / 'synthetic-private-identity.db'
        shutil.copyfile(db.url.database, archive)
        restored = create_engine('sqlite:///' + str(archive))
        event.listen(restored, 'connect', lambda connection, _: connection.execute('PRAGMA foreign_keys=ON'))
        try:
            with restored.connect() as connection:
                assert installed_revision(connection, RuntimeError) == 'c33identity001'
            assert rows(restored) == before
        finally:
            restored.dispose()
        return
    raw = make_url(os.environ['WW_CLASSROOM_TEST_POSTGRES_URL'])
    assert raw.host == '127.0.0.1' and raw.database.startswith('classroom_s1_') and not raw.query
    with db.connect() as connection:
        schema = connection.scalar(text('SELECT current_schema()'))
    target = 'classroom_s1_identity_restore_' + uuid4().hex
    binary = Path('/usr/lib/postgresql/18/bin')
    archive = tmp_path / 'synthetic-private-identity.dump'
    def run(*args):
        result = subprocess.run([str(arg) for arg in args], capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/home/geph', 'PGPASSFILE': '/dev/null'})
        assert result.returncode == 0, result.stderr.decode()[-1500:]
    address = ['-h', raw.host, '-p', str(raw.port), '-U', raw.username]
    run(binary/'pg_dump', *address, '-d', raw.database, '--format=custom', '--schema=' + schema, '--file=' + str(archive))
    run(binary/'createdb', *address, '--template=template0', '--encoding=UTF8', '--locale=C', target)
    restored = None
    try:
        run(binary/'pg_restore', *address, '-d', target, '--single-transaction', '--exit-on-error', archive)
        restored = create_engine(raw.set(database=target).update_query_dict({'options': '-csearch_path=' + schema}))
        with restored.connect() as connection:
            assert connection.scalar(text('SHOW server_encoding')) == 'UTF8'
            assert installed_revision(connection, RuntimeError) == 'c33identity001'
        assert rows(restored) == before
        record_property('archive_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
        record_property('native_restore', 'PostgreSQL 18 synthetic schema, retained P31/C32 guards, private identity and minimal audits')
    finally:
        if restored is not None: restored.dispose()
        run(binary/'dropdb', *address, target)


def test_multibyte_name_bounds_and_unique_scope_evidence(migrated_db, monkeypatch, adult_pin_hash):
    db = migrated_db
    factory, refs = installed_history(db, monkeypatch, adult_pin_hash)
    command.upgrade(config(), 'c33identity001')
    seed_identity(factory, refs)
    before = rows(db)
    with db.connect() as connection:
        if db.dialect.name == 'postgresql':
            assert connection.scalar(text('SHOW server_encoding')) == 'UTF8'
        with connection.begin_nested() as boundary:
            connection.execute(text('UPDATE classroom_roster_identities SET school_name=:name'), {'name': '李' * 150})
            assert connection.scalar(text('SELECT length(school_name) FROM classroom_roster_identities')) == 150
            boundary.rollback()
    for sql in [
        'INSERT INTO classroom_roster_identities (program_id,profile_id,school_name,version,updated_at) '
        'SELECT program_id,profile_id,school_name,version,updated_at FROM classroom_roster_identities',
        'INSERT INTO classroom_roster_identity_audits '
        '(identity_id,program_id,profile_id,class_id,actor_verifier_id,version,action,occurred_at) '
        'SELECT identity_id,program_id,profile_id,class_id,actor_verifier_id,version,action,occurred_at '
        'FROM classroom_roster_identity_audits',
    ]:
        with pytest.raises(IntegrityError), db.begin() as connection:
            connection.execute(text(sql))
    with pytest.raises((IntegrityError, DataError)), db.begin() as connection:
        connection.execute(text('UPDATE classroom_roster_identities SET school_name=:name'), {'name': '李' * 151})
    assert rows(db) == before
