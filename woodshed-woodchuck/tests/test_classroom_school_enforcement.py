"""Phil's C35 matrix: positive controls before every school-denial assertion.

All legal acceptance is isolated monkeypatch data. Production allowlists remain
empty. The same TestClient and the same SQLAlchemy Session survive revocation.
"""
from datetime import timedelta
from types import SimpleNamespace
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import (classroom as s1, classroom_s2 as s2, classroom_reporting as boundary,
    classroom_school_authorization as school, classroom_progress_authorization as progress,
    classroom_comprehensive_reporting as reports, classroom_chart_sharing as charts,
    classroom_identity as identity, classroom_management_views as management,
    classroom_enrollment as enrollment, classroom_enrollment_views as offers,
    classroom_student_invitations as chart_invites, main)
from app.age_models import AccountPrivacy
from app.classroom_school_models import ClassroomSchoolAuthorization as Approval
from app.classroom_models import ClassroomProgram, ClassroomStudentMembership, ClassroomTeachingAssignment
from app.models import Organization, PracticeChart, StudentVerifierConnection
from test_classroom_s1 import classroom_db, adult_pin_hash, actor
from test_classroom_s2 import s2_db, NOW, institutional, create, code, join, student
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2_postgres import s2_pg
from test_classroom_progress_authorization import accept
from school_approval_fixture import approve_programs


OPERATIONS = ('roster', 'identity', 'reporting', 'pchart', 'invitation')
HTTP_OPERATIONS = OPERATIONS + ('dashboard_roster', 'management_setup', 'invitation_list',
    'chart_invitation_list', 'chart_invitation_issue', 'invitation_revoke',
    'chart_invitation_revoke', 'membership_suspend', 'membership_remove', 'membership_reinstate')
NAME = 'PRIVATE_ROSTER_SENTINEL'
NOTE = 'EXPLICIT_CHART_SENTINEL'
HIDDEN = 'UNSELECTED_PRIVATE_SENTINEL'


@pytest.fixture(params=['sqlite', 'postgresql'])
def matrix_db(request, monkeypatch):
    factory = request.getfixturevalue('s2_db' if request.param == 'sqlite' else 's2_pg')
    for flag in ('CLASSROOM_S3_ENABLED', 'CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED',
                 'CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_ENROLLMENT_V2_ENABLED',
                 'CLASSROOM_PRIVATE_IDENTITY_ENABLED'):
        monkeypatch.setenv(flag, '1')
    clock = [NOW]
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    monkeypatch.setattr(boundary, '_now', lambda: clock[0])
    with factory() as session:
        if session.get(ClassroomProgram, 2) is None:
            session.add(Organization(id=2, name='Second synthetic Program', organization_type='music'))
            session.flush()
            s1.provision_program(session, actor=actor(session), organization_id=2, owner=actor(session))
            session.commit()
    approved = approve_programs(factory, monkeypatch)
    with factory() as session:
        institutional(session)
        room = create(session)
        code(session, room.id)
        member = join(session)
        teacher = s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=room.id, verifier_id=2)
        refs = SimpleNamespace(factory=factory, class_id=room.id, membership_id=member.id,
            assignment_id=teacher.id, clock=clock, approval_id=approved[1], profile_id=1)
        session.commit()
        accept(session, refs)
        identity.set_school_name(session, actor=actor(session, 2), program_id=1, class_id=room.id,
            woodchuck_id='WC-SYNTHETIC', school_name=NAME, expected_version=0)
        selected = PracticeChart(profile_id=1, practice_date=NOW.date()+timedelta(days=1),
            created_at=NOW+timedelta(days=2), minutes=17, instrument='Trumpet', note=NOTE)
        hidden = PracticeChart(profile_id=1, practice_date=NOW.date()+timedelta(days=1),
            created_at=NOW+timedelta(days=2), minutes=999, instrument='Trumpet', note=HIDDEN)
        session.add_all([selected, hidden]); session.commit()
        refs.chart_id, refs.hidden_id = selected.id, hidden.id
    clock[0] += timedelta(days=10)
    with factory() as session:
        charts.set_sharing(session, student=student(session), chart_id=refs.chart_id,
            assignment_ids=[refs.assignment_id])
        chart_invites.issue(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id,
            assignment_id=refs.assignment_id, profile_id=1)
        enrollment.issue_invitation(session, actor=actor(session, 2), program_id=1,
            class_id=refs.class_id, woodchuck_id='WC-SYNTHETIC')
        session.commit()
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    return refs


def invoke(session, refs, operation, adult=2):
    proof = actor(session, adult)
    args = dict(actor=proof, program_id=1, class_id=refs.class_id)
    if operation == 'roster':
        return management.detail(session, **args, section='roster')['selected_class']['roster']
    if operation == 'identity':
        return identity.set_school_name(session, **args, woodchuck_id='WC-SYNTHETIC',
            school_name=NAME, expected_version=1)
    if operation == 'reporting':
        return reports.report(session, **args, profile_id=1)['values']
    if operation == 'pchart':
        return charts.shared_charts(session, **args)
    return enrollment.issue_invitation(session, **args, woodchuck_id='WC-SYNTHETIC')


def revoke(refs):
    with refs.factory() as session:
        row = session.get(Approval, refs.approval_id)
        school.revoke(session, actor=actor(session), program_id=1, record_id=row.id,
            previous=school.revision(row), reason='authority_revoked')
        session.commit()


def signed(adult=2):
    client = TestClient(main.app)
    assert client.post('/trusted-verifiers/login', data={
        'email': f'adult{adult}@example.test', 'pin': '2468'}).status_code == 200
    return client


def request_spec(client, refs, operation):
    room = f'1/classes/{refs.class_id}'
    path = f'/classroom/programs/{room}'
    fields = dict(director='2', pin='2468')
    if operation.startswith('membership_'):
        with refs.factory() as session:
            version = session.get(ClassroomProgram, 1).owner_version
        fields.update(director='1', action=operation.removeprefix('membership_'),
            stage='review', owner_version=str(version), membership_id=str(refs.membership_id))
    elif operation in {'roster', 'management_setup'}:
        fields.update(action='view', before='', section='roster' if operation == 'roster' else 'classes')
    elif operation == 'identity':
        path += '/identity'
        fields.update(woodchuck_id='WC-SYNTHETIC', school_name=NAME, expected_version='1')
    elif operation in {'reporting', 'dashboard_roster'}:
        path = f'/classroom/dashboard/{room}'
        if operation == 'reporting':
            path += f'/students/{refs.membership_id}'
        else:
            fields['before'] = ''
    elif operation == 'pchart':
        path = f'/classroom/director/{room}'
        fields = dict(pin='2468')
    elif operation in {'invitation', 'invitation_list', 'invitation_revoke'}:
        path = f'/classroom/enrollment/director/{room}'
        if operation == 'invitation_revoke':
            from app.classroom_enrollment_models import ClassroomEnrollmentInvitation
            with refs.factory() as session:
                ident = session.scalar(select(ClassroomEnrollmentInvitation.id).where(
                    ClassroomEnrollmentInvitation.program_id == 1))
            fields.update(action='revoke', invitation_id=str(ident))
        elif operation == 'invitation':
            fields.update(action='issue', woodchuck_id='WC-SYNTHETIC')
        else:
            fields.update(action='view', before='')
    else:
        path = f'/classroom/director/{room}/invitations'
        fields = dict(pin='2468', action='view')
        if operation == 'chart_invitation_revoke':
            from app.classroom_models import ClassroomStudentInvitation
            with refs.factory() as session:
                ident = session.scalar(select(ClassroomStudentInvitation.id).where(
                    ClassroomStudentInvitation.program_id == 1))
            fields.update(action='revoke', invitation_id=str(ident))
        if operation == 'chart_invitation_issue':
            from app.classroom_models import ClassroomStudentInvitation
            with refs.factory() as session:
                for row in session.scalars(select(ClassroomStudentInvitation).where(
                        ClassroomStudentInvitation.program_id == 1,
                        ClassroomStudentInvitation.revoked_at.is_(None))):
                    chart_invites.revoke(session, actor=actor(session, 2), invitation_id=row.id)
                session.commit()
                token = chart_invites.prepare_identity(session, student=student(session))
            fields.update(action='issue', identity_code=token)
    page = client.get(path.removesuffix('/identity'))
    assert page.status_code == 200
    return path, dict(csrf=page.context['csrf'], **fields)


def assert_positive(response, operation):
    assert response.status_code == 200, response.text
    if operation.startswith('membership_'):
        target = response.context['confirmation']['target']
        assert target.startswith('Membership reference ') and target in response.text
    elif operation == 'invitation_revoke':
        assert 'Invitation revoked.' in response.text
    elif operation == 'chart_invitation_revoke':
        assert ' revoked.' in response.text
    elif operation in {'roster', 'identity', 'dashboard_roster', 'management_setup'}:
        assert NAME in response.text and 'WC-SYNTHETIC' in response.text
    elif operation == 'reporting':
        assert response.context['data']['statistics']['values'] is not None
        assert response.context['data']['statistics']['values']['fields']['lifetime_practice_minutes'] == 1016
    elif operation == 'pchart':
        assert NOTE in response.text and HIDDEN not in response.text
    elif operation in {'chart_invitation_list', 'chart_invitation_issue'}:
        assert response.context['invitations']
        if operation == 'chart_invitation_issue': assert ' created.' in response.text
    else:
        assert 'WC-SYNTHETIC' in response.text


def assert_withheld(response):
    assert response.status_code in {200, 404, 409}, response.text
    assert NAME not in response.text and NOTE not in response.text
    assert 'WC-SYNTHETIC' not in response.text and HIDDEN not in response.text
    assert 'data-membership=' not in response.text
    assert 'Membership reference ' not in response.text


@pytest.mark.parametrize('operation', OPERATIONS)
def test_same_sqlalchemy_session_revocation_all_five(matrix_db, operation):
    refs = matrix_db
    with refs.factory() as session:
        cached = session.get(Approval, refs.approval_id)
        assert invoke(session, refs, operation)
        session.commit()
        revoke(refs)
        assert cached.revoked_at is None  # Deliberately retained stale identity-map object.
        with pytest.raises(s1.ClassroomDenied):
            invoke(session, refs, operation)


@pytest.mark.parametrize('operation', HTTP_OPERATIONS)
def test_same_browser_session_identical_request_after_revocation(matrix_db, operation):
    refs = matrix_db
    adult = 1 if operation.startswith('membership_') else 2
    client = signed(adult)
    path, data = request_spec(client, refs, operation)
    assert_positive(client.post(path, data=data), operation)
    revoke(refs)
    assert_withheld(client.post(path, data=data))
    assert client.get('/trusted-verifiers/me').json()['verifier']['id'] == adult


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('loss', ['shipped', 'missing', 'expired', 'future', 'wrong_program',
    'version', 'digest', 'allowlist_version', 'allowlist_digest', 'forged_evidence', 'school_gate'])
def test_each_school_failure_denies_every_protected_operation(matrix_db, monkeypatch, operation, loss):
    refs = matrix_db
    with refs.factory() as session:
        assert invoke(session, refs, operation)
        session.commit()
        row = session.get(Approval, refs.approval_id)
        if loss == 'shipped':
            monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS', frozenset())
        elif loss in {'missing', 'wrong_program'}:
            # A separate valid Program-2 agreement cannot authorize Program 1.
            session.delete(row)
        elif loss == 'expired':
            refs.clock[0] = school.year_bounds(row.year_start)[1]
        elif loss == 'future':
            row.year_start += 1
            row.expires_at = school.year_bounds(row.year_start)[1]
            row.evidence_digest = school._evidence_digest(row)
        elif loss == 'version':
            row.agreement_version = 'unapproved-school-version'
        elif loss == 'digest':
            row.agreement_digest = '0'*64
        elif loss == 'allowlist_version':
            monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS',
                frozenset({('unapproved-version', school.AGREEMENT_DIGEST)}))
        elif loss == 'allowlist_digest':
            monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS',
                frozenset({(school.AGREEMENT_VERSION, '0'*64)}))
        elif loss == 'forged_evidence':
            row.school_name = 'Unverified substitute school'
        else:
            monkeypatch.delenv('CLASSROOM_SCHOOL_AUTHORIZATION_ENABLED')
        session.commit()
        if loss == 'wrong_program':
            with refs.factory() as other:
                assert school.authorized_program(other, program_id=2)
        with pytest.raises(s1.ClassroomDenied):
            invoke(session, refs, operation)


@pytest.mark.parametrize('operation', HTTP_OPERATIONS)
def test_shipped_allowlist_denies_every_http_surface(matrix_db, monkeypatch, operation):
    client = signed(1 if operation.startswith('membership_') else 2)
    path, data = request_spec(client, matrix_db, operation)
    assert_positive(client.post(path, data=data), operation)
    monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS', frozenset())
    assert_withheld(client.post(path, data=data))


@pytest.mark.parametrize('action', ['suspend', 'remove', 'reinstate'])
@pytest.mark.parametrize('loss', ['revoke', 'shipped'])
def test_direct_membership_services_recheck_school(matrix_db, monkeypatch, action, loss):
    refs = matrix_db
    with refs.factory() as session:
        if action == 'reinstate':
            s2.suspend_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id, reason='conduct')
            session.commit()
        def request():
            args = dict(actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id)
            if action == 'reinstate':
                return s2.reinstate_member(session, **args)
            return s2.suspend_member(session, **args, reason='conduct', remove=action == 'remove')
        assert request() is not None
        session.rollback()  # Positive control must not change the repeated operation.
        if loss == 'revoke':
            revoke(refs)
        else:
            monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS', frozenset())
        with pytest.raises(s1.ClassroomDenied):
            request()


@pytest.mark.parametrize('operation', OPERATIONS)
def test_school_never_substitutes_for_director_class_or_student(matrix_db, operation):
    refs = matrix_db
    with refs.factory() as session:
        assert invoke(session, refs, operation)
        session.commit()
        with pytest.raises(s1.ClassroomDenied):
            invoke(session, refs, operation, adult=3)
        session.commit()
        session.get(AccountPrivacy, 1).age_band = 'unknown'
        session.commit()
        result = None
        try:
            result = invoke(session, refs, operation)
        except s1.ClassroomDenied:
            pass
        if operation == 'roster':
            assert result and result[0]['woodchuck_id'] is None and result[0]['school_name'] is None
        elif operation == 'pchart':
            assert result == []
        else:
            assert result is None


def test_reporting_and_chart_permissions_remain_independent_and_fields_deny_before_sql(matrix_db):
    refs = matrix_db
    with refs.factory() as session:
        assert invoke(session, refs, 'reporting') and invoke(session, refs, 'pchart')
        session.commit()
        statements = []
        engine = session.get_bind()
        def capture(_c, _u, sql, *_args): statements.append(sql.lower())
        event.listen(engine, 'before_cursor_execute', capture)
        try:
            with pytest.raises(s1.ClassroomDenied):
                reports.report(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id,
                    profile_id=1, fields=('note',))
        finally:
            event.remove(engine, 'before_cursor_execute', capture)
        assert not any('from practice_charts' in sql for sql in statements)
        progress.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)
        session.commit()
        with pytest.raises(s1.ClassroomDenied): invoke(session, refs, 'reporting')
        assert invoke(session, refs, 'pchart')
        charts.set_sharing(session, student=student(session), chart_id=refs.chart_id, assignment_ids=[])
        session.commit()
        assert invoke(session, refs, 'pchart') == []


def test_preapproval_setup_has_no_membership_query_or_signal_and_lookup_is_uniform(matrix_db, monkeypatch):
    refs = matrix_db
    monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS', frozenset())
    client = signed(1)
    path = f'/classroom/programs/1/classes/{refs.class_id}'
    page = client.get(path)
    data = dict(csrf=page.context['csrf'], director='1', pin='2468', action='view', before='', section='classes')
    statements = []
    engine = refs.factory.kw['bind']
    def capture(_c, _u, sql, *_args): statements.append(sql.lower())
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.post(path, data=data)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert response.status_code == 200
    assert_withheld(response)
    assert 'Student access unavailable' in response.text
    assert 'roster-page-pin' not in response.text
    assert response.context['detail']['selected_class']['next_roster'] is None
    private_tables = ('classroom_student_memberships', 'classroom_membership_periods',
        'classroom_membership_holds', 'classroom_roster_identities', 'woodchuck_profiles',
        'classroom_enrollment_invitations', 'classroom_student_invitations', 'practice_charts')
    assert not any(table in sql for table in private_tables for sql in statements)
    with refs.factory() as session:
        made = s2.create_class(session, actor=actor(session), program_id=1, display_name='Preapproval setup')
        session.commit()
        assert made.id != refs.class_id
    director = signed()
    invite_path, form = request_spec(director, refs, 'invitation')
    existing = director.post(invite_path, data=form)
    absent = director.post(invite_path, data={**form, 'woodchuck_id': 'WC-NONEXISTENT'})
    assert existing.status_code == absent.status_code
    assert existing.content == absent.content
    identity_path, form = request_spec(director, refs, 'identity')
    existing = director.post(identity_path, data=form)
    absent = director.post(identity_path, data={**form, 'woodchuck_id': 'WC-NONEXISTENT'})
    assert existing.status_code == absent.status_code and existing.content == absent.content
    # A fresh Program can be configured and verified while legal approval stays
    # closed; these are adult/organizational operations, not student lookups.
    from test_classroom_s2 import admin
    with refs.factory() as session:
        session.add(Organization(id=3, name='Preapproval Program', organization_type='music'))
        session.flush()
        s1.provision_program(session, actor=actor(session), organization_id=3, owner=actor(session))
        session.commit()
        institutional(session, program_id=3)
        session.commit()
        room = create(session, program_id=3)
        session.commit()
        row = school.verify_authority(session, admin=admin(), reviewer=actor(session, 4),
            program_id=3, approver_id=1, authority_kind='administrator', school_name='Preapproval school',
            authority_description='Synthetic independent Program authority', contact_name='Official contact',
            contact_role='Principal', contact_address='+1 555 010 9999',
            contact_source='https://school.example.test/directory', verification_method='phone',
            verification_notes='Synthetic documented call', contacted_at=refs.clock[0]-timedelta(minutes=1),
            year_start=school.school_year(refs.clock[0]), independently_confirmed=True,
            previous=(None, None, None, None))
        school.approve(session, actor=actor(session), program_id=3, record_id=row.id,
            previous=school.revision(row), agreement_version=school.AGREEMENT_VERSION,
            agreement_digest=school.AGREEMENT_DIGEST, explicit=True)
        session.commit()
        assert room.program_id == 3
        assert school.status(row, refs.clock[0]) == 'legal_review_pending'
        assert not school.authorized_program(session, program_id=3)


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('matrix_db', ['postgresql'], indirect=True)
def test_postgres_revocation_read_observed_wait(matrix_db, operation):
    refs = matrix_db
    with refs.factory() as session:
        assert invoke(session, refs, operation)
        session.commit()
    def writer(session):
        row = session.get(Approval, refs.approval_id)
        school.revoke(session, actor=actor(session), program_id=1, record_id=row.id,
            previous=school.revision(row), reason='authority_revoked')
    outcome = ordered_wait(refs.factory, writer, lambda session: invoke(session, refs, operation))
    assert [row[0] for row in outcome] == ['ok', 'denied']


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('matrix_db', ['postgresql'], indirect=True)
def test_school_expiry_during_account_lock_wait(matrix_db, operation):
    from app.models import WoodchuckProfile
    refs = matrix_db
    with refs.factory() as session:
        assert invoke(session, refs, operation)
        session.commit()
        end = school.year_bounds(session.get(Approval, refs.approval_id).year_start)[1]
    def writer(session):
        session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == 1).with_for_update())
        # ordered_wait releases this commit only after observing the reader's
        # actual pg_blocking_pids wait. Approval expires while it is waiting.
        event.listen(session, 'before_commit', lambda _: refs.clock.__setitem__(0, end), once=True)
    outcome = ordered_wait(refs.factory, writer, lambda session: invoke(session, refs, operation))
    assert [row[0] for row in outcome] == ['ok', 'denied']


@pytest.mark.parametrize('operation', OPERATIONS)
def test_valid_school_cannot_substitute_for_class_scope(matrix_db, operation):
    client = signed()
    path, data = request_spec(client, matrix_db, operation)
    assert_positive(client.post(path, data=data), operation)
    path = path.replace(f'/classes/{matrix_db.class_id}', '/classes/2147483000')
    assert_withheld(client.post(path, data=data))


@pytest.mark.parametrize('surface', ['view', 'issue'])
def test_invitation_response_cannot_redisclose_an_ineligible_account(matrix_db, surface):
    client = signed()
    path, form = request_spec(client, matrix_db, 'invitation' if surface == 'issue' else 'invitation_list')
    assert_positive(client.post(path, data=form), 'invitation' if surface == 'issue' else 'invitation_list')
    with matrix_db.factory() as session:
        session.get(AccountPrivacy, 1).age_band = 'unknown'
        session.commit()
    result = client.post(path, data=form)
    assert result.status_code in {200, 409}
    assert 'WC-SYNTHETIC' not in result.text


@pytest.mark.parametrize('loss', ['connection', 'account', 'wrong_verifier', 'review_complete'])
def test_same_adult_private_verifier_route_is_independent_but_only_selected_chart(matrix_db, loss):
    from datetime import datetime
    from app import verifiers
    from app.models import WoodchuckProfile, PracticeChartVerification
    from app.practice_charts import create_practice_chart_verification_request
    refs = matrix_db
    with refs.factory() as session:
        profile = s2._student(session, student(session))
        offer = verifiers.create_trusted_verifier_invitation(session, profile=profile,
            email='adult2@example.test', role='verifier')
        accepted = verifiers.accept_trusted_verifier_invitation(session, token=offer.token,
            display_name='Existing private verifier', pin='2468')
        assert accepted.verifier.id == 2
        selected = create_practice_chart_verification_request(session, profile=session.get(WoodchuckProfile, 1),
            verifier_id=2, practice_date=datetime.now(boundary.CENTRAL).date(), minutes=9, note='PRIVATE_VERIFIER_SELECTED',
            include_contests=False, include_team_contests=False, submission_key='school-independent-private-review')
        session.commit()
        connection_id, review_id, chart_id = accepted.connection.id, selected.verification.id, selected.chart.id
    client = signed(2)
    school_path, form = request_spec(client, refs, 'pchart')
    assert_positive(client.post(school_path, data=form), 'pchart')
    private_path = f'/trusted-verifiers/practice-charts?connection_id={connection_id}'
    def private_read():
        result = client.get(private_path)
        assert result.status_code == 200, result.text
        values = result.json()['pending_charts']
        assert [row['chart']['id'] for row in values] == [chart_id]
        assert HIDDEN not in result.text and NAME not in result.text and NOTE not in result.text
        return result
    private_read()
    revoke(refs)
    assert_withheld(client.post(school_path, data=form))
    private_read()  # Same adult, same browser session; no school permission reuse.
    with refs.factory() as session:
        if loss == 'connection': session.get(StudentVerifierConnection, connection_id).status = 'disconnected'
        elif loss == 'account': session.get(AccountPrivacy, 1).age_band = 'unknown'
        elif loss == 'wrong_verifier': session.get(PracticeChartVerification, review_id).verifier_id = 3
        else: session.get(PracticeChartVerification, review_id).status = 'approved'
        session.commit()
    result = client.get(private_path)
    assert result.status_code == 404 or result.json()['pending_charts'] == []


def test_shipped_constants_and_default_gates_are_closed_in_fresh_process():
    import ast
    from pathlib import Path
    import subprocess
    import os
    for name, constant in [('classroom_school_authorization.py', 'LEGALLY_APPROVED_AGREEMENTS'),
                           ('classroom_chart_consent.py', 'LEGALLY_APPROVED_POLICIES')]:
        tree = ast.parse((Path(__file__).parents[1]/'app'/name).read_text())
        value = next(n.value for n in tree.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == constant for t in n.targets))
        assert isinstance(value, ast.Call) and value.func.id == 'frozenset' and not value.args and not value.keywords
    clean = {key: value for key, value in os.environ.items() if not key.startswith('CLASSROOM_')}
    result = subprocess.run([sys.executable, '-B', '-c',
        'from app import classroom_school_authorization as s, classroom_chart_consent as c; '
        'assert not s.LEGALLY_APPROVED_AGREEMENTS and not c.LEGALLY_APPROVED_POLICIES; assert not s.enabled()'],
        env=clean, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
