from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers

from app import account_routes, verifier_routes, membership_routes, main, login_limits as limits
from app.db import Base
from app.models import WoodchuckProfile, TrustedVerifier
from app.security import hash_pin


@pytest.fixture
def limiter(monkeypatch):
    for name in ('RENDER', 'APP_ENV', 'LOGIN_TRUSTED_PROXY_CIDRS', 'LOGIN_RATE_LIMIT_REQUIRED'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', 'memory')
    now = [100.0]
    backend = limits.MemoryBackend(lambda: now[0])
    monkeypatch.setattr(limits, '_backend', lambda *args: backend)
    return backend, now


@pytest.fixture
def login_db(limiter, monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (account_routes, verifier_routes, membership_routes, main):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    with factory() as session:
        pin = hash_pin('2468')
        session.add(WoodchuckProfile(woodchuck_id='WC-LIMITED', display_name='Student A', pin_hash=pin,
            instrument='Flute', level='Beginner', goal='Practice'))
        session.add(TrustedVerifier(email='verifier@example.test', display_name='Verifier A', pin_hash=pin))
        session.commit()
    yield factory
    engine.dispose()


def client(ip='198.51.100.1'):
    return TestClient(main.app, client=(ip, 5000))


def request(ip='198.51.100.1', headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers=Headers(headers or {}))


@pytest.mark.parametrize('route,field,identifier', [('/account/login', 'woodchuck_id', 'WC-LIMITED'),
    ('/trusted-verifiers/login', 'email', 'verifier@example.test')])
def test_valid_login_mistakes_normalization_account_limit_and_expiry(login_db, limiter, route, field, identifier):
    backend, now = limiter
    for attempt in range(3):
        assert client().post(route, data={field: identifier, 'pin': '0000'}).status_code == 401
    assert client().post(route, data={field: ' '+identifier.lower()+' ', 'pin':'2468'}).status_code == 200
    for attempt in range(6):
        assert client(f'198.51.100.{attempt+2}').post(route,
            data={field: identifier.upper(), 'pin':'0000'}).status_code == 401
    response = client('203.0.113.20').post(route, data={field:identifier, 'pin':'2468'})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '900'
    now[0] += 901
    assert client().post(route, data={field:identifier, 'pin':'2468'}).status_code == 200


@pytest.mark.parametrize('route,field', [('/account/login','woodchuck_id'), ('/trusted-verifiers/login','email')])
def test_ip_spray_and_forged_forwarding_headers(login_db, route, field):
    first = None
    for index in range(100):
        response = client().post(route, data={field:f'nonexistent-{index}@example.test', 'pin':'0000'},
            headers={'X-Forwarded-For': f'203.0.113.{index}', 'Forwarded': f'for=192.0.2.{index}'})
        assert response.status_code == 401
        first = first or response.json()
        assert response.json() == first
    assert client().post(route, data={field:'another', 'pin':'0000'}).status_code == 429
    assert client('198.51.100.2').post(route, data={field:'another', 'pin':'0000'}).status_code == 401


def test_admin_stricter_and_recoverable(login_db, limiter, monkeypatch):
    monkeypatch.setenv('SITE_ADMIN_TOKEN', 'test-admin-secret')
    admin = client()
    csrf = admin.get('/admin/login').context['csrf']
    assert admin.post('/admin/login', data={'csrf':csrf, 'token':'wrong'}).status_code == 403
    assert admin.post('/admin/login', data={'csrf':csrf, 'token':'test-admin-secret'},
                      follow_redirects=False).status_code == 303

    csrf = admin.get('/admin/login').context['csrf']
    for _ in range(3):
        assert admin.post('/admin/login', data={'csrf':csrf, 'token':'wrong'}).status_code == 403
    assert admin.post('/admin/login', data={'csrf':csrf, 'token':'test-admin-secret'}).status_code == 429
    limiter[1][0] += 901
    assert admin.post('/admin/login', data={'csrf':csrf, 'token':'test-admin-secret'},
                      follow_redirects=False).status_code == 303


def test_invitation_acceptance_cannot_bypass_verifier_login_limit(login_db, limiter):
    from datetime import datetime, timedelta, timezone
    from app.models import TrustedVerifierInvitation
    from app.security import hash_invitation_token
    token = 'isolated-invitation-secret'
    with login_db() as session:
        session.add(TrustedVerifierInvitation(profile_id=1, email='verifier@example.test',
            role='verifier', token_hash=hash_invitation_token(token),
            expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
        session.commit()
    for _ in range(9):
        assert client().post('/trusted-verifiers/login', data={
            'email':'VERIFIER@example.test', 'pin':'0000'}).status_code == 401
    path = f'/trusted-verifiers/invitations/{token}/accept'
    assert client().post(path, data={'display_name':'Verifier', 'pin':'0000'}).status_code == 400
    assert client('192.0.2.8').post(path, data={'display_name':'Verifier', 'pin':'2468'}).status_code == 429
    assert token not in str(limiter[0].rows)
    limiter[1][0] += 901
    assert client().post(path, data={'display_name':'Verifier', 'pin':'2468'}).status_code == 200


def test_concurrent_reservations_do_not_overshoot(limiter):
    def attempt(_):
        try:
            limits.enforce_login_limit(request(), 'student', 'WC-ONE')
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=20) as pool:
        result = list(pool.map(attempt, range(60)))
    assert result.count(200) == 10
    assert result.count(429) == 50


def test_blocked_attempts_do_not_extend_window(limiter):
    for _ in range(10): limits.enforce_login_limit(request(), 'student', 'WC-ONE')
    limiter[1][0] += 899
    with pytest.raises(HTTPException) as blocked:
        limits.enforce_login_limit(request(), 'student', 'WC-ONE')
    assert blocked.value.headers['Retry-After'] == '1'
    limiter[1][0] += 1
    limits.enforce_login_limit(request(), 'student', 'WC-ONE')


def test_proxy_allowlist_right_to_left_and_spoof_prefix(monkeypatch):
    monkeypatch.setenv('LOGIN_TRUSTED_PROXY_CIDRS', '10.0.0.0/24')
    for forged in ('1.1.1.1', '8.8.8.8'):
        assert limits.source_ip(request('10.0.0.2', {'x-forwarded-for':
            f'{forged}, 198.51.100.8, 10.0.0.3'})) == '198.51.100.8'
        assert limits.source_ip(request('192.0.2.9', {'x-forwarded-for': forged})) == '192.0.2.9'
    assert limits.source_ip(request('10.0.0.2', {'x-forwarded-for':'garbage'})) == '10.0.0.2'
    assert limits.source_ip(request('::ffff:192.0.2.9')) == '192.0.2.9'
    monkeypatch.setenv('LOGIN_TRUSTED_PROXY_CIDRS', '0.0.0.0/0')
    with pytest.raises(ValueError): limits.source_ip(request())


def test_uvicorn_raw_peer_configuration_does_not_trust_client_headers(login_db, limiter):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    app = ProxyHeadersMiddleware(main.app, trusted_hosts='')
    with TestClient(app, client=('198.51.100.19', 5000)) as browser:
        for fake in ('1.1.1.1','8.8.8.8'):
            assert browser.post('/account/login', data={'woodchuck_id':'unknown','pin':'0000'},
                headers={'X-Forwarded-For': fake}).status_code == 401
    key = limits.limiter_keys('student','unknown','198.51.100.19')[0][0]
    assert limiter[0].rows[key][0] == 2


@pytest.mark.parametrize('mode,required', [('off','true'), ('memory','true'), ('redis','true'), ('off','typo')])
def test_required_missing_backend_fails_closed_on_real_routes(limiter, monkeypatch, mode, required):
    monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', mode)
    monkeypatch.setenv('LOGIN_RATE_LIMIT_REQUIRED', required)
    monkeypatch.delenv('LOGIN_RATE_LIMIT_REDIS_URL', raising=False)
    for route, data in [('/account/login', {'woodchuck_id':'WC-X','pin':'1234'}),
                        ('/trusted-verifiers/login', {'email':'x@example.test','pin':'1234'}),
                        ('/admin/login', {'token':'secret'})]:
        assert client().post(route, data=data).status_code == 503


def test_outage_never_falls_back_or_leaks_secrets(limiter, monkeypatch, caplog):
    monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', 'redis')
    monkeypatch.setenv('LOGIN_RATE_LIMIT_REQUIRED', 'true')
    monkeypatch.setenv('LOGIN_RATE_LIMIT_REDIS_URL', 'redis://unused.test')
    def broken(*args):
        raise RuntimeError('redis://user:backend-password@host token=private-token pin=4321')
    monkeypatch.setattr(limits, '_backend', broken)
    result = client().post('/account/login', data={'woodchuck_id':'WC-X','pin':'4321'})
    assert result.status_code == 503
    assert not limiter[0].rows
    for secret in ('backend-password', 'private-token', '4321'):
        assert secret not in result.text + caplog.text
    status = limits.protection_status()
    assert status['configured'] and status['state'] == 'unavailable'


def test_production_rejects_memory_and_uvicorn_rewriting(limiter, monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    for mode, forwarded in [('memory',''), ('redis','*'), ('redis','127.0.0.1')]:
        monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', mode)
        monkeypatch.setenv('FORWARDED_ALLOW_IPS', forwarded)
        monkeypatch.setenv('LOGIN_RATE_LIMIT_REDIS_URL', 'redis://unused.test')
        assert client().post('/account/login', data={'woodchuck_id':'WC-X','pin':'1234'}).status_code == 503


def test_identifiers_hashed_normalized_and_credentials_not_logged(login_db, limiter, caplog):
    assert limits.limiter_keys('student',' wc-abc ', '192.0.2.1') == limits.limiter_keys('student','WC-ABC','192.0.2.1')
    assert limits.limiter_keys('verifier',' User@Example.Test ', '192.0.2.1') == limits.limiter_keys('verifier','user@example.test','192.0.2.1')
    client().post('/trusted-verifiers/login', data={'email':'private@example.test','pin':'pin-must-stay-private'})
    combined = str(limiter[0].rows) + caplog.text
    for secret in ('private@example.test', 'pin-must-stay-private', os.environ['SESSION_SECRET']):
        assert secret not in combined


def test_status_distinguishes_disabled_configured_local_and_operational(limiter, monkeypatch):
    assert limits.protection_status()['state'] == 'local_only'
    monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', 'off')
    assert limits.protection_status()['state'] == 'disabled'
    monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', 'redis')
    monkeypatch.setenv('LOGIN_RATE_LIMIT_REDIS_URL', 'redis://unused.test')
    assert limits.protection_status(check_backend=False)['state'] == 'configured'
    assert limits.protection_status()['state'] == 'backend_operational'
    assert limits.protection_status()['production_verified'] is False


def _process_attempt(args):
    url, key = args
    return limits._backend('redis', url).consume([key], [10])


@pytest.fixture
def redis_backend():
    url = os.getenv('WW_TEST_REDIS_URL')
    if not url:
        pytest.skip('Explicit disposable local Redis required')
    assert url == 'redis://127.0.0.1:56389/15'
    backend = limits.RedisBackend(url)
    directory = Path(os.environ['WW_TEST_REDIS_DIR']).resolve()
    assert str(directory).startswith('/tmp/ww-security-round1-continuation.')
    assert backend.client.config_get('dir')['dir'] == str(directory)
    assert backend.client.info('server')['process_id'] == int((directory/'redis.pid').read_text())
    yield url, backend
    backend.client.close()


def test_real_redis_atomic_multi_process_expiration(redis_backend):
    url, backend = redis_backend
    key = f'ww:login:v1:test:{uuid4().hex}'
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
        result = list(pool.map(_process_attempt, [(url,key)]*40))
    assert result.count(0) == 10
    assert backend.client.get(key) == '40'
    ttl = backend.client.pttl(key)
    assert 0 < ttl <= 900000
    short_key = key + ':expiry'
    assert backend.consume([short_key], [1], window=1) == 0
    assert backend.consume([short_key], [1], window=1) > 0
    time.sleep(1.05)
    assert backend.consume([short_key], [1], window=1) == 0
    assert backend.client.get(short_key) == '1'


def test_real_redis_both_dimensions_have_expiration(redis_backend):
    _, backend = redis_backend
    prefix = uuid4().hex
    keys = [f'ww:login:v1:test:{prefix}:{suffix}' for suffix in ('ip','account')]
    assert backend.consume(keys, [100,10]) == 0
    for _ in range(9): assert backend.consume(keys, [100,10]) == 0
    assert backend.consume(keys, [100,10]) > 0
    assert all(0 < backend.client.ttl(key) <= 900 for key in keys)


@pytest.mark.parametrize('kind,route,field,identifier', [
    ('student', '/account/login', 'woodchuck_id', 'WC-LIMITED'),
    ('verifier', '/trusted-verifiers/login', 'email', 'verifier@example.test'),
])
@pytest.mark.parametrize('backend_name', ['memory', 'redis'])
def test_blocked_ip_cannot_poison_fresh_account(login_db, limiter, monkeypatch, request,
                                               kind, route, field, identifier, backend_name):
    backend = limiter[0]
    if backend_name == 'redis':
        _, backend = request.getfixturevalue('redis_backend')
        monkeypatch.setattr(limits, '_backend', lambda *args: backend)
        # Isolate keys across repeated runs without touching any other counters.
        monkeypatch.setenv('SESSION_SECRET', uuid4().hex)
    attacker = client('198.51.100.80')
    for index in range(100):
        assert attacker.post(route, data={field:f'unknown-{index}', 'pin':'0000'}).status_code == 401
    for _ in range(10):
        assert attacker.post(route, data={field:identifier, 'pin':'0000'}).status_code == 429
    assert client('192.0.2.80').post(route, data={field:identifier, 'pin':'2468'}).status_code == 200
    account_key = limits.limiter_keys(kind, identifier, '192.0.2.80')[0][1]
    count = backend.rows[account_key][0] if backend_name == 'memory' else int(backend.client.get(account_key))
    assert count == 1


CONTEST_ENTRIES = [
    ('get', '/contests/admin', {}),
    ('post', '/contests/admin/band-directors', {'woodchuck_id':'WC-LIMITED'}),
    ('post', '/contests/admin/band-directors/1/revoke', {}),
    ('post', '/contests/admin/teams/1/moderation', {'state':'active'}),
    ('post', '/contests/admin/team-reports/1/resolve', {'action':'dismissed'}),
    ('post', '/contests/admin/finalize-current', {}),
    ('post', '/contests/admin/finalize-due', {}),
    ('post', '/contests/admin/readiness', {}),
    ('post', '/contests/admin/rollover', {'source_key':'old', 'next_key':'next',
        'next_name':'Next', 'next_start':'2026-09-28', 'next_end':'2026-12-01',
        'confirmation':'ROLL OVER'}),
    ('post', '/contests/weeks/2026-09-07/finalize', {}),
]


@pytest.mark.parametrize('method,path,data', CONTEST_ENTRIES)
def test_all_contest_credential_entries_share_strict_ip_limit(limiter, monkeypatch, method, path, data):
    monkeypatch.setenv('CONTEST_ADMIN_TOKEN', 'test-contest-only-secret')
    browser = client()
    for index in range(5):
        response = (browser.get('/contests/admin') if index % 2 else
                    browser.post('/contests/weeks/2026-09-07/finalize'))
        assert response.status_code == 403
    kwargs = {'data':data} if method == 'post' else {}
    assert getattr(browser,method)(path, **kwargs,
        headers={'X-Contest-Admin-Token':'test-contest-only-secret'}).status_code == 429
    assert client('192.0.2.2').get('/contests/admin').status_code == 403
    limiter[1][0] += 900
    assert browser.get('/contests/admin').status_code == 403


def test_contest_valid_session_header_only_route_and_site_boundary(login_db, limiter, monkeypatch):
    from app import contest_admin, contests
    from fastapi.responses import JSONResponse
    monkeypatch.setenv('CONTEST_ADMIN_TOKEN', 'test-contest-only-secret')
    monkeypatch.setenv('SITE_ADMIN_TOKEN', 'test-site-only-secret')
    monkeypatch.setattr(contest_admin, 'SessionLocal', login_db)
    monkeypatch.setattr(contests, 'SessionLocal', login_db)
    monkeypatch.setattr(contest_admin, 'admin_status', lambda *args, **kwargs: {})
    monkeypatch.setattr(contest_admin.templates, 'TemplateResponse', lambda **kwargs: JSONResponse({'ok':True}))
    monkeypatch.setattr(contests, 'finalize_contest_week', lambda *args, **kwargs: object())
    monkeypatch.setattr(contests, 'contest_results_payload', lambda *args: {'finalized':True})
    browser = client()
    assert browser.get('/contests/admin', headers={'X-Contest-Admin-Token':'wrong'}).status_code == 403
    assert browser.get('/contests/admin', headers={'X-Contest-Admin-Token':'test-site-only-secret'}).status_code == 403
    assert browser.get('/contests/admin', headers={'X-Contest-Admin-Token':'test-contest-only-secret'}).status_code == 200
    for _ in range(7):
        assert browser.get('/contests/admin').status_code == 200  # Existing fingerprint, no credential guess.
    assert browser.get('/admin/analytics').status_code == 403
    assert browser.post('/contests/weeks/2026-09-07/finalize').status_code == 403  # Still header-only.
    assert browser.post('/contests/weeks/2026-09-07/finalize',
        headers={'X-Contest-Admin-Token':'test-contest-only-secret'}).json() == {'finalized':True}
    csrf = browser.get('/admin/login').context['csrf']
    assert browser.post('/admin/login',data={'csrf':csrf,'token':'test-contest-only-secret'}).status_code == 403
    assert browser.post('/admin/login',data={'csrf':csrf,'token':'test-site-only-secret'},follow_redirects=False).status_code == 303
    assert client('192.0.2.3').get('/contests/admin',headers={'X-Contest-Admin-Token':'test-site-only-secret'}).status_code == 403
    site_only = client('192.0.2.4')
    csrf = site_only.get('/admin/login').context['csrf']
    assert site_only.post('/admin/login', data={'csrf':csrf,'token':'test-site-only-secret'}, follow_redirects=False).status_code == 303
    assert site_only.get('/contests/admin').status_code == 403
    keys = str(limiter[0].rows)
    assert 'test-contest-only-secret' not in keys and 'test-site-only-secret' not in keys


@pytest.mark.parametrize('path', ['/contests/admin', '/contests/weeks/2026-09-07/finalize'])
def test_contest_credentials_fail_closed_when_required_backend_unavailable(limiter, monkeypatch, path):
    monkeypatch.setenv('CONTEST_ADMIN_TOKEN', 'test-contest-only-secret')
    monkeypatch.setenv('LOGIN_RATE_LIMIT_REQUIRED', 'true')
    call = client().get if path == '/contests/admin' else client().post
    assert call(path,headers={'X-Contest-Admin-Token':'test-contest-only-secret'}).status_code == 503


def _process_ip_boundary(args):
    url, keys = args
    return limits._backend('redis', url).consume(keys, [100, 10])


@pytest.mark.parametrize('backend_name', ['memory', 'redis'])
def test_concurrent_ip_boundary_reserves_only_admitted_account(limiter, request, backend_name):
    backend = limiter[0]
    if backend_name == 'redis':
        url, backend = request.getfixturevalue('redis_backend')
    prefix = f'ww:login:v1:test:{uuid4().hex}'
    ip = prefix + ':ip'
    accounts = [prefix + f':account:{index}' for index in range(40)]
    for _ in range(99):
        assert backend.consume([ip], [100]) == 0
    if backend_name == 'redis':
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(_process_ip_boundary, [(url, [ip, account]) for account in accounts]))
        counts = [int(backend.client.get(key) or 0) for key in accounts]
        assert 0 < backend.client.pttl(ip) <= 900000
    else:
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda key: backend.consume([ip,key], [100,10]), accounts))
        counts = [backend.rows.get(key, (0, 0))[0] for key in accounts]
    assert results.count(0) == 1
    assert sum(counts) == 1
