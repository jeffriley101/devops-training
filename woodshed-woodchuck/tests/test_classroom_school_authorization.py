"""Synthetic independently recorded authority; no legal/provider approval is implied."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select

from app import classroom as s1, classroom_s2 as s2, classroom_school_authorization as school
from app.classroom_school_models import ClassroomSchoolAuthorization as Approval
from app.classroom_models import ClassroomMembershipPeriod, ClassroomChartDirectorShare
from app.models import PracticeChart
from app.student_practice_metrics import CENTRAL
from test_classroom_guardian_authorization import (guardian_data, migrated_db, captured, adult_pin_hash,
    actor, read, parent_accept, parent_proof, student, fully_authorize)
from test_classroom_s2 import admin
from test_classroom_s1_postgres import ordered_wait


@pytest.fixture
def school_data(guardian_data, monkeypatch):
    monkeypatch.setenv('CLASSROOM_SCHOOL_AUTHORIZATION_ENABLED', '1')
    return guardian_data


def verify(session, refs, *, kind='director', approver_id=2, **changes):
    now = refs.clock[0]
    year = changes.get('year_start', school.school_year(now))
    values = dict(admin=admin(), reviewer=actor(session, 1), program_id=1, approver_id=approver_id,
        authority_kind=kind, school_name='Synthetic Example School',
        authority_description='School official confirmed authority to approve this Program Classroom agreement.',
        contact_name='Synthetic School Principal', contact_role='Principal', contact_address='+1 (555) 010-1234',
        contact_source='https://school.example.test/official-directory', verification_method='phone',
        verification_notes='Synthetic documented call: official confirmed the named approver and exact Program authority.',
        contacted_at=now-timedelta(minutes=5), year_start=year, independently_confirmed=True,
        previous=school.revision(school._latest(session, changes.get('program_id', 1), year)))
    return school.verify_authority(session, **(values | changes))


def approve(session, refs, row, *, adult_id=None, **changes):
    return school.approve(session, **(dict(actor=actor(session, adult_id or row.approver_id),
        program_id=1, record_id=row.id, previous=school.revision(row),
        agreement_version=school.AGREEMENT_VERSION, agreement_digest=school.AGREEMENT_DIGEST,
        explicit=True) | changes))


def simulated_legal_review(monkeypatch):
    # Unit-test only: the shipped live policy has NO approved version.
    monkeypatch.setattr(school, 'LEGALLY_APPROVED_AGREEMENTS',
        frozenset({(school.AGREEMENT_VERSION, school.AGREEMENT_DIGEST)}))


@pytest.mark.parametrize('kind,adult_id', [('director', 2), ('administrator', 3)])
@pytest.mark.parametrize('method', ['phone', 'official_email'])
def test_either_verified_approver_uses_the_same_agreement_without_student_permissions(school_data, kind, adult_id, method):
    refs = school_data
    with refs.factory() as session:
        row = verify(session, refs, kind=kind, approver_id=adult_id, verification_method=method,
            contact_address='principal@school.example.test' if method == 'official_email' else '+1 555 010 1234')
        session.commit()
        assert row.verified_by_id == 1 and row.approver_id == adult_id and row.approved_at is None
        with pytest.raises(s1.ClassroomDenied): approve(session, refs, row, adult_id=1)
        approve(session, refs, row); session.commit()
        assert row.agreement_version == school.AGREEMENT_VERSION
        assert school.status(row, refs.clock[0]) == 'legal_review_pending'
        assert not school.authorized_program(session, program_id=1)
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert session.scalar(select(ClassroomMembershipPeriod)).ended_at is None
        assert session.scalar(select(func.count(ClassroomChartDirectorShare.id))) == 0
        assert session.scalar(select(func.count(PracticeChart.id))) == 0
        displayed = school.view(session, actor=actor(session, adult_id), program_id=1)
        assert displayed['records'][0]['approver'] == f'Synthetic Adult {adult_id}'
        assert 'evidence' not in displayed['records'][0]  # official contact stays on the staff screen


@pytest.mark.parametrize('failure', ['no_staff', 'self_review', 'self_contact', 'self_email', 'no_confirmation', 'future_call', 'http_source', 'empty_call'])
def test_roles_school_email_and_self_assertions_never_establish_authority(school_data, failure):
    refs = school_data
    with refs.factory() as session:
        changes = {'no_staff': {'admin':actor(session)}, 'self_review': {'reviewer':actor(session, 2)},
            'self_contact': {'contact_name':'Synthetic Adult 2'}, 'self_email': {'contact_address':'adult2@example.test'},
            'no_confirmation': {'independently_confirmed':False},
            'future_call': {'contacted_at':refs.clock[0]+timedelta(seconds=1)},
            'http_source': {'contact_source':'http://school.example.test/directory'},
            'empty_call': {'verification_notes':' '}}[failure]
        with pytest.raises(s1.ClassroomDenied): verify(session, refs, **changes)
        assert session.scalar(select(func.count(Approval.id))) == 0


def test_midyear_renewal_expiration_and_revocation_never_renew_automatically(school_data, monkeypatch):
    refs = school_data
    simulated_legal_review(monkeypatch)
    with refs.factory() as session:
        row = verify(session, refs); session.commit()
        assert not school.authorized_program(session, program_id=1)
        approve(session, refs, row); session.commit()
        assert row.approved_at == refs.clock[0]
        assert school.authorized_program(session, program_id=1)
        end = school.year_bounds(row.year_start)[1]
        refs.clock[0] = end
        assert school.status(row, refs.clock[0]) == 'expired'
        assert not school.authorized_program(session, program_id=1)
        renewed = verify(session, refs, kind='administrator', approver_id=3); session.commit()
        assert renewed.id != row.id and renewed.year_start == row.year_start+1
        assert not school.authorized_program(session, program_id=1)
        approve(session, refs, renewed); session.commit()
        assert school.authorized_program(session, program_id=1)
        previous = school.revision(renewed)
        school.revoke(session, actor=actor(session, 3), program_id=1, record_id=renewed.id,
            previous=previous, reason='authority_revoked'); session.commit()
        assert not school.authorized_program(session, program_id=1)
        with pytest.raises(s1.ClassroomDenied): approve(session, refs, renewed, previous=previous)
        assert session.scalar(select(func.count(Approval.id))) == 2


def test_school_approval_never_opens_history_or_replaces_join_share_and_parent_consent(school_data, monkeypatch):
    refs = school_data
    simulated_legal_review(monkeypatch)
    with refs.factory() as session:
        row = verify(session, refs); session.commit(); approve(session, refs, row); session.commit()
        assert school.authorized_program(session, program_id=1)
        statements = []
        def capture(_c, _u, sql, *_args): statements.append(sql.lower())
        event.listen(refs.factory.engine, 'before_cursor_execute', capture)
        try:
            with pytest.raises(s1.ClassroomDenied): read(session, refs)
        finally:
            event.remove(refs.factory.engine, 'before_cursor_execute', capture)
        assert not any('from practice_charts' in sql for sql in statements)
        assert session.scalar(select(func.count(ClassroomChartDirectorShare.id))) == 0
        with pytest.raises(s1.ClassroomDenied):
            school.approve(session, actor=actor(session, 2), program_id=2, record_id=row.id,
                previous=school.revision(row), agreement_version=school.AGREEMENT_VERSION,
                agreement_digest=school.AGREEMENT_DIGEST, explicit=True)


def test_tampered_or_stale_verified_evidence_never_becomes_an_approval(school_data, monkeypatch):
    refs = school_data
    simulated_legal_review(monkeypatch)
    with refs.factory() as session:
        row = verify(session, refs); session.commit()
        revision = school.revision(row)
        school.revoke(session, actor=actor(session), program_id=1, record_id=row.id,
            previous=revision, reason='replaced'); session.commit()
        replacement = verify(session, refs, approver_id=3, kind='administrator'); session.commit()
        with pytest.raises(s1.ClassroomDenied): approve(session, refs, row, previous=revision)
        replacement.authority_description = 'Changed without new official contact verification'
        session.commit()
        with pytest.raises(s1.ClassroomDenied): approve(session, refs, replacement)
        assert not school.authorized_program(session, program_id=1)


@pytest.mark.parametrize('instant,year', [('2026-07-01T04:59:59+00:00', 2025),
    ('2026-07-01T05:00:00+00:00', 2026), ('2027-01-01T00:00:00+00:00', 2026)])
def test_july_year_boundary_uses_existing_central_calendar(instant, year):
    assert school.school_year(datetime.fromisoformat(instant)) == year
    start, end = school.year_bounds(year)
    assert start.astimezone(CENTRAL).isoformat() == f'{year}-07-01T00:00:00-05:00'
    assert end.astimezone(CENTRAL).isoformat() == f'{year+1}-07-01T00:00:00-05:00'


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_postgres_competing_verifications_observe_wait_and_keep_one_year_record(school_data):
    refs = school_data
    result = ordered_wait(refs.factory, lambda session: verify(session, refs, previous=(None,None,None,None)).id,
        lambda session: verify(session, refs, previous=(None,None,None,None)).id)
    assert tuple(row[0] for row in result) == ('ok', 'denied')
    with refs.factory() as session: assert session.scalar(select(func.count(Approval.id))) == 1


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('revoke_first', [False, True])
def test_postgres_approval_and_revocation_use_the_same_program_fence(school_data, revoke_first, monkeypatch):
    refs = school_data
    simulated_legal_review(monkeypatch)
    with refs.factory() as session:
        row = verify(session, refs); session.commit(); ident = row.id; original = school.revision(row)
    def accept(session):
        cached = session.get(Approval, ident)
        return approve(session, refs, cached, previous=original).id
    def revoke(session):
        s1._lock_program(session, 1)
        current = session.get(Approval, ident, populate_existing=True)
        return school.revoke(session, actor=actor(session), program_id=1, record_id=ident,
            previous=school.revision(current) if not revoke_first else original, reason='authority_revoked').id
    result = ordered_wait(refs.factory, *( (revoke, accept) if revoke_first else (accept, revoke) ))
    assert tuple(row[0] for row in result) == (('ok','denied') if revoke_first else ('ok','ok'))
    with refs.factory() as session: assert not school.authorized_program(session, program_id=1)


def adult_client(ident):
    from app.main import app
    client = TestClient(app)
    assert client.post('/trusted-verifiers/login', data={'email':f'adult{ident}@example.test','pin':'2468'}).status_code == 200
    client.school_actor = ident
    return client


def web_view(client, path):
    page = client.get(path)
    assert page.status_code == 200, page.text
    result = client.post(path, data={'csrf':page.context['csrf'], 'director':str(client.school_actor),
        'pin':'2468', 'action':'view'})
    assert result.status_code == 200, result.text
    return result


def test_school_http_exact_actor_csrf_origin_scope_and_evidence_separation(school_data):
    refs = school_data
    with refs.factory() as session: row = verify(session, refs, kind='administrator', approver_id=3); session.commit()
    client = adult_client(3)
    path = '/classroom/school/1'
    page = web_view(client, path)
    assert 'Synthetic Example School' in page.text and 'Synthetic School Principal' not in page.text
    assert page.headers['cache-control'] == 'no-store' and page.headers['x-frame-options'] == 'DENY'
    token = page.context['detail']['records'][0]['token']
    data = {'csrf':page.context['csrf'], 'director':'3', 'pin':'2468', 'action':'approve', 'token':token, 'approve':'yes'}
    for patch, expected in [({'csrf':'bad'},403), ({'director':'2'},401), ({'approve':'no'},409),
        ({'token':'forged'},409), ({'fields':'notes'},400)]:
        assert client.post(path, data=data|patch).status_code == expected
    assert client.post(path, data=data, headers={'Origin':'https://outside.example.test'}).status_code == 403
    assert client.post('/classroom/school/2', data=data).status_code == 409
    assert client.post(path, data=data).status_code == 200
    assert client.post(path, data=data).status_code == 409  # stale original form
    assert client.get('/admin/classroom/school/1').status_code == 403
    outsider = adult_client(2)
    # Teacher may see organizational status but never sign as the verified administrator.
    other = web_view(outsider, path)
    assert other.context['detail']['records'][0]['can_approve'] is False
    assert other.context['detail']['records'][0]['can_revoke'] is False
