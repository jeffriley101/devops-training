"""C33 normalization stays exact and cannot broaden older audited SQL forms."""
import pytest

from app.operator_classroom_identity_contract import C33
from app.operator_schema_compatibility import _check_sql, _sql


@pytest.mark.parametrize('table,constraint', [(table, name) for table, contract in C33.items() for name in contract['checks']])
def test_native_identity_checks_are_exact_and_scope_specific(table, constraint):
    contract = C33[table]
    native = contract['pg_checks'][constraint]
    assert _check_sql('postgresql', table, constraint, native) == _sql(native)
    for changed in [native + ' OR TRUE', native + ' AND TRUE', native + ' OR NULL',
                    native.replace('>=', '>'), native.replace('150', '1500'),
                    native.replace("'created'", "'CREATED'"), native.replace('program_id', 'profile_id')]:
        if changed != native:
            assert _check_sql('postgresql', table, constraint, changed) != _sql(native)


def test_identity_audit_contract_has_no_personal_name_or_email_fields():
    columns = C33['classroom_roster_identity_audits']['columns']
    assert set(columns) == {'id', 'identity_id', 'program_id', 'profile_id', 'class_id',
        'actor_verifier_id', 'version', 'action', 'occurred_at'}
    assert C33['classroom_roster_identities']['columns']['school_name'] == ('String', 150, False)
