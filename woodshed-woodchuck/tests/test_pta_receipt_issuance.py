"""Database-backed PTA provenance on actual disposable migrations only."""
from copy import deepcopy

from alembic import command
import pytest
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.exc import IntegrityError

from app import pta_receipt_schema as issuance
from tests.test_c22_operational_compatibility import all_rows, calendar, config, cutover, set_clock
from tests.test_c30_operational_compatibility import populate_extensions
from tests.test_persistent_team_cutover import BOUNDARY_AT, NOW, activate, plan, seed, stage
from tests.test_persistent_team_migration import disposable_sqlite_configuration
from tests.test_team_families import disposable_url
from tests.test_weekly2_operator_compatibility import install, migrated_db, populate_weekly
from tests.pta_legacy_receipts import activate_legacy_fixture


def verify(db, approved, receipt):
    return cutover.verify_cutover(db[0], approved, approved['plan_sha256'], receipt)


def issued(db):
    return all_rows(db[1])[issuance.NAME]


def test_staged_c24_receipt_has_authoritative_full_coverage(migrated_db):
    db = migrated_db
    install(db, 'c24class001')
    approved = plan(db)
    frozen = deepcopy(approved)
    stage(db, approved)
    command.upgrade(config(), 'c30class001')
    before = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match='receipt_issuance_schema_required'):
        activate(db, approved)
    assert all_rows(db[1]) == before
    command.upgrade(config(), issuance.REVISION)
    receipt = activate(db, approved)
    rows = issued(db)
    assert len(rows) == 1
    assert rows[0]['receipt_sha256'] == cutover.digest(receipt)
    assert rows[0]['target_sha256'] == cutover.digest(approved['content']['target'])
    assert rows[0]['authority_sha256'] == approved['content']['authority_sha256']
    assert rows[0]['plan_sha256'] == frozen['plan_sha256']
    assert rows[0]['effective_at'] == frozen['content']['effective_at']
    assert rows[0]['activation_revision'] == issuance.REVISION
    assert rows[0]['evidence_contract'] == issuance.CONTRACT
    before = all_rows(db[1])
    result = verify(db, approved, receipt)
    assert result['passed'] and result['receipt_coverage']['excluded_added_columns'] == []
    assert result['receipt_coverage']['contract'] == issuance.CONTRACT
    assert all_rows(db[1]) == before and approved == frozen
    with pytest.raises(cutover.CutoverError, match='receipt_issuance_already_present'):
        activate(db, approved)
    assert all_rows(db[1]) == before


def test_p1_and_marker_digest_identity_substitutions_refuse(migrated_db):
    db = migrated_db
    install(db, 'c24class001')
    approved = plan(db)
    stage(db, approved)
    command.upgrade(config(), issuance.REVISION)
    receipt = activate(db, approved)
    assert verify(db, approved, receipt)['passed']
    original = deepcopy((approved, receipt, issued(db)))
    forged_receipts = []
    for marker in (None, 'pta-p21-through-c24-original', 'unknown'):
        for replace_digest in (False, True):
            forged = deepcopy(receipt)
            if marker is None:
                del forged['verification']['evidence_contract']
            else:
                forged['verification']['evidence_contract'] = marker
            if replace_digest:
                forged['verification']['after']['woodchuck_profiles'] = deepcopy(
                    approved['content']['before']['woodchuck_profiles'])
            forged_receipts.append(forged)
    digest_only = deepcopy(receipt)
    digest_only['verification']['after']['woodchuck_profiles'] = deepcopy(
        approved['content']['before']['woodchuck_profiles'])
    forged_receipts.append(digest_only)
    for field, value in (('receipt_id', '0' * 32), ('target_sha256', '0' * 64),
                         ('control_id', 2), ('plan_sha256', '0' * 64),
                         ('evidence_contract', 'pta-p21-through-c24-original'),
                         ('activation_revision', 'c24class001')):
        forged = deepcopy(receipt)
        forged['issuance'][field] = value
        forged_receipts.append(forged)
    missing = deepcopy(receipt)
    del missing['issuance']
    forged_receipts.append(missing)
    # Reject tampering even before any database field changes.
    for forged in forged_receipts:
        with pytest.raises(cutover.CutoverError, match='activation_receipt_issuance_mismatch'):
            verify(db, approved, forged)
    for mutation in ("game_mode='Performance'", "first_choice_instrument='Flute'",
                     "display_name='covered-original-column'"):
        with db[1].begin() as c:
            c.execute(text('UPDATE woodchuck_profiles SET ' + mutation + ' WHERE id=24'))
        before = all_rows(db[1])
        for _ in range(2):
            with pytest.raises(cutover.CutoverError, match='post_activation_state_changed'):
                verify(db, approved, receipt)
        for forged in forged_receipts:
            with pytest.raises(cutover.CutoverError, match='activation_receipt_issuance_mismatch'):
                verify(db, approved, forged)
        assert all_rows(db[1]) == before
    assert (approved, receipt, issued(db)) == original


def test_issuance_update_delete_replace_and_truncate_refuse(migrated_db):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    before = all_rows(db[1])
    statements = ["UPDATE persistent_team_receipt_issuances SET evidence_contract='pta-p21-through-c24-original'",
                  "UPDATE persistent_team_receipt_issuances SET receipt_sha256=receipt_sha256",
                  "DELETE FROM persistent_team_receipt_issuances",
                  "DELETE FROM persistent_team_control"]
    if db[1].dialect.name == 'postgresql':
        statements += ['TRUNCATE persistent_team_receipt_issuances',
                       'TRUNCATE persistent_team_control CASCADE']
    else:
        statements += ['INSERT OR REPLACE INTO persistent_team_receipt_issuances SELECT * FROM persistent_team_receipt_issuances']
    for sql in statements:
        with pytest.raises(IntegrityError):
            with db[1].begin() as c:
                c.execute(text(sql))
        assert all_rows(db[1]) == before
    assert verify(db, approved, receipt)['passed']


def test_issuance_and_activation_roll_back_together(migrated_db, monkeypatch):
    db = migrated_db
    install(db, 'w25weekly005')
    command.upgrade(config(), issuance.REVISION)
    populate_weekly(db)
    populate_extensions(db)
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    stage(db, approved)
    before = all_rows(db[1])
    seen = []
    original = cutover.compatibility.assert_classroom_unchanged
    def unauthorized_write(c, revision, prior, error):
        assert c.scalar(text('SELECT count(*) FROM persistent_team_receipt_issuances')) == 1
        assert c.scalar(text('SELECT activated_at FROM persistent_team_control WHERE id=1')) is not None
        seen.append(True)
        c.execute(text("UPDATE classroom_progress_authorizations SET source_revision='forbidden'"))
        c.execute(text("UPDATE classroom_teaching_assignments SET starts_at='2026-09-01 00:00:00'"))
        c.execute(text("UPDATE weekly_plans SET generator_version='forbidden'"))
        original(c, revision, prior, error)
    with monkeypatch.context() as patch:
        patch.setattr(cutover.compatibility, 'assert_classroom_unchanged', unauthorized_write)
        with pytest.raises(cutover.CutoverError, match='unexpected_classroom_row_change'):
            activate(db, approved)
    assert seen and all_rows(db[1]) == before and issued(db) == []
    receipt = activate(db, approved)
    assert verify(db, approved, receipt)['passed']


def test_empty_issuance_migration_round_trip_and_used_downgrade_refusal(migrated_db):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), 'c30class001')
    before = all_rows(db[1])
    command.upgrade(config(), issuance.REVISION)
    after = all_rows(db[1])
    assert after.pop(issuance.NAME) == [] and after == before
    command.downgrade(config(), 'c30class001')
    assert all_rows(db[1]) == before
    command.upgrade(config(), issuance.REVISION)
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    before = all_rows(db[1])
    with pytest.raises(RuntimeError, match='pta_receipt_evidence_in_use: downgrade refused'):
        command.downgrade(config(), 'c30class001')
    with db[1].connect() as c:
        assert c.scalar(text('SELECT version_num FROM alembic_version')) == issuance.REVISION
    assert all_rows(db[1]) == before and verify(db, approved, receipt)['passed']


@pytest.mark.parametrize('original', ['c24class001', 'w25weekly005', 'c25class001', 'c30class001'])
def test_historical_activation_is_not_backfilled_or_reissued(migrated_db, original):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), original)
    approved = plan(db)
    stage(db, approved)
    receipt = activate_legacy_fixture(db, approved)
    frozen = deepcopy((approved, receipt))
    with pytest.raises(cutover.CutoverError, match='trusted_receipt_issuance_required'):
        verify(db, approved, receipt)
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    assert before[issuance.NAME] == []
    with pytest.raises(cutover.CutoverError, match='trusted_receipt_issuance_required'):
        verify(db, approved, receipt)
    with pytest.raises(cutover.CutoverError, match='disabled_staged_control_required'):
        activate(db, approved)
    # Direct INSERT cannot retrospectively authenticate an old activation either.
    values = dict(receipt_id='a' * 32, control_id=1, target_sha256=cutover.digest(approved['content']['target']),
        plan_sha256=approved['plan_sha256'], authority_sha256=approved['content']['authority_sha256'],
        activation_revision=issuance.REVISION, evidence_contract=issuance.CONTRACT,
        effective_at=BOUNDARY_AT, issued_at=BOUNDARY_AT, receipt_sha256=cutover.digest(receipt))
    with pytest.raises(IntegrityError, match='pta_receipt_staged_authority_required'):
        with db[1].begin() as c:
            t = Table(issuance.NAME, MetaData(), autoload_with=c, resolve_fks=False)
            c.execute(t.insert().values(**values))
    assert all_rows(db[1]) == before and (approved, receipt) == frozen


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
def test_fresh_upgrade_contains_empty_valid_issuance_contract(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv('DATABASE_URL', url)
    command.upgrade(config(), 'head')
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            assert cutover._schema_guard(c) == issuance.REVISION
            assert c.scalar(text('SELECT count(*) FROM persistent_team_receipt_issuances')) == 0
            assert c.scalar(text('SELECT activated_at FROM persistent_team_control WHERE id=1')) is None
    finally:
        engine.dispose()


def test_receipt_cannot_cross_database_authority_scopes(migrated_db, tmp_path, monkeypatch):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    approved = plan(db)
    stage(db, approved)
    receipt = activate(db, approved)
    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    other_url = disposable_url(other_dir, db[1].dialect.name)
    with monkeypatch.context() as patch:
        patch.setenv('DATABASE_URL', other_url)
        command.upgrade(config(), issuance.REVISION)
    other = (other_url, seed(other_url, migrated=True))
    try:
        other_plan = plan(other)
        stage(other, other_plan)
        other_receipt = activate(other, other_plan)
        assert verify(other, other_plan, other_receipt)['passed']
        assert receipt['issuance']['control_id'] == other_receipt['issuance']['control_id'] == 1
        assert receipt['issuance']['target_sha256'] != other_receipt['issuance']['target_sha256']
        before = all_rows(db[1]), all_rows(other[1])
        with pytest.raises(cutover.CutoverError, match='approved_activation_receipt_required'):
            verify(other, other_plan, receipt)
        with pytest.raises(cutover.CutoverError, match='target_mismatch'):
            verify(other, approved, receipt)
        forged = deepcopy(receipt)
        forged['verification']['approved_plan_sha256'] = other_plan['plan_sha256']
        forged['issuance'] = deepcopy(other_receipt['issuance'])
        with pytest.raises(cutover.CutoverError, match='activation_receipt_issuance_mismatch'):
            verify(other, other_plan, forged)
        assert (all_rows(db[1]), all_rows(other[1])) == before
    finally:
        other[1].dispose()


@pytest.mark.parametrize('damage', ['unknown_stamp', 'old_stamp', 'missing_guard', 'changed_guard', 'missing_table'])
def test_p31_schema_tampering_refuses_before_writes(migrated_db, damage):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    approved = plan(db)
    stage(db, approved)
    with db[1].begin() as c:
        if damage in {'unknown_stamp', 'old_stamp'}:
            c.execute(text('UPDATE alembic_version SET version_num=:revision'),
                      {'revision': 'p32pta001' if damage == 'unknown_stamp' else 'c30class001'})
        elif damage == 'missing_table':
            c.execute(text('DROP TABLE persistent_team_receipt_issuances'))
        elif c.dialect.name == 'sqlite':
            c.execute(text('DROP TRIGGER pta_receipt_update'))
            if damage == 'changed_guard':
                c.execute(text('CREATE TRIGGER pta_receipt_update BEFORE UPDATE ON persistent_team_receipt_issuances BEGIN SELECT 1; END'))
        elif damage == 'missing_guard':
            c.execute(text('ALTER TABLE persistent_team_receipt_issuances DISABLE TRIGGER pta_receipt_change'))
        else:
            c.execute(text("CREATE OR REPLACE FUNCTION enforce_pta_receipt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"))
    before = all_rows(db[1])
    for operation in (lambda: plan(db), lambda: stage(db, approved), lambda: activate(db, approved),
                      lambda: calendar.provision(db[0], seasons=['halloween-2026'])):
        with pytest.raises(cutover.inventory.InventoryError, match='revision_not_approved|schema_missing_or_changed'):
            operation()
    assert all_rows(db[1]) == before


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_disabled_issuance_foreign_key_is_not_accepted(migrated_db):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    with db[1].begin() as c:
        trigger = c.scalar(text("SELECT tgname FROM pg_trigger WHERE tgrelid='persistent_team_receipt_issuances'::regclass AND tgisinternal LIMIT 1"))
        quoted = c.dialect.identifier_preparer.quote(trigger)
        c.execute(text('ALTER TABLE persistent_team_receipt_issuances DISABLE TRIGGER ' + quoted))
    with pytest.raises(cutover.CutoverError, match='pta_receipt_schema_missing_or_changed'):
        plan(db)


def test_failed_issuance_insert_rolls_back_every_activation_write(migrated_db, monkeypatch):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    approved = plan(db)
    stage(db, approved)
    before = all_rows(db[1])
    # A malformed generated identity is rejected by the actual database CHECK.
    class BadIdentity:
        hex = 'bad'
    with monkeypatch.context() as patch:
        patch.setattr(cutover, 'uuid4', BadIdentity)
        with pytest.raises(cutover.CutoverError, match='transaction_rolled_back'):
            activate(db, approved)
    assert all_rows(db[1]) == before and issued(db) == []
    assert verify(db, approved, activate(db, approved))['passed']


@pytest.mark.parametrize('operation', ['stage', 'calendar'])
def test_other_operators_cannot_commit_injected_issuance(migrated_db, monkeypatch, operation):
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    if operation == 'calendar':
        stage(db, approved)
    before = all_rows(db[1])
    original = cutover.compatibility.assert_classroom_unchanged
    inserted = []

    def inject(c, revision, prior, error):
        t = Table(issuance.NAME, MetaData(), autoload_with=c, resolve_fks=False)
        c.execute(t.insert().values(receipt_id='a' * 32, control_id=1,
            target_sha256=cutover.digest(approved['content']['target']),
            plan_sha256=approved['plan_sha256'], authority_sha256=approved['content']['authority_sha256'],
            activation_revision=issuance.REVISION, evidence_contract=issuance.CONTRACT,
            effective_at=BOUNDARY_AT, issued_at=BOUNDARY_AT, receipt_sha256='b' * 64))
        assert c.scalar(text('SELECT count(*) FROM persistent_team_receipt_issuances')) == 1
        inserted.append(True)
        original(c, revision, prior, error)

    monkeypatch.setattr(cutover.compatibility, 'assert_classroom_unchanged', inject)
    with pytest.raises(cutover.inventory.InventoryError, match='unexpected_classroom_row_change'):
        if operation == 'stage':
            stage(db, approved)
        else:
            calendar.provision(db[0], apply=True, seasons=['halloween-2026'])
    assert inserted and all_rows(db[1]) == before and issued(db) == []


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_downgrade_refuses_stale_snapshot_isolation(migrated_db):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    def repeatable_read(connection, _):
        if type(connection).__module__.startswith('psycopg'):
            connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.commit()
    event.listen(Engine, 'connect', repeatable_read)
    try:
        with pytest.raises(RuntimeError, match='pta_receipt_downgrade_requires_read_committed'):
            command.downgrade(config(), 'c30class001')
    finally:
        event.remove(Engine, 'connect', repeatable_read)
    assert all_rows(db[1]) == before
    with db[1].connect() as c:
        assert c.scalar(text('SELECT version_num FROM alembic_version')) == issuance.REVISION


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_downgrade_waits_for_activation_and_preserves_committed_issuance(migrated_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from time import monotonic, sleep
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    db = migrated_db
    install(db, 'c24class001')
    command.upgrade(config(), issuance.REVISION)
    approved = plan(db)
    stage(db, approved)
    activation_ready, release_activation, downgrade_started = Event(), Event(), Event()
    waiting_pid = []
    original = cutover.compatibility.assert_classroom_unchanged

    def hold_activation(c, revision, before, error):
        original(c, revision, before, error)
        assert c.scalar(text('SELECT count(*) FROM persistent_team_receipt_issuances')) == 1
        activation_ready.set()
        assert release_activation.wait(20), 'activation barrier timed out'

    def observe_downgrade(c, cursor, statement, parameters, context, executemany):
        if (current_thread().name.startswith('pta-downgrade')
                and statement == 'SELECT id FROM persistent_team_control WHERE id=1 FOR UPDATE'):
            waiting_pid.append(c.connection.driver_connection.info.backend_pid)
            downgrade_started.set()

    monkeypatch.setattr(cutover.compatibility, 'assert_classroom_unchanged', hold_activation)
    event.listen(Engine, 'before_cursor_execute', observe_downgrade)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='pta-activate') as activating, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix='pta-downgrade') as downgrading:
            activation = activating.submit(activate, db, approved)
            try:
                assert activation_ready.wait(20), 'activation did not reach commit barrier'
                downgrade = downgrading.submit(command.downgrade, config(), 'c30class001')
                assert downgrade_started.wait(20), 'downgrade did not reach control fence'
                deadline = monotonic() + 10
                while True:
                    with db[1].connect() as c:
                        wait_type = c.scalar(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'),
                                             {'pid': waiting_pid[0]})
                    if wait_type == 'Lock':
                        break
                    assert monotonic() < deadline, 'downgrade did not wait for activation lock'
                    sleep(0.01)
                assert not downgrade.done()
            finally:
                release_activation.set()
            receipt = activation.result(timeout=20)
            with pytest.raises(RuntimeError, match='pta_receipt_evidence_in_use: downgrade refused'):
                downgrade.result(timeout=20)
    finally:
        release_activation.set()
        event.remove(Engine, 'before_cursor_execute', observe_downgrade)
    with db[1].connect() as c:
        assert c.scalar(text('SELECT version_num FROM alembic_version')) == issuance.REVISION
    assert len(issued(db)) == 1 and verify(db, approved, receipt)['passed']
