"""Finite PostgreSQL reflection pairs, independently captured from the OPS audit."""
import json
from pathlib import Path
import re

import pytest

from app import operator_schema_compatibility as compatibility
from app.operator_classroom_s2_contract import C23
from app.operator_classroom_s3_contract import C24
from app.operator_classroom_progress_contract import C29
from app.operator_weekly_contract import W25


INVENTORY = json.loads((Path(__file__).parent / 'fixtures' /
                        'postgresql_check_restore_forms.json').read_text())
FORMS = INVENTORY['forms']
IDS = [form['constraint'] for form in FORMS]


def test_exact_audited_inventory_and_frozen_contract_anchors():
    pairs = compatibility._POSTGRES_CHECK_RESTORE_FORMS
    assert set(pairs) == {(f['table'], f['constraint']) for f in FORMS}
    assert len(pairs) == 24
    assert sum(table.startswith('classroom_') for table, _ in pairs) == 20
    assert sum(table.startswith('weekly_') for table, _ in pairs) == 3
    assert ('woodchuck_profiles', 'ck_woodchuck_profile_game_mode') in pairs
    contracts = {**compatibility._C22, **compatibility._C25, **C23, **C24, **W25, **C29}
    for form in FORMS:
        native, restored = pairs[form['table'], form['constraint']]
        assert native == form['native'] and restored == form['restored']
        assert compatibility._sql(native) == compatibility._sql(form['expected'])
        assert compatibility._sql(native) != compatibility._sql(restored)
        if form['table'] != 'woodchuck_profiles':
            assert form['expected'] == contracts[form['table']]['pg_checks'][form['constraint']]


@pytest.mark.parametrize('form', FORMS, ids=IDS)
@pytest.mark.parametrize('representation', ['native', 'restored'])
def test_only_audited_pair_normalizes_to_the_contract(form, representation):
    assert compatibility._check_sql('postgresql', form['table'], form['constraint'],
                                    form[representation]) == compatibility._sql(form['expected'])


@pytest.mark.parametrize('form', FORMS, ids=IDS)
@pytest.mark.parametrize('change', [
    'literal', 'literal_case', 'operator', 'cast', 'grouping', 'weakened', 'unknown', 'missing',
])
def test_changed_and_unknown_representations_are_not_accepted(form, change):
    value = form['restored']
    mutations = {
        'literal': lambda sql: re.sub(r"'([^']*)'", "'unapproved-value'", sql, count=1),
        'literal_case': lambda sql: re.sub(r"'([^']*)'", lambda m: "'" + m[1].swapcase() + "'", sql, count=1),
        'operator': lambda sql: re.sub(r'= ANY|<> ALL',
                                     lambda m: '<> ANY' if m[0] == '= ANY' else '= ALL', sql, count=1),
        'cast': lambda sql: sql.replace('::character varying', '::character varying(1)', 1),
        'grouping': lambda sql: '(' + sql + ')',
        'weakened': lambda sql: '(' + sql + ') OR TRUE',
        'unknown': lambda sql: '(' + sql + ') AND TRUE',
        'missing': lambda sql: '',
    }
    changed = mutations[change](value)
    assert changed != value
    assert compatibility._check_sql('postgresql', form['table'], form['constraint'],
                                    changed) != compatibility._sql(form['expected'])


@pytest.mark.parametrize('form', FORMS, ids=IDS)
def test_alias_is_postgresql_and_named_constraint_only(form):
    raw = compatibility._sql(form['restored'])
    for dialect, table, constraint in [
        ('sqlite', form['table'], form['constraint']),
        ('unknown', form['table'], form['constraint']),
        ('postgresql', 'other_table', form['constraint']),
        ('postgresql', form['table'], 'other_constraint'),
    ]:
        assert compatibility._check_sql(dialect, table, constraint, form['restored']) == raw


@pytest.mark.parametrize('form', [f for f in FORMS if ' IS NULL' in f['restored']], ids=lambda f: f['constraint'])
def test_null_branches_and_boolean_operators_remain_significant(form):
    value = form['restored'].replace(' IS NULL', ' IS NOT NULL', 1)
    assert compatibility._check_sql('postgresql', form['table'], form['constraint'], value) != compatibility._sql(form['expected'])
    value = form['restored'].replace(' OR ', ' AND ', 1)
    assert value != form['restored']
    assert compatibility._check_sql('postgresql', form['table'], form['constraint'], value) != compatibility._sql(form['expected'])
