"""Migrated chart Origin boundary checks; credit/reward writers remain unchanged."""
import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.feature_access import FEATURES, Feature
from app.models import (PracticeChart, PracticeChartVerification, RewardGrant,
                        StudentVerifierConnection, TrustedVerifier, WoodchuckProfile)
from app.security import hash_pin
from app.weekly import weekly_credit
from tests.test_weekly2_first_streak import db, streak_db, chart, END, START, NOW
from tests.test_weekly2_first_streak_concurrency import snapshot
from tests.test_weekly2_thirds import arcade_factory


ROUTES = ('book', 'pristine', 'family', 'resend', 'review', 'family-review')


def gates(monkeypatch, enabled=False):
    for name in FEATURES:
        if name.startswith('weekly_'):
            monkeypatch.setitem(FEATURES, name, Feature(enabled, 'open'))


@pytest.fixture
def chart_clients(streak_db, monkeypatch):
    gates(monkeypatch)
    student = TestClient(app)
    verifier = TestClient(app)
    assert student.post('/account/login', data={
        'woodchuck_id': 'WC-STREAK-1', 'pin': '2468'}).status_code == 200
    with streak_db.begin() as session:
        session.add(TrustedVerifier(id=1, email='origin-review@example.test',
            display_name='Synthetic verifier', pin_hash=hash_pin('1357')))
        session.flush()
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=1,
            role='verifier', status='accepted'))
        item = chart(END)
        session.add(item)
        session.flush()
        session.add(PracticeChartVerification(id=1, practice_chart_id=item.id, verifier_id=1))
    assert verifier.post('/trusted-verifiers/login', data={
        'email': 'origin-review@example.test', 'pin': '1357'}).status_code == 200
    page = student.get('/family/practice')
    assert page.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    yield streak_db, student, verifier, csrf
    student.close()
    verifier.close()


def request_for(student, verifier, csrf, route, key='origin-check'):
    if route == 'book':
        return student, student.build_request('POST', '/practice-charts', json={
            'practice_date': str(END), 'minutes': 5, 'submission_key': key,
            'include_contests': False, 'include_team_contests': False})
    if route == 'pristine':
        return student, student.build_request('POST', '/practice-charts/pristine', json={
            'detected_playing_seconds': 299, 'submission_key': key,
            'include_contests': False, 'include_team_contests': False})
    if route == 'family':
        return student, student.build_request('POST', '/family/practice', data={
            'csrf': csrf, 'practice_date': str(END - timedelta(days=1)),
            'minutes': 5, 'submission_key': key})
    if route == 'resend':
        return student, student.build_request('POST', '/practice-charts/verifications/1/resend-email')
    if route == 'review':
        return verifier, verifier.build_request('POST', '/trusted-verifiers/practice-charts/1/respond',
                                                 json={'decision': 'approved'})
    # Boundary-only coverage: this student is deliberately not a director.
    return student, student.build_request('POST', '/family/director/review/1',
                                         data={'csrf': csrf, 'decision': 'approved'})


@pytest.mark.parametrize('enabled', [False, True], ids=['all-gates-off', 'all-gates-on'])
@pytest.mark.parametrize('route', ROUTES)
def test_origin_rejection_before_any_business_write(chart_clients, monkeypatch, route, enabled, record_property):
    factory, student, verifier, csrf = chart_clients
    gates(monkeypatch, enabled)
    before = snapshot(factory)
    probes = [None, '', 'https://foreign.invalid', 'http://testserver:81',
              'https://testserver', 'not-an-origin', 'http://testserver/path', 'null']
    results = []
    for origin in probes:
        client, request = request_for(student, verifier, csrf, route)
        request.headers.pop('origin', None)
        if origin is not None:
            request.headers['origin'] = origin
        response = client.send(request)
        assert response.status_code == 403, response.text
        assert response.json() == {'detail': 'A same-origin request is required.'}
        assert response.headers['cache-control'] == 'no-store'
        assert snapshot(factory) == before
        results.append({'origin': origin, 'status': response.status_code})
    for origin in ('null', 'http://testserver'):
        client, request = request_for(student, verifier, csrf, route)
        request.headers.update({'origin': origin, 'sec-fetch-site': 'cross-site',
                                'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document'})
        response = client.send(request)
        assert response.status_code == 403
        assert snapshot(factory) == before
    record_property('origin_matrix', json.dumps({'route': route, 'weekly_gates': enabled,
                                                 'probes': results, 'all_business_rows_unchanged': True}))


@pytest.mark.parametrize('base', ['http://testserver', 'https://practice.example.test:8443'])
def test_legitimate_configured_origin_credit_late_history_and_retry(streak_db, monkeypatch, base, record_property):
    gates(monkeypatch)
    monkeypatch.setenv('PUBLIC_BASE_URL', base)
    with TestClient(app, base_url=base, headers={'Origin': base}) as student:
        assert student.post('/account/login', data={'woodchuck_id': 'WC-STREAK-1', 'pin': '2468'}).status_code == 200
        page = student.get('/family/practice')
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        before = snapshot(streak_db)
        for route in ('book', 'pristine', 'family'):
            client, request = request_for(student, student, csrf, route, key='legitimate-' + route)
            response = client.send(request)
            assert response.status_code in (200, 201, 303), response.text
            saved = snapshot(streak_db)
            client, request = request_for(student, student, csrf, route, key='legitimate-' + route)
            assert client.send(request).status_code in (200, 201, 303)
            assert snapshot(streak_db) == saved
        after = snapshot(streak_db)
        for table in before:
            if table != 'practice_charts':
                assert after[table] == before[table], table
        with streak_db() as session:
            charts = session.scalars(select(PracticeChart)).all()
            assert len(charts) == 3
            assert sorted((c.source, c.minutes, c.detected_playing_seconds) for c in charts) == [
                ('p-book', 5, None), ('p-book', 5, None), ('pristine', 4, 299)]
            credit = weekly_credit(charts, week_start=START, now=NOW)
            assert credit['practice_seconds'] == 899
            assert all(c.credits_awarded == 0 for c in charts)
            assert any(c.practice_date == END - timedelta(days=1) for c in charts)
        record_property('credit', json.dumps({'seconds': 899, 'book_plus_pristine': True,
                                               'late_chart_preserved': True, 'base': base}))


def test_native_opaque_origin_policy_and_no_configured_origin_alias(chart_clients, monkeypatch):
    factory, student, verifier, csrf = chart_clients
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://configured.example.test')
    before = snapshot(factory)
    client, request = request_for(student, verifier, csrf, 'book')
    request.headers['origin'] = 'https://configured.example.test'
    assert client.send(request).status_code == 403
    assert snapshot(factory) == before
    client, request = request_for(student, verifier, csrf, 'family')
    request.headers.update({'origin': 'null', 'sec-fetch-site': 'same-origin',
                            'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document'})
    assert client.send(request).status_code == 403  # Independent family CSRF configuration.
    assert snapshot(factory) == before
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://testserver')
    client, request = request_for(student, verifier, csrf, 'family')
    request.headers.update({'origin': 'null', 'sec-fetch-site': 'same-origin',
                            'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document'})
    assert client.send(request).status_code in (200, 303)
    assert len(snapshot(factory)['practice_charts']) == len(before['practice_charts']) + 1


def test_owned_verification_updates_and_unrelated_student_denial(chart_clients):
    factory, student, verifier, csrf = chart_clients
    with TestClient(app) as other:
        assert other.post('/account/login', data={'woodchuck_id': 'WC-STREAK-2', 'pin': '2468'}).status_code == 200
        before = snapshot(factory)
        assert other.post('/practice-charts/verifications/1/resend-email').status_code == 404
        assert snapshot(factory) == before
    client, request = request_for(student, verifier, csrf, 'resend')
    assert client.send(request).status_code == 200
    before_review = snapshot(factory)
    client, request = request_for(student, verifier, csrf, 'review')
    assert client.send(request).status_code == 200
    after = snapshot(factory)
    assert after['practice_charts'][0]['credits_awarded'] == 1
    assert after['practice_chart_verifications'][0]['status'] == 'approved'
    for table in before_review:
        if table not in ('practice_charts', 'practice_chart_verifications', 'woodchuck_states', 'reward_grants'):
            assert after[table] == before_review[table], table
    client, request = request_for(student, verifier, csrf, 'review')
    assert client.send(request).status_code == 400
    assert snapshot(factory) == after


@pytest.mark.parametrize('route', ['book', 'pristine', 'family'])
def test_current_account_binding_and_retired_session(chart_clients, route):
    factory, student, verifier, csrf = chart_clients
    before = snapshot(factory)
    client, request = request_for(student, verifier, csrf, route)
    request.headers['x-woodshed-account'] = 'WC-STREAK-2'
    response = client.send(request)
    assert response.status_code == 401
    assert snapshot(factory) == before
    cookie = student.cookies.get('session')
    assert student.post('/account/logout').status_code == 200
    before = snapshot(factory)
    with TestClient(app) as stale:
        stale.cookies.set('session', cookie)
        client, request = request_for(stale, verifier, csrf, route, key='revoked')
        response = client.send(request)
        assert response.status_code in (401, 403)
    assert snapshot(factory) == before


def test_authorized_private_child_and_withdrawn_consent(arcade_factory, monkeypatch):
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import (
        test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED)
    gates(monkeypatch)
    monkeypatch.setattr(testers, 'clock', lambda: CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(
        arcade_factory, monkeypatch, '/prebeta/C002', 'C002', 'DIRECTOR1')
    with arcade_factory() as session:
        profile = session.scalar(select(WoodchuckProfile))
        wid = profile.woodchuck_id
    with TestClient(app) as student:
        assert student.post('/account/login', data={'woodchuck_id': wid, 'pin': '2468'}).status_code == 200
        page = student.get('/family/practice')
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        from app.practice_charts import datetime
        from app.weekly import CENTRAL
        day = datetime.now(CENTRAL).date().isoformat()
        data = {'csrf': csrf, 'practice_date': day, 'minutes': '5', 'submission_key': 'authorized-child'}
        before = snapshot(arcade_factory)
        assert student.post('/family/practice', data=data, headers={'Origin': 'https://foreign.invalid'}).status_code == 403
        assert snapshot(arcade_factory) == before
        assert student.post('/family/practice', data=data).status_code == 200
        with arcade_factory.begin() as session:
            item = session.scalar(select(PracticeChart))
            assert item and not item.include_contests and not item.include_team_contests
            consent.withdraw_evidence(session, session.scalar(select(ConsentEvidence)))
        before = snapshot(arcade_factory)
        response = student.post('/family/practice', data=dict(data, submission_key='withdrawn'))
        assert response.status_code in (401, 403)
        after = snapshot(arcade_factory)
        for table in before:
            if table != 'revoked_browser_sessions':
                assert after[table] == before[table], table


def test_registered_chart_mutation_inventory_and_default_gates():
    mutations = {route.path for route in app.routes
                 if set(getattr(route, 'methods', ())) - {'GET', 'HEAD', 'OPTIONS'}
                 and ('practice-chart' in route.path or route.path == '/family/practice'
                      or route.path.startswith('/family/director/review/'))}
    assert mutations == {'/practice-charts', '/practice-charts/pristine',
        '/practice-charts/verifications/{verification_id}/resend-email',
        '/trusted-verifiers/practice-charts/{verification_id}/respond',
        '/family/practice', '/family/director/review/{review_id}'}
    assert all(not feature.enabled for name, feature in FEATURES.items() if name.startswith('weekly_'))
