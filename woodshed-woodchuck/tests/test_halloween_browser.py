"""Chromium presentation checks against an isolated app and synthetic accounts."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from test_r4_browser import SERVER as ROOM_SERVER


SERVER = '''
# Durability is unnecessary for this disposable fixture; retain separate
# connections for the app's concurrent requests.
from sqlalchemy import event
from app.db import engine
@event.listens_for(engine, 'connect')
def disposable_connection(raw, record):
    raw.execute('PRAGMA synchronous=OFF')
''' + ROOM_SERVER + '''
from datetime import datetime, timezone
from app import main
from app.seasons import bootstrap_canonical_seasons
with SessionLocal() as session:
    bootstrap_canonical_seasons(session)
    session.commit()
class Clock(datetime):
    moment = datetime(2026, 9, 29, 18, tzinfo=timezone.utc)
    @classmethod
    def now(cls, tz=None):
        return cls.moment.astimezone(tz)
main.datetime = Clock
@app.post('/test/season/{key}')
def test_season(key: str):
    Clock.moment = datetime(2026, 9, 20 if key == 'school' else 29, 18, tzinfo=timezone.utc)
    return {'ok': True}
'''


def test_halloween_visuals_accessibility_and_fallbacks(tmp_path):
    chrome = shutil.which("google-chrome")
    if not chrome or not shutil.which("node"):
        pytest.skip("Local Node and Chromium required")
    source = Path(__file__).resolve().parents[1]
    (tmp_path / "halloween_test_app.py").write_text(SERVER)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "PYTHONPATH": str(source),
           "DATABASE_URL": "sqlite:///" + str(tmp_path / "halloween.db"),
           "SESSION_SECRET": "halloween-synthetic-browser-session-secret",
           "SESSION_COOKIE_SECURE": "false", "LOGIN_RATE_LIMIT_MODE": "off",
           "LOGIN_RATE_LIMIT_REQUIRED": "false", "PYTHONDONTWRITEBYTECODE": "1",
           "XDG_CACHE_HOME": str(tmp_path / "cache"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_DATA_HOME": str(tmp_path / "data")}
    with (tmp_path / "uvicorn.log").open("w") as stream:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "halloween_test_app:app",
            "--app-dir", str(tmp_path), "--host", "127.0.0.1", "--port", str(port),
            "--no-proxy-headers", "--timeout-graceful-shutdown", "2"], cwd=source, env=env,
            stdout=stream, stderr=stream)
        try:
            last_error = None
            for _ in range(1000):
                try:
                    with urlopen(origin + "/guest", timeout=2) as response:
                        assert response.status == 200
                    break
                except URLError as error:
                    last_error = error
                    assert process.poll() is None, (tmp_path / "uvicorn.log").read_text()
                    time.sleep(.05)
            else:
                pytest.fail(f"Synthetic app did not start: {last_error}\n" + (tmp_path / "uvicorn.log").read_text())
            config = {"origin": origin, "chrome": chrome, "profile": str(tmp_path / "chrome"),
                      "output": str(tmp_path)}
            result = subprocess.run(["node", str(source / "tests/halloween_browser_driver.cjs")],
                input=json.dumps(config), text=True, capture_output=True, cwd=source, env=env, timeout=160)
            (tmp_path / "browser-stdout.json").write_text(result.stdout)
            (tmp_path / "browser-stderr.txt").write_text(result.stderr)
            assert result.returncode == 0, result.stdout + "\n" + result.stderr
            proof = json.loads(result.stdout)
            assert proof["widths"] == [1440, 390, 320]
            assert proof["checks"] >= 60
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
