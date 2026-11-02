"""Native signed synthetic KWS activation plus exact-Class guardian choices.

Every positive case runs real account/parent authentication and the actual C34
migration. No test fixture creates a positive KWS/guardian consent row directly.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import sys

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app import classroom as s1, classroom_s2 as s2, classroom_reporting as boundary
from app import classroom_progress_authorization as progress, classroom_guardian_authorization as guardian
from app import child_authorization as consent, classroom_director_workspace as workspace, parent_access
from app.age_privacy import utc, can_publish
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence, ParentAccess, DirectorPermission
from app.classroom_guardian_models import ClassroomGuardianAuthorization as Grant, ClassroomGuardianAudit as Audit
from app.classroom_models import (ClassroomProgressAuthorization, ClassroomMembershipPeriod,
    ClassroomStudentMembership, ClassroomEntitlement, ClassroomTeachingAssignment)
from app.classroom_comprehensive_reporting import report, FIELD_KEYS
from app.classroom_capabilities import capability_decision
from app.db import Base
from app.feature_access import FEATURES, Feature
from app.kws_models import KWSVerification
from app.models import Organization, TrustedVerifier, WoodchuckProfile, WoodchuckState, PracticeChart
from app.security import hash_pin
from app.student_practice_metrics import CENTRAL, week_start
from test_classroom_s1 import adult_pin_hash, actor
from test_classroom_s2 import institutional, create, code
from test_classroom_s1_postgres import ordered_wait
from test_classroom_progress_migration import migrated_db
from tests.test_weekly2_foundation_migration import config
from test_private_practice import captured, authorize, open_parent, post, path, connect


@pytest.fixture
def guardian_data(migrated_db, captured, monkeypatch, adult_pin_hash, request):
    db = migrated_db
    command.upgrade(config(), 'head')
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    factory.engine = db
    factory.control_engine = create_engine(db.url)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    for name in FEATURES:
        if name.startswith('weekly_'):
            monkeypatch.setitem(FEATURES, name, Feature(False, 'open'))
    for flag in ('CLASSROOM_S1_ENABLED', 'CLASSROOM_S1_LOCAL_PROVISIONING', 'CLASSROOM_S2_ENABLED',
                 'CLASSROOM_S3_ENABLED', 'CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_ENROLLMENT_V2_ENABLED',
                 'CLASSROOM_PRIVATE_IDENTITY_ENABLED', 'CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED',
                 'CLASSROOM_GUARDIAN_AUTHORIZATION_ENABLED', 'CLASSROOM_MANAGEMENT_UI_ENABLED'):
        monkeypatch.setenv(flag, '1')
    monkeypatch.setenv('CLASSROOM_S1_PROVISIONER_IDS', '1')
    monkeypatch.setenv('SITE_ADMIN_TOKEN', 'synthetic-classroom-s2-admin')
    clock = captured[1]
    clock[0] += timedelta(hours=1)
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    monkeypatch.setattr(boundary, '_now', lambda: clock[0])
    with factory() as session:
        for label, credit in [('A', 37), ('B', 83)]:
            profile = WoodchuckProfile(woodchuck_id='WC-AGE-' + label, display_name='Synthetic ' + label,
                pin_hash=adult_pin_hash, instrument='Flute', level='Beginner', goal='Practice every day')
            session.add(profile); session.flush()
            session.add(WoodchuckState(profile_id=profile.id, revision=7, state_json={
                'account': {'woodchuckId': profile.woodchuck_id, 'authenticated': True, 'serverRevision': 7},
                'profile': {'woodchuckName': profile.display_name, 'instrument': 'Flute',
                    'level': 'Beginner', 'goal': 'Practice every day'},
                'progress': {'credits': credit}, 'practiceLog': [{'note': 'saved ' + label}]}))
        session.commit()
    options = getattr(request, 'param', {})
    child, parent, pid, wid = authorize(factory, captured, sharing=options.get('sharing', True))
    open_parent(parent, captured, wid)
    # Comprehensive director reporting now remains unavailable under 13. The
    # positive reporting fixture uses the existing native parent age transition,
    # retaining its consent link. Dedicated younger cases stay under 13.
    if not options.get('under13', False):
        with factory() as session:
            parent_access.confirm_age(session, parent_request(SimpleNamespace(parent=parent)),
                explicit='yes', version=parent_access.AGE_WORDING_VERSION)
            session.commit()
    with factory() as session:
        session.add_all(TrustedVerifier(email=f'adult{i}@example.test', display_name=f'Synthetic Adult {i}',
            pin_hash=adult_pin_hash) for i in range(1, 4))
        session.flush()
        session.add_all(Organization(id=i, name=f'Synthetic Program {i}', organization_type='music',
            created_by_verifier_id=1) for i in (1, 2))
        session.commit()
        for program_id in (1, 2):
            s1.provision_program(session, actor=actor(session), organization_id=program_id, owner=actor(session))
            session.commit()
        institutional(session, starts_at=clock[0], ends_at=clock[0] + timedelta(days=365))
        session.commit()
        room = create(session, name='Guardian synthetic Class')
        code(session, room.id)
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=room.id, verifier_id=2)
        session.commit()
        membership = s2.join_class(session, student=student(session), program_id=1, code='BAND2026')
        session.commit()
        refs = SimpleNamespace(factory=factory, child=child, parent=parent, profile_id=pid, woodchuck_id=wid,
            clock=clock, captured=captured, class_id=room.id, assignment_id=assignment.id,
            membership_id=membership.id, url=f'/classroom/dashboard/1/classes/{room.id}/students/{membership.id}')
    yield refs
    factory.control_engine.dispose()


def student(session):
    return s2.authenticate_student(session, woodchuck_id='WC-AGE-A', pin='2468')


def parent_proof(session, refs):
    # Same signed parent browser session obtained through the native email link.
    return guardian.parent_from_request(session, parent_request(refs))


def parent_request(refs):
    from itsdangerous import TimestampSigner
    from base64 import b64decode
    from app.main import SESSION_SECRET
    data = json.loads(b64decode(TimestampSigner(SESSION_SECRET).unsign(refs.parent.cookies.get('session'))))
    return SimpleNamespace(session=data)


def parent_accept(session, refs, purpose):
    proof = parent_proof(session, refs)
    choice = guardian.choice(session, parent=proof, program_id=1, class_id=refs.class_id, purpose=purpose)
    version, _, digest, _ = guardian._notice(purpose)
    return guardian.accept(session, parent=proof, program_id=1, class_id=refs.class_id, purpose=purpose,
        notice_version=version, notice_digest=digest, guardian_attested=True, opted_in=True,
        previous=choice['revision'], source_revision=choice['source_revision'],
        assignment_ids=choice['assignment_ids'], participation_id=choice['participation_id'])


def student_accept(session, refs):
    proof = student(session)
    choice = progress.choice(session, student=proof, program_id=1, class_id=refs.class_id)
    return progress.accept(session, student=proof, program_id=1, class_id=refs.class_id,
        notice_version=progress.NOTICE_VERSION, opted_in=True, assignment_ids=choice.assignment_ids,
        previous=choice.revision, source_revision=choice.source_revision)


def fully_authorize(refs):
    with refs.factory() as session:
        parent_accept(session, refs, 'participation'); session.commit()
        parent_accept(session, refs, 'reporting'); session.commit()
        student_accept(session, refs); session.commit()


def read(session, refs, **changes):
    return report(session, **(dict(actor=actor(session, 2), program_id=1,
        class_id=refs.class_id, profile_id=refs.profile_id) | changes))


def test_native_evidence_is_distinct_from_each_class_permission(guardian_data):
    refs = guardian_data
    with refs.factory() as session:
        verification = session.scalar(select(KWSVerification))
        assert verification.state == 'activated' and verification.guardian_attested_at
        assert verification.notice_accepted_at and verification.transaction_hash
        assert session.scalar(select(func.count(Grant.id))) == 0
        assert session.scalar(select(func.count(ClassroomStudentMembership.id))) == 1
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        parent_accept(session, refs, 'participation'); session.commit()
        assert capability_decision(session, refs.profile_id, 'note-names').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        parent_accept(session, refs, 'reporting'); session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)  # child has not opted in
        student_accept(session, refs); session.commit()
        data = read(session, refs)
        assert set(data['values']['fields']) == set(FIELD_KEYS) and len(FIELD_KEYS) == 41
        assert all(value is None for value in data['values']['fields'].values())
        assert session.scalar(select(func.count(Grant.id))) == 2
        assert session.scalar(select(func.count(Audit.id))) == 2


@pytest.mark.parametrize('guardian_data', [{'sharing':False}], indirect=True)
def test_class_permissions_do_not_require_or_create_a_free_director_or_chart_share(guardian_data):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        from app.classroom_models import ClassroomChartShare, ClassroomChartDirectorShare
        from app.models import StudentVerifierConnection, MembershipSeat
        assert session.scalar(select(KWSVerification)).director_allowed is False
        assert session.scalar(select(func.count(DirectorPermission.id))) == 0
        assert session.scalar(select(func.count(StudentVerifierConnection.id))) == 0
        assert session.scalar(select(func.count()).select_from(ClassroomChartShare)) == 0
        assert session.scalar(select(func.count(ClassroomChartDirectorShare.id))) == 0
        assert session.scalar(select(func.count(MembershipSeat.id))) == 0
        assert len(read(session, refs)['values']['fields']) == 41


def test_parent_reporting_withdrawal_preserves_membership_participation_and_private_director(guardian_data):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        legacy = session.scalar(select(DirectorPermission))
        guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose='reporting')
        session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert capability_decision(session, refs.profile_id, 'note-names').allowed
        assert session.scalar(select(ClassroomMembershipPeriod)).ended_at is None
        assert consent.permission_valid(session, legacy)
        assert session.scalar(select(ClassroomProgressAuthorization)).withdrawn_at is None


def test_no_director_role_or_email_shortcut_and_no_school_authority(guardian_data):
    refs = guardian_data
    outsider = TestClient(__import__('app.main', fromlist=['app']).app)
    assert outsider.post('/trusted-verifiers/login', data={'email':'adult2@example.test','pin':'2468'}).status_code == 200
    assert outsider.get('/classroom/parent').status_code == 403
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied):
            guardian.withdraw(session, parent=actor(session, 2), program_id=1,
                class_id=refs.class_id, purpose='participation')
        for evidence in ({'actor':actor(session, 2)}, {'email':'teacher@school.example.test'},
                         {'membership_id':refs.membership_id}, {'verified':True}):
            with pytest.raises(s1.ClassroomDenied): guardian.authorize_school(session, **evidence)
        assert session.scalar(select(func.count(Grant.id))) == 0


def history(refs):
    fully_authorize(refs)
    day = refs.clock[0].astimezone(CENTRAL).date()
    valid_day = day + timedelta(days=1)
    submitted = datetime.combine(valid_day, datetime.min.time(), CENTRAL).astimezone(timezone.utc) + timedelta(hours=18)
    with refs.factory() as session:
        session.add_all([
            PracticeChart(profile_id=refs.profile_id, practice_date=valid_day, minutes=17,
                instrument='Flute', created_at=submitted, note='PRIVATE_GUARDIAN_CHART_NOTE'),
            PracticeChart(profile_id=refs.profile_id, practice_date=day-timedelta(days=1), minutes=999,
                instrument='Flute', created_at=submitted, note='PRIVATE_HISTORICAL_OCCURRENCE'),
            PracticeChart(profile_id=refs.profile_id, practice_date=valid_day, minutes=999,
                instrument='Flute', created_at=refs.clock[0]-timedelta(seconds=1), note='PRIVATE_EARLY_SUBMISSION'),
        ])
        session.commit()
    refs.clock[0] = datetime.combine(day+timedelta(days=3), datetime.min.time(), CENTRAL).astimezone(timezone.utc)
    return valid_day


def refresh_parent(refs):
    return open_parent(refs.parent, refs.captured, refs.woodchuck_id)


def test_only_post_guardian_and_student_occurrence_and_submission_history_is_visible(guardian_data):
    refs = guardian_data
    valid_day = history(refs)
    with refs.factory() as session:
        data = read(session, refs)
        fields = data['values']['fields']
        assert fields['lifetime_practice_minutes'] == 17
        assert fields['lifetime_practice_charts'] == 1
        assert fields['last_p_chart_submission'].startswith(valid_day.isoformat())
        assert fields['current_practice_streak'] is None
        assert fields['rating_trend'] is None  # No unauthorized baseline zeros.
        assert all('PRIVATE_' not in str(value) for value in fields.values())
        assert session.get(AccountPrivacy, refs.profile_id).age_band == '13to17'
        assert session.get(AccountPrivacy, refs.profile_id).consent_id is not None


def test_regrant_requires_fresh_child_choice_and_never_backfills_history(guardian_data):
    refs = guardian_data
    history(refs); refresh_parent(refs)
    with refs.factory() as session:
        original = session.scalar(select(ClassroomProgressAuthorization))
        original_id = original.id
        guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose='reporting'); session.commit()
        parent_accept(session, refs, 'reporting'); session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        student_accept(session, refs); session.commit()
        fields = read(session, refs)['values']['fields']
        assert fields['lifetime_practice_minutes'] is None
        assert fields['last_p_chart_submission'] is None
        assert session.scalar(select(ClassroomProgressAuthorization).where(
            ClassroomProgressAuthorization.withdrawn_at.is_(None))).id != original_id
        assert session.scalar(select(func.count(PracticeChart.id))) == 3


@pytest.mark.parametrize('loss', ['leave', 'suspend', 'expire', 'replacement', 'archive'])
def test_source_loss_and_reversal_require_fresh_parent_and_student_choices(guardian_data, loss):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        old_ids = set(session.scalars(select(Grant.id)))
        refs.clock[0] += timedelta(seconds=2)
        if loss == 'leave':
            s2.leave_class(session, student=student(session), program_id=1, class_id=refs.class_id)
        elif loss == 'suspend':
            s2.suspend_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id)
        elif loss == 'expire':
            refs.clock[0] = utc(session.scalar(select(ClassroomEntitlement).where(
                ClassroomEntitlement.status == 'active')).ends_at)
        elif loss == 'replacement':
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
            s2.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, verifier_id=3)
        else:
            s2.set_class_state(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, state='archived')
        session.commit()
    refs.clock[0] += timedelta(minutes=6)  # Native parent-link resend interval.
    refresh_parent(refs)
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        # Withdrawal remains available during the source loss. Roll this one back
        # so restoration must reject the original, still-stored receipts as well.
        with session.begin_nested() as checkpoint:
            guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
                class_id=refs.class_id, purpose='reporting')
            assert session.scalar(select(Grant).where(Grant.purpose == 'reporting')).withdrawn_at
            checkpoint.rollback()
        refs.clock[0] += timedelta(seconds=2)
        if loss == 'leave':
            s2.join_class(session, student=student(session), program_id=1, code='BAND2026')
        elif loss == 'suspend':
            s2.reinstate_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id)
        elif loss == 'expire':
            institutional(session, starts_at=refs.clock[0], ends_at=refs.clock[0] + timedelta(days=30))
        elif loss == 'replacement':
            new_assignment = session.scalar(select(ClassroomTeachingAssignment).where(
                ClassroomTeachingAssignment.class_id == refs.class_id,
                ClassroomTeachingAssignment.verifier_id == 3))
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=new_assignment.id)
            s2.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, verifier_id=2)
        else:
            s2.set_class_state(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, state='active')
        session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        parent_accept(session, refs, 'participation'); session.commit()
        assert capability_decision(session, refs.profile_id, 'note-names').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        parent_accept(session, refs, 'reporting'); session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        student_accept(session, refs); session.commit()
        assert len(read(session, refs)['values']['fields']) == 41
        assert not old_ids.intersection(session.scalars(select(Grant.id).where(Grant.withdrawn_at.is_(None))))


def test_participation_withdrawal_preserves_membership_and_account_but_stops_reporting(guardian_data):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose='participation'); session.commit()
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        assert capability_decision(session, refs.profile_id, 'personal_progress_save').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert session.scalar(select(ClassroomMembershipPeriod)).ended_at is None
        assert session.scalar(select(ConsentEvidence)).withdrawn_at is None
        assert session.scalar(select(Grant).where(Grant.purpose == 'reporting')).withdrawn_at is None
        parent_accept(session, refs, 'participation'); session.commit()
        assert capability_decision(session, refs.profile_id, 'note-names').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)  # old report cannot revive


def test_participation_and_its_withdrawal_do_not_depend_on_reporting_gates(guardian_data, monkeypatch):
    refs = guardian_data
    with refs.factory() as session:
        parent_accept(session, refs, 'participation'); session.commit()
    monkeypatch.delenv('CLASSROOM_S3_ENABLED')
    monkeypatch.delenv('CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED')
    with refs.factory() as session:
        assert capability_decision(session, refs.profile_id, 'note-names').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose='participation'); session.commit()
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        assert capability_decision(session, refs.profile_id, 'personal_progress_save').allowed


@pytest.mark.parametrize('guardian_data', [{'under13':True}], indirect=True)
def test_native_parent_age_transition_keeps_consent_link_and_requires_fresh_class_choices(guardian_data):
    refs = guardian_data
    with refs.factory() as session:
        parent_accept(session, refs, 'participation'); session.commit()
        with pytest.raises(s1.ClassroomDenied): parent_accept(session, refs, 'reporting')
    refs.clock[0] += timedelta(minutes=1)
    with refs.factory() as session:
        parent_access.confirm_age(session, parent_request(refs), explicit='yes',
            version=parent_access.AGE_WORDING_VERSION)
        session.commit()
        rule = session.get(AccountPrivacy, refs.profile_id)
        assert rule.age_band == '13to17' and rule.consent_id is not None
        assert guardian.required(session, refs.profile_id)
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        parent_accept(session, refs, 'participation'); session.commit()
        parent_accept(session, refs, 'reporting'); session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        student_accept(session, refs); session.commit()
        assert len(read(session, refs)['values']['fields']) == 41
        consent.withdraw_evidence(session, session.get(ConsentEvidence, rule.consent_id))
        session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)


@pytest.mark.parametrize('evidence_failure', ['adult_only', 'binding', 'attestation', 'environment', 'notice', 'consent', 'receipt'])
def test_every_required_evidence_predicate_is_independently_required(guardian_data, evidence_failure):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        verification = session.scalar(select(KWSVerification))
        if evidence_failure == 'adult_only':
            verification.state = 'verified'
        elif evidence_failure == 'binding':
            verification.binding_sha256 = '0' * 64
        elif evidence_failure == 'attestation':
            from app.kws_verification import binding
            from app.child_models import PendingConsent
            verification.guardian_attested_at = utc(verification.completed_at) + timedelta(seconds=1)
            verification.binding_sha256 = binding(session.get(PendingConsent, verification.pending_id), verification)
        elif evidence_failure == 'environment':
            verification.environment = 'production'
        elif evidence_failure == 'notice':
            session.scalar(select(ConsentEvidence)).notice_sha256 = '0' * 64
        elif evidence_failure == 'consent':
            consent.withdraw_evidence(session, session.scalar(select(ConsentEvidence)))
        else:
            receipt = session.scalar(select(Audit).where(Audit.action == 'accepted'))
            session.delete(receipt)
        session.commit()
        statements = []
        def capture(_conn, _cursor, sql, *_args): statements.append(sql.lower())
        event.listen(refs.factory.engine, 'before_cursor_execute', capture)
        try:
            with pytest.raises(s1.ClassroomDenied): read(session, refs)
        finally:
            event.remove(refs.factory.engine, 'before_cursor_execute', capture)
        assert not any('from practice_charts' in sql or 'from weekly_plans' in sql for sql in statements)


def test_exact_class_and_program_consents_are_not_substitutable(guardian_data):
    refs = guardian_data
    fully_authorize(refs)
    scopes = []
    for program_id in (1, 2):
        with refs.factory() as session:
            if program_id == 2:
                institutional(session, program_id=2, starts_at=refs.clock[0],
                    ends_at=refs.clock[0] + timedelta(days=365)); session.commit()
            room = create(session, program_id=program_id, name='Other authorized Class')
            code(session, room.id, preferred='OTHER2026', program_id=program_id)
            s2.assign_teacher(session, actor=actor(session), program_id=program_id, class_id=room.id, verifier_id=2)
            s2.join_class(session, student=student(session), program_id=program_id, code='OTHER2026')
            session.commit(); scopes.append((program_id, room.id))
    for program_id, class_id in scopes:
        with refs.factory() as session:
            with pytest.raises(s1.ClassroomDenied): read(session, refs, program_id=program_id, class_id=class_id)
            assert guardian.choice(session, parent=parent_proof(session, refs), program_id=program_id,
                class_id=class_id, purpose='participation')['authorized'] is False


def test_parent_grants_do_not_expand_pchart_or_s3_boundary_authority(guardian_data):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        from app import classroom_chart_sharing
        with pytest.raises(s1.ClassroomDenied):
            boundary.authorize_reporting(session, actor=actor(session, 2), program_id=1,
                class_id=refs.class_id, profile_id=refs.profile_id)
        chart = PracticeChart(profile_id=refs.profile_id, practice_date=refs.clock[0].date(), minutes=3,
            instrument='Flute', created_at=refs.clock[0])
        session.add(chart); session.flush()
        with pytest.raises(s1.ClassroomDenied):
            classroom_chart_sharing.set_sharing(session, student=student(session), chart_id=chart.id,
                assignment_ids=[refs.assignment_id])
        assert consent.permission_valid(session, session.scalar(select(DirectorPermission)))


def test_guardian_roster_identity_stays_private_and_exact_class_scoped(guardian_data):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        from app.classroom_identity import set_school_name
        set_school_name(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id,
            woodchuck_id=refs.woodchuck_id, school_name='PRIVATE SCHOOL IDENTITY', expected_version=0)
        session.get(WoodchuckProfile, refs.profile_id).display_name = 'PUBLIC_NICKNAME_SENTINEL'
        session.commit()
        data = workspace.student_view(session, actor=actor(session, 2), program_id=1,
            class_id=refs.class_id, membership_id=refs.membership_id)
        encoded = json.dumps(data)
        assert 'PRIVATE SCHOOL IDENTITY' in encoded and refs.woodchuck_id in encoded
        assert 'PUBLIC_NICKNAME_SENTINEL' not in encoded
        for forbidden in ('consent_id', 'verification_id', 'guardian_attested_at', 'source_revision', 'parent_email'):
            assert forbidden not in encoded
        assert data['shared_pcharts']['students'] == []
        assert len(data['statistics']['values']['fields']) == 41


def test_c33_gate_off_masks_protected_identity_without_new_per_row_locks(guardian_data, monkeypatch):
    refs = guardian_data
    fully_authorize(refs)
    monkeypatch.delenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED')
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Protected identity must not add unordered account locks with C33 disabled.')
    monkeypatch.setattr(guardian, 'participation_allowed', forbidden)
    with refs.factory() as session:
        data = workspace.class_view(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id)
        assert data['admin']['roster'][0]['woodchuck_id'] is None
        assert data['admin']['roster'][0]['school_name'] is None


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('writer_first', [False, True])
@pytest.mark.parametrize('withdrawal', ['reporting', 'participation', 'account'])
def test_pg_parent_withdrawal_and_actual_report_reader_observe_lock_wait(guardian_data, writer_first, withdrawal):
    refs = guardian_data
    history(refs); refresh_parent(refs)
    def withdraw(session):
        if withdrawal == 'account':
            evidence, _, _ = parent_access.authenticated(session, parent_request(refs), lock=True)
            return consent.withdraw_evidence(session, evidence).id
        return guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose=withdrawal).id
    def observe(session):
        cached = session.scalar(select(Grant).where(Grant.purpose == 'reporting'))
        assert cached.withdrawn_at is None
        return read(session, refs)['values']['fields']['lifetime_practice_minutes']
    operations = (withdraw, observe) if writer_first else (observe, withdraw)
    results = ordered_wait(refs.factory, *operations)
    assert tuple(row[0] for row in results) == (('ok', 'denied') if writer_first else ('ok', 'ok'))
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert session.scalar(select(func.count(PracticeChart.id))) == 3


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_pg_rolled_back_parent_withdrawal_never_revokes_actual_reader(guardian_data):
    refs = guardian_data
    history(refs); refresh_parent(refs)
    results = ordered_wait(refs.factory,
        lambda session: guardian.withdraw(session, parent=parent_proof(session, refs),
            program_id=1, class_id=refs.class_id, purpose='reporting').id,
        lambda session: read(session, refs)['values']['fields']['lifetime_practice_minutes'],
        rollback_first=True)
    assert tuple(row[0] for row in results) == ('ok', 'ok')
    assert results[1][1] == 17


def parent_form(refs, purpose):
    page = refs.parent.get('/classroom/parent')
    assert page.status_code == 200, page.text
    room = next(row for row in page.context['classes'] if row['class_id'] == refs.class_id)
    item = next(row for row in room['choices'] if row['purpose'] == purpose)
    return {'csrf': page.context['csrf'], 'notice_token': item['token'],
            'guardian_attestation': 'yes', 'opt_in': 'yes'}


def parent_url(refs, purpose, action='authorize'):
    return f'/classroom/parent/1/classes/{refs.class_id}/{purpose}/{action}'


def test_http_choices_are_explicit_session_bound_and_replay_cannot_undo_withdrawal(guardian_data):
    refs = guardian_data
    data = parent_form(refs, 'participation')
    before = refs.parent.get('/classroom/parent')
    assert 'checked' not in before.text
    assert all(set(choice['choice']) == {'eligible','authorized','saved','under13_reporting'}
        for room in before.context['classes'] for choice in room['choices'])
    for patch, status in [({'guardian_attestation':'no'},409), ({'opt_in':'no'},409),
                          ({'notice_token':'forged'},409), ({'csrf':'bad'},403),
                          ({'fields':'private_notes'},400), ({'expires_at':'2099-01-01'},400),
                          ({'school_email':'director@school.example.test'},400)]:
        result = refs.parent.post(parent_url(refs,'participation'),data={**data,**patch},follow_redirects=False)
        assert result.status_code == status,result.text
    assert refs.parent.post(parent_url(refs,'school'),data=data,follow_redirects=False).status_code == 409
    assert refs.parent.post(parent_url(refs,'participation'),data=data,
        headers={'Origin':'https://foreign.example.test'},follow_redirects=False).status_code == 403
    assert refs.parent.post(parent_url(refs,'participation'),json=data).status_code == 415
    assert refs.parent.post(parent_url(refs,'participation'),data=data,follow_redirects=False).status_code == 303
    page = refs.parent.get('/classroom/parent')
    assert refs.parent.post(parent_url(refs,'participation','withdraw'),
        data={'csrf':page.context['csrf'],'confirm':'yes'},follow_redirects=False).status_code == 303
    assert refs.parent.post(parent_url(refs,'participation'),data=data,follow_redirects=False).status_code == 409
    with refs.factory() as session:
        assert session.scalar(select(func.count(Grant.id))) == 1
        assert session.scalar(select(Grant)).withdrawn_at is not None


def test_parent_session_expiry_or_other_adult_identity_never_grants_permission(guardian_data):
    refs = guardian_data
    data = parent_form(refs, 'participation')
    refs.clock[0] += timedelta(minutes=31)
    assert refs.parent.post(parent_url(refs,'participation'),data=data,follow_redirects=False).status_code == 403
    refresh_parent(refs)
    assert refs.parent.post(parent_url(refs,'participation'),data=data,follow_redirects=False).status_code == 409
    # An actual verifier with the parent's exact email still lacks private parent authentication.
    with refs.factory() as session:
        evidence = session.scalar(select(ConsentEvidence))
        session.add(TrustedVerifier(email=evidence.parent_email, display_name='Parent email is not authority',
            pin_hash=hash_pin('2468'))); session.commit()
    from app.main import app
    outsider = TestClient(app)
    assert outsider.post('/trusted-verifiers/login',data={'email':evidence.parent_email,'pin':'2468'}).status_code == 200
    assert outsider.get('/classroom/parent').status_code == 403
    with refs.factory() as session:
        assert session.scalar(select(func.count(Grant.id))) == 0


def test_entitlement_expiry_and_gate_loss_fail_closed_without_measurements(guardian_data, monkeypatch):
    refs = guardian_data
    fully_authorize(refs)
    with refs.factory() as session:
        package = session.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.status == 'active'))
        ends_at = utc(package.ends_at)
        monkeypatch.delenv('CLASSROOM_GUARDIAN_AUTHORIZATION_ENABLED')
        assert refs.parent.get('/classroom/parent').status_code == 404
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        monkeypatch.setenv('CLASSROOM_GUARDIAN_AUTHORIZATION_ENABLED','1')
        refs.clock[0] = ends_at
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert not capability_decision(session, refs.profile_id, 'note-names').allowed


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_pg_current_parent_session_rechecked_after_program_wait(guardian_data):
    refs = guardian_data
    # Capture an actual old-session form, not a fabricated approval capability.
    data = parent_form(refs, 'participation')
    from app.classroom_guardian_routes import _signer
    presented = _signer().loads(data['notice_token'])
    def rotate_session(session):
        s1._lock_program(session, 1)
        # Exercise the native server-side link rotation and consumption.
        refs.clock[0] += timedelta(minutes=6)
        parent_access.request_link(session, woodchuck_id=refs.woodchuck_id, email='parent-a@example.test')
        token = path(refs.captured[0], 'parent-access').rsplit('/',1)[-1]
        parent_access.consume_link(session, token)
        return 'native parent credential rotated'
    def accept_old_form(session):
        # Authenticate before waiting, so the service must refresh the credential.
        old = parent_proof(session, refs)
        return guardian.accept(session, parent=old, program_id=1, class_id=refs.class_id,
            purpose='participation', notice_version=presented[6], notice_digest=presented[7],
            guardian_attested=True, opted_in=True, previous=tuple(presented[8]),
            source_revision=presented[9], assignment_ids=tuple(presented[10]), participation_id=presented[11]).id
    results = ordered_wait(refs.factory, rotate_session, accept_old_form)
    assert tuple(row[0] for row in results) == ('ok','denied')
