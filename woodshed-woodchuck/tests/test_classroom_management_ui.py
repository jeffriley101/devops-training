"""Actual management HTTP flows; unchanged S1/S2 authority, synthetic local data."""
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import (classroom as s1, classroom_s2 as s2, classroom_management_routes as routes,
    classroom_management_views as views, classroom_invitation_routes, classroom_invitation, main)
from app.age_models import AccountPrivacy
from app.classroom_models import (ClassroomProgram, ClassroomRoleGrant, ClassroomClass,
    ClassroomClassState, ClassroomTeachingAssignment, ClassroomStudentMembership,
    ClassroomMembershipPeriod, ClassroomMembershipHold, ClassroomEntitlement,
    ClassroomEntryCode, ClassroomAuditEvent, ClassroomS2AuditEvent)
from app.models import Organization, WoodchuckProfile, PracticeChart, TrustedVerifier
from test_classroom_s1 import actor, adult_pin_hash, classroom_db, count
from test_classroom_s2 import s2_db, NOW, institutional
from test_classroom_enrollment_ui import enrollment_web, director_client, student_client, protected_counts

PATH = routes.PATH


@pytest.fixture
def management_web(enrollment_web, monkeypatch):
    refs = enrollment_web
    for module in (routes, classroom_invitation_routes):
        monkeypatch.setattr(module, 'SessionLocal', refs.factory)
    with refs.factory() as session:
        proposal = s1.propose_transfer(session, actor=actor(session), program_id=2, recipient_id=3)
        s1.accept_transfer(session, actor=actor(session, 3), program_id=2, transfer_id=proposal.id)
        old_head = session.scalar(select(ClassroomRoleGrant).where(ClassroomRoleGrant.program_id == 2,
            ClassroomRoleGrant.verifier_id == 1, ClassroomRoleGrant.role == 'head_director'))
        s1.revoke_role(session, actor=actor(session, 3), program_id=2, grant_id=old_head.id)
        s1.grant_role(session, actor=actor(session, 3), program_id=2, verifier_id=3, role='head_director')
        session.commit()
        for ident, class_code in [(1, 'BAND2026'), (2, 'OTHER2026')]:
            s2.join_class(session, student=s2.authenticate_student(session, woodchuck_id=f'WC-TEST000{ident}', pin='2468'), program_id=1, code=class_code)
            session.get(WoodchuckProfile, ident).display_name = f'PRIVATE_PUBLIC_ALIAS_{ident}'
        session.add(PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=7,
            instrument='Trumpet', note='PRIVATE_SENTINEL_CHART'))
        session.commit()
        session.add(Organization(id=3, name='Invited Music Program', organization_type='music'))
        session.commit()
        refs.member_id = session.scalar(select(ClassroomStudentMembership.id).where(
            ClassroomStudentMembership.class_id == refs.class_id, ClassroomStudentMembership.profile_id == 1))
        refs.other_member_id = session.scalar(select(ClassroomStudentMembership.id).where(
            ClassroomStudentMembership.class_id == refs.other_id, ClassroomStudentMembership.profile_id == 2))
    refs.program_path = PATH + '/1'
    refs.class_path = refs.program_path + f'/classes/{refs.class_id}'
    return refs


def signed(ident=1):
    client = director_client(ident)
    client.management_actor = ident
    return client


def view(client, path, *, before='', section='classes'):
    page = client.get(path)
    return client.post(path, data={'csrf': page.context['csrf'], 'director': str(client.management_actor),
        'pin': '2468', 'action': 'view', 'before': str(before), 'section': section})


def catalog(client, before=''):
    page = client.get(PATH)
    return client.post(PATH, data={'csrf': page.context['csrf'], 'director': str(client.management_actor),
        'pin': '2468', 'before': str(before)})


def preview(client, path, action, *, owner_version=1, **payload):
    page = client.get(path)
    return client.post(path, data={'csrf': page.context['csrf'], 'director': str(client.management_actor),
        'pin': '2468', 'action': action, 'stage': 'review', 'owner_version': str(owner_version), **payload})


def apply(client, path, page):
    confirmation = page.context['confirmation']
    return client.post(path, data={'csrf': page.context['csrf'], 'director': str(client.management_actor),
        'pin': '2468', 'action': confirmation['action'], 'stage': 'apply',
        'owner_version': confirmation['owner_version'], 'confirm_change': 'yes', **confirmation['payload']})


def change(client, path, action, **payload):
    prepared = preview(client, path, action, **payload)
    assert prepared.status_code == 200, prepared.text[:300]
    assert prepared.context['confirmation']
    return apply(client, path, prepared)


@pytest.mark.parametrize('flag', ['CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED', 'CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_ENROLLMENT_V2_ENABLED'])
def test_dependency_gates_no_management_queries(management_web, monkeypatch, flag):
    client = signed()
    monkeypatch.delenv(flag)
    statements = []
    def record(conn, cursor, statement, params, context, many): statements.append(statement.lower())
    engine = management_web.factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', record)
    try:
        for path in [PATH, management_web.program_path, management_web.class_path]:
            assert client.get(path).status_code == client.post(path, data={}).status_code == 404
        assert not any('classroom_' in sql for sql in statements)
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert PATH not in client.get('/trusted-verifiers/dashboard').text


def test_gets_reauthenticate_and_roster_is_exact_minimal_projection(management_web):
    refs = management_web
    client = signed()
    assert 'Program 1' not in client.get(PATH).text
    assert 'WC-TEST' not in client.get(refs.class_path).text
    listing = catalog(client)
    assert listing.status_code == 200 and 'Program 1' in listing.text and 'Program 2' not in listing.text
    statements = []
    def record(conn, cursor, statement, params, context, many): statements.append(statement.lower())
    engine = refs.factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', record)
    try:
        page = view(client, refs.class_path)
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert page.status_code == 200 and 'WC-TEST0001' in page.text and 'Trumpet' in page.text
    assert not any(value in page.text for value in ['WC-TEST0002', 'PRIVATE_PUBLIC_ALIAS', 'PRIVATE_SENTINEL_CHART', 'pin_hash'])
    assert not any(name in sql for sql in statements for name in ['practice_charts', 'classroom_reporting_periods',
        'classroom_progress_authorizations', 'classroom_chart_director_shares', 'reward_grants', 'team_memberships'])
    assert page.headers['cache-control'] == 'no-store' and page.headers['referrer-policy'] == 'no-referrer'
    assert page.headers['x-frame-options'] == 'DENY'


def test_class_creation_code_rotation_closure_archive_reactivate(management_web, monkeypatch):
    refs = management_web
    client = signed()
    before = protected_counts(refs)
    prepared = preview(client, refs.program_path, 'create-class', display_name='New Wind Ensemble')
    with refs.factory() as session:
        assert count(session, ClassroomClass) == 3
    created = apply(client, refs.program_path, prepared)
    assert created.status_code == 200
    class_id = created.context['detail']['selected_class']['id']
    path = refs.program_path + f'/classes/{class_id}'
    assert created.context['detail']['selected_class']['enrollment_open']
    code = change(client, path, 'code', preferred='NEWWIND26')
    assert code.status_code == 200 and code.context['code_result']['code'] == 'NEWWIND26'
    assert 'NEWWIND26' not in view(client, path).text
    rotated = change(client, path, 'code', preferred='WINDNEW27')
    assert rotated.status_code == 200 and rotated.context['code_result']['generation'] == 2
    assert change(client, path, 'close').status_code == 200
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied):
            s2.join_class(session, student=s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468'), program_id=1, code='WINDNEW27')
    assert change(client, path, 'archive').status_code == 200
    active = change(client, path, 'reactivate')
    assert active.status_code == 200 and not active.context['detail']['selected_class']['enrollment_open']
    assert active.context['detail']['selected_class']['code'] is None
    assert change(client, path, 'code', preferred='RETURN2026').status_code == 200
    assert change(client, path, 'open').status_code == 200
    assert protected_counts(refs) == before


@pytest.mark.parametrize('role', ['head_director', 'admin', 'code_manager', 'billing'])
def test_owner_delegates_existing_roles_and_can_revoke(management_web, role):
    refs = management_web
    owner = signed()
    result = change(owner, refs.program_path, 'grant-role', target_email='adult4@example.test', role=role)
    assert result.status_code == 200
    with refs.factory() as session:
        grant_id = session.scalar(select(ClassroomRoleGrant.id).where(ClassroomRoleGrant.program_id == 1,
            ClassroomRoleGrant.verifier_id == 4, ClassroomRoleGrant.role == role, ClassroomRoleGrant.ended_at.is_(None)))
    staff = signed(4)
    page = view(staff, refs.program_path)
    assert role in page.context['detail']['roles'] and not page.context['detail']['is_owner']
    assert preview(staff, refs.program_path, 'grant-role', target_email='adult3@example.test', role='admin').status_code == 409
    assert change(owner, refs.program_path, 'revoke-role', grant_id=str(grant_id)).status_code == 200
    assert view(staff, refs.program_path).status_code == 409
    assert PATH not in str(page.context['detail'].get('roster', ''))


def test_teaching_assignment_revocation_and_own_departure(management_web):
    refs = management_web
    owner, teacher = signed(), signed(2)
    assert view(teacher, refs.class_path).status_code == 409
    assigned = change(owner, refs.class_path, 'assign-teacher', target_email='adult2@example.test')
    assert assigned.status_code == 200
    with refs.factory() as session:
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(ClassroomTeachingAssignment.class_id == refs.class_id,
            ClassroomTeachingAssignment.verifier_id == 2, ClassroomTeachingAssignment.ended_at.is_(None)))
        assignment_id = assignment.id
    page = view(teacher, refs.class_path)
    assert page.status_code == 200 and 'WC-TEST0001' in page.text
    assert not page.context['detail']['manage'] and not page.context['detail']['codes']
    assert preview(teacher, refs.class_path, 'remove', membership_id=str(refs.member_id)).status_code == 409
    assert change(owner, refs.class_path, 'end-teaching', assignment_id=str(assignment_id)).status_code == 200
    assert view(teacher, refs.class_path).status_code == 409
    assert change(owner, refs.class_path, 'assign-teacher', target_email='adult2@example.test').status_code == 200
    with refs.factory() as session:
        new_id = session.scalar(select(ClassroomTeachingAssignment.id).where(ClassroomTeachingAssignment.class_id == refs.class_id,
            ClassroomTeachingAssignment.verifier_id == 2, ClassroomTeachingAssignment.ended_at.is_(None)))
    assert change(teacher, refs.class_path, 'depart-teaching', assignment_id=str(new_id)).status_code == 200
    assert view(teacher, refs.class_path).status_code == 409


@pytest.mark.parametrize('action', ['suspend', 'remove'])
def test_roster_actions_preserve_history_and_explicitly_reinstate(management_web, monkeypatch, action):
    refs = management_web
    client = signed()
    before = protected_counts(refs)
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
    result = change(client, refs.class_path, action, membership_id=str(refs.member_id))
    assert result.status_code == 200
    member = result.context['detail']['selected_class']['roster'][0]
    assert member['status'] == ('Removed' if action == 'remove' else 'Suspended')
    with refs.factory() as session:
        assert session.get(ClassroomMembershipHold, refs.member_id).released_at is None
        assert count(session, ClassroomMembershipPeriod) == 2
    assert change(client, refs.class_path, 'reinstate', membership_id=str(refs.member_id)).status_code == 200
    with refs.factory() as session:
        assert session.get(ClassroomMembershipHold, refs.member_id).released_at is not None
        assert count(session, ClassroomMembershipPeriod) == 3
        assert count(session, ClassroomStudentMembership) == 2
    assert protected_counts(refs) == before


def test_owner_transfer_inbox_exact_recipient_and_outgoing_role_preservation(management_web):
    refs = management_web
    owner, successor, outsider = signed(), signed(4), signed(2)
    before = protected_counts(refs)
    proposed = change(owner, refs.program_path, 'propose-transfer', target_email='adult4@example.test')
    assert proposed.status_code == 200
    ident = proposed.context['detail']['proposal']['id']
    with refs.factory() as session:
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1
    listing = catalog(successor)
    assert listing.context['catalog']['programs'][0]['incoming']
    incoming = view(successor, refs.program_path)
    assert incoming.context['detail']['incoming_only']
    assert incoming.context['detail']['entitlement'] is None and 'WC-TEST' not in incoming.text
    assert preview(outsider, refs.program_path, 'accept-transfer', transfer_id=str(ident)).status_code == 409
    assert change(successor, refs.program_path, 'accept-transfer', transfer_id=str(ident)).status_code == 200
    with refs.factory() as session:
        program = session.get(ClassroomProgram, 1)
        assert program.owner_verifier_id == 4 and program.owner_version == 2
        assert 'head_director' in s1._roles(session, 1, 1)
    previous = view(owner, refs.program_path)
    assert not previous.context['detail']['delegate'] and previous.context['detail']['selected_class'] is None
    assert preview(owner, refs.program_path, 'grant-role', target_email='adult2@example.test', role='admin').status_code == 409
    assert preview(successor, refs.program_path, 'accept-transfer', owner_version=2, transfer_id=str(ident)).status_code == 409
    assert protected_counts(refs) == before


@pytest.mark.parametrize('resolution', ['cancel', 'supersede'])
def test_cancelled_or_superseded_transfer_confirmation_refuses(management_web, resolution):
    refs = management_web
    owner, successor = signed(), signed(4)
    first = change(owner, refs.program_path, 'propose-transfer', target_email='adult4@example.test')
    ident = first.context['detail']['proposal']['id']
    saved = preview(successor, refs.program_path, 'accept-transfer', transfer_id=str(ident))
    if resolution == 'cancel':
        assert change(owner, refs.program_path, 'cancel-transfer', transfer_id=str(ident)).status_code == 200
    else:
        assert change(owner, refs.program_path, 'propose-transfer', target_email='adult3@example.test').status_code == 200
    assert apply(successor, refs.program_path, saved).status_code == 409
    with refs.factory() as session:
        assert session.get(ClassroomProgram, 1).owner_verifier_id == 1


def test_cross_program_class_role_member_and_assignment_substitution(management_web):
    refs = management_web
    client = signed()
    before = protected_counts(refs)
    assert view(client, PATH + '/2').status_code == 409
    assert view(client, PATH + f'/1/classes/{refs.foreign_id}').status_code == 409
    assert preview(client, refs.class_path, 'remove', membership_id=str(refs.other_member_id)).status_code == 409
    with refs.factory() as session:
        foreign_grant = session.scalar(select(ClassroomRoleGrant.id).where(ClassroomRoleGrant.program_id == 2, ClassroomRoleGrant.ended_at.is_(None)))
        foreign_assignment = session.scalar(select(ClassroomTeachingAssignment.id).where(ClassroomTeachingAssignment.class_id == refs.other_id, ClassroomTeachingAssignment.ended_at.is_(None)))
    assert preview(client, refs.program_path, 'revoke-role', grant_id=str(foreign_grant)).status_code == 409
    assert preview(client, refs.class_path, 'end-teaching', assignment_id=str(foreign_assignment)).status_code == 409
    assert protected_counts(refs) == before


def test_management_form_security_current_identity_and_explicit_confirmation(management_web):
    refs = management_web
    client = signed()
    page = client.get(refs.program_path)
    data = {'csrf': page.context['csrf'], 'director': '1', 'pin': '2468', 'action': 'create-class',
        'stage': 'review', 'owner_version': '1', 'display_name': 'Unauthorized extra'}
    for header in [{'Origin': 'https://foreign.invalid'}, {'Origin': ''}]:
        assert client.post(refs.program_path, data=data, headers=header).status_code == 403
    for extra, expected in [({'csrf': 'bad'}, 403), ({'pin': '9999'}, 401), ({'director': '2'}, 401),
        ({'actor_id': '1'}, 400), ({'display_name': 'X' * 5000}, 413), ({'owner_version': '0'}, 400),
        ({'stage': 'apply', 'confirm_change': 'no'}, 400), ({'role': 'admin'}, 400)]:
        assert client.post(refs.program_path, data={**data, **extra}).status_code == expected
    assert client.post(refs.program_path, json=data).status_code == 415
    assert client.post(refs.program_path, content=urlencode(data) + '&action=trial',
        headers={'Content-Type': 'application/x-www-form-urlencoded'}).status_code == 400
    assert client.get(refs.program_path + '?pin=unsafe').status_code == 400
    guest = TestClient(main.app)
    assert 'Adult sign in' in guest.get(PATH).text
    csrf = guest.get(refs.program_path).context['csrf']
    assert guest.post(refs.program_path, data={**data, 'csrf': csrf}).status_code == 401
    assert student_client().post(refs.program_path, data=data).status_code in {401, 403}


def test_revocation_between_review_and_apply_is_effective(management_web):
    refs = management_web
    owner, delegate = signed(), signed(4)
    assert change(owner, refs.program_path, 'grant-role', target_email='adult4@example.test', role='admin').status_code == 200
    saved = preview(delegate, refs.program_path, 'create-class', display_name='Stale delegate Class')
    with refs.factory() as session:
        grant = session.scalar(select(ClassroomRoleGrant).where(ClassroomRoleGrant.verifier_id == 4,
            ClassroomRoleGrant.program_id == 1, ClassroomRoleGrant.role == 'admin', ClassroomRoleGrant.ended_at.is_(None)))
    assert change(owner, refs.program_path, 'revoke-role', grant_id=str(grant.id)).status_code == 200
    assert apply(delegate, refs.program_path, saved).status_code == 409
    with refs.factory() as session:
        assert count(session, ClassroomClass) == 3


@pytest.mark.parametrize('restricted', ['child', 'unknown', 'deleted'])
def test_roster_account_privacy_restrictions_never_use_public_names(management_web, restricted):
    refs = management_web
    with refs.factory() as session:
        if restricted == 'deleted':
            session.get(WoodchuckProfile, 1).status = 'deleted'
        else:
            session.get(AccountPrivacy, 1).age_band = 'under13' if restricted == 'child' else 'unknown'
        session.commit()
    page = view(signed(), refs.class_path)
    assert page.status_code == 200
    student = page.context['detail']['selected_class']['roster'][0]
    assert student['woodchuck_id'] is None and student['instrument'] is None
    assert 'WC-TEST0001' not in page.text and 'PRIVATE_PUBLIC_ALIAS' not in page.text


def test_roster_pagination_and_teacher_removed_identity_denial(management_web, monkeypatch):
    refs = management_web
    client, teacher = signed(), signed(2)
    assert change(client, refs.class_path, 'assign-teacher', target_email='adult2@example.test').status_code == 200
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
    assert change(client, refs.class_path, 'remove', membership_id=str(refs.member_id)).status_code == 200
    page = view(teacher, refs.class_path)
    assert page.context['detail']['selected_class']['roster'] == [] and 'WC-TEST0001' not in page.text
    monkeypatch.setattr(views, 'PAGE_SIZE', 1)
    listing = view(client, refs.program_path)
    assert len(listing.context['detail']['classes']) == 1 and listing.context['detail']['next_class']
    older = view(client, refs.program_path, before=listing.context['detail']['next_class'])
    assert older.context['detail']['classes'][0]['id'] == refs.class_id


def test_entitlement_expiry_capacity_and_reduction(management_web, monkeypatch):
    refs = management_web
    owner = signed()
    with refs.factory() as session:
        institutional(session, class_limit=1, teacher_limit=1)
        session.commit()
    page = view(owner, refs.program_path)
    assert page.context['detail']['entitlement']['reason'] == 'over_limit'
    assert change(owner, refs.program_path, 'create-class', display_name='Over capacity').status_code == 409
    assert change(owner, refs.class_path, 'archive').status_code == 200
    assert view(owner, refs.class_path).context['detail']['selected_class']['roster'][0]['woodchuck_id'] is None
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(days=366))
    expired = view(owner, refs.program_path)
    assert expired.context['detail']['entitlement']['reason'] == 'expired'
    assert change(owner, refs.class_path, 'reactivate').status_code == 409
    assert change(owner, refs.class_path, 'remove', membership_id=str(refs.member_id)).status_code == 200
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == 2


def test_code_manager_can_operate_exact_reference_without_roster_browsing(management_web):
    refs = management_web
    owner, manager = signed(), signed(4)
    assert change(owner, refs.program_path, 'grant-role', target_email='adult4@example.test', role='code_manager').status_code == 200
    detail = view(manager, refs.program_path)
    assert detail.context['detail']['classes'] == [] and 'WC-TEST' not in detail.text
    assert view(manager, refs.class_path).status_code == 409
    coded = change(manager, refs.program_path, 'code', class_id=str(refs.class_id), preferred='MANAGED26')
    assert coded.status_code == 200 and coded.context['code_result']['code'] == 'MANAGED26'
    assert 'WC-TEST' not in coded.text and 'PRIVATE_PUBLIC_ALIAS' not in coded.text
    assert preview(manager, refs.program_path, 'code', class_id=str(refs.foreign_id), preferred='ATTACK26').status_code == 409


def test_approved_onboarding_handoff_and_one_deliberate_trial(management_web):
    refs = management_web
    token = classroom_invitation.create_program_invitation(3, 'adult4@example.test')
    path = '/classroom/invitations/' + token
    client = TestClient(main.app)
    page = client.get(path)
    assert page.status_code == 200
    before = protected_counts(refs)
    accepted = client.post(path, data={'csrf': page.context['csrf'], 'pin': '2468'}, follow_redirects=False)
    assert accepted.status_code == 303 and accepted.headers['location'] == PATH + '/3'
    client.management_actor = 4
    assert client.get(path).status_code == 410
    detail = view(client, PATH + '/3')
    assert detail.context['detail']['is_owner'] and 'head_director' in detail.context['detail']['roles']
    assert not detail.context['detail']['entitlement']['allowed']
    started = change(client, PATH + '/3', 'trial')
    assert started.status_code == 200 and started.context['detail']['entitlement']['allowed']
    assert change(client, PATH + '/3', 'trial').status_code == 409
    with refs.factory() as session:
        period = session.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.program_id == 3))
        from app.age_privacy import utc
        assert utc(period.ends_at) - utc(period.starts_at) == timedelta(days=14)
    assert protected_counts(refs) == before


def test_admin_manages_classes_but_cannot_delegate_or_activate_institutional_access(management_web, monkeypatch):
    refs = management_web
    owner, admin = signed(), signed(4)
    assert change(owner, refs.program_path, 'grant-role', target_email='adult4@example.test', role='admin').status_code == 200
    assert change(admin, refs.program_path, 'create-class', display_name='Delegated Class').status_code == 200
    assert change(admin, refs.class_path, 'assign-teacher', target_email='adult2@example.test').status_code == 200
    assert change(admin, refs.class_path, 'code', preferred='ADMINCODE').status_code == 200
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
    assert change(admin, refs.class_path, 'suspend', membership_id=str(refs.member_id)).status_code == 200
    assert preview(admin, refs.program_path, 'propose-transfer', target_email='adult2@example.test').status_code == 409
    assert preview(admin, refs.program_path, 'grant-role', target_email='adult2@example.test', role='admin').status_code == 409
    assert admin.get('/admin/classroom/entitlement').status_code == 403


def test_refresh_integrity_failure_does_not_commit_or_report_false_success(management_web, monkeypatch):
    refs = management_web
    owner = signed()
    saved = preview(owner, refs.program_path, 'create-class', display_name='Must Roll Back')
    def corrupted(*args, **kwargs): raise s1.ClassroomDenied('Synthetic retained-history validation failure')
    monkeypatch.setattr(views, 'detail', corrupted)
    failed = apply(owner, refs.program_path, saved)
    assert failed.status_code == 409 and 'completed.' not in failed.text
    with refs.factory() as session:
        assert count(session, ClassroomClass) == 3
        assert not session.scalar(select(ClassroomClass.id).where(ClassroomClass.display_name == 'Must Roll Back'))


def test_ownership_away_and_back_invalidates_stale_management_confirmation(management_web):
    refs = management_web
    first, second = signed(), signed(4)
    saved = preview(first, refs.program_path, 'create-class', display_name='Obsolete owner intent')
    offer = change(first, refs.program_path, 'propose-transfer', target_email='adult4@example.test')
    ident = offer.context['detail']['proposal']['id']
    assert change(second, refs.program_path, 'accept-transfer', transfer_id=str(ident)).status_code == 200
    back = change(second, refs.program_path, 'propose-transfer', owner_version=2, target_email='adult1@example.test')
    ident = back.context['detail']['proposal']['id']
    assert change(first, refs.program_path, 'accept-transfer', owner_version=2, transfer_id=str(ident)).status_code == 200
    assert apply(first, refs.program_path, saved).status_code == 409
    with refs.factory() as session:
        assert session.get(ClassroomProgram, 1).owner_version == 3 and count(session, ClassroomClass) == 3


def test_teacher_roster_withdraws_on_expiry_and_class_archive(management_web, monkeypatch):
    refs = management_web
    owner, teacher = signed(), signed(2)
    assert change(owner, refs.class_path, 'assign-teacher', target_email='adult2@example.test').status_code == 200
    assert 'WC-TEST0001' in view(teacher, refs.class_path).text
    assert change(owner, refs.class_path, 'archive').status_code == 200
    assert view(teacher, refs.class_path).context['detail']['selected_class']['roster'] == []
    assert change(owner, refs.class_path, 'reactivate').status_code == 200
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(days=15))
    assert view(teacher, refs.class_path).context['detail']['selected_class']['roster'] == []


def test_names_are_escaped_and_management_review_is_not_a_business_mutation(management_web):
    refs = management_web
    client = signed()
    before = protected_counts(refs)
    name = '<script>window.BAD_MANAGEMENT=true</script>'
    page = preview(client, refs.program_path, 'create-class', display_name=name)
    assert page.status_code == 200 and name not in page.text and '&lt;script&gt;' in page.text
    with refs.factory() as session:
        assert count(session, ClassroomClass) == 3
    assert protected_counts(refs) == before


@pytest.mark.parametrize('role,allowed', [('head_director', True), ('billing', False)])
def test_head_director_trial_authority_is_not_billing_authority(management_web, role, allowed):
    refs = management_web
    token = classroom_invitation.create_program_invitation(3, 'adult3@example.test')
    with refs.factory() as session:
        s1.accept_program_invitation(session, token=token, pin='2468')
        session.commit()
        s1.grant_role(session, actor=actor(session, 3), program_id=3, verifier_id=2, role=role)
        session.commit()
    staff = signed(2)
    path = PATH + '/3'
    detail = view(staff, path)
    assert detail.context['detail']['trial'] is allowed
    prepared = preview(staff, path, 'trial')
    if allowed:
        assert prepared.status_code == 200 and apply(staff, path, prepared).status_code == 200
    else:
        assert prepared.status_code == 409
