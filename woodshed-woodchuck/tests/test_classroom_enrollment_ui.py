"""Enrollment screens over the accepted services, using disposable synthetic data."""
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit, parse_qs

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import (account_routes, age_routes, classroom, classroom_director,
    classroom_enrollment as enrollment, classroom_enrollment_routes as routes,
    classroom_s2 as s2, main, session_revocations, verifier_routes, child_authorization)
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation
from app.classroom_models import (ClassroomStudentMembership, ClassroomMembershipPeriod,
    ClassroomTeachingAssignment, ClassroomReportingPeriod)
from app.models import WoodchuckProfile
from test_classroom_s1 import actor, adult_pin_hash, classroom_db, count
from test_classroom_s2 import s2_db, NOW, ready, create, code
from test_classroom_enrollment_v2 import setup_enrollment, PROTECTED

PATH = routes.PATH


@pytest.fixture
def enrollment_web(s2_db, monkeypatch):
    refs = setup_enrollment(s2_db, monkeypatch)
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://testserver')
    monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
    for module in (routes, account_routes, age_routes, main, session_revocations,
                   verifier_routes, classroom_director):
        monkeypatch.setattr(module, 'SessionLocal', s2_db)
    with s2_db() as session:
        for ident in (1, 2):
            session.get(WoodchuckProfile, ident).woodchuck_id = f'WC-TEST000{ident}'
        other = create(session, name='Private Other Class')
        other_code, _ = code(session, other.id, 'OTHER2026')
        classroom.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=other.id, verifier_id=2)
        refs.other_id = other.id
        session.commit()
        foreign, _, _ = ready(session, program_id=2, preferred='FOREIGN26')
        foreign.display_name = 'Private Other Program'
        classroom.assign_teacher(session, actor=actor(session), program_id=2,
            class_id=foreign.id, verifier_id=2)
        refs.foreign_id = foreign.id
        session.commit()
    refs.path = f'{routes.DIRECTOR}/1/classes/{refs.class_id}'
    return refs


def student_client(ident=1):
    client = TestClient(main.app)
    assert client.post('/account/login', data={'woodchuck_id': f'WC-TEST000{ident}', 'pin': '2468'}).status_code == 200
    return client


def director_client(ident=1):
    client = TestClient(main.app)
    assert client.post('/trusted-verifiers/login', data={'email': f'adult{ident}@example.test', 'pin': '2468'}).status_code == 200
    return client


def form(client, path=PATH, **values):
    page = client.get(path)
    assert page.status_code == 200
    return {'csrf': page.context['csrf'], **values}


def review(client, program=1, class_code='BAND2026', account='WC-TEST0001'):
    return client.post(PATH + '/review', data=form(client, account=account, program_id=str(program), code=class_code))


def confirm_join(client, page):
    return client.post(PATH + '/join', data={'csrf': page.context['csrf'], 'account': page.context['account']['woodchuck_id'],
        'program_id': str(page.context['review']['program_id']), 'intent': page.context['review']['intent'], 'confirm_join': 'yes'})


def direct(client, refs, action='view', **values):
    return client.post(refs.path, data=form(client, refs.path, director='1', pin='2468', action=action,
        **({'before': ''} if action == 'view' else {}), **values))


def invite(refs, ident=1):
    with refs.factory() as session:
        row = enrollment.issue_invitation(session, actor=actor(session), program_id=1,
            class_id=refs.class_id, woodchuck_id=f'WC-TEST000{ident}')
        result = row.id
        session.commit()
        return result


def respond(client, invitation, choice, account='WC-TEST0001'):
    return client.post(f'{PATH}/invitations/{invitation}/respond',
        data=form(client, account=account, choice=choice))


def protected_counts(refs):
    with refs.factory() as session:
        return {model.__tablename__: count(session, model) for model in PROTECTED}


def test_default_off_has_no_classroom_queries_or_navigation(enrollment_web, monkeypatch):
    client = director_client()
    monkeypatch.delenv('CLASSROOM_ENROLLMENT_V2_ENABLED')
    statements = []
    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement.lower())
    engine = enrollment_web.factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        for path in (PATH, PATH + '/invitations', routes.DIRECTOR, enrollment_web.path):
            assert client.get(path).status_code == 404
        for path in (PATH + '/review', PATH + '/join', PATH + '/invitations/1/respond', routes.DIRECTOR, enrollment_web.path):
            assert client.post(path, data={}).status_code == 404
        assert not any('classroom_' in statement for statement in statements)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert routes.DIRECTOR not in client.get('/band-director/dashboard').text
    assert routes.DIRECTOR not in client.get('/trusted-verifiers/dashboard').text


@pytest.mark.parametrize('flag', ['CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED'])
def test_dependency_gate(enrollment_web, monkeypatch, flag):
    monkeypatch.delenv(flag)
    assert TestClient(main.app).get(PATH).status_code == 404


def test_manual_confirmation_and_invitation_use_one_membership_without_sharing(enrollment_web):
    refs = enrollment_web
    client = student_client()
    before = protected_counts(refs)
    page = review(client)
    assert page.status_code == 200 and page.context['review']['class_name'] == 'Concert Band'
    assert 'Nothing is shared automatically' in page.text and 'Join and Share' in page.text
    assert page.headers['cache-control'] == 'no-store' and page.headers['referrer-policy'] == 'no-referrer'
    assert 'BAND2026' not in page.text
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == 0
    assert confirm_join(client, page).status_code == 200
    assert confirm_join(client, page).status_code == 200
    invitation = invite(refs)
    assert respond(client, invitation, 'accept').status_code == 200
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 1
        assert session.get(Invitation, invitation).state == 'accepted'
    assert protected_counts(refs) == before
    assert 'Membership: <strong>Member' in client.get(PATH).text


def test_qr_requires_exact_class_authority_and_uses_fragment(enrollment_web):
    refs = enrollment_web
    director = director_client()
    page = direct(director, refs, 'qr', code=' band2026 ')
    assert page.status_code == 200
    qr = page.context['qr']
    parsed = urlsplit(qr['url'])
    assert parsed.query == '' and parsed.path == PATH and parsed.netloc == 'testserver'
    assert parse_qs(parsed.fragment) == {'program': ['1'], 'code': ['BAND2026']}
    assert qr['image'].startswith('data:image/svg+xml;base64,')
    assert 'student=' not in qr['url'] and 'intent=' not in qr['url']
    assert direct(director, refs, 'qr', code='OTHER2026').status_code == 409
    assert direct(director, refs, 'qr', code='not-current').status_code == 409
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == 0


def test_director_assignment_reauthentication_and_scope(enrollment_web):
    refs = enrollment_web
    client = director_client()
    page = client.get(routes.DIRECTOR)
    assert 'Concert Band' not in page.text and 'WC-TEST' not in page.text
    classes = client.post(routes.DIRECTOR, data=form(client, routes.DIRECTOR, director='1', pin='2468'))
    assert 'Concert Band' in classes.text and 'Private Other' not in classes.text
    assert 'WC-TEST' not in classes.text and 'Private Student' not in classes.text
    view = direct(client, refs)
    assert view.context['detail']['can_issue']
    sent = direct(client, refs, 'issue', woodchuck_id='WC-TEST0001')
    assert sent.status_code == 200 and sent.context['detail']['offers'][0]['state'] == 'pending'
    assert 'Private Student' not in sent.text and '@example.test' not in sent.text
    for path in (f'{routes.DIRECTOR}/1/classes/{refs.other_id}', f'{routes.DIRECTOR}/2/classes/{refs.foreign_id}',
                 f'{routes.DIRECTOR}/2/classes/{refs.class_id}'):
        denied = client.post(path, data=form(client, path, director='1', pin='2468', action='view', before=''))
        assert denied.status_code == 409 and 'Private Other' not in denied.text and 'WC-TEST' not in denied.text
    owner_only = director_client(4)
    data = form(owner_only, refs.path, director='4', pin='2468', action='issue', woodchuck_id='WC-TEST0001')
    assert owner_only.post(refs.path, data=data).status_code == 409
    with refs.factory() as session:
        assert count(session, Invitation) == 1 and count(session, ClassroomStudentMembership) == 0


@pytest.mark.parametrize('choice,state', [('accept', 'accepted'), ('decline', 'declined'), ('defer', 'deferred')])
def test_own_inbox_choices_and_status(enrollment_web, choice, state):
    refs = enrollment_web
    invitation = invite(refs)
    client = student_client()
    before = protected_counts(refs)
    page = client.get(PATH + '/invitations')
    assert page.context['offers'][0]['state'] == 'pending'
    assert 'Private Other' not in page.text and 'WC-TEST0002' not in page.text
    assert respond(client, invitation, choice).status_code == 200
    with refs.factory() as session:
        assert session.get(Invitation, invitation).state == state
        assert count(session, ClassroomStudentMembership) == int(choice == 'accept')
    assert protected_counts(refs) == before
    assert client.get(PATH + '/invitations').context['offers'][0]['state'] == state


def test_not_now_then_accept_and_decline_preserves_code_membership(enrollment_web):
    refs = enrollment_web
    invitation = invite(refs)
    client = student_client()
    assert respond(client, invitation, 'defer').status_code == 200
    with refs.factory() as session:
        deadline = session.get(Invitation, invitation).expires_at
    assert confirm_join(client, review(client)).status_code == 200
    assert respond(client, invitation, 'decline').status_code == 200
    with refs.factory() as session:
        assert session.get(Invitation, invitation).expires_at == deadline
        assert count(session, ClassroomStudentMembership) == 1
    assert client.get(PATH + '/invitations').context['offers'][0]['membership']['active']
    newer = invite(refs)
    assert respond(client, newer, 'defer').status_code == 200
    assert respond(client, newer, 'accept').status_code == 200
    with refs.factory() as session:
        assert count(session, ClassroomMembershipPeriod) == 1


def test_revoke_and_expired_ui_never_enroll(enrollment_web, monkeypatch):
    refs = enrollment_web
    invitation = invite(refs)
    director = director_client()
    assert direct(director, refs, 'revoke', invitation_id=str(invitation)).status_code == 200
    client = student_client()
    assert client.get(PATH + '/invitations').context['offers'][0]['state'] == 'revoked'
    newer = invite(refs)
    monkeypatch.setattr(s2, '_now', lambda: NOW + enrollment.LIFETIME)
    page = client.get(PATH + '/invitations')
    assert page.context['offers'][0]['state'] == 'expired' and 'value="accept"' not in page.text
    with refs.factory() as session:
        assert session.get(Invitation, newer).state == 'pending'  # GET computes; no durable write.
    assert respond(client, newer, 'accept').status_code == 200
    with refs.factory() as session:
        assert session.get(Invitation, newer).state == 'expired'
        assert count(session, ClassroomStudentMembership) == 0


def test_form_security_and_student_session_binding(enrollment_web):
    client = student_client()
    data = form(client, account='WC-TEST0001', program_id='1', code='BAND2026')
    endpoint = PATH + '/review'
    for headers in ({'Origin': 'https://outside.invalid'}, {'Origin': ''}):
        assert client.post(endpoint, data=data, headers=headers).status_code == 403
    assert client.post(endpoint, data={**data, 'csrf': 'bad'}).status_code == 403
    assert client.post(endpoint, data={**data, 'assignment_ids': '1'}).status_code == 400
    assert client.post(endpoint, data={**data, 'account': 'WC-TEST0002'}).status_code == 401
    assert client.post(endpoint, json=data).status_code == 415
    assert client.post(endpoint, content=urlencode(data) + '&code=OTHER2026',
        headers={'Content-Type': 'application/x-www-form-urlencoded'}).status_code == 400
    assert client.post(endpoint, data={**data, 'code': 'X' * 5000}).status_code == 413
    assert client.get(PATH + '?code=BAND2026').status_code == 400
    assert client.get(PATH + '/invitations?before=1&before=2').status_code == 400
    guest = TestClient(main.app)
    assert guest.post(endpoint, data=form(guest, account='WC-TEST0001', program_id='1', code='BAND2026')).status_code == 401
    assert client.post(endpoint, data=data, headers={'x-woodshed-account': 'WC-TEST0002'}).status_code == 401


@pytest.mark.parametrize('bad', ['', '0', '-1', '1.0', '2147483648', '١', '1e1'])
def test_invalid_program_references(enrollment_web, bad):
    assert review(student_client(), program=bad).status_code == 400


def test_cross_student_and_stale_tab_cannot_accept_or_revoke(enrollment_web):
    refs = enrollment_web
    invitation = invite(refs)
    other = student_client(2)
    assert other.get(PATH + '/invitations').context['offers'] == []
    assert respond(other, invitation, 'accept', 'WC-TEST0002').status_code == 409
    client = student_client()
    saved = review(client)
    assert client.post('/account/login', data={'woodchuck_id': 'WC-TEST0002', 'pin': '2468'}).status_code == 200
    assert confirm_join(client, saved).status_code == 401
    director = director_client(2)
    path = f'{routes.DIRECTOR}/1/classes/{refs.other_id}'
    data = form(director, path, director='2', pin='2468', action='revoke', invitation_id=str(invitation))
    assert director.post(path, data=data).status_code == 409
    with refs.factory() as session:
        assert session.get(Invitation, invitation).state == 'pending'
        assert count(session, ClassroomStudentMembership) == 0


def test_director_pin_origin_and_posted_identity_are_not_authority(enrollment_web):
    refs = enrollment_web
    client = director_client()
    data = form(client, refs.path, director='1', pin='2468', action='issue', woodchuck_id='WC-TEST0001')
    assert client.post(refs.path, data={**data, 'pin': '9999'}).status_code == 401
    assert client.post(refs.path, data={**data, 'director': '2'}).status_code == 401
    assert client.post(refs.path, data={**data, 'actor_id': '1'}).status_code == 400
    assert client.post(refs.path, data={**data, 'csrf': 'bad'}).status_code == 403
    assert client.post(refs.path, data=data, headers={'Origin': 'https://outside.invalid'}).status_code == 403
    with refs.factory() as session:
        assert count(session, Invitation) == 0


@pytest.mark.parametrize('loss', ['closed', 'assignment', 'hold', 'code_rotation'])
def test_stale_confirmations_recheck_current_authority(enrollment_web, monkeypatch, loss):
    refs = enrollment_web
    invitation = invite(refs)
    client = student_client()
    saved = review(client)
    with refs.factory() as session:
        if loss == 'closed':
            s2.set_enrollment(session, actor=actor(session), program_id=1, class_id=refs.class_id, open=False)
        elif loss == 'assignment':
            classroom.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
        elif loss == 'hold':
            member = s2.join_class(session, student=s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468'), program_id=1, code='BAND2026')
            monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
            s2.suspend_member(session, actor=actor(session), program_id=1, class_id=refs.class_id, membership_id=member.id)
        else:
            code(session, refs.class_id, 'NEWBAND26')
        session.commit()
    if loss != 'assignment':
        assert confirm_join(client, saved).status_code == 409
    if loss == 'hold':
        held = review(client)
        assert not held.context['review']['can_join'] and held.context['review']['account_ready']
        assert 'Class code cannot release' in held.text and 'Account authorization needed' not in held.text
    if loss != 'code_rotation':
        assert respond(client, invitation, 'accept').status_code == 409
        assert not client.get(PATH + '/invitations').context['offers'][0]['can_accept']
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == int(loss == 'hold')


def test_former_teacher_cleanup_hides_targets_and_class_names(enrollment_web):
    refs = enrollment_web
    invitation = invite(refs)
    with refs.factory() as session:
        classroom.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
        session.commit()
    client = director_client()
    page = direct(client, refs)
    assert page.status_code == 200 and 'WC-TEST' not in page.text and 'Concert Band' not in page.text
    assert not page.context['detail']['can_issue'] and 'Retained invitation' in page.text
    assert direct(client, refs, 'revoke', invitation_id=str(invitation)).status_code == 200
    assert direct(client, refs, 'issue', woodchuck_id='WC-TEST0002').status_code == 409


@pytest.mark.parametrize('where', ['code', 'invitations'])
def test_existing_account_authorization_returns_to_explicit_enrollment(enrollment_web, where):
    refs = enrollment_web
    invitation = invite(refs)
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = 'unknown', None
        session.commit()
    client = student_client()
    page = review(client) if where == 'code' else client.get(PATH + '/invitations')
    assert page.status_code == 200 and 'authorization' in page.text
    if where == 'code':
        assert not page.context['review']['can_join']
        assert confirm_join(client, page).status_code == 409
    else:
        assert not page.context['offers'][0]['can_accept']
    age = client.get('/account/age')
    expected = PATH + ('/resume' if where == 'code' else '/invitations')
    assert age.context['classroom_return'] == expected
    response = client.post('/account/age', data={'csrf': age.context['csrf'], 'confirm_account': 'WC-TEST0001', 'age_band': '13to17'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == expected
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == 0
    if where == 'code':
        assert confirm_join(client, client.get(expected)).status_code == 200
    else:
        assert respond(client, invitation, 'accept').status_code == 200


def test_parent_authorization_help_never_bypasses_consent(enrollment_web, monkeypatch):
    refs = enrollment_web
    invitation = invite(refs)
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = 'under13', None
        session.commit()
    client = student_client()
    page = review(client)
    assert not page.context['review']['can_join'] and 'parent authorization' in page.text
    assert confirm_join(client, page).status_code == 409
    age = client.get('/account/age')
    assert age.context['blocked_child'] and '/account/help' in age.text
    # The existing flow offers a request only with a qualified notice policy.
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda *args: ('synthetic-notice', 'Synthetic', 'a' * 64))
    age = client.get('/account/age')
    assert age.context['blocked_child'] and '/family/request' in age.text
    assert respond(client, invitation, 'accept').status_code == 409
    assert respond(client, invitation, 'defer').status_code == 200
    assert respond(client, invitation, 'decline').status_code == 200
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == count(session, ConsentEvidence) == 0


def test_existing_approved_parent_consent_enrolls_without_reporting(enrollment_web, monkeypatch):
    refs = enrollment_web
    monkeypatch.setattr(child_authorization, 'under13_available', lambda: True)
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda *args: ('synthetic-notice', 'Synthetic', 'a' * 64))
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = 'under13', None
        evidence = ConsentEvidence(profile_id=1, parent_email='parent@example.test', notice_version='synthetic-notice', notice_sha256='a' * 64, approved_at=NOW, confirmed_at=NOW)
        session.add(evidence); session.flush(); rule.consent_id = evidence.id
        session.commit()
    client = student_client()
    before = protected_counts(refs)
    assert confirm_join(client, review(client)).status_code == 200
    page = client.get(PATH)
    assert not page.context['account']['sharing']
    assert 'Choose Practice Chart sharing' not in page.text
    assert protected_counts(refs) == before


def test_expired_account_return_preserves_completed_authorization(enrollment_web, monkeypatch):
    refs = enrollment_web
    client = student_client()
    review(client)
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=31))
    assert client.get(PATH + '/resume').status_code == 409
    with refs.factory() as session:
        assert session.get(AccountPrivacy, 1).age_band == '13to17'
        assert count(session, ClassroomStudentMembership) == 0


def test_account_authorization_form_retains_csrf_account_and_field_checks(enrollment_web):
    refs = enrollment_web
    with refs.factory() as session:
        session.get(AccountPrivacy, 1).age_band = 'unknown'
        session.commit()
    client = student_client()
    review(client)
    age = client.get('/account/age')
    data = {'csrf': age.context['csrf'], 'confirm_account': 'WC-TEST0001', 'age_band': '13to17'}
    for extra, expected in (({'csrf': 'bad'}, 403), ({'confirm_account': 'WC-TEST0002'}, 409),
                            ({'parent_email': 'untrusted@example.test'}, 400), ({'age_band': ''}, 409)):
        assert client.post('/account/age', data={**data, **extra}, follow_redirects=False).status_code == expected
    with refs.factory() as session:
        assert session.get(AccountPrivacy, 1).age_band == 'unknown'
        assert count(session, ClassroomStudentMembership) == 0


def test_child_cannot_use_enrollment_continuation_to_self_override_age(enrollment_web):
    refs = enrollment_web
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = 'under13', None
        original = rule.declared_at
        session.commit()
    client = student_client()
    review(client)
    age = client.get('/account/age')
    data = {'csrf': age.context['csrf'], 'confirm_account': 'WC-TEST0001'}
    assert client.post('/account/age', data={**data, 'age_band': 'adult'}, follow_redirects=False).status_code == 409
    assert client.post('/account/age', data={**data, 'age_band': 'under13'}, follow_redirects=False).headers['location'] == '/account/age'
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        assert rule.age_band == 'under13' and rule.declared_at == original and rule.public_from is None
        assert count(session, ClassroomStudentMembership) == 0


def test_normal_account_authorization_without_enrollment_context_returns_home(enrollment_web):
    refs = enrollment_web
    with refs.factory() as session:
        session.get(AccountPrivacy, 1).age_band = 'unknown'
        session.commit()
    client = student_client()
    age = client.get('/account/age')
    assert age.context['classroom_return'] is None
    response = client.post('/account/age', data={'csrf': age.context['csrf'], 'confirm_account': 'WC-TEST0001', 'age_band': '13to17'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/home'
    with refs.factory() as session:
        assert count(session, ClassroomStudentMembership) == count(session, ClassroomReportingPeriod) == 0


def test_pending_inbox_pagination_is_bounded_self_only(enrollment_web, monkeypatch):
    refs = enrollment_web
    # A smaller page exercises the real cursor without creating excess accounts.
    monkeypatch.setattr(routes.views, 'PAGE_SIZE', 1)
    first = invite(refs)
    client = student_client()
    respond(client, first, 'decline')
    second = invite(refs)
    page = client.get(PATH + '/invitations')
    assert [row['id'] for row in page.context['offers']] == [second]
    assert page.context['next_before'] == second
    older = client.get(PATH + f'/invitations?before={second}')
    assert [row['id'] for row in older.context['offers']] == [first]
    assert older.context['next_before'] is None
    direct_page = direct(director_client(), refs)
    assert len(direct_page.context['detail']['offers']) == 1
    assert direct_page.context['detail']['next_before'] == second
