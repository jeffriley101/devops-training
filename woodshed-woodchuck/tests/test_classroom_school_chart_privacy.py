"""All ages use explicit individual selections; new parent scope is legally gated."""
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from app import classroom as s1, classroom_s2 as s2, classroom_chart_consent as consent
from app import classroom_chart_sharing as sharing, classroom_guardian_authorization as guardian, parent_access
from app.age_models import AccountPrivacy
from app.child_models import DirectorPermission
from app.classroom_models import ClassroomChartDirectorShare, ClassroomProgressAuthorization
from app.models import PracticeChart, TrustedVerifier
from test_classroom_guardian_authorization import (guardian_data, migrated_db, captured, adult_pin_hash,
    actor, student, fully_authorize, read, parent_accept, parent_proof, parent_request)
from test_private_practice import connect
from test_classroom_s1_postgres import ordered_wait


@pytest.fixture
def proposed_chart_data(guardian_data, monkeypatch):
    refs = guardian_data
    monkeypatch.setenv('CLASSROOM_GUARDIAN_PCHART_SHARING_ENABLED', '1')
    # Explicit test-only simulation of legal acceptance. Shipped policy stays empty.
    monkeypatch.setattr(consent, 'LEGALLY_APPROVED_POLICIES', frozenset({consent.POLICY_VERSION}))
    refs.private_director = connect(refs.captured[0])
    with refs.factory() as session:
        director = session.scalar(select(TrustedVerifier).where(TrustedVerifier.email == 'director-a@example.test'))
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=refs.class_id, verifier_id=director.id)
        first = PracticeChart(profile_id=refs.profile_id, practice_date=refs.clock[0].date()-timedelta(days=14),
            created_at=refs.clock[0]-timedelta(days=13), minutes=19, instrument='Flute', note='EXPLICIT_PRIVATE_CHART')
        second = PracticeChart(profile_id=refs.profile_id, practice_date=refs.clock[0].date()-timedelta(days=12),
            created_at=refs.clock[0]-timedelta(days=11), minutes=999, instrument='Flute', note='UNSELECTED_PRIVATE_CHART')
        session.add_all([first, second]); session.commit()
        refs.private_assignment_id, refs.private_director_id = assignment.id, director.id
        refs.chart_id, refs.hidden_chart_id = first.id, second.id
        refs.permission_id = session.scalar(select(DirectorPermission.id))
    return refs


def teacher(session):
    return s1.authenticate_adult(session, email='director-a@example.test', pin='1357')


def chart_read(session, refs, **changes):
    return sharing.shared_charts(session, **(dict(actor=teacher(session), program_id=1,
        class_id=refs.class_id) | changes))


@pytest.mark.parametrize('guardian_data', [{'under13':True}, {'under13':False}], indirect=True)
def test_individual_chart_selection_and_exact_parent_permission_work_the_same_across_ages(proposed_chart_data):
    refs = proposed_chart_data
    with refs.factory() as session:
        assert chart_read(session, refs) == []
        choices = sharing.choices(session, student(session))
        assert [row['assignment_id'] for row in choices] == [refs.private_assignment_id]
        with pytest.raises(s1.ClassroomDenied):
            sharing.set_sharing(session, student=student(session), chart_id=refs.chart_id,
                assignment_ids=[refs.assignment_id])  # another Class teacher is not the named parent recipient
        sharing.set_sharing(session, student=student(session), chart_id=refs.chart_id,
            assignment_ids=[refs.private_assignment_id]); session.commit()
        result = chart_read(session, refs)
        assert len(result) == 1 and result[0]['minutes'] == 19 and result[0]['note'] == 'EXPLICIT_PRIVATE_CHART'
        assert 'UNSELECTED_PRIVATE_CHART' not in str(result) and 'Synthetic A' not in str(result)
        row = session.scalar(select(ClassroomChartDirectorShare))
        assert row.guardian_permission_id == refs.permission_id
        assert session.scalar(select(func.count(ClassroomProgressAuthorization.id))) == 0
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        sharing.set_sharing(session, student=student(session), chart_id=refs.chart_id, assignment_ids=[])
        session.commit()
        assert chart_read(session, refs) == []


@pytest.mark.parametrize('guardian_data', [{'under13':True}], indirect=True)
def test_under13_comprehensive_reporting_remains_denied_with_native_parent_evidence(proposed_chart_data):
    refs = proposed_chart_data
    with refs.factory() as session:
        parent_accept(session, refs, 'participation'); session.commit()
        with pytest.raises(s1.ClassroomDenied): parent_accept(session, refs, 'reporting')
        choice = guardian.choice(session, parent=parent_proof(session, refs), program_id=1,
            class_id=refs.class_id, purpose='reporting')
        assert choice['under13_reporting'] and not choice['eligible']
        statements = []
        def capture(_c, _u, sql, *_args): statements.append(sql.lower())
        event.listen(refs.factory.engine, 'before_cursor_execute', capture)
        try:
            with pytest.raises(s1.ClassroomDenied): read(session, refs)
        finally:
            event.remove(refs.factory.engine, 'before_cursor_execute', capture)
        assert not any('from practice_charts' in sql for sql in statements)
    page = refs.parent.get('/classroom/parent')
    assert 'Comprehensive director reporting is unavailable for under-13 students' in page.text
    assert 'Authorize reporting' not in page.text


def test_restrictive_age_change_blocks_previously_authorized_reporting(school_data):
    # A pre-existing guardian receipt cannot override the new under-13 policy.
    refs = school_data
    fully_authorize(refs)
    with refs.factory() as session:
        session.get(AccountPrivacy, refs.profile_id).age_band = 'under13'; session.commit()
        with pytest.raises(s1.ClassroomDenied): read(session, refs)
        assert session.scalar(select(ClassroomProgressAuthorization)).withdrawn_at is None


@pytest.mark.parametrize('loss', ['legal', 'gate', 'parent_permission', 'reference', 'teacher', 'account'])
def test_chart_readers_fail_closed_when_any_exact_source_is_lost(proposed_chart_data, monkeypatch, loss):
    refs = proposed_chart_data
    with refs.factory() as session:
        sharing.set_sharing(session, student=student(session), chart_id=refs.chart_id,
            assignment_ids=[refs.private_assignment_id]); session.commit()
        if loss == 'legal':
            monkeypatch.setattr(consent, 'LEGALLY_APPROVED_POLICIES', frozenset())
        elif loss == 'gate':
            monkeypatch.delenv('CLASSROOM_GUARDIAN_PCHART_SHARING_ENABLED')
        elif loss == 'parent_permission':
            session.get(DirectorPermission, refs.permission_id).revoked_at = refs.clock[0]
        elif loss == 'reference':
            session.scalar(select(ClassroomChartDirectorShare)).guardian_permission_id = None
        elif loss == 'teacher':
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.private_assignment_id)
        else:
            from app import child_authorization
            evidence, _, _ = parent_access.authenticated(session, parent_request(refs), lock=True)
            child_authorization.withdraw_evidence(session, evidence)
        session.commit()
        if loss == 'teacher':
            with pytest.raises(s1.ClassroomDenied): chart_read(session, refs)
        else:
            assert chart_read(session, refs) == []
        assert session.scalar(select(func.count(PracticeChart.id))) == 2


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('withdraw_first', [False, True])
def test_postgres_native_parent_revocation_serializes_with_individual_chart_reader(proposed_chart_data, withdraw_first):
    refs = proposed_chart_data
    with refs.factory() as session:
        sharing.set_sharing(session, student=student(session), chart_id=refs.chart_id,
            assignment_ids=[refs.private_assignment_id]); session.commit()
    def withdraw(session):
        evidence, _, _ = parent_access.authenticated(session, parent_request(refs), lock=True)
        permission = session.get(DirectorPermission, refs.permission_id, populate_existing=True)
        assert permission.consent_id == evidence.id
        permission.revoked_at = refs.clock[0]
        permission.session_hash = None
        return permission.id
    def observe(session):
        cached = session.get(DirectorPermission, refs.permission_id)
        assert cached.revoked_at is None
        return len(chart_read(session, refs))
    result = ordered_wait(refs.factory, *((withdraw, observe) if withdraw_first else (observe, withdraw)))
    assert tuple(row[0] for row in result) == ('ok','ok')
    assert result[1][1] == (0 if withdraw_first else refs.permission_id)
    with refs.factory() as session: assert chart_read(session, refs) == []


from test_classroom_school_authorization import school_data
