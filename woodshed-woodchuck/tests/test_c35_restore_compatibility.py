"""C35 native/independent-restore qualification on explicitly disposable PostgreSQL.

No SQLite fallback, legal overrides, or Classroom gate activation. Every negative
DDL mutation is rolled back before the next assertion. Existing C5 restore-form
inventory is deliberately separate and unchanged.
"""
from contextlib import contextmanager
import json
import re

from alembic import command
import pytest
from sqlalchemy import create_engine, inspect, text

from app import operator_schema_compatibility as compatibility
from app import pta_receipt_schema as issuance
from app.operator_classroom_c35_restore_forms import POSTGRES_RESTORE_CHECK_FORMS as FORMS
from app.operator_classroom_guardian_contract import C34
from app.operator_classroom_school_contract import C35
from tests.test_c22_operational_compatibility import all_rows, config
from tests.test_operator_postgresql_restore import (
    archive, drop_schema, restore, restore_target, scoped_target,
)
from tests.test_team_families import disposable_url


@pytest.fixture(scope='module')
def c35_pair(tmp_path_factory, restore_target):
    evidence = tmp_path_factory.mktemp('c35-restore')
    source_url = disposable_url(evidence, 'postgresql')
    target_url = scoped_target(source_url, restore_target)
    source = create_engine(source_url)
    restored = create_engine(target_url)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('DATABASE_URL', source_url)
            command.upgrade(config(), 'c35school001')
        with source.connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == 'c35school001'
        before = all_rows(source)
        dump = archive(source_url, evidence)
        restore(restore_target, dump, evidence)
        assert all_rows(restored) == before
        (evidence / 'data-preservation.json').write_text(json.dumps({
            'all_rows_equal': True, 'tables': len(before),
            'source_and_restore_are_independent_databases': True,
            'source': source_url, 'restore': target_url,
        }, indent=2) + '\n')
        yield {'native': source, 'restored': restored, 'evidence': evidence}
        for engine in (source, restored):
            with engine.connect() as connection:
                assert compatibility.installed_revision(connection, RuntimeError) == 'c35school001'
            assert all_rows(engine) == before
    finally:
        source.dispose()
        restored.dispose()
        drop_schema(target_url)
        drop_schema(source_url)


@pytest.fixture(params=['native', 'restored'])
def c35_database(c35_pair, request):
    return c35_pair[request.param]


def test_original_and_restored_c35_validate(c35_database):
    with c35_database.connect() as connection:
        assert compatibility.installed_revision(connection, RuntimeError) == 'c35school001'


@pytest.mark.parametrize('key', list(FORMS), ids=lambda key: key[1])
def test_exact_native_and_restored_forms_are_scoped_to_six_contracts(c35_pair, key):
    table, name = key
    native, restored = FORMS[key]
    assert len(FORMS) == 6
    assert native == {**C34, **C35}[table]['pg_checks'][name]
    found = {}
    for kind, expected in (('native', native), ('restored', restored)):
        checks = {c['name']: c['sqltext'] for c in inspect(c35_pair[kind]).get_check_constraints(table)}
        found[kind] = checks[name]
        assert compatibility._sql(checks[name]) == compatibility._sql(expected)
        assert compatibility._check_sql('postgresql', table, name, checks[name]) == compatibility._sql(native)
    assert compatibility._sql(native) != compatibility._sql(restored)
    for dialect, other_table, other_name in (
        ('postgresql', 'wrong_table', name),
        ('postgresql', table, 'wrong_constraint'),
        ('sqlite', table, name),
    ):
        assert compatibility._check_sql(dialect, other_table, other_name, restored) == compatibility._sql(restored)
    (c35_pair['evidence'] / (name + '.json')).write_text(json.dumps(found, indent=2) + '\n')


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


@pytest.mark.parametrize('key', list(FORMS), ids=lambda key: key[1])
@pytest.mark.parametrize('damage', ['missing', 'modified_literal', 'weakened', 'renamed', 'not_valid'])
def test_required_checks_fail_closed_on_native_and_restored(c35_database, key, damage):
    table, name = key
    expression = FORMS[key][1]
    with rejected_mutation(c35_database) as connection:
        connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"')
        if damage == 'missing':
            return
        if damage == 'modified_literal':
            expression = re.sub(r"'([^']*)'", "'unapproved-value'", expression, count=1)
        elif damage == 'weakened':
            expression = '(' + expression + ') OR TRUE'
        new_name = name + '_wrong_scope' if damage == 'renamed' else name
        suffix = ' NOT VALID' if damage == 'not_valid' else ''
        connection.exec_driver_sql(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{new_name}" CHECK ({expression}){suffix}')


@pytest.mark.parametrize('key', list(FORMS), ids=lambda key: key[1])
def test_unrecognized_cast_operator_and_boolean_forms_are_not_normalized(c35_pair, key):
    table, name = key
    native, restored = FORMS[key]
    changes = [
        restored.replace('::character varying', '::character varying(1)', 1),
        restored.replace('= ANY', '<> ANY', 1),
        re.sub(r"'([^']*)'", lambda m: "'" + m[1].upper() + "'", restored, count=1),
        '(' + restored + ') AND TRUE',
        '(' + restored + ') OR TRUE',
    ]
    if ' IS NULL' in restored:
        changes.extend([restored.replace(' IS NULL', ' IS NOT NULL', 1),
                        restored.replace(' OR ', ' AND ', 1)])
    for changed in changes:
        assert changed != restored
        assert compatibility._check_sql('postgresql', table, name, changed) != compatibility._sql(native)


@pytest.mark.parametrize('damage', [
    'guardian_index', 'school_index', 'school_foreign_key', 'chart_guardian_foreign_key',
    'pta_foreign_key', 'pta_unique', 'pta_revision_check',
])
def test_required_structure_remains_enforced(c35_database, damage):
    with rejected_mutation(c35_database) as connection:
        if damage in {'guardian_index', 'school_index'}:
            name = 'uq_classroom_guardian_open' if damage == 'guardian_index' else 'uq_classroom_school_open_year'
            connection.exec_driver_sql(f'DROP INDEX "{name}"')
        elif damage == 'pta_unique':
            connection.exec_driver_sql(f'ALTER TABLE {issuance.NAME} DROP CONSTRAINT uq_pta_receipt_digest')
        elif damage == 'pta_revision_check':
            connection.exec_driver_sql(f'ALTER TABLE {issuance.NAME} DROP CONSTRAINT ck_pta_receipt_revision')
        else:
            table = {'school_foreign_key': 'classroom_school_authorizations',
                     'chart_guardian_foreign_key': 'classroom_chart_director_shares',
                     'pta_foreign_key': issuance.NAME}[damage]
            keys = inspect(connection).get_foreign_keys(table)
            key = (next(k for k in keys if k['constrained_columns'] == ['guardian_permission_id'])
                   if damage == 'chart_guardian_foreign_key' else keys[0])
            connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT "{key["name"]}"')


@pytest.mark.parametrize('trigger', list(issuance.PG_TRIGGERS))
@pytest.mark.parametrize('damage', ['missing', 'disabled', 'ordinary', 'replica'])
def test_restored_pta_guard_enforcement_is_not_relaxed(c35_pair, trigger, damage):
    with rejected_mutation(c35_pair['restored'], 'pta_receipt_schema_missing_or_changed: guards') as connection:
        if damage == 'missing':
            sql = f'DROP TRIGGER {trigger} ON {issuance.NAME}'
        else:
            clause = {'disabled': 'DISABLE', 'ordinary': 'ENABLE', 'replica': 'ENABLE REPLICA'}[damage]
            sql = f'ALTER TABLE {issuance.NAME} {clause} TRIGGER {trigger}'
        connection.exec_driver_sql(sql)
