"""Receipt-only C35 compatibility on explicit disposable PostgreSQL schemas.

No gates, legal allowlists, receipt authenticators, or schema guards are patched.
The legacy helper represents unauthenticated historical evidence, never issuance.
"""
from copy import deepcopy
import os

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app import persistent_team_cutover as cutover, pta_receipt_schema as issuance
from tests.test_c22_operational_compatibility import all_rows, canonical_seed_names, config
from tests.test_persistent_team_cutover import BOUNDARY_AT, activate, plan, seed, stage
from tests.test_team_families import disposable_url
from tests.pta_legacy_receipts import activate_legacy_fixture


LINEAGE = ('p31pta001', 'c32enroll001', 'c33identity001', 'c34guardian001', 'c35school001')


def upgrade(url, revision):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('DATABASE_URL', url)
        command.upgrade(config(), revision)


def populated_postgres(tmp_path, revision='p31pta001'):
    # Fail rather than silently skipping this release qualification without PG.
    assert os.getenv('WW_BILLING_TEST_POSTGRES_URL'), 'Explicit disposable PostgreSQL URL required'
    url = disposable_url(tmp_path, 'postgresql')
    upgrade(url, revision)
    engine = seed(url, migrated=True)
    canonical_seed_names(engine)
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == revision
        assert not connection.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user'))
    return url, engine


def verify(db, approved, receipt):
    return cutover.verify_cutover(db[0], approved, approved['plan_sha256'], receipt)


@pytest.fixture(scope='module')
def issued_c35(tmp_path_factory):
    db = populated_postgres(tmp_path_factory.mktemp('c35-receipt'))
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    assert verify(db, approved, receipt)['passed']
    original = deepcopy((approved, receipt, all_rows(db[1])))
    verified = ['p31pta001']
    for revision in LINEAGE[1:]:
        upgrade(db[0], revision)
        assert verify(db, approved, receipt)['passed']
        verified.append(revision)
    try:
        yield db, approved, receipt, original, verified
    finally:
        assert (approved, receipt) == original[:2]
        assert all_rows(db[1])[issuance.NAME] == original[2][issuance.NAME]
        db[1].dispose()


def test_receipt_lineage_matches_actual_single_alembic_chain():
    scripts = ScriptDirectory.from_config(config())
    assert scripts.get_heads() == ['c35school001']
    for parent, successor in zip(LINEAGE, LINEAGE[1:]):
        revision = scripts.get_revision(successor)
        assert revision.down_revision == parent
        assert not revision.dependencies and not revision.branch_labels
    assert cutover._REVIEWED_ISSUANCE_REVISIONS == LINEAGE
    assert tuple(cutover._REVIEWED_RECEIPT_REVISIONS[-5:]) == LINEAGE


@pytest.mark.parametrize('original,current', [
    ('p31pta001', 'unknown'), ('unknown', 'c35school001'),
    ('p31pta001', 'c36unreviewed001'), ('c35school001', 'p31pta001'),
    ('c35school001', 'c34guardian001'), ('c34guardian001', 'c33identity001'),
])
def test_unreviewed_and_reverse_receipt_lineage_refuses(original, current):
    with pytest.raises(cutover.CutoverError, match='plan_schema_revision_changed'):
        cutover._plan_schema_guard({'revision': original}, current, verifying_receipt=True)


def test_same_original_p31_receipt_verifies_at_every_successor_without_rewriting(issued_c35):
    db, approved, receipt, original, verified = issued_c35
    assert verified == list(LINEAGE)
    assert (approved, receipt) == original[:2]
    after = all_rows(db[1])
    for name, rows in original[2].items():
        assert after[name] == rows
    assert receipt['issuance']['activation_revision'] == 'p31pta001'
    assert after[issuance.NAME][0]['receipt_sha256'] == cutover.digest(receipt)
    result = verify(db, approved, receipt)
    assert result['passed'] and result['receipt_coverage']['excluded_added_columns'] == []
    assert all_rows(db[1]) == after


@pytest.mark.parametrize('field,value', [
    ('receipt_id', '0' * 32), ('control_id', 2), ('target_sha256', '0' * 64),
    ('plan_sha256', '0' * 64), ('authority_sha256', '0' * 64),
    ('activation_revision', 'c35school001'), ('evidence_contract', 'unapproved-contract'),
    ('effective_at', '2026-10-12T05:00:00+00:00'), ('issued_at', '2026-10-12T05:00:00+00:00'),
    ('missing_issuance', None), ('changed_body', None), ('changed_contract', None),
])
def test_forged_receipt_binding_or_body_refuses_at_c35(issued_c35, field, value):
    db, approved, receipt, _, _ = issued_c35
    forged = deepcopy(receipt)
    if field == 'missing_issuance':
        del forged['issuance']
    elif field == 'changed_body':
        forged['verification']['after']['practice_charts']['sha256'] = '0' * 64
    elif field == 'changed_contract':
        forged['verification']['evidence_contract'] = 'unapproved-contract'
    else:
        forged['issuance'][field] = value
    with pytest.raises(cutover.CutoverError, match='activation_receipt_issuance_mismatch'):
        verify(db, approved, forged)
    assert verify(db, approved, receipt)['passed']


@pytest.mark.parametrize('field,value', [('host', 'wrong-host.invalid'), ('port', 1),
                                         ('database', 'wrong_database'), ('schema', 'public')])
def test_rehashed_plan_cannot_rebind_receipt_target(issued_c35, field, value):
    db, approved, receipt, _, _ = issued_c35
    forged_plan, forged_receipt = deepcopy((approved, receipt))
    assert forged_plan['content']['target'][field] != value
    forged_plan['content']['target'][field] = value
    forged_plan['plan_sha256'] = cutover.digest(forged_plan['content'])
    forged_receipt['verification']['approved_plan_sha256'] = forged_plan['plan_sha256']
    with pytest.raises(cutover.CutoverError, match='target_mismatch'):
        verify(db, forged_plan, forged_receipt)
    assert verify(db, approved, receipt)['passed']


def test_changed_approval_hash_refuses(issued_c35):
    db, approved, receipt, _, _ = issued_c35
    with pytest.raises(cutover.CutoverError, match='approved_plan_hash_mismatch'):
        cutover.verify_cutover(db[0], approved, '0' * 64, receipt)


def test_legitimate_receipt_from_another_authority_scope_cannot_be_substituted(issued_c35, tmp_path):
    db, approved, receipt, _, _ = issued_c35
    other = populated_postgres(tmp_path)
    try:
        other_plan = plan(other)
        stage(other, other_plan)
        other_receipt = activate(other, other_plan)
        upgrade(other[0], 'c35school001')
        assert verify(other, other_plan, other_receipt)['passed']
        assert other_receipt['issuance']['target_sha256'] != receipt['issuance']['target_sha256']
        with pytest.raises(cutover.CutoverError, match='target_mismatch'):
            verify(db, other_plan, other_receipt)
        with pytest.raises(cutover.CutoverError, match='approved_activation_receipt_required'):
            verify(db, approved, other_receipt)
        substituted = deepcopy(other_receipt)
        substituted['verification']['approved_plan_sha256'] = approved['plan_sha256']
        with pytest.raises(cutover.CutoverError, match='activation_receipt_issuance_mismatch'):
            verify(db, approved, substituted)
    finally:
        other[1].dispose()


def test_stale_receipt_refuses_after_protected_practice_change(issued_c35):
    db, approved, receipt, _, _ = issued_c35
    with db[1].connect() as connection:
        original_note = connection.scalar(text('SELECT note FROM practice_charts WHERE id=1'))
    with db[1].begin() as connection:
        connection.execute(text("UPDATE practice_charts SET note='new synthetic activity' WHERE id=1"))
    try:
        with pytest.raises(cutover.CutoverError, match='post_activation_state_changed'):
            verify(db, approved, receipt)
    finally:
        with db[1].begin() as connection:
            connection.execute(text('UPDATE practice_charts SET note=:note WHERE id=1'), {'note': original_note})
    assert verify(db, approved, receipt)['passed']


@pytest.mark.parametrize('sql', [
    'UPDATE persistent_team_receipt_issuances SET receipt_sha256=receipt_sha256',
    'DELETE FROM persistent_team_receipt_issuances',
    'TRUNCATE persistent_team_receipt_issuances',
    'DELETE FROM persistent_team_control',
    'TRUNCATE persistent_team_control CASCADE',
    'INSERT INTO persistent_team_receipt_issuances SELECT * FROM persistent_team_receipt_issuances',
])
def test_c35_keeps_database_issuance_guards(issued_c35, sql):
    db, approved, receipt, _, _ = issued_c35
    before = all_rows(db[1])
    with pytest.raises(IntegrityError):
        with db[1].begin() as connection:
            connection.execute(text(sql))
    assert all_rows(db[1]) == before
    assert verify(db, approved, receipt)['passed']


@pytest.mark.parametrize('revision', ['c34guardian001', 'c35school001'])
def test_receipt_compatibility_does_not_enable_new_staging_or_activation(tmp_path, revision):
    db = populated_postgres(tmp_path)
    try:
        approved = plan(db)
        stage(db, approved)
        upgrade(db[0], revision)
        before = all_rows(db[1])
        with pytest.raises(cutover.CutoverError, match='plan_schema_revision_changed'):
            activate(db, approved)
        with pytest.raises(cutover.CutoverError, match='plan_schema_revision_changed'):
            stage(db, plan(db))
        assert all_rows(db[1]) == before and before[issuance.NAME] == []
    finally:
        db[1].dispose()


def test_historical_unissued_activation_is_not_authenticated_or_backfilled(tmp_path):
    db = populated_postgres(tmp_path, 'c30class001')
    try:
        approved = plan(db)
        stage(db, approved)
        historical = activate_legacy_fixture(db, approved)
        frozen = deepcopy((approved, historical))
        upgrade(db[0], 'c35school001')
        before = all_rows(db[1])
        assert before[issuance.NAME] == []
        with pytest.raises(cutover.CutoverError, match='trusted_receipt_issuance_required'):
            verify(db, approved, historical)
        values = dict(receipt_id='a' * 32, control_id=1,
            target_sha256=cutover.digest(approved['content']['target']),
            plan_sha256=approved['plan_sha256'], authority_sha256=approved['content']['authority_sha256'],
            activation_revision=issuance.REVISION, evidence_contract=issuance.CONTRACT,
            effective_at=BOUNDARY_AT, issued_at=BOUNDARY_AT, receipt_sha256=cutover.digest(historical))
        with pytest.raises(IntegrityError, match='pta_receipt_revision_mismatch'):
            with db[1].begin() as connection:
                table = Table(issuance.NAME, MetaData(), autoload_with=connection, resolve_fks=False)
                connection.execute(table.insert().values(**values))
        assert all_rows(db[1]) == before and (approved, historical) == frozen
    finally:
        db[1].dispose()


def test_same_identity_c35_restore_keeps_original_p31_receipt_trusted(issued_c35, tmp_path):
    # A different database/schema must reject the bound receipt (tested above
    # and in the independent restore qualification). Here only the disposable
    # fixture schema is recreated, keeping the exact original target identity.
    from tests.test_operator_postgresql_restore import archive, drop_schema, restore
    db, approved, receipt, _, _ = issued_c35
    before = all_rows(db[1])
    original = deepcopy((approved, receipt))
    assert verify(db, approved, receipt)['passed']
    dump = archive(db[0], tmp_path)
    db[1].dispose()
    drop_schema(db[0])
    restore(make_url(db[0]), dump, tmp_path, label='same-identity-restore')
    assert all_rows(db[1]) == before
    assert (approved, receipt) == original
    assert verify(db, approved, receipt)['passed']
