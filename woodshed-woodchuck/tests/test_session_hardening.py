import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.session_config import DEVELOPMENT_SECRET, session_secret, secure_session_cookie
from app import site_admin, contest_admin, accounts


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for key in ('APP_ENV', 'RENDER', 'SESSION_SECRET', 'SESSION_COOKIE_SECURE'):
        monkeypatch.delenv(key, raising=False)


def test_local_defaults_remain_practical():
    assert session_secret() == DEVELOPMENT_SECRET
    assert secure_session_cookie() is False


@pytest.mark.parametrize('marker', ['APP_ENV', 'RENDER'])
@pytest.mark.parametrize('secret', ['', 'short', DEVELOPMENT_SECRET, DEVELOPMENT_SECRET+' ', ' ' * 40])
def test_production_rejects_unsafe_secret(monkeypatch, marker, secret):
    monkeypatch.setenv(marker, 'production' if marker == 'APP_ENV' else 'true')
    monkeypatch.setenv('SESSION_SECRET', secret)
    for function in (session_secret, lambda: site_admin._fingerprint('test-token'),
                     lambda: contest_admin._fingerprint('test-token'),
                     lambda: accounts.retired_identifier_hash('WC-TEST')):
        with pytest.raises(RuntimeError, match='SESSION_SECRET'):
            function()


@pytest.mark.parametrize('value', ['false', '0', '', 'typo'])
def test_production_rejects_insecure_cookie(monkeypatch, value):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('SESSION_COOKIE_SECURE', value)
    with pytest.raises(RuntimeError, match='secure session'):
        secure_session_cookie()


def test_production_defaults_secure_and_preserves_exact_key(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('APP_ENV', 'development')  # Cannot override Render's marker.
    secret = 'a-test-only-long-session-key-of-40-characters'
    monkeypatch.setenv('SESSION_SECRET', secret)
    assert session_secret() == secret
    assert secure_session_cookie() is True


def test_admin_fingerprint_and_rotation(monkeypatch):
    monkeypatch.setenv('SITE_ADMIN_TOKEN', 'isolated-admin-token')
    request = SimpleNamespace(session={})
    site_admin.sign_in_site_admin(request, 'isolated-admin-token')
    site_admin.require_site_admin(request)
    assert 'isolated-admin-token' not in str(request.session)
    monkeypatch.setenv('SESSION_SECRET', 'rotated-test-key')
    with pytest.raises(HTTPException):
        site_admin.require_site_admin(request)


@pytest.mark.parametrize('secret,secure,ok', [('', 'true', False),
    (DEVELOPMENT_SECRET, 'true', False), ('x'*40, 'false', False), ('x'*40, 'true', True)])
def test_actual_startup_validation(monkeypatch, secret, secure, ok):
    env = {**os.environ, 'APP_ENV': 'production', 'SESSION_SECRET': secret,
           'SESSION_COOKIE_SECURE': secure, 'DATABASE_URL': 'sqlite://'}
    result = subprocess.run([sys.executable, '-c', 'import app.main'], env=env,
                            capture_output=True, timeout=30)
    assert (result.returncode == 0) is ok


def test_production_limiter_off_allows_https_login_without_backend(tmp_path):
    """The code-only release must not activate a saved Redis URL implicitly."""
    script = '''
from fastapi.testclient import TestClient
from app.main import app
from app import login_limits
from app.db import Base, engine, SessionLocal
from app.models import WoodchuckProfile, WoodchuckState
from app.security import hash_pin
assert engine.url.get_backend_name() == "sqlite"
Base.metadata.create_all(engine)
def forbidden_backend(*args):
    raise AssertionError("Off mode contacted a backend")
login_limits._backend = forbidden_backend
assert login_limits.protection_status() == {
    "mode": "off", "configured": False, "required": False,
    "production_verified": False, "state": "disabled"}
with SessionLocal() as session:
    profile = WoodchuckProfile(woodchuck_id="WC-OFF-TEST", display_name="Synthetic",
        pin_hash=hash_pin("2468"), instrument="Flute", level="Beginner", goal="Practice")
    session.add(profile)
    session.flush()
    session.add(WoodchuckState(profile_id=profile.id, state_json={}, revision=0))
    session.commit()
with TestClient(app, base_url="https://woodshed.example.test") as client:
    response = client.post("/account/login", data={"woodchuck_id": "WC-OFF-TEST", "pin": "2468"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("secure", "httponly", "samesite=lax"))
    assert client.get("/account/state").status_code == 200
    assert client.post("/account/logout").status_code == 200
    assert client.get("/account/state").status_code == 401
'''
    env = {**os.environ, 'APP_ENV': 'production',
           'SESSION_SECRET': 'synthetic-production-session-secret-for-closeout',
           'SESSION_COOKIE_SECURE': 'true',
           'LOGIN_RATE_LIMIT_MODE': 'off', 'LOGIN_RATE_LIMIT_REQUIRED': 'false',
           'LOGIN_RATE_LIMIT_REDIS_URL': 'redis://unused.example.test:6379/0',
           'FORWARDED_ALLOW_IPS': '*', 'DATABASE_URL': 'sqlite:///' + str(tmp_path / 'off.db')}
    env.pop('LOGIN_TRUSTED_PROXY_CIDRS', None)
    result = subprocess.run([sys.executable, '-c', script], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
