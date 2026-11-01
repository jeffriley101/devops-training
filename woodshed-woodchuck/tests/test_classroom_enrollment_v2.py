"""Enrollment V2 policy and atomicity, on synthetic SQLite and PostgreSQL."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import classroom, classroom_s2 as s2, classroom_enrollment as enrollment
from app import classroom_reporting as reporting, child_authorization
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation, ClassroomEnrollmentAuditEvent as Audit
from app.classroom_models import (ClassroomMembershipHold, ClassroomMembershipPeriod, ClassroomStudentMembership,
    ClassroomS2AuditEvent, ClassroomTeachingAssignment, ClassroomStudentInvitation,
    ClassroomReportingPeriod, ClassroomProgressAuthorization, ClassroomChartDirectorShare)
from app.models import (WoodchuckProfile, StudentVerifierConnection, TeamMembership, BillingAccount,
    Membership, TesterEnrollment as PersonalTesterAccess, ProfileCapability, PracticeChart)
from test_classroom_s1 import adult_pin_hash, actor, classroom_db, count
from test_classroom_s2 import s2_db, NOW, ready, student, join, create
from test_classroom_s1_postgres import classroom_pg
from test_classroom_s2_postgres import s2_pg


def setup_enrollment(factory, monkeypatch):
    monkeypatch.setenv('CLASSROOM_ENROLLMENT_V2_ENABLED', '1')
    with factory() as session:
        room, _, _ = ready(session)
        assignment = classroom.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=room.id, verifier_id=1)
        profile = session.get(WoodchuckProfile, 1)
        session.add(WoodchuckProfile(id=2, woodchuck_id='WC-OTHER', display_name='Synthetic Other',
            pin_hash=profile.pin_hash, instrument='Trumpet', level='Beginner', goal='Practice'))
        session.flush()
        session.add(AccountPrivacy(profile_id=2, age_band='13to17', declared_at=NOW, public_from=NOW))
        session.commit()
        return SimpleNamespace(factory=factory, class_id=room.id, assignment_id=assignment.id)


@pytest.fixture(params=['sqlite', 'postgresql'])
def enrollment_db(request, monkeypatch):
    return setup_enrollment(request.getfixturevalue('s2_db' if request.param == 'sqlite' else 's2_pg'), monkeypatch)


def issue(session, refs, *, adult_id=1, wc='WC-SYNTHETIC'):
    return enrollment.issue_invitation(session, actor=actor(session, adult_id),
        program_id=1, class_id=refs.class_id, woodchuck_id=wc)


def answer(session, ident, choice='accept', *, wc='WC-SYNTHETIC'):
    return enrollment.respond(session, student=s2.authenticate_student(session, woodchuck_id=wc, pin='2468'),
        invitation_id=ident, choice=choice)


def pending(refs):
    with refs.factory() as session:
        ident = issue(session, refs).id
        session.commit()
        return ident


PROTECTED = [ClassroomStudentInvitation, ClassroomReportingPeriod, ClassroomProgressAuthorization,
    ClassroomChartDirectorShare, StudentVerifierConnection, TeamMembership, BillingAccount,
    Membership, PersonalTesterAccess, ProfileCapability, PracticeChart]


def test_issue_is_purpose_separated_idempotent_and_grants_nothing(enrollment_db):
    refs = enrollment_db
    with refs.factory() as session:
        before = {m.__tablename__: count(session, m) for m in PROTECTED}
        first = issue(session, refs, wc=' wc-synthetic ')
        assert issue(session, refs).id == first.id
        assert first.purpose == enrollment.PURPOSE and first.state == 'pending'
        assert first.profile_id == 1 and first.assignment_id == refs.assignment_id
        assert count(session, Invitation) == count(session, Audit) == 1
        assert count(session, ClassroomStudentMembership) == 0
        assert {m.__tablename__: count(session, m) for m in PROTECTED} == before
        session.commit()


@pytest.mark.parametrize('code_first', [True, False])
def test_both_methods_share_one_anchor_period_and_join_audit(enrollment_db, code_first):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        before = {m.__tablename__: count(session, m) for m in PROTECTED}
        if code_first:
            membership = join(session).id
            result = answer(session, ident)
        else:
            result = answer(session, ident)
            membership = join(session).id
        assert result.state == 'accepted' and result.membership_id == membership
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomS2AuditEvent).where(
            ClassroomS2AuditEvent.action == 'student_joined')) == 1
        assert {m.__tablename__: count(session, m) for m in PROTECTED} == before
        assert answer(session, ident) == result
        assert count(session, Audit) == 2
        session.commit()


def test_two_assigned_directors_can_invite_without_duplicate_membership(enrollment_db):
    refs = enrollment_db
    with refs.factory() as session:
        classroom.assign_teacher(session, actor=actor(session), program_id=1, class_id=refs.class_id, verifier_id=2)
        first, second = issue(session, refs).id, issue(session, refs, adult_id=2).id
        a, b = answer(session, first), answer(session, second)
        assert a.membership_id == b.membership_id and a.membership_period_id == b.membership_period_id
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 1


@pytest.mark.parametrize('next_choice', ['accept', 'decline'])
def test_not_now_never_enrolls_or_extends_deadline(enrollment_db, next_choice):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        expiry = session.get(Invitation, ident).expires_at
        assert answer(session, ident, 'defer').state == 'deferred'
        assert answer(session, ident, 'defer').state == 'deferred'
        assert count(session, ClassroomStudentMembership) == 0 and count(session, Audit) == 2
        assert session.get(Invitation, ident).expires_at == expiry
        result = answer(session, ident, next_choice)
        assert result.state == ('accepted' if next_choice == 'accept' else 'declined')
        assert count(session, ClassroomStudentMembership) == (next_choice == 'accept')


def test_decline_is_terminal_but_a_new_offer_can_be_issued(enrollment_db):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        assert answer(session, ident, 'decline').state == 'declined'
        assert answer(session, ident).state == 'declined'
        assert count(session, ClassroomStudentMembership) == 0
        newer = issue(session, refs)
        assert newer.id != ident and newer.state == 'pending'
        assert count(session, Audit) == 3


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_exact_expiry_is_effective_without_a_scheduler(enrollment_db, monkeypatch, offset):
    refs = enrollment_db
    ident = pending(refs)
    monkeypatch.setattr(s2, '_now', lambda: NOW + enrollment.LIFETIME + timedelta(microseconds=offset))
    with refs.factory() as session:
        row = enrollment.student_invitation(session, student=student(session), invitation_id=ident)
        assert enrollment.status(row) == ('pending' if offset < 0 else 'expired')
        result = answer(session, ident)
        assert result.state == ('accepted' if offset < 0 else 'expired')
        assert count(session, ClassroomStudentMembership) == (offset < 0)
        if offset >= 0:
            assert issue(session, refs).id != ident


def test_revocation_and_ended_issuer_cleanup_do_not_mutate_membership(enrollment_db):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        classroom.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
        assert enrollment.revoke_invitation(session, actor=actor(session), invitation_id=ident).state == 'revoked'
        enrollment.revoke_invitation(session, actor=actor(session), invitation_id=ident)
        assert answer(session, ident).state == 'revoked'
        assert count(session, Audit) == 2 and count(session, ClassroomStudentMembership) == 0


@pytest.mark.parametrize('loss', ['close', 'archive', 'teacher', 'entitlement', 'hold', 'account', 'consent'])
def test_acceptance_rechecks_current_sources_and_never_releases_holds(enrollment_db, monkeypatch, loss):
    refs = enrollment_db
    if loss == 'entitlement':
        # Issue near the real 14-day trial end, so only the entitlement expires.
        # An offer issued at trial start would already have expired on day seven.
        monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(days=13))
    ident = pending(refs)
    with refs.factory() as session:
        if loss == 'close':
            s2.set_enrollment(session, actor=actor(session), program_id=1, class_id=refs.class_id, open=False)
        elif loss == 'archive':
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=refs.class_id, state='archived')
        elif loss == 'teacher':
            classroom.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
        elif loss == 'entitlement':
            monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(days=14))
        elif loss == 'hold':
            member = join(session)
            monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
            s2.suspend_member(session, actor=actor(session), program_id=1, class_id=refs.class_id, membership_id=member.id)
        elif loss == 'account':
            session.get(WoodchuckProfile, 1).status = 'deleted'
        else:
            session.get(AccountPrivacy, 1).age_band = 'under13'
        session.commit()
        with pytest.raises(classroom.ClassroomDenied):
            answer(session, ident)
        assert session.get(Invitation, ident).state == 'pending'
        if loss == 'hold':
            assert session.scalar(select(ClassroomMembershipHold)).released_at is None
        else:
            assert count(session, ClassroomStudentMembership) == 0


def test_closed_enrollment_blocks_issue_and_code_but_allows_decline(enrollment_db):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        s2.set_enrollment(session, actor=actor(session), program_id=1, class_id=refs.class_id, open=False)
        with pytest.raises(classroom.ClassroomDenied): issue(session, refs)
        with pytest.raises(classroom.ClassroomDenied): join(session)
        assert answer(session, ident, 'decline').state == 'declined'
        assert count(session, ClassroomStudentMembership) == 0


def test_accepted_replay_cannot_rejoin_or_revoke_enrollment(enrollment_db, monkeypatch):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        accepted = answer(session, ident)
        with pytest.raises(classroom.ClassroomDenied):
            enrollment.revoke_invitation(session, actor=actor(session), invitation_id=ident)
        monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
        s2.leave_class(session, student=student(session), program_id=1, class_id=refs.class_id)
        # Even an identical leave/rejoin instant cannot substitute another period.
        assert join(session).id == accepted.membership_id
        with pytest.raises(classroom.ClassroomDenied): answer(session, ident)
        assert count(session, ClassroomMembershipPeriod) == 2


@pytest.mark.parametrize('role', [None, 'head_director', 'admin', 'code_manager', 'billing'])
def test_roles_alone_cannot_issue_and_foreign_student_cannot_answer(enrollment_db, role):
    refs = enrollment_db
    with refs.factory() as session:
        if role:
            classroom.grant_role(session, actor=actor(session), program_id=1, verifier_id=2, role=role)
        with pytest.raises(classroom.ClassroomDenied): issue(session, refs, adult_id=2)
        row = issue(session, refs)
        with pytest.raises(classroom.ClassroomDenied): answer(session, row.id, wc='WC-OTHER')
        other = s2.authenticate_student(session, woodchuck_id='WC-OTHER', pin='2468')
        with pytest.raises(classroom.ClassroomDenied):
            enrollment.student_invitation(session, student=other, invitation_id=row.id)
        with pytest.raises(classroom.ClassroomDenied):
            enrollment.revoke_invitation(session, actor=actor(session, 2), invitation_id=row.id)
        assert count(session, ClassroomStudentMembership) == 0


@pytest.mark.parametrize('bad', ['', 'UNKNOWN', True, None, 'WC-OTHER%', 'x' * 65])
def test_target_is_an_exact_existing_account_not_a_search(enrollment_db, bad):
    refs = enrollment_db
    with refs.factory() as session:
        before = count(session, WoodchuckProfile)
        with pytest.raises(classroom.ClassroomDenied): issue(session, refs, wc=bad)
        assert count(session, Invitation) == 0 and count(session, WoodchuckProfile) == before


@pytest.mark.parametrize('bad', [True, -1, 0, '1', 1.0])
def test_scoped_ids_and_authentication_are_not_client_claims(enrollment_db, bad):
    refs = enrollment_db
    with refs.factory() as session:
        with pytest.raises(classroom.ClassroomDenied):
            enrollment.issue_invitation(session, actor=actor(session), program_id=bad,
                class_id=refs.class_id, woodchuck_id='WC-SYNTHETIC')
        with pytest.raises(classroom.ClassroomDenied):
            enrollment.respond(session, student=SimpleNamespace(profile_id=1), invitation_id=bad, choice='accept')


def test_eligible_parent_authorized_enrollment_does_not_create_reporting(enrollment_db, monkeypatch):
    refs = enrollment_db
    monkeypatch.setattr(child_authorization, 'under13_available', lambda: True)
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda *args: ('synthetic-notice', 'Synthetic', 'a' * 64))
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = 'under13', None
        evidence = ConsentEvidence(profile_id=1, parent_email='parent@example.test', notice_version='synthetic-notice',
            notice_sha256='a' * 64, approved_at=NOW, confirmed_at=NOW)
        session.add(evidence); session.flush(); rule.consent_id = evidence.id; session.commit()
        ident = issue(session, refs).id
        assert answer(session, ident).state == 'accepted'
        with pytest.raises(classroom.ClassroomDenied): reporting._account_rule(session, 1)
        assert count(session, ClassroomReportingPeriod) == count(session, ClassroomProgressAuthorization) == 0
        assert count(session, ConsentEvidence) == 1 and session.get(AccountPrivacy, 1).consent_id == evidence.id


@pytest.mark.parametrize('action', ['issue', 'accept', 'decline', 'defer', 'revoke'])
def test_audit_failure_rolls_back_the_entire_transition(enrollment_db, monkeypatch, action):
    refs = enrollment_db
    ident = pending(refs) if action != 'issue' else None
    def fail(*args, **kwargs): raise RuntimeError('synthetic enrollment audit failure')
    monkeypatch.setattr(enrollment, '_audit', fail)
    with refs.factory() as session:
        with pytest.raises(RuntimeError, match='synthetic enrollment audit failure'):
            if action == 'issue': issue(session, refs)
            elif action == 'revoke': enrollment.revoke_invitation(session, actor=actor(session), invitation_id=ident)
            else: answer(session, ident, action)
        session.commit()
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 0
        assert count(session, Invitation) == (ident is not None)
        assert count(session, Audit) == (ident is not None)
        if ident: assert session.get(Invitation, ident).state == 'pending'


def test_outer_rollback_and_stale_student_proof(enrollment_db):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        answer(session, ident); session.rollback()
        assert session.get(Invitation, ident).state == 'pending'
        assert count(session, ClassroomStudentMembership) == 0
        proof = student(session); session.commit()
        with pytest.raises(classroom.ClassroomDenied): enrollment.respond(session, student=proof, invitation_id=ident, choice='accept')


def test_new_gate_defaults_off_without_changing_code_join(enrollment_db, monkeypatch):
    refs = enrollment_db
    monkeypatch.delenv('CLASSROOM_ENROLLMENT_V2_ENABLED')
    with refs.factory() as session:
        assert not enrollment.enabled()
        with pytest.raises(classroom.ClassroomDenied): issue(session, refs)
        assert join(session).profile_id == 1


def test_database_rejects_wrong_purpose_and_cross_class_assignment(enrollment_db):
    refs = enrollment_db
    with refs.factory() as session:
        row = issue(session, refs); session.commit(); ident = row.id
        with pytest.raises(IntegrityError), session.begin_nested():
            session.get(Invitation, ident).purpose = 'classroom-student-invitation'; session.flush()
        other_class = create(session, name='Other Class'); session.commit()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.get(Invitation, ident).class_id = other_class.id; session.flush()


def test_valid_invitation_never_authenticates_a_forged_or_foreign_session(enrollment_db):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        for forged in [None, SimpleNamespace(profile_id=1), actor(session)]:
            with pytest.raises(classroom.ClassroomDenied):
                enrollment.respond(session, student=forged, invitation_id=ident, choice='accept')
        for bad_choice in [None, True, {}, ['accept'], 'automatic']:
            with pytest.raises(classroom.ClassroomDenied):
                enrollment.respond(session, student=student(session), invitation_id=ident, choice=bad_choice)
        with refs.factory() as other:
            foreign_proof = student(other)
            with pytest.raises(classroom.ClassroomDenied):
                enrollment.respond(session, student=foreign_proof, invitation_id=ident, choice='accept')
        assert count(session, ClassroomStudentMembership) == 0


def test_enrollment_never_shares_private_history_or_authorizes_reporting(enrollment_db, monkeypatch):
    from app import classroom_dashboard
    refs = enrollment_db
    ident = pending(refs)
    monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
    monkeypatch.setenv('CLASSROOM_S3_ENABLED', '1')
    monkeypatch.setattr(reporting, '_now', lambda: NOW)
    with refs.factory() as session:
        session.add(PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=15,
            instrument='Trumpet', source='p-book', note='Synthetic private history'))
        session.commit()
        assert answer(session, ident).state == 'accepted'
        result = classroom_dashboard.dashboard(session, actor=actor(session), program_id=1, class_id=refs.class_id)
        assert result['students'] == []
        with pytest.raises(classroom.ClassroomDenied):
            reporting.authorize_reporting(session, actor=actor(session), program_id=1, class_id=refs.class_id,
                profile_id=1, operation='history_boundary', fields=())
        assert count(session, ClassroomChartDirectorShare) == count(session, ClassroomReportingPeriod) == 0


def test_a_sharing_only_invitation_is_never_an_enrollment_offer(enrollment_db, monkeypatch):
    refs = enrollment_db
    with refs.factory() as session:
        from app import classroom_student_invitations as sharing
        # Sharing policy uses its existing independent gate and exact assignment.
        monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
        row = sharing.issue(session, actor=actor(session), program_id=1, class_id=refs.class_id,
            assignment_id=refs.assignment_id, profile_id=1)
        sharing.accept(session, student=student(session), invitation_id=row.id)
        with pytest.raises(classroom.ClassroomDenied): answer(session, row.id)
        assert count(session, Invitation) == count(session, ClassroomStudentMembership) == 0


def test_real_cross_program_and_class_substitution_is_refused(enrollment_db):
    from app.models import Organization
    from app.classroom_models import ClassroomProgram
    from test_classroom_s1 import provision
    refs = enrollment_db
    with refs.factory() as session:
        if session.get(Organization, 2) is None:
            session.add(Organization(id=2, name='Synthetic Other Program', organization_type='music'))
            session.commit()
        if session.get(ClassroomProgram, 2) is None:
            provision(session, 2); session.commit()
        s2.start_trial(session, actor=actor(session), program_id=2)
        other = s2.create_class(session, actor=actor(session), program_id=2, display_name='Other Class')
        classroom.assign_teacher(session, actor=actor(session), program_id=2, class_id=other.id, verifier_id=1)
        other_class = other.id
        session.commit()
    for program_id, class_id in [(1, other_class), (2, refs.class_id)]:
        with refs.factory() as session:
            with pytest.raises(classroom.ClassroomDenied):
                enrollment.issue_invitation(session, actor=actor(session), program_id=program_id,
                    class_id=class_id, woodchuck_id='WC-SYNTHETIC')
    with refs.factory() as session:
        other_offer = enrollment.issue_invitation(session, actor=actor(session), program_id=2,
            class_id=other_class, woodchuck_id='WC-OTHER').id
        session.commit()
        with pytest.raises(classroom.ClassroomDenied): answer(session, other_offer)
        assert count(session, ClassroomStudentMembership) == 0


def test_a_retained_acceptance_cannot_appear_before_its_resolution(enrollment_db, monkeypatch):
    refs = enrollment_db
    with refs.factory() as session:
        join(session); session.commit()
        monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
        ident = issue(session, refs).id
        monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=3))
        assert answer(session, ident).state == 'accepted'
        session.commit()
        monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=2))
        with pytest.raises(classroom.ClassroomDenied): answer(session, ident)
        assert count(session, ClassroomMembershipPeriod) == 1


@pytest.mark.parametrize('decision', ['decline', 'revoke'])
def test_an_offer_decision_cannot_remove_a_code_enrollment(enrollment_db, decision):
    refs = enrollment_db
    ident = pending(refs)
    with refs.factory() as session:
        member = join(session).id
        if decision == 'decline':
            assert answer(session, ident, 'decline').state == 'declined'
        else:
            assert enrollment.revoke_invitation(session, actor=actor(session), invitation_id=ident).state == 'revoked'
        assert s2.contribution_active(session, program_id=1, class_id=refs.class_id, profile_id=1)
        assert session.get(ClassroomStudentMembership, member).profile_id == 1
        assert count(session, ClassroomMembershipPeriod) == 1
