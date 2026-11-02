"""Director workspace source separation on disposable PostgreSQL, with real waits."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app import classroom as s1, classroom_s2 as s2, classroom_reporting as boundary
from app import classroom_progress_authorization as progress, classroom_chart_sharing as sharing
from app import classroom_identity as identity
from app.classroom_models import ClassroomProgressAuthorization, ClassroomProgressRecipient
from app.models import PracticeChart, WoodchuckProfile
from test_classroom_s1 import actor
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2 import NOW, student, create, code
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting
from test_classroom_progress_authorization import accept


@pytest.fixture
def workspace_pg(s3_pg, monkeypatch):
    for flag in ('CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_ENROLLMENT_V2_ENABLED',
                 'CLASSROOM_PRIVATE_IDENTITY_ENABLED', 'CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED'):
        monkeypatch.setenv(flag, '1')
    factory = s3_pg
    refs = setup_reporting(factory, granted=False)
    refs.factory = factory
    refs.clock = factory.reporting_clock
    with factory() as session:
        identity.set_school_name(session, actor=actor(session), program_id=1, class_id=refs.class_id,
            woodchuck_id='WC-SYNTHETIC', school_name='PG Workspace School Synthetic', expected_version=0)
        shared = PracticeChart(profile_id=1, practice_date=NOW.date() - timedelta(days=40),
            minutes=7, instrument='Trumpet', note='SHARED_PG_WORKSPACE_SENTINEL',
            created_at=NOW - timedelta(days=40))
        private = PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=999,
            instrument='Flute', note='PRIVATE_PG_WORKSPACE_SENTINEL', created_at=NOW)
        session.add_all([shared, private]); session.flush()
        refs.shared_chart_id, refs.private_chart_id = shared.id, private.id
        sharing.set_sharing(session, student=student(session), chart_id=shared.id,
            assignment_ids=[refs.assignment_id])
        session.commit()
        session.get(WoodchuckProfile, 1).display_name = 'PUBLIC_PG_NICKNAME_SENTINEL'
        session.commit()
    return refs


def workspace():
    # Lazy import permits fixtures to be prepared while the implementation is
    # authored; qualification still calls the actual final service entrypoints.
    from app import classroom_director_workspace
    return classroom_director_workspace


def read(session, refs, *, adult=2, **changes):
    options = {'actor': actor(session, adult), 'program_id': 1, 'class_id': refs.class_id,
        'membership_id': refs.membership_id}
    return workspace().student_view(session, **(options | changes))


def serialized(payload):
    return json.dumps(payload, default=lambda value: (_ for _ in ()).throw(
        AssertionError('Non-public object in director response: ' + type(value).__name__)))


def test_pg_admin_roster_reads_no_practice_dataset(workspace_pg):
    refs = workspace_pg
    statements = []
    def capture(conn, cursor, statement, params, context, many): statements.append(statement.lower())
    engine = refs.factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with refs.factory() as session:
            data = workspace().class_view(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert set(data) >= {'admin', 'statistics', 'shared_pcharts'}
    encoded = serialized(data)
    assert 'PG Workspace School Synthetic' in encoded and 'WC-SYNTHETIC' in encoded
    assert 'PUBLIC_PG_NICKNAME_SENTINEL' not in encoded
    assert not any('practice_charts' in statement for statement in statements)
    assert 'PRIVATE_PG_WORKSPACE_SENTINEL' not in encoded and 'SHARED_PG_WORKSPACE_SENTINEL' not in encoded


def test_pg_student_detail_only_explicit_shared_chart_and_no_boundary_dto(workspace_pg):
    refs = workspace_pg
    with refs.factory() as session:
        data = read(session, refs)
        encoded = serialized(data)
        assert 'PG Workspace School Synthetic' in encoded and 'WC-SYNTHETIC' in encoded
        assert 'SHARED_PG_WORKSPACE_SENTINEL' in encoded
        for forbidden in ('PRIVATE_PG_WORKSPACE_SENTINEL', 'PUBLIC_PG_NICKNAME_SENTINEL',
                          'profile_id', 'authorization_id', 'acceptance_session_version',
                          'notice_digest', '_transaction', '_session'):
            assert forbidden not in encoded
        assert data['statistics'].get('values') is None
        # Canonical ownership never implies a teaching/chart-sharing grant.
        owner = read(session, refs, adult=1)
        assert 'SHARED_PG_WORKSPACE_SENTINEL' not in serialized(owner)
        assert count_rows(session, ClassroomProgressAuthorization) == 0


def count_rows(session, model):
    from sqlalchemy import func
    return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize('changes', [{'program_id': 2}, {'class_id': 99999}, {'membership_id': 99999}])
def test_pg_exact_program_class_and_membership_scope(workspace_pg, changes):
    refs = workspace_pg
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): read(session, refs, **changes)
        with pytest.raises(s1.ClassroomDenied): read(session, refs, adult=3)


def test_pg_valid_reporting_notice_uses_only_new_approved_field_reader(workspace_pg):
    refs = workspace_pg
    with refs.factory() as session:
        row = accept(session, refs)
        accepted_at, authorization_id = row.accepted_at, row.id
        session.commit()
        proof = progress.authorize_boundary(session, actor=actor(session, 2), program_id=1,
            class_id=refs.class_id, profile_id=1)
        assert proof.allowed and proof.starts_at == boundary.utc(accepted_at)
        data = read(session, refs)
        assert data['statistics']['authorization_available'] is True
        assert data['statistics']['field_contract'] == 'classroom-reporting-41-v1'
        assert len(data['statistics']['values']['fields']) == 41
        # No complete occurrence day has elapsed in this accepted interval.
        assert all(value is None for value in data['statistics']['values']['fields'].values())
        assert 'SHARED_PG_WORKSPACE_SENTINEL' in serialized(data)
        assert count_rows(session, ClassroomProgressAuthorization) == 1
        assert session.get(ClassroomProgressAuthorization, authorization_id).withdrawn_at is None
        with pytest.raises(s1.ClassroomDenied):
            progress.authorize_boundary(session, actor=actor(session, 2), program_id=1,
                class_id=refs.class_id, profile_id=1, fields=('practice_minutes',))


def test_pg_reporting_withdrawal_does_not_withdraw_individual_chart(workspace_pg):
    refs = workspace_pg
    with refs.factory() as session:
        accept(session, refs); session.commit()
        refs.clock[0] += timedelta(seconds=2)
        progress.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)
        session.commit()
        with pytest.raises(s1.ClassroomDenied):
            progress.authorize_boundary(session, actor=actor(session, 2), program_id=1,
                class_id=refs.class_id, profile_id=1)
        data = read(session, refs)
        assert data['statistics']['authorization_available'] is False
        assert data['statistics'].get('values') is None
        assert 'SHARED_PG_WORKSPACE_SENTINEL' in serialized(data)
        assert 'PRIVATE_PG_WORKSPACE_SENTINEL' not in serialized(data)


def test_pg_chart_withdrawal_keeps_admin_and_reporting_independent(workspace_pg):
    refs = workspace_pg
    with refs.factory() as session:
        accept(session, refs); session.commit()
        refs.clock[0] += timedelta(seconds=2)
        sharing.set_sharing(session, student=student(session), chart_id=refs.shared_chart_id, assignment_ids=[])
        session.commit()
        assert progress.authorize_boundary(session, actor=actor(session, 2), program_id=1,
            class_id=refs.class_id, profile_id=1).allowed
        data = read(session, refs)
        assert data['statistics']['authorization_available'] is True
        encoded = serialized(data)
        assert 'PG Workspace School Synthetic' in encoded
        assert 'SHARED_PG_WORKSPACE_SENTINEL' not in encoded
        assert len(data['statistics']['values']['fields']) == 41


def test_pg_waiting_view_does_not_retain_revoked_teaching_authority(workspace_pg):
    refs = workspace_pg
    first, second = ordered_wait(refs.factory,
        lambda session: s2.end_teaching(session, actor=actor(session), program_id=1,
            assignment_id=refs.assignment_id).id,
        lambda session: read(session, refs))
    assert first[0] == 'ok' and second[0] == 'denied'


def test_pg_waiting_view_rechecks_explicit_chart_withdrawal(workspace_pg):
    refs = workspace_pg
    def revoke(session):
        sharing.set_sharing(session, student=student(session), chart_id=refs.shared_chart_id, assignment_ids=[])
        return True
    first, second = ordered_wait(refs.factory, revoke, lambda session: read(session, refs))
    assert first[0] == second[0] == 'ok'
    encoded = serialized(second[1])
    assert 'PG Workspace School Synthetic' in encoded and 'SHARED_PG_WORKSPACE_SENTINEL' not in encoded


def test_pg_waiting_view_rechecks_reporting_withdrawal_without_losing_chart(workspace_pg):
    refs = workspace_pg
    with refs.factory() as session: accept(session, refs); session.commit()
    refs.clock[0] += timedelta(seconds=2)
    def revoke(session):
        progress.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)
        return True
    first, second = ordered_wait(refs.factory, revoke, lambda session: read(session, refs))
    assert first[0] == second[0] == 'ok'
    assert second[1]['statistics']['authorization_available'] is False
    assert second[1]['statistics'].get('values') is None
    assert 'SHARED_PG_WORKSPACE_SENTINEL' in serialized(second[1])


@pytest.mark.parametrize('loss', ['hold', 'archive', 'expiry', 'child'])
def test_pg_current_membership_account_and_entitlement_loss_never_leak(workspace_pg, loss):
    from app.age_models import AccountPrivacy
    refs = workspace_pg
    refs.clock[0] += timedelta(seconds=2)
    with refs.factory() as session:
        if loss == 'hold':
            s2.suspend_member(session, actor=actor(session), program_id=1, class_id=refs.class_id,
                membership_id=refs.membership_id)
        elif loss == 'archive':
            s2.set_class_state(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, state='archived')
        elif loss == 'child':
            session.get(AccountPrivacy, 1).age_band = 'under13'
        else:
            refs.clock[0] += timedelta(days=400)
        session.commit()
        try:
            data = read(session, refs)
        except s1.ClassroomDenied:
            return
        encoded = serialized(data)
        assert 'PG Workspace School Synthetic' not in encoded
        assert 'SHARED_PG_WORKSPACE_SENTINEL' not in encoded
        assert 'PRIVATE_PG_WORKSPACE_SENTINEL' not in encoded
        assert data['statistics'].get('values') is None


def test_pg_connected_students_are_not_merged_into_classroom_roster(workspace_pg):
    from app.age_models import AccountPrivacy
    from app.models import StudentVerifierConnection
    refs = workspace_pg
    with refs.factory() as session:
        original = session.get(WoodchuckProfile, 1)
        peer = WoodchuckProfile(id=2, woodchuck_id='WC-CONNECTED', display_name='CONNECTED_PRIVATE_ALIAS',
            pin_hash=original.pin_hash, instrument='Flute', level='Beginner', goal='Practice')
        session.add(peer); session.flush()
        session.add(AccountPrivacy(profile_id=peer.id, age_band='13to17', declared_at=NOW, public_from=NOW))
        session.add(StudentVerifierConnection(profile_id=peer.id, verifier_id=2, role='band_director', status='accepted'))
        session.add(PracticeChart(profile_id=peer.id, practice_date=NOW.date(), minutes=999,
            instrument='Flute', note='CONNECTED_UNSHARED_PG_SENTINEL', created_at=NOW))
        session.commit()
        data = workspace().class_view(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        encoded = serialized(data)
        for value in ('WC-CONNECTED', 'CONNECTED_PRIVATE_ALIAS', 'CONNECTED_UNSHARED_PG_SENTINEL'):
            assert value not in encoded
        assert len(data['admin']['roster']) == 1
        with pytest.raises(s1.ClassroomDenied): read(session, refs, membership_id=99999)
