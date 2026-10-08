"""Server limits, atomic C001 activation, privacy and real PostgreSQL races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import c001_abuse as abuse, tester_enrollments as testers
from app.age_privacy import declare_age
from app.child_models import ConsentEvidence, PendingConsent
from app.db import Base
from app.main import app
from app.memberships import student_has_full_access
from app.models import TesterEnrollment as Enrollment, WoodchuckProfile
from tests.test_tester_enrollments import (tester_db, account_form, count, profile,
    prepare_child_services, CLAIMED, consent)
from tests.test_team_families import disposable_url


def identity(net='network', browser='browser'):
    return abuse.fingerprint('test-network', net), abuse.fingerprint('test-browser', browser)


def symbol(client, code='C001'):
    return client.post('/guest/secret-symbol', data={'passcode': code})


def test_guest_symbols_use_server_limits_without_creating_student_state(tester_db):
    client = TestClient(app)
    with tester_db() as s:
        before = {t.name: s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables if not t.name.startswith('c001_')}
    assert 'did not match' in symbol(client, 'C003').text
    assert testers.registration_context(client.get('/guest').context['request']) is None
    for _ in range(9):
        page = symbol(client, ' c001 ')
        assert 'C001 Pre-Beta recognized.' in page.text
        assert 'data-guest="local"' in page.text
        assert testers.registration_source(page.context['request']) == 'DIRECTOR1'
    assert 'Please wait before trying again' in symbol(client).text
    with tester_db() as s:
        after = {t.name: s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables if not t.name.startswith('c001_')}
        assert before == after
        assert abuse.count(s, 'symbol', 600, abuse.clock()) == 10


@pytest.mark.parametrize('kind,window,maximum,scope', [
    ('symbol', 600, 10, 'browser'), ('symbol', 600, 30, 'network'),
    ('creation', 3600, 5, 'browser'), ('creation', 3600, 40, 'network'),
    ('creation', 86400, 100, 'network'),
])
def test_rolling_attempt_limits_and_expiry(tester_db, monkeypatch, kind, window, maximum, scope):
    now = CLAIMED
    monkeypatch.setattr(abuse, 'clock', lambda: now)
    net, browser = identity()
    # Exact rolling windows, including preceding-hour attempts for the daily limit.
    stamp = now - timedelta(seconds=window-1)
    with tester_db() as s:
        for i in range(maximum):
            abuse.add_event(s, kind, stamp, net if scope == 'network' else identity(str(i))[0], browser if scope == 'browser' else identity(browser=str(i))[1])
        s.commit()
    with pytest.raises(HTTPException) as failure:
        abuse.check_attempt(kind, (net, browser))
    assert failure.value.status_code == 429
    assert int(failure.value.headers['Retry-After']) <= 2
    now += timedelta(seconds=2)
    abuse.check_attempt(kind, (net, browser))


def test_validation_failures_count_and_cookie_replay_cannot_reset(tester_db):
    client = TestClient(app)
    assert client.post('/account/create', data=account_form() | {'pin': 'bad'}).status_code == 400
    cookie = client.cookies.get('session')
    for _ in range(4):
        client.cookies.set('session', cookie)
        assert client.post('/account/create', data=account_form() | {'pin': 'bad'}).status_code == 400
    limited = client.post('/account/create', data=account_form())
    assert limited.status_code == 429 and int(limited.headers['retry-after']) > 0
    with tester_db() as s:
        assert count(s, WoodchuckProfile) == count(s, Enrollment) == 0


def test_network_attempt_limit_survives_new_browser_and_untrusted_forwarded_header(tester_db):
    for _ in range(30):
        client = TestClient(app)
        assert client.post('/account/daily-secret', json={'passcode': 'invalid'}, headers={'X-Forwarded-For': '198.51.100.9'}).status_code == 400
    assert TestClient(app).post('/account/daily-secret', json={'passcode': 'C001'}, headers={'X-Forwarded-For': '203.0.113.8'}).status_code == 429


@pytest.mark.parametrize('mode', ['environment', 'operator'])
def test_kill_switch_atomic_rollback_recognition_and_recovery(tester_db, monkeypatch, mode):
    client = TestClient(app)
    symbol(client)
    existing = client.post('/account/create', data=account_form())
    assert existing.status_code == 200
    if mode == 'environment':
        monkeypatch.setenv('C001_ACTIVATION_ENABLED', 'false')
    else:
        with tester_db() as s:
            abuse.control(s).enabled = False
            s.commit()
    fresh = TestClient(app)
    assert 'C001 Pre-Beta recognized.' in symbol(fresh).text
    failed = fresh.post('/account/create', data=account_form())
    assert failed.status_code == 503
    with tester_db() as s:
        assert count(s, Enrollment) == count(s, WoodchuckProfile) == 1
        assert student_has_full_access(s, existing.json()['profile']['id'])
    assert testers.registration_source(fresh.get('/guest').context['request']) == 'DIRECTOR1'
    monkeypatch.setenv('C001_ACTIVATION_ENABLED', 'true')
    with tester_db() as s:
        abuse.control(s).enabled = True
        s.commit()
    assert fresh.post('/account/create', data=account_form()).status_code == 200
    with tester_db() as s:
        assert count(s, Enrollment) == count(s, WoodchuckProfile) == 2


@pytest.mark.parametrize('seconds,maximum', [(3600, 40), (86400, 100)])
def test_activation_limits_are_success_only_and_rollback_account(tester_db, seconds, maximum):
    client = TestClient(app)
    page = symbol(client)
    net, browser = abuse.request_identity(page.context['request'])
    with tester_db() as s:
        stamp = abuse.clock() - timedelta(seconds=seconds-60)
        for _ in range(maximum):
            abuse.add_event(s, 'activation', stamp, net)
        s.commit()
    assert client.post('/account/create', data=account_form()).status_code == 429
    with tester_db() as s:
        assert count(s, WoodchuckProfile) == count(s, Enrollment) == 0
        assert abuse.count(s, 'activation', seconds, abuse.clock(), network=net) == maximum


def test_parent_approval_kill_switch_is_recoverable_without_partial_evidence(tester_db, monkeypatch):
    from app import kws_verification
    from app.security import generate_invitation_token, hash_invitation_token
    prepare_child_services(monkeypatch)
    token = generate_invitation_token()
    with tester_db() as s:
        row = PendingConsent(parent_email='synthetic@example.test', director_email='', director_name='', review_allowed=False,
            approve_hash=hash_invitation_token(generate_invitation_token()), activation_hash=hash_invitation_token(token),
            created_at=CLAIMED, expires_at=CLAIMED+timedelta(days=2), approved_at=CLAIMED, confirmed_at=CLAIMED,
            notice_version=consent.NOTICE_VERSION, cohort_key='C001', cohort_claimed_at=CLAIMED, cohort_source='DIRECTOR1')
        s.add(row); s.commit()
    verification = SimpleNamespace(notice_sha256=consent.NOTICE_SHA256, director_allowed=False, state='verified', activated_consent_id=None)
    monkeypatch.setattr(kws_verification, 'verified_for_activation', lambda *args: verification)
    monkeypatch.setenv('C001_ACTIVATION_ENABLED', 'false')
    with tester_db() as s:
        with pytest.raises(HTTPException) as error:
            consent.activate(s, token, profile=None, fields=account_form())
        assert error.value.status_code == 503
        s.rollback()
        assert count(s, WoodchuckProfile) == count(s, Enrollment) == count(s, ConsentEvidence) == 0
        pending = s.scalar(select(PendingConsent))
        assert pending.confirmed_at and pending.activation_hash == hash_invitation_token(token)
        assert testers.utc(pending.expires_at) > CLAIMED
        assert verification.state == 'verified'
    monkeypatch.setenv('C001_ACTIVATION_ENABLED', 'true')
    with tester_db() as s:
        p, _, _ = consent.activate(s, token, profile=None, fields=account_form())
        s.commit()
        assert student_has_full_access(s, p.id)
        assert count(s, Enrollment) == count(s, ConsentEvidence) == 1


def test_alerts_are_minimized_and_metadata_expires(tester_db, monkeypatch, caplog):
    monkeypatch.setattr(abuse, 'clock', lambda: CLAIMED)
    network, browser = identity('198.51.100.88', 'student-secret')
    caplog.set_level(logging.WARNING, logger='woodshed.security.c001')
    with tester_db() as s:
        for kind, attempts in [('secret_failure',21), ('c001_creation',26), ('quick_deletion',3), ('age_rejected',3), ('rejected',11), ('limited',3)]:
            for _ in range(attempts):
                abuse.observe(s, kind, CLAIMED, network, browser)
            assert 'reason='+kind in caplog.text
        assert not any(private in caplog.text for private in (network, browser, '198.51.100.88', 'student-secret'))
        abuse.prune(s, CLAIMED + timedelta(hours=24, seconds=1))
        assert count(s, abuse.AbuseEvent) == 0


def test_quick_deletion_uses_creation_network_and_leaves_history(tester_db, monkeypatch, caplog):
    from app.account_deletion import anonymize_woodchuck_account
    monkeypatch.setattr(abuse, 'clock', lambda: CLAIMED)
    caplog.set_level(logging.WARNING, logger='woodshed.security.c001')
    token = abuse._identity.set(identity())
    try:
        with tester_db() as s:
            for i in range(3):
                p = profile(s, 'CHURN'+str(i), CLAIMED, age='adult')
                testers.enroll_tester(s,p.id,'C001',CLAIMED)
                abuse.record_creation(s,p.id)
                s.commit()
                anonymize_woodchuck_account(s, profile=p, now=CLAIMED+timedelta(minutes=1))
                s.commit()
                assert not student_has_full_access(s,p.id)
            assert abuse.active_count(s) == 0
            assert count(s, Enrollment) == 3
        assert 'reason=quick_deletion' in caplog.text
    finally:
        abuse._identity.reset(token)


def test_postgres_parallel_activation_never_exceeds_fuse_and_deleted_slots_recover(tmp_path):
    engine = create_engine(disposable_url(tmp_path, 'postgresql'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory() as s:
        for i in range(999):
            p = WoodchuckProfile(woodchuck_id=f'WC-FUSE-{i}',display_name='Synthetic',pin_hash='unused',instrument='Flute',level='Beginner',goal='Practice')
            s.add(p);s.flush()
            s.add(Enrollment(profile_id=p.id,cohort_key='C001',joined_at=CLAIMED))
        abuse.control(s)
        s.commit()
    barrier = Barrier(2)
    def activate(i):
        identity_token = abuse._identity.set(identity(str(i)))
        try:
            with factory() as s:
                p = WoodchuckProfile(woodchuck_id=f'WC-RACE-{i}',display_name='Synthetic',pin_hash='unused',instrument='Flute',level='Beginner',goal='Practice')
                s.add(p);s.flush();declare_age(s,p.id,'adult')
                barrier.wait(timeout=10)
                try:
                    testers.enroll_tester(s,p.id,'C001',CLAIMED)
                    s.commit()
                    return 'activated'
                except HTTPException as exc:
                    s.rollback()
                    return exc.status_code
        finally:
            abuse._identity.reset(identity_token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, (1,2)))
    assert sorted(map(str, results)) == ['503','activated']
    with factory() as s:
        assert abuse.active_count(s) == count(s, Enrollment) == count(s, WoodchuckProfile) == 1000
        # A deleted account retains its immutable enrollment but frees capacity.
        s.get(WoodchuckProfile,1).status='deleted';s.commit()
        assert abuse.active_count(s) == 999
        p = profile(s,'RECOVERY',CLAIMED,age='adult')
        testers.enroll_tester(s,p.id,'C001',CLAIMED);s.commit()
        assert student_has_full_access(s,p.id)
        assert abuse.active_count(s) == 1000 and count(s, Enrollment) == 1001
        abuse.control(s).enabled=False;s.commit()
        # Cached control state cannot bypass a durable operator disable.
        with factory() as other:
            assert abuse.control(other).enabled is False
        assert testers.enroll_tester(s,p.id,'C001',CLAIMED).profile_id == p.id
    engine.dispose()


def test_postgres_parallel_requests_share_limit_across_sessions(tmp_path, monkeypatch):
    engine = create_engine(disposable_url(tmp_path, 'postgresql'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(abuse, 'SessionLocal', factory)
    net, browser = identity()
    with factory() as s:
        for i in range(39):
            abuse.add_event(s, 'creation', abuse.clock(), net, identity(browser=str(i))[1])
        s.commit()
    barrier = Barrier(2)
    def attempt(i):
        barrier.wait(timeout=10)
        try:
            abuse.check_attempt('creation', (net, identity(browser='race'+str(i))[1]))
            return 'allowed'
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(attempt,(1,2)))) == ['429','allowed']
    with factory() as s:
        assert abuse.count(s,'creation',3600,abuse.clock(),network=net) == 40
    engine.dispose()


def test_global_velocity_and_capacity_warnings(tester_db, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger='woodshed.security.c001')
    now = abuse.clock()
    with tester_db() as s:
        for i in range(200):
            abuse.add_event(s, 'activation', now if i < 100 else now-timedelta(hours=2), identity(str(i))[0])
        monkeypatch.setattr(abuse, 'active_count', lambda session: 749)
        abuse.authorize_activation(s, 1)
        assert 'reason=global_activation_velocity count=101' in caplog.text
        assert 'reason=global_activation_velocity count=201' in caplog.text
        assert 'reason=capacity_warning count=750' in caplog.text
        monkeypatch.setattr(abuse, 'active_count', lambda session: 899)
        abuse.authorize_activation(s, 2)
        assert 'reason=high_priority_capacity_review count=900' in caplog.text
        s.rollback()
        assert count(s, abuse.AbuseEvent) == 0  # Uncommitted activations never consume quota.


def test_malformed_submissions_count_before_route_validation(tester_db):
    client = TestClient(app)
    for _ in range(10):
        assert client.post('/account/daily-secret', json={'unrecognized': 'field'}).status_code == 422
    assert client.post('/account/daily-secret', json={'passcode':'C001'}).status_code == 429


def test_age_gate_reactivation_cannot_bypass_operator_disable(tester_db, monkeypatch):
    with tester_db() as s:
        p = profile(s, 'LEGACY', CLAIMED, age='unknown')
        s.add(Enrollment(profile_id=p.id,cohort_key='C001',joined_at=CLAIMED))
        s.commit()
        monkeypatch.setenv('C001_ACTIVATION_ENABLED', 'false')
        with pytest.raises(HTTPException):
            declare_age(s,p.id,'adult')
        s.rollback()
        assert not student_has_full_access(s,p.id)
        monkeypatch.setenv('C001_ACTIVATION_ENABLED', 'true')
        declare_age(s,p.id,'adult');s.commit()
        assert student_has_full_access(s,p.id)
        assert count(s,Enrollment)==1


def test_postgres_cached_control_cannot_ignore_concurrent_disable(tmp_path):
    engine = create_engine(disposable_url(tmp_path, 'postgresql'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as first:
        cached = abuse.control(first)
        first.commit()
        assert cached.enabled
        with factory() as operator:
            abuse.control(operator).enabled = False
            operator.commit()
        assert cached.enabled  # Reproduce stale SQLAlchemy identity.
        p = profile(first, 'STALE', CLAIMED, age='adult')
        with pytest.raises(HTTPException) as failure:
            testers.enroll_tester(first,p.id,'C001',CLAIMED)
        assert failure.value.status_code == 503
        first.rollback()
        assert count(first,Enrollment)==count(first,WoodchuckProfile)==0
    engine.dispose()


def test_operator_cli_round_trip_on_disposable_database(tmp_path):
    import os
    import subprocess
    import sys
    url = 'sqlite:///' + str(tmp_path / 'operator.db')
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    def command(*args):
        env = {k:v for k,v in os.environ.items() if k not in ('RENDER','APP_ENV','C001_ACTIVATION_ENABLED')}
        env['DATABASE_URL'] = url
        result = subprocess.run([sys.executable,'-m','app.c001_abuse',*args], env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        return result.stdout
    assert 'enabled=True active=0 emergency_ceiling=1000' in command('status')
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(abuse.ActivationControl)) == 0
    assert 'enabled=False' in command('disable')
    assert 'enabled=False' in command('status')
    assert 'emergency_ceiling=1200' in command('set-ceiling','--ceiling','1200')
    assert 'enabled=True' in command('enable')
    command('prune')
    engine.dispose()


def test_existing_active_child_access_survives_support_correction_during_kill_switch(tester_db, monkeypatch):
    from app.age_privacy import correct_age
    prepare_child_services(monkeypatch)
    with tester_db() as s:
        p = profile(s, 'CHILD', CLAIMED, age='under13')
        evidence = ConsentEvidence(profile_id=p.id,parent_email='synthetic@example.test',notice_version=consent.NOTICE_VERSION,
            notice_sha256=consent.NOTICE_SHA256,approved_at=CLAIMED,confirmed_at=CLAIMED)
        s.add(evidence);s.flush()
        from app.age_models import AccountPrivacy
        rule = s.get(AccountPrivacy,p.id)
        rule.consent_id=evidence.id;s.flush()
        testers.enroll_tester(s,p.id,'C001',CLAIMED);s.commit()
        before = count(s,abuse.AbuseEvent)
        monkeypatch.setenv('C001_ACTIVATION_ENABLED','false')
        correct_age(s,p.id,'adult',expected_band='under13',expected_declared_at=testers.utc(rule.declared_at).isoformat(),actor='test',case_reference='test-case')
        s.commit()
        assert student_has_full_access(s,p.id)
        assert count(s,abuse.AbuseEvent)==before


def test_network_identity_requires_trusted_render_header(monkeypatch):
    from starlette.datastructures import Headers
    monkeypatch.setenv('RENDER','true')
    monkeypatch.setenv('SESSION_SECRET','c001-network-unit-secret-at-least-32-characters')
    request = SimpleNamespace(session={},client=SimpleNamespace(host='127.0.0.1'),headers=Headers({'X-Forwarded-For':'198.51.100.8'}))
    with pytest.raises(HTTPException) as error:
        abuse.request_identity(request)
    assert error.value.status_code==503
    request.headers=Headers({'CF-Connecting-IP':'198.51.100.8','X-Forwarded-For':'203.0.113.9'})
    network,_=abuse.request_identity(request)
    assert network == abuse.fingerprint('c001-network','198.51.100.8')
