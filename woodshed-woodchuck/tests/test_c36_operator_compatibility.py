"""C35 -> C36 on disposable PostgreSQL, with unchanged trusted P31 evidence.

Reuse accepted adversarial receipt assertions at the new revision. Fixtures
never use a real student, receipt, credential, legal approval or remote database.
"""
from copy import deepcopy
import json

from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text

from app import operator_schema_compatibility as compatibility
from app import persistent_team_cutover as cutover, pta_receipt_schema as issuance
from app.operator_classroom_choice_contract import C36
from app import classroom_choice_guards as guards
from tests.test_c22_operational_compatibility import all_rows, config
from tests.test_c35_pta_compatibility import (
    populated_postgres, upgrade, verify, plan, stage, activate,
    test_forged_receipt_binding_or_body_refuses_at_c35 as test_c36_forged_receipt,
    test_rehashed_plan_cannot_rebind_receipt_target as test_c36_rebound_target,
    test_changed_approval_hash_refuses as test_c36_changed_approval,
    test_legitimate_receipt_from_another_authority_scope_cannot_be_substituted as test_c36_substituted_receipt,
    test_stale_receipt_refuses_after_protected_practice_change as test_c36_changed_practice,
    test_c35_keeps_database_issuance_guards as test_c36_issuance_guards,
    test_same_identity_c35_restore_keeps_original_p31_receipt_trusted as same_identity_restore,
    test_receipt_compatibility_does_not_enable_new_staging_or_activation as no_new_writers,
)
from tests.test_operator_postgresql_restore import (
    archive, drop_schema, restore, restore_target, scoped_target,
)
from tests.test_c35_restore_compatibility import rejected_mutation


def sequences(engine):
    with engine.connect() as connection:
        quote = connection.dialect.identifier_preparer.quote
        names = inspect(connection).get_sequence_names()
        return {name: tuple(connection.exec_driver_sql(
            'SELECT last_value, is_called FROM ' + quote(name)).one()) for name in names}


@pytest.fixture(scope='module')
def issued_c35(tmp_path_factory):
    # Fixture name retained solely for reuse of accepted receipt assertions.
    evidence = tmp_path_factory.mktemp('c36-operator')
    db = populated_postgres(evidence)
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    upgrade(db[0], 'c35school001')
    assert verify(db, approved, receipt)['passed']
    before, counters = all_rows(db[1]), sequences(db[1])
    frozen = deepcopy((approved, receipt))
    upgrade(db[0], 'c36choice001')
    after = all_rows(db[1])
    assert set(after) - set(before) == set(C36)
    assert all(after[name] == rows for name, rows in before.items())
    assert all(after[name] == [] for name in C36)
    after_counters = sequences(db[1])
    assert {name: after_counters[name] for name in counters} == counters
    assert set(after_counters) - set(counters) == {'classroom_choice_assignments_id_seq'}
    assert verify(db, approved, receipt)['passed']
    (evidence / 'preservation.json').write_text(json.dumps({
        'from': 'c35school001', 'to': 'c36choice001',
        'old_tables': len(before), 'old_records': sum(map(len, before.values())),
        'old_sequences': len(counters), 'all_old_rows_and_sequences_equal': True,
        'new_tables_empty': True, 'receipt_and_plan_unchanged': (approved, receipt) == frozen,
    }, indent=2) + '\n')
    try:
        yield db, approved, receipt, (frozen[0], frozen[1], before), ['c35school001', 'c36choice001']
        assert (approved, receipt) == frozen
        assert all_rows(db[1]) == after
        assert sequences(db[1]) == after_counters
    finally:
        db[1].dispose()
        drop_schema(db[0])


def test_c36_exact_lineage_and_verify_only_scope():
    scripts = ScriptDirectory.from_config(config())
    assert scripts.get_heads() == ['c36choice001']
    revision = scripts.get_revision('c36choice001')
    assert revision.down_revision == 'c35school001'
    assert not revision.dependencies and not revision.branch_labels
    assert cutover._REVIEWED_REVISIONS[-1] == 'c33identity001'
    assert cutover._REVIEWED_RECEIPT_REVISIONS[-3:] == ('c34guardian001', 'c35school001', 'c36choice001')
    assert cutover._REVIEWED_ISSUANCE_REVISIONS == (
        'p31pta001', 'c32enroll001', 'c33identity001', 'c34guardian001', 'c35school001', 'c36choice001')
    for original, current in [('c36choice001', 'c35school001'), ('p31pta001', 'c37unreviewed001')]:
        with pytest.raises(cutover.CutoverError, match='plan_schema_revision_changed'):
            cutover._plan_schema_guard({'revision': original}, current, verifying_receipt=True)


def test_c36_does_not_enable_stage_or_activate(tmp_path):
    no_new_writers(tmp_path, 'c36choice001')


def test_c36_same_identity_restore(tmp_path):
    # A separate fixture keeps the pair below genuinely native vs restored.
    db = populated_postgres(tmp_path)
    try:
        approved = plan(db)
        stage(db, approved)
        receipt = activate(db, approved)
        upgrade(db[0], 'c36choice001')
        same_identity_restore((db, approved, receipt, None, None), tmp_path)
    finally:
        db[1].dispose()
        drop_schema(db[0])


def test_c36_does_not_authenticate_or_backfill_historical_unissued_receipt(tmp_path):
    from tests.pta_legacy_receipts import activate_legacy_fixture
    db = populated_postgres(tmp_path, 'c30class001')
    try:
        approved = plan(db)
        stage(db, approved)
        historical = activate_legacy_fixture(db, approved)
        frozen = deepcopy((approved, historical))
        upgrade(db[0], 'c36choice001')
        before = all_rows(db[1])
        assert before[issuance.NAME] == []
        with pytest.raises(cutover.CutoverError, match='trusted_receipt_issuance_required'):
            verify(db, approved, historical)
        assert (approved, historical) == frozen and all_rows(db[1]) == before
    finally:
        db[1].dispose()
        drop_schema(db[0])


@pytest.fixture(scope='module')
def c36_pair(issued_c35, tmp_path_factory, restore_target):
    db, approved, receipt, _, _ = issued_c35
    evidence = tmp_path_factory.mktemp('c36-restore')
    url = scoped_target(db[0], restore_target)
    restored = create_engine(url)
    before = all_rows(db[1])
    counters = sequences(db[1])
    try:
        restore(restore_target, archive(db[0], evidence), evidence)
        assert all_rows(restored) == before and sequences(restored) == counters
        with pytest.raises(cutover.CutoverError, match='target_mismatch'):
            verify((url, restored), approved, receipt)
        yield {'native': db[1], 'restored': restored, 'evidence': evidence}
        assert all_rows(restored) == before and sequences(restored) == counters
    finally:
        restored.dispose()
        drop_schema(url)


@pytest.fixture(params=['native', 'restored'])
def c36_database(c36_pair, request):
    return c36_pair[request.param]


def test_c36_original_and_restored_schema_and_accepted_c35_forms(c36_database):
    from app.operator_classroom_c35_restore_forms import POSTGRES_RESTORE_CHECK_FORMS
    with c36_database.connect() as connection:
        assert compatibility.installed_revision(connection, RuntimeError) == 'c36choice001'
        for (table, constraint), (native, restored) in POSTGRES_RESTORE_CHECK_FORMS.items():
            found = {row['name']: row['sqltext'] for row in inspect(connection).get_check_constraints(table)}
            assert compatibility._check_sql('postgresql', table, constraint, found[constraint]) == compatibility._sql(native)


@pytest.mark.parametrize('damage', ['check', 'check_not_valid', 'check_weakened', 'check_literal', 'scope_fk', 'index', 'primary_key'])
def test_c36_structure_remains_fail_closed(c36_database, damage):
    with rejected_mutation(c36_database) as connection:
        if damage.startswith('check'):
            connection.exec_driver_sql('ALTER TABLE classroom_choice_versions DROP CONSTRAINT ck_classroom_choice_state')
            if damage == 'check_not_valid':
                connection.exec_driver_sql("ALTER TABLE classroom_choice_versions ADD CONSTRAINT ck_classroom_choice_state CHECK (state IN ('active', 'cancelled')) NOT VALID")
            elif damage in {'check_weakened', 'check_literal'}:
                predicate = "state IN ('active', 'cancelled') OR TRUE" if damage == 'check_weakened' else "state IN ('active', 'cancelled', 'unapproved')"
                connection.exec_driver_sql('ALTER TABLE classroom_choice_versions ADD CONSTRAINT ck_classroom_choice_state CHECK (' + predicate + ')')
        elif damage == 'scope_fk':
            name = inspect(connection).get_foreign_keys('classroom_choice_versions')[0]['name']
            connection.exec_driver_sql(f'ALTER TABLE classroom_choice_versions DROP CONSTRAINT "{name}"')
        elif damage == 'index':
            connection.exec_driver_sql('DROP INDEX ix_classroom_choice_week')
        else:
            name = inspect(connection).get_pk_constraint('classroom_choice_versions')['name']
            connection.exec_driver_sql(f'ALTER TABLE classroom_choice_versions DROP CONSTRAINT "{name}"')


def test_c36_observed_restore_form_is_exact_and_scoped(c36_pair):
    from app.operator_classroom_choice_contract import POSTGRES_RESTORE_CHECK_FORMS
    table, name = 'classroom_choice_versions', 'ck_classroom_choice_state'
    native = C36[table]['pg_checks'][name]
    restored = POSTGRES_RESTORE_CHECK_FORMS[(table, name)]
    assert len(POSTGRES_RESTORE_CHECK_FORMS) == 1
    assert compatibility._sql(native) != compatibility._sql(restored)
    for kind, expected in [('native', native), ('restored', restored)]:
        actual = {c['name']: c['sqltext'] for c in inspect(c36_pair[kind]).get_check_constraints(table)}[name]
        assert compatibility._sql(actual) == compatibility._sql(expected)
        assert compatibility._check_sql('postgresql', table, name, actual) == compatibility._sql(native)
    for dialect, other_table, other_name in [('sqlite', table, name), ('postgresql', 'wrong_table', name),
                                            ('postgresql', table, 'wrong_constraint')]:
        assert compatibility._check_sql(dialect, other_table, other_name, restored) == compatibility._sql(restored)
    for changed in [restored.replace('active', 'ACTIVE'), restored.replace('= ANY', '<> ANY'),
                    restored.replace('::character varying', '::character varying(1)', 1),
                    restored + ' OR TRUE', restored + ' AND TRUE']:
        assert compatibility._check_sql('postgresql', table, name, changed) != compatibility._sql(native)


@pytest.mark.parametrize('trigger', list(guards.PG_TRIGGERS))
@pytest.mark.parametrize('damage', ['missing', 'disabled', 'ordinary', 'replica', 'body', 'search_path'])
def test_c36_assignment_guards_remain_required(c36_database, trigger, damage):
    with rejected_mutation(c36_database, 'classroom_choice_schema_missing_or_changed: guards') as connection:
        if damage == 'missing':
            connection.exec_driver_sql(f'DROP TRIGGER {trigger} ON classroom_choice_versions')
        elif damage == 'body':
            connection.exec_driver_sql('CREATE OR REPLACE FUNCTION enforce_classroom_choice_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$')
        elif damage == 'search_path':
            connection.exec_driver_sql('ALTER FUNCTION enforce_classroom_choice_immutable() SET search_path = public')
        else:
            clause = {'disabled': 'DISABLE', 'ordinary': 'ENABLE', 'replica': 'ENABLE REPLICA'}[damage]
            connection.exec_driver_sql(f'ALTER TABLE classroom_choice_versions {clause} TRIGGER {trigger}')


@pytest.mark.parametrize('trigger', list(issuance.PG_TRIGGERS))
@pytest.mark.parametrize('damage', ['missing', 'disabled', 'ordinary', 'replica'])
def test_c36_restored_issuance_guards_remain_required(c36_pair, trigger, damage):
    from tests.test_c35_restore_compatibility import test_restored_pta_guard_enforcement_is_not_relaxed
    test_restored_pta_guard_enforcement_is_not_relaxed(c36_pair, trigger, damage)


def test_c36_upgrade_failure_rolls_back_all_ddl_and_data(tmp_path, monkeypatch):
    from app import classroom_choice_guards
    db = populated_postgres(tmp_path, 'c35school001')
    before, counters = all_rows(db[1]), sequences(db[1])
    original = classroom_choice_guards.install
    def fail_after_guards(connection):
        original(connection)
        raise RuntimeError('synthetic_failure_after_c36_ddl')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(classroom_choice_guards, 'install', fail_after_guards)
            with pytest.raises(RuntimeError, match='synthetic_failure_after_c36_ddl'):
                upgrade(db[0], 'c36choice001')
        assert all_rows(db[1]) == before and sequences(db[1]) == counters
        with db[1].connect() as connection:
            assert compatibility.installed_revision(connection, RuntimeError) == 'c35school001'
            assert connection.scalar(text("SELECT to_regprocedure('enforce_classroom_choice_immutable()')")) is None
    finally:
        db[1].dispose()
        drop_schema(db[0])
