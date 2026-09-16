"""Exercise emitted default Uvicorn logs over a loopback socket, not mock filters."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest


SERVER = '''
from app.main import app
from app.db import Base, engine, SessionLocal
from app.models import WoodchuckProfile, WoodchuckState, TrustedVerifier
from app.security import hash_pin
Base.metadata.create_all(engine)
with SessionLocal() as s:
    p = WoodchuckProfile(woodchuck_id="WC-LOG-TEST", display_name="Synthetic",
        pin_hash=hash_pin("2468"), instrument="Flute", level="Beginner", goal="Practice")
    s.add(p)
    s.flush()
    s.add(WoodchuckState(profile_id=p.id, state_json={"progress":{"credits":20}}, revision=0))
    s.add(TrustedVerifier(email="synthetic-verifier@example.test", display_name="Synthetic adult", pin_hash=hash_pin("2468")))
    s.commit()
@app.get("/test-error/{token}")
def fail(token: str):
    raise RuntimeError("exception-secret-" + token)
'''


@pytest.mark.parametrize("level", ["info", "trace"])
def test_emitted_uvicorn_logs_exclude_capabilities_and_exception_payloads(tmp_path, level):
    (tmp_path / 'log_app.py').write_text(SERVER)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {"PATH": os.environ['PATH'], "PYTHONPATH": str(Path.cwd()),
           "DATABASE_URL": 'sqlite:///' + str(tmp_path / 'logging.db'),
           "SESSION_SECRET": 'synthetic-session-secret-for-logging',
           "SESSION_COOKIE_SECURE": 'false', "LOGIN_RATE_LIMIT_MODE": 'off',
           "PYTHONPYCACHEPREFIX": os.environ.get('PYTHONPYCACHEPREFIX', str(tmp_path / 'pycache'))}
    output = tmp_path / 'uvicorn.log'
    with output.open('w') as stream:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'log_app:app',
            '--app-dir', str(tmp_path), '--host', '127.0.0.1', '--port', str(port),
            '--no-proxy-headers', '--timeout-graceful-shutdown', '2', '--log-level', level],
            env=env, stdout=stream, stderr=stream)
        def request(path, data=None, cookie=None):
            headers = {'X-Admin-Token': 'synthetic-admin-header-secret',
                       'Authorization': 'Bearer synthetic-header-secret'}
            if cookie:
                headers['Cookie'] = cookie
            if isinstance(data, dict):
                headers['Content-Type'] = 'application/json'
                data = json.dumps(data).encode()
            elif data is not None:
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
                data = data.encode()
            try:
                response = urlopen(Request(f'http://127.0.0.1:{port}' + path, data=data,
                                           headers=headers), timeout=5)
            except HTTPError as error:
                response = error
            return response.status, response.headers, response.read()
        try:
            for _ in range(1000):
                try:
                    request('/unknown?query-secret-marker=value')
                    break
                except URLError:
                    assert process.poll() is None, output.read_text()
                    time.sleep(.05)
            else:
                pytest.fail('Local Uvicorn did not start')
            status, headers, _ = request('/account/login?query-secret-marker=value',
                'woodchuck_id=WC-LOG-TEST&pin=2468')
            assert status == 200
            cookie = headers['set-cookie'].split(';')[0]
            assert request('/account/login', 'woodchuck_id=WC-LOG-TEST&pin=synthetic-pin-secret')[0] == 401
            assert request('/trusted-verifiers/login',
                'email=synthetic-verifier%40example.test&pin=2468')[0] == 200
            assert request('/trusted-verifiers/login',
                'email=synthetic-verifier%40example.test&pin=synthetic-verifier-pin-secret')[0] == 401
            assert request('/admin/login', 'token=synthetic-admin-form-secret&csrf=wrong')[0] == 403
            paths = ['/trusted-verifiers/accept/invitation-secret-marker',
                     '/trusted-verifiers/invitations/verifier-secret-marker/accept',
                     '/membership/invitations/membership-secret-marker',
                     '/unknown/path-secret-marker?token=query-secret-marker']
            for path in paths:
                request(path, {} if path.endswith('/accept') else None)
            status, _, body = request('/arcade/plays', {'game_key': 'blue'}, cookie)
            assert status == 200
            play_token = json.loads(body)['play_token']
            assert request(f'/arcade/plays/{play_token}/complete?token=query-secret-marker',
                           {'score': 0}, cookie)[0] == 200
            assert request('/arcade/plays/arcade-secret-marker/complete', {'score': 5}, cookie)[0] == 404
            assert request('/arcade/plays/arcade-secret-marker/complete', {'score': 'bad'}, cookie)[0] == 422
            assert request('/test-error/exception-path-marker?token=query-secret-marker')[0] == 500
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    logs = output.read_text()
    for secret in ['query-secret-marker', 'invitation-secret-marker', 'verifier-secret-marker',
                   'membership-secret-marker', 'path-secret-marker', 'synthetic-pin-secret',
                   'synthetic-admin-header-secret', 'synthetic-header-secret',
                   'synthetic-session-secret-for-logging', 'exception-path-marker',
                   'synthetic-verifier@example.test', 'synthetic-verifier-pin-secret',
                   'synthetic-admin-form-secret', 'exception-secret-', 'arcade-secret-marker', play_token, cookie]:
        assert secret not in logs
    for diagnostic in ['POST /account/login HTTP/1.1" 200', 'POST /account/login HTTP/1.1" 401',
                       'POST /trusted-verifiers/login HTTP/1.1" 200',
                       'POST /trusted-verifiers/login HTTP/1.1" 401', 'POST /admin/login HTTP/1.1" 403',
                       '/trusted-verifiers/accept/{token}', '/membership/invitations/{token}',
                       '/arcade/plays/{play_token}/complete HTTP/1.1" 200',
                       '/arcade/plays/{play_token}/complete HTTP/1.1" 404',
                       '/arcade/plays/{play_token}/complete HTTP/1.1" 422',
                       '<unmatched>', 'request_id=', 'duration_ms=',
                       'route=/test-error/{token} status=500', 'exception_type=RuntimeError',
                       'log_app.py:', ':fail']:
        assert diagnostic in logs, logs
