"""Real disposable Redis: cross-worker renewal, retirement and TTL safety."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import classroom_guest as guest, main, login_limits
from test_classroom_guest_s4 import s4_web, classroom_db, adult_pin_hash, enter, check, fingerprints


@pytest.fixture
def transient_redis(tmp_path):
    redis = pytest.importorskip('redis')
    binary = os.getenv('WW_S4_TEST_REDIS_SERVER') or shutil.which('redis-server')
    if not binary or not Path(binary).is_file():
        pytest.skip('Disposable Redis binary required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with (tmp_path/'redis.log').open('w') as output:
        process = subprocess.Popen([binary, '--bind', '127.0.0.1', '--port', str(port),
            '--save', '', '--appendonly', 'no', '--dir', str(tmp_path)], stdout=output, stderr=output)
        url = f'redis://127.0.0.1:{port}/0'
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1)
        try:
            for _ in range(100):
                assert process.poll() is None, (tmp_path/'redis.log').read_text()
                try:
                    if client.ping(): break
                except redis.ConnectionError: time.sleep(.05)
            else: pytest.fail('Disposable Redis startup failed')
            yield url, client
        finally:
            client.close(); process.terminate(); process.wait(timeout=10)


def test_redis_context_is_shared_and_retirement_wins_over_late_renewal(transient_redis):
    url, client = transient_redis
    first = guest.RedisContexts(client)
    second = guest.RedisContexts(login_limits.RedisBackend(url).client)
    key = 'synthetic-context'
    assert first.exchange(key, None, 'original')
    assert second.get(key) == 'original'
    assert not second.exchange(key, None, 'collision')
    observed = second.get(key)
    first.delete(key)
    assert not second.exchange(key, observed, 'late-renewal')
    assert first.get(key) is None


def test_expired_redis_context_cannot_be_revived(transient_redis, monkeypatch):
    _, client = transient_redis
    monkeypatch.setattr(guest, 'LIFETIME', 1)
    store = guest.RedisContexts(client)
    assert store.exchange('synthetic-expiry', None, 'original')
    time.sleep(1.05)
    assert store.get('synthetic-expiry') is None
    assert not store.exchange('synthetic-expiry', 'original', 'revived')


def test_actual_routes_with_existing_redis_backend_have_no_sql_writes(s4_web, transient_redis, monkeypatch):
    url, storage = transient_redis
    monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', 'redis')
    monkeypatch.setenv('LOGIN_RATE_LIMIT_REDIS_URL', url)
    before = fingerprints(s4_web.factory)
    client = TestClient(main.app)
    response = enter(client)
    assert response.status_code == 200, response.text
    binding = response.json()['binding']
    cookie = client.cookies.get('session')
    assert check(client, binding).status_code == 200
    keys = list(storage.scan_iter('ww:classroom-guest:v1:*'))
    assert keys and all(0 < storage.ttl(k) <= 900 for k in keys)
    stored = ' '.join(storage.get(k) for k in keys)
    assert all(secret not in stored for secret in ('BAND2026', 'WC-SYNTHETIC', 'Private Student', '127.0.0.1'))
    assert check(client, binding, 'leave').status_code == 200
    replay = TestClient(main.app); replay.cookies.set('session', cookie)
    assert check(replay, binding).status_code == 403
    assert fingerprints(s4_web.factory) == before
