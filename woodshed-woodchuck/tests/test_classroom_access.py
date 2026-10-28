"""Student benefits are enrollment-based, private, and independent of reporting."""
from datetime import datetime, timedelta
import sys

import pytest
from sqlalchemy import select

from app import classroom_capabilities as capabilities, classroom_s2 as s2
from app import classroom_reporting as reporting, memberships
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.classroom_models import (
    ClassroomEntitlement, ClassroomMembershipPeriod,
    ClassroomReportingPeriod, ClassroomStudentMembership,
)
from app.feature_access import can_use_feature
from app.models import PracticeChart, PracticeChartVerification, WoodchuckProfile
from app.practice_chart_routes import CENTRAL
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import NOW, join, s2_db, student
from test_classroom_director import web
from test_classroom_student_invitation_routes import (
    invitation_web, issue, student_client,
)
from test_student_rating_privacy import safe


@pytest.fixture
def access_web(invitation_web, monkeypatch):
    factory = invitation_web
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(memberships, 'clock', lambda: s2._now())
    monkeypatch.delenv('CLASSROOM_S3_ENABLED', raising=False)
    today = datetime.now(CENTRAL).date()
    with factory() as session:
        chart = PracticeChart(profile_id=1, practice_date=today-timedelta(days=7),
                              minutes=17, instrument='Trumpet', source='manual', note='PRIVATE NOTE')
        session.add(chart)
        session.flush()
        session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,
                                             status='approved', response_note='PRIVATE REVIEW'))
        session.add(PracticeChart(profile_id=2, practice_date=today-timedelta(days=7),
                                  minutes=999, instrument='Flute', source='manual', note='OTHER STUDENT'))
        session.commit()
    return factory


def enroll(factory):
    with factory() as session:
        member = join(session, 'FIRST2026')
        session.commit()
        return member.id


def protected_rows(factory):
    from app.db import Base
    with factory.kw['bind'].connect() as connection:
        return {table.name: connection.execute(select(table).order_by(*table.primary_key)).all()
                for table in Base.metadata.sorted_tables
                if table.name.startswith(('classroom_', 'practice_chart', 'membership', 'billing_',
                                          'woodchuck_', 'student_verifier'))}


@pytest.mark.parametrize('source', ['classroom', 'personal', 'invitation', 'ordinary'])
def test_real_student_routes_use_only_approved_sources(access_web, source, monkeypatch):
    if source == 'classroom':
        enroll(access_web)
    elif source == 'personal':
        with access_web() as session:
            memberships.create_complimentary_membership(session, memberships.Actor('student', 1),
                                                        memberships.Actor('admin'))
            session.commit()
    elif source == 'invitation':
        browser, _, ident, csrf = issue(access_web)
        assert browser.post(f'/classroom/student-invitations/{ident}/accept',
                            data={'csrf': csrf}, follow_redirects=False).status_code == 303
    # Student benefits must work with both adult-facing gates OFF.
    monkeypatch.delenv('CLASSROOM_DIRECTOR_ENABLED')
    browser = student_client()
    expected = source in {'classroom', 'personal'}
    before = protected_rows(access_web)
    result = browser.get('/practice-charts/insights?profile_id=2&full=true')
    assert result.status_code == (200 if expected else 403)
    assert result.headers['cache-control'] == 'no-store'
    if expected:
        safe(result.json())
        assert result.json()['total_minutes'] == 17
        assert sum(w['verified_minutes'] for w in result.json()['weeks']) == 17
        assert all(value not in result.text for value in ('PRIVATE', 'OTHER STUDENT', 'profile_id'))
    private = browser.get('/family/practice/data')
    assert private.status_code == 200
    safe(private.json()['metrics'])
    assert ('insights' in private.json()['metrics']) is expected
    assert 'OTHER STUDENT' not in private.text
    assert protected_rows(access_web) == before
    assert browser.get('/practice-charts/insights', headers={'X-Woodshed-Account': 'WC-SECOND'}).status_code == 401
    page = browser.get('/p-book')
    assert f'data-enabled="{str(expected).lower()}"' in page.text
    exercises = browser.get('/practice/skill-building')
    assert exercises.headers['cache-control'] == 'no-store'
    assert ('Classroom access confirmed.' in exercises.text) is (source == 'classroom')
    assert 'coming soon' in exercises.text
    assert 'href="/arcade/note-names"' not in exercises.text
    assert browser.get('/practice-charts/totals').status_code == 200
    assert browser.get('/practice/pristine').status_code == 200
    with access_web() as session:
        assert session.scalar(select(ClassroomReportingPeriod.id)) is None
        assert (session.scalar(select(ClassroomStudentMembership.id)) is not None) is (source == 'classroom')
        assert memberships.student_has_full_access(session, 1) is (source == 'personal')


@pytest.mark.parametrize('loss', ['leave', 'hold', 'archive', 'expired', 'revoked', 'future', 's1_off', 's2_off'])
def test_current_enrollment_and_entitlement_required_on_every_read(access_web, monkeypatch, loss):
    membership_id = enroll(access_web)
    browser = student_client()
    assert browser.get('/practice-charts/insights').status_code == 200
    access_web.clock[0] += timedelta(seconds=1)
    with access_web() as session:
        if loss == 'leave':
            s2.leave_class(session, student=student(session), program_id=1, class_id=access_web.ids[0])
        elif loss == 'hold':
            s2.suspend_member(session, actor=actor(session), program_id=1,
                              class_id=access_web.ids[0], membership_id=membership_id)
        elif loss == 'archive':
            s2.set_class_state(session, actor=actor(session), program_id=1,
                               class_id=access_web.ids[0], state='archived')
        elif loss == 'future':
            session.scalar(select(ClassroomMembershipPeriod)).starts_at = NOW + timedelta(days=1)
        elif loss in {'expired', 'revoked'}:
            entitlement = session.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.program_id == 1))
            if loss == 'expired':
                access_web.clock[0] = entitlement.ends_at.replace(tzinfo=NOW.tzinfo)
            else:
                entitlement.status = 'ended'
                entitlement.ended_at = s2._now()
        else:
            monkeypatch.delenv('CLASSROOM_S1_ENABLED' if loss == 's1_off' else 'CLASSROOM_S2_ENABLED')
        session.commit()
    assert browser.get('/practice-charts/insights').status_code == 403
    assert 'Classroom access confirmed.' not in browser.get('/practice/skill-building').text
    assert browser.get('/practice-charts').status_code == 200
    assert browser.get('/practice-charts/totals').status_code == 200


def test_withdrawing_reporting_does_not_withdraw_student_benefits(access_web, monkeypatch):
    enroll(access_web)
    monkeypatch.setenv('CLASSROOM_S3_ENABLED', '1')
    with access_web() as session:
        reporting.grant_reporting(session, student=student(session), program_id=1,
            class_id=access_web.ids[0], scope_version=reporting.SCOPE_VERSION,
            notice_version=reporting.NOTICE_VERSION)
        session.commit()
    access_web.clock[0] += timedelta(seconds=1)
    with access_web() as session:
        reporting.withdraw_reporting(session, student=student(session), program_id=1,
                                      class_id=access_web.ids[0])
        session.commit()
        assert can_use_feature(session, 1, 'practice_insights')
        assert capabilities.capability_decision(session, 1, 'note-names').allowed
        assert not capabilities.capability_decision(session, 1, 'class_reporting').allowed
        with pytest.raises(s2.ClassroomDenied):
            reporting.authorize_reporting(session, actor=actor(session, 2), program_id=1,
                                           class_id=access_web.ids[0], profile_id=1)
        from app.practice_rating_access import adult_rating_allowed
        assert not adult_rating_allowed(session, 1, verifier_id=2)
        assert not adult_rating_allowed(session, 1, guardian=True)


@pytest.mark.parametrize('age,consent,allowed,exercises', [
    ('adult', False, True, True), ('13to17', False, True, True),
    ('unknown', False, False, False), ('under13', False, False, False),
    ('under13', True, False, True), ('13to17', True, True, True),
])
def test_age_and_consent_remain_independent_from_reporting(access_web, monkeypatch, age, consent, allowed, exercises):
    enroll(access_web)
    from app import child_authorization
    monkeypatch.setattr(child_authorization, 'under13_available', lambda: True)
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda: ('synthetic', '', 'a' * 64))
    with access_web() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band = age
        if consent:
            evidence = ConsentEvidence(profile_id=1, parent_email='parent@example.test', approved_at=NOW,
                confirmed_at=NOW, notice_version='synthetic', notice_sha256='a' * 64)
            session.add(evidence)
            session.flush()
            rule.consent_id = evidence.id
        session.commit()
        assert can_use_feature(session, 1, 'practice_insights') is allowed
        assert capabilities.capability_decision(session, 1, 'note-names').allowed is exercises
        if consent:
            if age == 'under13':
                # Existing parent-authorized personal Full insights stay available.
                memberships.create_complimentary_membership(session, memberships.Actor('student', 1),
                                                            memberships.Actor('admin'))
                session.commit()
                assert can_use_feature(session, 1, 'practice_insights')
                assert student_client().get('/practice-charts/insights').status_code == 200
            evidence.withdrawn_at = NOW + timedelta(seconds=1)
            session.commit()
            assert not can_use_feature(session, 1, 'practice_insights')
            assert not capabilities.capability_decision(session, 1, 'note-names').allowed


def test_inactive_account_cannot_reuse_an_enrollment(access_web):
    enroll(access_web)
    with access_web() as session:
        session.get(WoodchuckProfile, 1).status = 'deleted'
        session.commit()
        assert not can_use_feature(session, 1, 'practice_insights')
        assert not capabilities.capability_decision(session, 1, 'note-names').allowed
