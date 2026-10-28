"""R1/R2 on actual SQLite/PostgreSQL migrations and authenticated HTTP routes."""
from datetime import timedelta

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, Table, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import (account_routes, classroom, classroom_chart_routes, classroom_director, main,
                 session_revocations, verifier_routes, classroom_chart_sharing as sharing)
from app import classroom_student_invitation_routes as routes
from app import classroom_student_invitations as invitations, classroom_models as cm, models as m
from app.age_privacy import utc
from app.operator_schema_compatibility import (installed_revision, classroom_snapshot,
                                              assert_classroom_unchanged)
from test_classroom_student_invitations import stored, pin_hash, actor, student, issue, CURRENT
from tests.test_weekly2_foundation_migration import db, config, revision, rows, schema_contract, NOW


@pytest.fixture
def web(stored, monkeypatch):
    engine, clock = stored
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (routes, classroom_chart_routes, classroom_director, account_routes,
                   main, session_revocations, verifier_routes):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://testserver')
    student_client, director = TestClient(main.app), TestClient(main.app)
    assert student_client.post('/account/login', data={'woodchuck_id': 'WL2-2', 'pin': '2468'}).status_code == 200
    assert director.post('/trusted-verifiers/login', data={'email': 'wl2@example.invalid', 'pin': '2468'}).status_code == 200
    with factory() as s:
        s.add_all([m.PracticeChart(id=i, profile_id=2, practice_date=NOW.date(),
            minutes=minutes, instrument='Trumpet', note=f'private-chart-{i}') for i, minutes in ((1, 17), (2, 11))])
        s.commit()
    return factory, clock, student_client, director


PATH = '/classroom/director/1/classes/2/invitations'


def manage(client, action='view', **values):
    csrf = client.get(PATH).context['csrf']
    return client.post(PATH, data={'csrf': csrf, 'pin': '2468', 'action': action, **values})


def issued(factory):
    with factory() as s:
        ident = issue(s).id
        s.commit()
        return ident


def accept(client, ident):
    csrf = client.get(routes.STUDENT_PATH).context['csrf']
    return client.post(f'{routes.STUDENT_PATH}/{ident}/accept', data={'csrf': csrf})


def edit(client, chart_id=1, assignments=(1,)):
    csrf = client.get('/classroom/chart-sharing').json()['csrf']
    return client.post(f'/classroom/chart-sharing/{chart_id}', json={'csrf': csrf, 'assignment_ids': list(assignments)})


def visible(factory):
    with factory() as s:
        return sharing.shared_charts(s, actor=actor(s), program_id=1, class_id=2)


@pytest.mark.parametrize('loss', ['entitlement', 'assignment'])
def test_issuer_can_view_and_revoke_after_loss_without_student_access(web, loss):
    factory, clock, client, director = web
    ident = issued(factory)
    assert accept(client, ident).status_code == 200
    assert edit(client).status_code == 200
    clock[0] += timedelta(minutes=1)
    with factory() as s:
        if loss == 'entitlement':
            s.get(cm.ClassroomEntitlement, 1).ends_at = clock[0]
        else:
            assignment = s.get(cm.ClassroomTeachingAssignment, 1)
            assignment.ended_at, assignment.ended_reason, assignment.ended_by_verifier_id = clock[0], 'revoked', 1
        s.commit()
    page = manage(director)
    assert page.status_code == 200 and not page.context['can_issue']
    assert f'Invitation #{ident}' in page.text and 'Create invitation' not in page.text
    assert manage(director, 'issue', identity_code='invalid').status_code == 404
    csrf = director.get(PATH).context['csrf']
    payload = {'csrf': csrf, 'pin': '2468', 'action': 'revoke', 'invitation_id': str(ident)}
    assert director.post(PATH, data={**payload, 'csrf': 'bad'}).status_code == 403
    assert director.post(PATH, data=payload, headers={'Origin': 'https://outside.invalid'}).status_code == 403
    assert director.post(PATH, data={**payload, 'pin': '9999'}).status_code == 401
    assert director.post(PATH, data={**payload, 'profile_id': '2'}).status_code == 400
    assert director.post(PATH.replace('/classes/2/', '/classes/1/'), data=payload).status_code == 404
    other = TestClient(main.app)
    assert other.post('/trusted-verifiers/login', data={'email': 'other@example.test', 'pin': '2468'}).status_code == 200
    assert manage(other, 'revoke', invitation_id=str(ident)).status_code == 404
    statements = []
    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement.lower())
    event.listen(factory.kw['bind'], 'before_cursor_execute', capture)
    try:
        assert director.post(PATH, data=payload).status_code == 200
    finally:
        event.remove(factory.kw['bind'], 'before_cursor_execute', capture)
    assert not any(name in sql for sql in statements for name in ('woodchuck_profiles', 'practice_charts'))
    with factory() as s:
        first = s.get(cm.ClassroomStudentInvitation, ident).revoked_at
        assert first is not None
    clock[0] += timedelta(minutes=1)
    assert manage(director, 'revoke', invitation_id=str(ident)).status_code == 200
    with factory() as s:
        assert s.get(cm.ClassroomStudentInvitation, ident).revoked_at == first


@pytest.mark.parametrize('offset', [timedelta(0), timedelta(days=7, seconds=-1), timedelta(days=7), timedelta(days=7, seconds=1)])
def test_acceptance_deadline_on_routes(web, offset):
    factory, clock, client, director = web
    ident = issued(factory)
    clock[0] += offset
    timely = offset < invitations.LIFETIME
    assert accept(client, ident).status_code == (200 if timely else 404)
    with factory() as s:
        row = s.get(cm.ClassroomStudentInvitation, ident)
        assert (row.accepted_at is not None) == timely
        assert row.deadline_accepted_at == row.accepted_at
    assert visible(factory) == []  # Acceptance is never a chart grant.
    if timely:
        assert edit(client).status_code == 200
        clock[0] += timedelta(days=8) - offset
        assert client.get(routes.STUDENT_PATH).context['invitations'][0]['status'] == 'Accepted'
        assert client.get('/classroom/chart-sharing/1').json()['selected'] == [1]
        assert [r['note'] for r in visible(factory)] == ['private-chart-1']
        # Existing accepted relationships prevent duplicate issuance after day seven.
        page = client.get(routes.STUDENT_PATH)
        code = client.post(routes.STUDENT_PATH + '/identity', data={'csrf': page.context['csrf']}).context['identity_code']
        assert manage(director, 'issue', identity_code=code).status_code == 404
        assert manage(director, 'revoke', invitation_id=str(ident)).status_code == 200
        assert visible(factory) == []
        assert client.get('/classroom/chart-sharing').json()['choices'] == []
        assert edit(client).status_code == 404


def test_confirmed_enrollment_still_uses_explicit_chart_selection(web):
    factory, clock, client, _ = web
    with factory() as s:
        anchor = cm.ClassroomStudentMembership(id=2, class_id=2, profile_id=2)
        s.add(anchor); s.flush()
        s.add(cm.ClassroomMembershipPeriod(id=2, membership_id=anchor.id, starts_at=clock[0], state='active'))
        s.commit()
    assert len(client.get('/classroom/chart-sharing').json()['choices']) == 1
    assert visible(factory) == []
    assert edit(client).status_code == 200
    assert [r['note'] for r in visible(factory)] == ['private-chart-1']
    with factory() as s:
        row = s.scalar(select(cm.ClassroomChartDirectorShare))
        assert row.membership_period_id is not None and row.invitation_id is None


@pytest.mark.parametrize('replacement', [1, 2])
def test_deadline_policy_never_transfers_to_replacement_assignment(web, replacement):
    factory, clock, client, _ = web
    ident = issued(factory)
    assert accept(client, ident).status_code == 200
    assert edit(client).status_code == 200
    clock[0] += timedelta(days=8)
    assert len(visible(factory)) == 1
    with factory() as s:
        old = s.get(cm.ClassroomTeachingAssignment, 1)
        old.ended_at, old.ended_reason, old.ended_by_verifier_id = clock[0], 'revoked', 1
        s.flush()
        s.add(cm.ClassroomTeachingAssignment(id=2, program_id=1, class_id=2,
            verifier_id=replacement, starts_at=clock[0], granted_by_verifier_id=1))
        s.commit()
    assert client.get('/classroom/chart-sharing').json()['choices'] == []
    assert client.get('/classroom/chart-sharing/1').json()['selected'] == []
    assert edit(client).status_code == 404
    with factory() as s:
        assert sharing.shared_charts(s, actor=actor(s, other=replacement == 2), program_id=1, class_id=2) == []


def test_deadline_policy_keeps_recipient_and_class_isolation(web):
    factory, clock, client, _ = web
    ident = issued(factory)
    assert accept(client, ident).status_code == 200
    assert edit(client).status_code == 200
    with factory() as s:
        s.add(cm.ClassroomClassState(class_id=1, program_id=1, state='active', enrollment_open=False,
            changed_by_verifier_id=1, created_at=NOW, updated_at=NOW))
        s.add(cm.ClassroomTeachingAssignment(id=2, program_id=1, class_id=1,
            verifier_id=2, starts_at=NOW, granted_by_verifier_id=1))
        s.flush()
        row = invitations.issue(s, actor=actor(s, other=True), program_id=1, class_id=1,
            assignment_id=2, profile_id=2)
        invitations.accept(s, student=student(s), invitation_id=row.id)
        s.commit()
    clock[0] += timedelta(days=8)
    assert len(client.get('/classroom/chart-sharing').json()['choices']) == 2
    with factory() as s:
        assert sharing.shared_charts(s, actor=actor(s, other=True), program_id=1, class_id=1) == []
    assert edit(client, assignments=(2,)).status_code == 200
    assert visible(factory) == []
    with factory() as s:
        assert [r['note'] for r in sharing.shared_charts(s, actor=actor(s, other=True),
            program_id=1, class_id=1)] == ['private-chart-1']


def test_transition_never_promotes_legacy_acceptances_or_old_shares(stored):
    engine, clock = stored
    with Session(engine) as s:
        s.get(cm.ClassroomEntitlement, 1).ends_at = NOW + timedelta(days=30)
        s.commit()
    command.downgrade(config(), 'c27class001')
    # Seed the frozen old schema, including a still-live acceptance that expires
    # during the schema/application rollout gap and an old pending invitation.
    with engine.begin() as c:
        table = Table('classroom_student_invitations', MetaData(), autoload_with=c)
        for ident, offset, accepted in ((1, 0, True), (2, 2, True), (3, 8, False)):
            issued_at = NOW + timedelta(days=offset)
            c.execute(table.insert().values(id=ident, program_id=1, class_id=2, assignment_id=1,
                profile_id=2, issued_by_verifier_id=1, issued_at=issued_at,
                expires_at=issued_at + invitations.LIFETIME,
                accepted_at=issued_at + timedelta(minutes=1) if accepted else None,
                accepted_by_profile_id=2 if accepted else None))
    with Session(engine) as s:
        s.add(m.PracticeChart(id=1, profile_id=2, practice_date=NOW.date(), minutes=17, instrument='Trumpet'))
        s.flush()
        s.add(cm.ClassroomChartDirectorShare(chart_id=1, class_id=2, assignment_id=1,
            invitation_id=1, entitlement_id=1, account_declared_at=NOW,
            class_activation_at=NOW, selected_at=NOW + timedelta(days=1)))
        s.commit()
    before, shape = rows(engine), schema_contract(engine)
    command.upgrade(config(), CURRENT)
    clock[0] = NOW + timedelta(days=8)
    with Session(engine) as s:
        assert routes._summary(s.get(cm.ClassroomStudentInvitation, 1))['status'] == 'Expired'
        assert routes._summary(s.get(cm.ClassroomStudentInvitation, 2))['status'] == 'Accepted'
    after = rows(engine)
    for row in after['classroom_student_invitations']:
        assert row.pop('deadline_accepted_at') is None
    assert after == before
    with engine.connect() as c:
        assert installed_revision(c, RuntimeError) == CURRENT
    # No policy evidence exists, so a round trip is lossless, including shares.
    command.downgrade(config(), 'c27class001')
    assert rows(engine) == before and schema_contract(engine) == shape
    command.upgrade(config(), CURRENT)
    clock[0] = NOW + timedelta(days=10)
    with Session(engine) as s:
        assert sharing.choices(s, student(s)) == []
        assert sharing.shared_charts(s, actor=actor(s), program_id=1, class_id=2) == []
        for ident in (1, 2):
            with pytest.raises(classroom.ClassroomDenied):
                invitations.accept(s, student=student(s), invitation_id=ident)
            assert s.get(cm.ClassroomStudentInvitation, ident).deadline_accepted_at is None
        # Pending legacy invitations can receive a fresh timely acceptance.
        invitations.accept(s, student=student(s), invitation_id=3)
        s.commit()
    clock[0] = NOW + timedelta(days=16)
    with Session(engine) as s:
        assert [r['assignment_id'] for r in sharing.choices(s, student(s))] == [1]
        assert sharing.shared_charts(s, actor=actor(s), program_id=1, class_id=2) == []
        with pytest.raises(classroom.ClassroomDenied):
            sharing.selection_current(s, student=student(s), share_id=1)
        sharing.set_sharing(s, student=student(s), chart_id=1, assignment_ids=[1])
        assert len(sharing.shared_charts(s, actor=actor(s), program_id=1, class_id=2)) == 1
        s.commit()
    # Retain even revoked deadline-policy evidence on downgrade.
    with Session(engine) as s:
        invitations.revoke(s, actor=actor(s), invitation_id=3)
        s.commit()
    with pytest.raises(RuntimeError, match='classroom_invitation_deadline_in_use'):
        command.downgrade(config(), 'c27class001')
    assert revision(engine) == CURRENT
    with pytest.raises(RuntimeError, match='unexpected_classroom_row_change'), engine.begin() as c:
        snapshot = classroom_snapshot(c, CURRENT)
        c.execute(text('UPDATE classroom_student_invitations SET deadline_accepted_at = NULL WHERE id = 3'))
        assert_classroom_unchanged(c, CURRENT, snapshot, RuntimeError)


def test_operator_rejects_missing_deadline_state(stored):
    engine, _ = stored
    with engine.begin() as c:
        c.execute(text('ALTER TABLE classroom_student_invitations DROP COLUMN deadline_accepted_at'))
    with engine.connect() as c, pytest.raises(RuntimeError, match='classroom_student_invitations: columns'):
        installed_revision(c, RuntimeError)
