"""Synthetic end-to-end checks for explicit invited and enrolled chart sharing."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import MetaData, select

from app import classroom_s2 as s2, practice_chart_routes
from app import classroom_student_invitations as invitations
from app.age_models import AccountPrivacy
from app.classroom_models import (ClassroomChartDirectorShare, ClassroomStudentInvitation,
    ClassroomStudentMembership, ClassroomTeachingAssignment, ClassroomEntitlement)
from app.models import PracticeChart
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, join
from test_classroom_director import web, login, confirm
from test_classroom_student_invitation_routes import (invitation_web, student_client, code,
    issue, manage, path, routes)


@pytest.fixture
def invited_web(invitation_web, monkeypatch):
    monkeypatch.setattr(practice_chart_routes, 'SessionLocal', invitation_web)
    return invitation_web


def accept(factory):
    student, director, ident, csrf = issue(factory)
    assert student.get('/classroom/chart-sharing').json()['choices'] == []
    assert student.post(f'{routes.STUDENT_PATH}/{ident}/accept', data={'csrf': csrf}).status_code == 200
    choices = student.get('/classroom/chart-sharing').json()
    assert len(choices['choices']) == 1
    return student, director, ident, choices['choices'][0]['assignment_id'], choices['csrf']


def submit(client, assignment=None, key='invited-chart'):
    return client.post('/practice-charts', json={
        'practice_date': datetime.now(ZoneInfo('America/Chicago')).date().isoformat(),
        'minutes': 12, 'note': key, 'practice_details': ['Scales'], 'submission_key': key,
        'include_contests': False, 'include_team_contests': False,
        'classroom_assignment_ids': [] if assignment is None else [assignment]})


def edit(client, chart, csrf, assignments, **extra):
    return client.post(f'/classroom/chart-sharing/{chart}',
        json={'csrf': csrf, 'assignment_ids': assignments, **extra})


def visible(factory, director, note):
    return note in confirm(director, f'/classroom/director/1/classes/{factory.ids[0]}').text


def personal_snapshot(factory):
    with factory.kw['bind'].connect() as c:
        md = MetaData(); md.reflect(c)
        # Editing sharing must not touch ANY other stored record: minutes,
        # verification, contests, rewards, personal stats, or invitation history.
        return {name: c.execute(select(t).order_by(*t.primary_key.columns)).all()
                for name, t in md.tables.items() if name != 'classroom_chart_director_shares'}


def test_acceptance_submission_editing_and_historical_privacy(invited_web):
    client = student_client()
    private = submit(client, key='private-before-invitation')
    assert private.status_code == 201
    old = private.json()['chart']['id']
    client, director, ident, assignment, csrf = accept(invited_web)
    assert not visible(invited_web, director, 'private-before-invitation')
    assert client.get(f'/classroom/chart-sharing/{old}').json()['selected'] == []
    # No selected checkbox still means private, even after acceptance.
    assert submit(client, key='private-after-invitation').status_code == 201
    assert not visible(invited_web, director, 'private-after-invitation')
    response = submit(client, assignment)
    assert response.status_code == 201, response.text
    chart = response.json()['chart']['id']
    assert submit(client, assignment).status_code == 201  # existing replay response
    assert visible(invited_web, director, 'invited-chart')
    assert client.get(f'/classroom/chart-sharing/{chart}').json()['selected'] == [assignment]
    with invited_web() as s:
        share = s.scalar(select(ClassroomChartDirectorShare))
        assert (share.chart_id, share.class_id, share.assignment_id, share.invitation_id,
                share.membership_period_id) == (chart, invited_web.ids[0], assignment, ident, None)
        assert s.scalar(select(ClassroomStudentMembership.id)) is None
    before = personal_snapshot(invited_web)
    assert edit(client, chart, csrf, [], minutes=999).status_code == 422
    assert edit(client, chart, csrf, []).status_code == 200
    assert not visible(invited_web, director, 'invited-chart')
    assert edit(client, old, csrf, [assignment]).status_code == 200
    assert visible(invited_web, director, 'private-before-invitation')
    assert personal_snapshot(invited_web) == before
    other = student_client(True)
    other_csrf = other.get('/classroom/chart-sharing').json()['csrf']
    assert other.get(f'/classroom/chart-sharing/{old}').status_code == 404
    assert edit(other, old, other_csrf, [assignment]).status_code == 404
    assert not visible(invited_web, login(3), 'private-before-invitation')


@pytest.mark.parametrize('change', ['legacy_expired', 'revoked', 'replacement', 'same_teacher',
    'entitlement', 'under13', 'unknown', 'suspended', 'class_archived'])
def test_current_sources_required_on_every_read_and_edit(invited_web, change):
    client, director, ident, assignment, csrf = accept(invited_web)
    response = submit(client, assignment)
    assert response.status_code == 201
    chart = response.json()['chart']['id']
    assert visible(invited_web, director, 'invited-chart')
    invited_web.clock[0] += timedelta(minutes=1)
    with invited_web() as s:
        if change == 'legacy_expired':
            s.get(ClassroomStudentInvitation, ident).deadline_accepted_at = None
            invited_web.clock[0] += invitations.LIFETIME
        elif change == 'revoked':
            invitations.revoke(s, actor=actor(s, 2), invitation_id=ident)
        elif change in ('replacement', 'same_teacher'):
            s2.end_teaching(s, actor=actor(s), program_id=1, assignment_id=assignment)
            replacement = s2.assign_teacher(s, actor=actor(s), program_id=1,
                class_id=invited_web.ids[0], verifier_id=3 if change == 'replacement' else 2)
            assert replacement.id != assignment
        elif change == 'entitlement':
            s.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.program_id == 1)).ends_at = invited_web.clock[0]
        elif change == 'suspended':
            member = join(s, visible='FIRST2026')
            invited_web.clock[0] += timedelta(minutes=1)
            s2.suspend_member(s, actor=actor(s), program_id=1, class_id=invited_web.ids[0], membership_id=member.id)
        elif change == 'class_archived':
            s2.set_class_state(s, actor=actor(s), program_id=1, class_id=invited_web.ids[0], state='archived')
        else:
            s.get(AccountPrivacy, 1).age_band = change
        s.commit()
    assert not visible(invited_web, director, 'invited-chart')
    if change == 'replacement':
        assert not visible(invited_web, login(3), 'invited-chart')
    options = client.get('/classroom/chart-sharing').json()
    assert options['choices'] == []
    selected = client.get(f'/classroom/chart-sharing/{chart}')
    assert selected.status_code == 404 or selected.json()['selected'] == []
    assert edit(client, chart, csrf, [assignment]).status_code == 404
    # Relationship loss permits withdrawal; existing account eligibility rules
    # still deny mutations for unknown-age and unauthorized under-13 accounts.
    assert edit(client, chart, csrf, []).status_code == (404 if change in ('under13', 'unknown') else 200)


def test_no_fallback_or_revival_and_both_paths_coexist(invited_web):
    client, director, ident, assignment, csrf = accept(invited_web)
    response = submit(client, assignment)
    chart = response.json()['chart']['id']
    with invited_web() as s:
        join(s, visible='FIRST2026')
        invitations.revoke(s, actor=actor(s, 2), invitation_id=ident)
        s.commit()
    # Current enrollment offers the same recipient, but cannot rescue the old
    # invitation-backed share. A deliberate edit can create a new enrollment share.
    assert len(client.get('/classroom/chart-sharing').json()['choices']) == 1
    assert not visible(invited_web, director, 'invited-chart')
    assert client.get(f'/classroom/chart-sharing/{chart}').json()['selected'] == []
    assert submit(client, assignment).status_code == 409
    assert edit(client, chart, csrf, [assignment]).status_code == 200
    assert visible(invited_web, director, 'invited-chart')
    with invited_web() as s:
        active = s.scalar(select(ClassroomChartDirectorShare).where(ClassroomChartDirectorShare.withdrawn_at.is_(None)))
        assert active.invitation_id is None and active.membership_period_id is not None
        assert len(s.scalars(select(ClassroomChartDirectorShare)).all()) == 2


def test_multiple_classes_recipient_isolation_and_explicit_switch(invited_web):
    client, director, ident, assignment, csrf = accept(invited_web)
    with invited_web() as s:
        other = s2.assign_teacher(s, actor=actor(s), program_id=2, class_id=invited_web.ids[1], verifier_id=3)
        other_id = other.id
        s.commit()
    token, student_csrf = code(client)
    second_path = f'/classroom/director/2/classes/{invited_web.ids[1]}/invitations'
    issued = manage(login(3), second_path, 'issue', identity_code=token)
    assert issued.status_code == 200
    ident2 = issued.context['invitations'][0]['id']
    assert client.post(f'{routes.STUDENT_PATH}/{ident2}/accept', data={'csrf': student_csrf}).status_code == 200
    assert len(client.get('/classroom/chart-sharing').json()['choices']) == 2
    response = submit(client, assignment)
    chart = response.json()['chart']['id']
    second_class = f'/classroom/director/2/classes/{invited_web.ids[1]}'
    assert 'invited-chart' not in confirm(login(3), second_class).text
    assert edit(client, chart, csrf, [assignment, other_id]).status_code == 422
    assert edit(client, chart, csrf, [other_id]).status_code == 404
    assert edit(client, chart, csrf, []).status_code == 200
    assert edit(client, chart, csrf, [other_id]).status_code == 200
    assert not visible(invited_web, director, 'invited-chart')
    assert 'invited-chart' in confirm(login(3), second_class).text


def test_gate_disabled_accepted_invitation_is_inert(invited_web, monkeypatch):
    client, director, ident, assignment, csrf = accept(invited_web)
    chart = submit(client, assignment).json()['chart']['id']
    for flag in ('CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED'):
        with monkeypatch.context() as off:
            off.delenv(flag)
            assert client.get('/classroom/chart-sharing').status_code == 404
            assert edit(client, chart, csrf, [assignment]).status_code == 404
            assert submit(client, assignment, key='must-not-save').status_code == 400
    with invited_web() as s:
        assert s.scalar(select(PracticeChart.id).where(PracticeChart.submission_key == 'must-not-save')) is None


def test_new_invitation_cannot_revive_old_share(invited_web):
    client, director, ident, assignment, csrf = accept(invited_web)
    chart = submit(client, assignment).json()['chart']['id']
    assert manage(director, path(invited_web), 'revoke', invitation_id=str(ident)).status_code == 200
    client, director, new_id, student_csrf = issue(invited_web, client, director)
    assert new_id != ident
    assert client.post(f'{routes.STUDENT_PATH}/{new_id}/accept', data={'csrf': student_csrf}).status_code == 200
    assert len(client.get('/classroom/chart-sharing').json()['choices']) == 1
    assert not visible(invited_web, director, 'invited-chart')
    assert client.get(f'/classroom/chart-sharing/{chart}').json()['selected'] == []
    assert submit(client, assignment).status_code == 409
    assert edit(client, chart, csrf, [assignment]).status_code == 200
    assert visible(invited_web, director, 'invited-chart')
    with invited_web() as s:
        assert s.scalar(select(ClassroomChartDirectorShare.invitation_id).where(
            ClassroomChartDirectorShare.withdrawn_at.is_(None))) == new_id


def test_consent_linked_account_cannot_use_invited_share(invited_web, monkeypatch):
    from app import child_authorization
    from app.child_models import ConsentEvidence
    client, director, ident, assignment, csrf = accept(invited_web)
    chart = submit(client, assignment).json()['chart']['id']
    monkeypatch.setattr(child_authorization, 'under13_available', lambda: True)
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda: ('synthetic', '', 'a' * 64))
    with invited_web() as s:
        evidence = ConsentEvidence(profile_id=1, parent_email='parent@example.test',
            notice_version='synthetic', notice_sha256='a' * 64,
            approved_at=invited_web.clock[0], confirmed_at=invited_web.clock[0])
        s.add(evidence); s.flush()
        rule = s.get(AccountPrivacy, 1)
        rule.consent_id = evidence.id
        s.flush()
        assert child_authorization.consent_active(s, rule)
        s.commit()
    assert not visible(invited_web, director, 'invited-chart')
    assert client.get('/classroom/chart-sharing').json()['choices'] == []
    assert edit(client, chart, csrf, [assignment]).status_code == 404
