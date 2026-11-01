"""New c32 finite restore aliases never broaden SQL, scope or old C5 contracts."""
import pytest

from app.operator_classroom_enrollment_contract import C32, POSTGRES_RESTORE_CHECK_FORMS
from app.operator_schema_compatibility import _check_sql, _sql


@pytest.mark.parametrize('scope,restored', list(POSTGRES_RESTORE_CHECK_FORMS.items()))
def test_only_exact_native_and_restored_forms_match(scope, restored):
    table, name = scope
    native = C32[table]['pg_checks'][name]
    assert _check_sql('postgresql', table, name, native) == _sql(native)
    assert _check_sql('postgresql', table, name, restored) == _sql(native)
    assert _check_sql('sqlite', table, name, restored) != _sql(native)
    assert _check_sql('postgresql', 'unrelated_table', name, restored) != _sql(native)
    assert _check_sql('postgresql', table, 'unrelated_constraint', restored) != _sql(native)
    variants = {
        restored + ' OR TRUE', restored + ' AND TRUE', restored + ' OR NULL',
        restored.replace("'accepted'", "'ACCEPTED'"),
        restored.replace("'accepted'", "'accepted '"),
        restored.replace('character varying::text', 'character varying(3)::text'),
        restored.replace('ANY', 'ALL'), restored.replace('ALL', 'ANY'),
        restored.replace('>=', '>'), restored.replace('profile_id', 'class_id'),
        '/* not an accepted form */ ' + restored,
    } - {restored}
    for weakened in variants:
        assert _check_sql('postgresql', table, name, weakened) != _sql(native), weakened
