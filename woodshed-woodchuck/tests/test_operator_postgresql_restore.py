"""Real sequential dump/restores, enabled only for two explicit local test DBs."""
from contextlib import contextmanager
import json
import os
import re
import shutil
import subprocess

from alembic import command
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app import operator_schema_compatibility as compatibility
from app import pta_receipt_schema as issuance
from tests.test_c22_operational_compatibility import all_rows, config, cutover, set_clock
from tests.test_c30_operational_compatibility import populate_extensions
from tests.test_operator_check_restore_forms import FORMS
from tests.test_persistent_team_cutover import NOW, activate, plan, stage
from tests.test_team_families import disposable_url
from tests.test_weekly2_operator_compatibility import install, migrated_db, populate_weekly


@pytest.fixture(scope='module')
def restore_target():
    raw = os.getenv('WW_OPERATOR_RESTORE_POSTGRES_URL')
    if not raw:
        pytest.skip('No explicit disposable PostgreSQL restore database configured')
    source = make_url(os.environ['WW_BILLING_TEST_POSTGRES_URL'])
    target = make_url(raw)
    assert source.database == 'ww_billing_a2_test'
    assert target.database == 'ww_check_adapter_restore'
    assert source.get_backend_name() == target.get_backend_name() == 'postgresql'
    assert source.host == target.host and source.host in {'localhost', '127.0.0.1', '::1'}
    assert source.port == target.port and source.username == target.username
    assert not target.query
    return target


def schema_name(url):
    options = make_url(url).query['options']
    match = re.search(r'(?:^| )-csearch_path=(team_family_[a-f0-9]{32})(?: |$)', options)
    assert match, options
    return match[1]


def tool(tmp_path, label, args):
    result = subprocess.run(args, capture_output=True, text=True)
    (tmp_path / (label + '.log')).write_text(result.stdout + result.stderr)
    (tmp_path / (label + '.json')).write_text(json.dumps(
        {'command': args, 'returncode': result.returncode}, indent=2) + '\n')
    assert result.returncode == 0, result.stdout + result.stderr


def archive(url, tmp_path):
    target = tmp_path / 'schema.dump'
    raw_url = make_url(url).set(drivername='postgresql', query={}).render_as_string(hide_password=False)
    tool(tmp_path, 'dump', [shutil.which('pg_dump') or 'pg_dump', '--dbname=' + raw_url,
                           '--format=custom', '--schema=' + schema_name(url), '--file=' + str(target)])
    return target


def restore(target, dump, tmp_path, label='restore'):
    raw_url = target.set(drivername='postgresql', query={}).render_as_string(hide_password=False)
    tool(tmp_path, label, [shutil.which('pg_restore') or 'pg_restore', '--exit-on-error',
                           '--dbname=' + raw_url, str(dump)])


def scoped_target(source_url, target):
    return target.update_query_dict(make_url(source_url).query).render_as_string(hide_password=False)


def drop_schema(url):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + schema_name(url) + '" CASCADE')
    finally:
        engine.dispose()


@pytest.mark.parametrize('revision', compatibility.SUPPORTED_REVISIONS)
def test_full_restore_preserves_supported_revision(tmp_path, monkeypatch, restore_target, revision):
    url = disposable_url(tmp_path, 'postgresql')
    restored_url = scoped_target(url, restore_target)
    monkeypatch.setenv('DATABASE_URL', url)
    command.upgrade(config(), revision)
    source = create_engine(url)
    restored = create_engine(restored_url)
    try:
        with source.connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == revision
        dump = archive(url, tmp_path)
        restore(restore_target, dump, tmp_path)
        with source.connect() as original, restored.connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == revision
            before, after = inspect(original), inspect(connection)
            tables = set(before.get_table_names())
            for form in FORMS:
                if form['table'] not in tables:
                    continue
                native = {c['name']: c['sqltext'] for c in before.get_check_constraints(form['table'])}
                if form['constraint'] not in native:
                    continue
                actual = {c['name']: c['sqltext'] for c in after.get_check_constraints(form['table'])}
                assert compatibility._sql(native[form['constraint']]) == compatibility._sql(form['native'])
                assert compatibility._sql(actual[form['constraint']]) == compatibility._sql(form['restored'])
    finally:
        source.dispose()
        restored.dispose()
        drop_schema(restored_url)
        drop_schema(url)


@pytest.fixture(scope='module')
def restored_head(tmp_path_factory, restore_target):
    tmp_path = tmp_path_factory.mktemp('restored-head')
    url = disposable_url(tmp_path, 'postgresql')
    target_url = scoped_target(url, restore_target)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('DATABASE_URL', url)
        command.upgrade(config(), issuance.REVISION)
    restore(restore_target, archive(url, tmp_path), tmp_path)
    engine = create_engine(target_url)
    try:
        with engine.connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == issuance.REVISION
        yield engine
        with engine.connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == issuance.REVISION
    finally:
        engine.dispose()
        drop_schema(target_url)
        drop_schema(url)


@contextmanager
def rejected_mutation(engine, match='schema_missing_or_changed'):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
            with pytest.raises(RuntimeError, match=match):
                compatibility.installed_revision(connection, RuntimeError)
        finally:
            transaction.rollback()


def replace_check(connection, table, name, predicate, suffix=''):
    connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"')
    connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" CHECK ({predicate}) {suffix}')


@pytest.mark.parametrize('form', FORMS, ids=lambda f: f['constraint'])
def test_restored_changed_predicate_is_rejected(restored_head, form):
    changed = re.sub(r"'([^']*)'", "'unapproved-value'", form['restored'], count=1)
    with rejected_mutation(restored_head) as connection:
        replace_check(connection, form['table'], form['constraint'], changed)


@pytest.mark.parametrize('damage', ['missing', 'weakened', 'not_valid', 'profile_not_valid',
                                  'primary_key', 'unique', 'foreign_key', 'deferred_foreign_key'])
def test_restored_structure_remains_required(restored_head, damage):
    with rejected_mutation(restored_head) as connection:
        if damage in {'missing', 'weakened', 'not_valid', 'profile_not_valid'}:
            form = next(f for f in FORMS if f['constraint'] == (
                'ck_woodchuck_profile_game_mode' if damage == 'profile_not_valid' else 'ck_classroom_transfer_status'))
            if damage == 'missing':
                connection.exec_driver_sql(f'ALTER TABLE "{form["table"]}" DROP CONSTRAINT "{form["constraint"]}"')
            else:
                predicate = '(' + form['restored'] + ') OR TRUE' if damage == 'weakened' else form['restored']
                replace_check(connection, form['table'], form['constraint'], predicate,
                              'NOT VALID' if 'not_valid' in damage else '')
        elif damage == 'primary_key':
            name = inspect(connection).get_pk_constraint(issuance.NAME)['name']
            connection.exec_driver_sql(f'ALTER TABLE {issuance.NAME} DROP CONSTRAINT "{name}"')
        elif damage == 'unique':
            connection.exec_driver_sql(f'ALTER TABLE {issuance.NAME} DROP CONSTRAINT uq_pta_receipt_control')
        else:
            name = inspect(connection).get_foreign_keys(issuance.NAME)[0]['name']
            connection.exec_driver_sql(f'ALTER TABLE {issuance.NAME} DROP CONSTRAINT "{name}"')
            clause = 'ON DELETE CASCADE' if damage == 'foreign_key' else 'ON DELETE RESTRICT DEFERRABLE'
            connection.exec_driver_sql(f'ALTER TABLE {issuance.NAME} ADD CONSTRAINT "{name}" '
                                      f'FOREIGN KEY(control_id) REFERENCES persistent_team_control(id) {clause}')


@pytest.mark.parametrize('trigger', list(issuance.PG_TRIGGERS))
@pytest.mark.parametrize('damage', ['missing', 'disabled', 'ordinary', 'replica'])
def test_restored_issuance_guards_remain_mandatory(restored_head, trigger, damage):
    with rejected_mutation(restored_head, 'pta_receipt_schema_missing_or_changed: guards') as connection:
        if damage == 'missing':
            sql = f'DROP TRIGGER {trigger} ON {issuance.NAME}'
        else:
            clause = {'disabled': 'DISABLE', 'ordinary': 'ENABLE', 'replica': 'ENABLE REPLICA'}[damage]
            sql = f'ALTER TABLE {issuance.NAME} {clause} TRIGGER {trigger}'
        connection.exec_driver_sql(sql)


@pytest.mark.parametrize('stamp', ['unknown', 'multiple', 'old_with_issuance'])
def test_restored_schema_does_not_authorize_unknown_revisions(restored_head, stamp):
    with rejected_mutation(restored_head, 'revision_not_approved') as connection:
        if stamp == 'multiple':
            connection.execute(text("INSERT INTO alembic_version VALUES ('c30class001')"))
        else:
            connection.execute(text('UPDATE alembic_version SET version_num=:revision'),
                               {'revision': 'unsupported_future' if stamp == 'unknown' else 'c30class001'})


def test_issued_receipt_survives_same_target_restore_and_refuses_other_target(
        migrated_db, tmp_path, monkeypatch, restore_target):
    db = migrated_db
    if db[1].dialect.name != 'postgresql':
        pytest.skip('Logical PostgreSQL restore regression')
    install(db, 'w25weekly005')
    command.upgrade(config(), issuance.REVISION)
    populate_weekly(db)
    populate_extensions(db)
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    frozen = json.dumps({'approval': approved, 'receipt': receipt}, sort_keys=True, indent=2)
    evidence = tmp_path / 'approval-and-receipt.json'
    evidence.write_text(frozen)
    before = all_rows(db[1])
    dump = archive(db[0], tmp_path)
    db[1].dispose()
    drop_schema(db[0])
    restore(make_url(db[0]), dump, tmp_path, 'restore-same-target')
    assert all_rows(db[1]) == before
    assert cutover.verify_cutover(db[0], approved, approved['plan_sha256'], receipt)['passed']
    other_url = scoped_target(db[0], restore_target)
    restore(restore_target, dump, tmp_path, 'restore-other-target')
    other = create_engine(other_url)
    try:
        with other.connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == issuance.REVISION
        assert all_rows(other) == before
        with pytest.raises(cutover.CutoverError, match='target_mismatch'):
            cutover.verify_cutover(other_url, approved, approved['plan_sha256'], receipt)
        assert all_rows(other) == before
    finally:
        other.dispose()
        drop_schema(other_url)
    assert all_rows(db[1]) == before
    assert evidence.read_text() == frozen == json.dumps({'approval': approved, 'receipt': receipt}, sort_keys=True, indent=2)
