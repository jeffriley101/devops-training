"""Real Program-lock waits for competing Enrollment V2 decisions."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import classroom, classroom_s2 as s2, classroom_enrollment as enrollment
from app.classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation, ClassroomEnrollmentAuditEvent as Audit
from app.classroom_models import ClassroomMembershipPeriod, ClassroomStudentMembership, ClassroomS2AuditEvent
from app.models import WoodchuckProfile, TrustedVerifier
from app.security import hash_pin
from test_classroom_s1 import actor
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2_postgres import s2_pg
from test_classroom_s2 import NOW, student, join, institutional
from test_classroom_enrollment_v2 import setup_enrollment, issue, answer, pending


@pytest.fixture
def enrollment_pg(s2_pg, monkeypatch):
    return setup_enrollment(s2_pg, monkeypatch)


def assert_one_membership(refs, *, periods=1):
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomMembershipPeriod)) == periods
        assert session.scalar(select(func.count()).select_from(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == 'student_joined')) == 1


@pytest.mark.parametrize('invitation_first', [True, False])
def test_code_and_invitation_wait_then_reuse_the_same_membership(enrollment_pg, invitation_first):
    refs = enrollment_pg; ident = pending(refs)
    code = lambda session: join(session).id
    invite = lambda session: answer(session, ident).membership_id
    first, second = (invite, code) if invitation_first else (code, invite)
    result = ordered_wait(refs.factory, first, second)
    assert [r[0] for r in result] == ['ok', 'ok'] and result[0][1] == result[1][1]
    assert_one_membership(refs)


def test_duplicate_issue_has_one_open_offer_and_audit(enrollment_pg):
    refs = enrollment_pg
    result = ordered_wait(refs.factory, lambda s: issue(s, refs).id, lambda s: issue(s, refs).id)
    assert result[0][0] == result[1][0] == 'ok' and result[0][1] == result[1][1]
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(Invitation)) == 1
        assert session.scalar(select(func.count()).select_from(Audit)) == 1


@pytest.mark.parametrize('rollback_first', [False, True])
def test_duplicate_acceptance_and_rollback_are_atomic(enrollment_pg, rollback_first):
    refs = enrollment_pg; ident = pending(refs)
    result = ordered_wait(refs.factory, lambda s: answer(s, ident).membership_id,
        lambda s: answer(s, ident).membership_id, rollback_first=rollback_first)
    assert result[0][0] == result[1][0] == 'ok'
    assert_one_membership(refs)
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'accepted')) == 1


@pytest.mark.parametrize('decision', ['decline', 'defer'])
def test_student_decision_serializes_with_acceptance(enrollment_pg, decision):
    refs = enrollment_pg; ident = pending(refs)
    result = ordered_wait(refs.factory, lambda s: answer(s, ident, decision).state,
        lambda s: answer(s, ident).state)
    assert result[0][0] == result[1][0] == 'ok'
    assert result[1][1] == ('declined' if decision == 'decline' else 'accepted')
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == (decision == 'defer')


@pytest.mark.parametrize('revoke_first', [False, True])
def test_revocation_cannot_race_into_an_enrollment_or_remove_an_accepted_member(enrollment_pg, revoke_first):
    refs = enrollment_pg; ident = pending(refs)
    revoke = lambda s: enrollment.revoke_invitation(s, actor=actor(s), invitation_id=ident).state
    accept = lambda s: answer(s, ident).state
    first, second = (revoke, accept) if revoke_first else (accept, revoke)
    result = ordered_wait(refs.factory, first, second)
    assert result[0][0] == 'ok'
    assert result[1][0] == ('ok' if revoke_first else 'denied')
    if revoke_first: assert result[1][1] == 'revoked'
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == (not revoke_first)


@pytest.mark.parametrize('change', ['close', 'teacher', 'entitlement', 'expiry', 'student_credential'])
def test_queued_acceptance_rechecks_sources_after_the_observed_wait(enrollment_pg, monkeypatch, change):
    refs = enrollment_pg; ident = pending(refs)
    def writer(session):
        classroom._lock_program(session, 1)
        if change == 'close':
            s2.set_enrollment(session, actor=actor(session), program_id=1, class_id=refs.class_id, open=False)
        elif change == 'teacher':
            classroom.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
        elif change == 'entitlement':
            institutional(session, class_limit=0)
        elif change == 'expiry':
            monkeypatch.setattr(s2, '_now', lambda: NOW + enrollment.LIFETIME)
        else:
            session.get(WoodchuckProfile, 1).session_version += 1
        return 'source changed under Program lock'
    result = ordered_wait(refs.factory, writer, lambda s: answer(s, ident).state)
    assert result[0][0] == 'ok'
    assert result[1][0] == ('ok' if change == 'expiry' else 'denied')
    if change == 'expiry': assert result[1][1] == 'expired'
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomStudentMembership)) == 0


def test_pending_invitation_cannot_bypass_a_concurrent_hold(enrollment_pg, monkeypatch):
    refs = enrollment_pg
    with refs.factory() as session:
        membership = join(session).id; session.commit()
    ident = pending(refs)
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
    suspend = lambda session: s2.suspend_member(session, actor=actor(session), program_id=1,
        class_id=refs.class_id, membership_id=membership).id
    result = ordered_wait(refs.factory, suspend, lambda s: answer(s, ident).state)
    assert [r[0] for r in result] == ['ok', 'denied']
    with refs.factory() as session:
        assert not s2.contribution_active(session, program_id=1, class_id=refs.class_id, profile_id=1)


def test_stale_director_credentials_are_rechecked_after_program_wait(enrollment_pg):
    refs = enrollment_pg
    def rotate(session):
        classroom._lock_program(session, 1)
        session.get(TrustedVerifier, 1).pin_hash = hash_pin('1357')
        return 'synthetic credential rotated'
    result = ordered_wait(refs.factory, rotate, lambda s: issue(s, refs).id)
    assert [r[0] for r in result] == ['ok', 'denied']
    with refs.factory() as session:
        assert session.scalar(select(func.count()).select_from(Invitation)) == 0


@pytest.mark.parametrize('isolation', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_stronger_snapshot_isolation_cannot_reuse_authority(enrollment_pg, isolation):
    refs = enrollment_pg
    with refs.factory() as session:
        session.connection(execution_options={'isolation_level': isolation})
        with pytest.raises(classroom.ClassroomDenied, match='READ COMMITTED'):
            issue(session, refs)
