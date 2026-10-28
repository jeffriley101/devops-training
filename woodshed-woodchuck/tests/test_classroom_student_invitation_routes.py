"""Existing-account invitations, without enrollment or chart/reporting access."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import classroom, classroom_s2 as s2, classroom_reporting as reporting, main
from app import classroom_chart_routes, classroom_student_invitation_routes as routes
from app import classroom_student_invitations as service
from app.age_models import AccountPrivacy
from app.classroom_models import ClassroomStudentInvitation, ClassroomTeachingAssignment, ClassroomEntitlement
from app.models import WoodchuckProfile
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW
from test_classroom_director import web, login, confirm


@pytest.fixture
def invitation_web(web, monkeypatch, adult_pin_hash):
    monkeypatch.setattr(routes, 'SessionLocal', web)
    monkeypatch.setattr(classroom_chart_routes, 'SessionLocal', web)
    clock = [NOW]
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    monkeypatch.setattr(reporting, '_now', lambda: clock[0])
    with web() as s:
        s.add(WoodchuckProfile(id=2, woodchuck_id='WC-SECOND', display_name='Another private student',
            pin_hash=adult_pin_hash, instrument='Flute', level='Beginner', goal='Practice'))
        s.flush()
        s.add(AccountPrivacy(profile_id=2, age_band='13to17', declared_at=NOW, public_from=NOW))
        s.commit()
    web.clock = clock
    return web


def student_client(second=False):
    client = TestClient(main.app)
    result = client.post('/account/login', data={'woodchuck_id': 'WC-SECOND' if second else 'WC-SYNTHETIC', 'pin': '2468'})
    assert result.status_code == 200
    return client


def code(client):
    page = client.get(routes.STUDENT_PATH)
    assert page.status_code == 200
    response = client.post(routes.STUDENT_PATH + '/identity', data={'csrf': page.context['csrf']})
    assert response.status_code == 200, response.text
    return response.context['identity_code'], response.context['csrf']


def path(factory):
    return f'/classroom/director/1/classes/{factory.ids[0]}/invitations'


def manage(director, url, action='view', **data):
    csrf = director.get(url).context['csrf']
    return director.post(url, data={'csrf': csrf, 'pin': '2468', 'action': action, **data})


def issue(factory, student=None, director=None):
    student = student or student_client()
    director = director or login(2)
    token, csrf = code(student)
    response = manage(director, path(factory), 'issue', identity_code=token)
    assert response.status_code == 200, response.text
    return student, director, response.context['invitations'][0]['id'], csrf


def test_invitation_end_to_end_no_enrollment_or_data_access(invitation_web):
    from sqlalchemy import MetaData
    db = invitation_web.kw['bind']
    def snapshot():
        with db.connect() as c:
            md = MetaData(); md.reflect(c)
            return {name: c.execute(select(t).order_by(*t.primary_key.columns)).all()
                    for name, t in md.tables.items() if name != 'classroom_student_invitations'}
    student, director, other = student_client(), login(2), student_client(True)
    before = snapshot()
    student, director, ident, csrf = issue(invitation_web, student, director)
    assert 'Private Student' not in manage(director, path(invitation_web)).text
    assert 'WC-SYNTHETIC' not in manage(director, path(invitation_web)).text
    page = student.get(routes.STUDENT_PATH)
    assert f'Invitation #{ident}' in page.text and 'Adult 2' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['referrer-policy'] == 'no-referrer'
    assert f'Invitation #{ident}' not in other.get(routes.STUDENT_PATH).text
    url = f'{routes.STUDENT_PATH}/{ident}/accept'
    assert student.post(url, data={'csrf': csrf}, follow_redirects=False).status_code == 303
    assert student.post(url, data={'csrf': csrf}).status_code == 404
    assert student.get(routes.STUDENT_PATH).context['invitations'][0]['status'] == 'Accepted'
    assert len(student.get('/classroom/chart-sharing').json()['choices']) == 1
    assert manage(director, path(invitation_web), 'revoke', invitation_id=str(ident)).status_code == 200
    assert student.get(routes.STUDENT_PATH).context['invitations'][0]['status'] == 'Revoked'
    assert student.post(url, data={'csrf': csrf}).status_code == 404
    assert snapshot() == before


def test_direct_requests_csrf_origin_cross_student_and_cross_class(invitation_web):
    student, director, ident, csrf = issue(invitation_web)
    other = student_client(True)
    _, other_csrf = code(other)
    accept_url = f'{routes.STUDENT_PATH}/{ident}/accept'
    assert other.post(accept_url, data={'csrf': other_csrf}).status_code == 404
    assert student.post(accept_url, data={'csrf': 'bad'}).status_code == 403
    assert student.post(accept_url, data={'csrf': csrf}, headers={'Origin': 'https://foreign.invalid'}).status_code == 403
    assert student.post(accept_url, data={'csrf': csrf, 'profile_id': 1}).status_code == 400
    guest = TestClient(main.app)
    assert guest.get(routes.STUDENT_PATH).status_code == 401
    assert guest.get(path(invitation_web)).status_code == 401
    # Owners and unrelated teachers cannot invite simply because they can see metadata.
    assert manage(login(1), path(invitation_web)).status_code == 404
    assert manage(login(3), path(invitation_web), 'revoke', invitation_id=str(ident)).status_code == 404
    cross_path = f'/classroom/director/2/classes/{invitation_web.ids[1]}/invitations'
    assert manage(director, cross_path, 'revoke', invitation_id=str(ident)).status_code == 404
    assert manage(director, path(invitation_web), 'issue', profile_id='1', identity_code='bad').status_code == 400
    assert student.post(f'{routes.STUDENT_PATH}/999999/accept', data={'csrf': csrf}).status_code == 404


def test_identity_proof_not_account_search_and_no_replay(invitation_web):
    student, director = student_client(), login(2)
    token, _ = code(student)
    captured = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement.lower())
    engine = invitation_web.kw['bind']
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        for value in ('WC-SYNTHETIC', 'WC-DOES-NOT-EXIST', token + 'x'):
            captured.clear()
            response = manage(director, path(invitation_web), 'issue', identity_code=value)
            assert response.status_code == 404 and response.json()['detail'] == service.UNAVAILABLE
            assert not any('woodchuck_profiles' in sql for sql in captured)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    issued = manage(director, path(invitation_web), 'issue', identity_code=token)
    assert issued.status_code == 200
    assert manage(director, path(invitation_web), 'issue', identity_code=token).status_code == 404
    ident = issued.context['invitations'][0]['id']
    assert manage(director, path(invitation_web), 'revoke', invitation_id=str(ident)).status_code == 200
    invitation_web.clock[0] += s2.INTENT_LENGTH
    assert manage(director, path(invitation_web), 'issue', identity_code=token).status_code == 404


def test_account_credential_change_invalidates_identity_code(invitation_web):
    student, director = student_client(), login(2)
    token, _ = code(student)
    with invitation_web() as s:
        s.get(WoodchuckProfile, 1).session_version += 1
        s.commit()
    assert manage(director, path(invitation_web), 'issue', identity_code=token).status_code == 404
    with invitation_web() as s:
        assert s.scalar(select(ClassroomStudentInvitation.id)) is None


@pytest.mark.parametrize('change', ['expired', 'revoked', 'teacher', 'entitlement', 'under13', 'unknown', 'credential'])
def test_invalid_sources_block_acceptance(invitation_web, change):
    student, director, ident, csrf = issue(invitation_web)
    with invitation_web() as s:
        if change == 'expired':
            invitation_web.clock[0] += service.LIFETIME
        elif change == 'revoked':
            service.revoke(s, actor=actor(s, 2), invitation_id=ident)
        elif change == 'teacher':
            assignment = s.scalar(select(ClassroomTeachingAssignment).where(ClassroomTeachingAssignment.class_id == invitation_web.ids[0]))
            s2.end_teaching(s, actor=actor(s), program_id=1, assignment_id=assignment.id)
            s2.assign_teacher(s, actor=actor(s), program_id=1, class_id=invitation_web.ids[0], verifier_id=3)
        elif change == 'entitlement':
            invitation_web.clock[0] += timedelta(seconds=1)
            s.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.program_id == 1)).ends_at = invitation_web.clock[0]
        elif change == 'credential':
            s.get(WoodchuckProfile, 1).session_version += 1
        else:
            s.get(AccountPrivacy, 1).age_band = change
        s.commit()
    assert student.post(f'{routes.STUDENT_PATH}/{ident}/accept', data={'csrf': csrf}).status_code in (401, 404)
    with invitation_web() as s:
        assert s.get(ClassroomStudentInvitation, ident).accepted_at is None


def test_gates_block_all_new_routes(invitation_web, monkeypatch):
    student, director, ident, csrf = issue(invitation_web)
    for flag in ('CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED'):
        with monkeypatch.context() as off:
            off.delenv(flag)
            for url in (routes.STUDENT_PATH, path(invitation_web)):
                assert student.get(url).status_code == 404
            assert student.post(routes.STUDENT_PATH + '/identity', data={'csrf': csrf}).status_code == 404
            assert student.post(f'{routes.STUDENT_PATH}/{ident}/accept', data={'csrf': csrf}).status_code == 404
            assert director.post(path(invitation_web), data={}).status_code == 404
