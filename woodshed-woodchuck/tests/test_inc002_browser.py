"""Real Chromium BOARD interactions against a disposable local app."""
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
from test_halloween_browser import SERVER


def test_inc002_board_interactions_and_reload(tmp_path):
    chrome = shutil.which("google-chrome")
    if not chrome or not shutil.which("node"):
        pytest.skip("Local Node and Chromium required")
    source = Path(__file__).resolve().parents[1]
    extra = '''
from app import contests
contests.datetime = Clock
@app.get('/test/trivia-answer')
def trivia_answer():
    today = Clock.moment.astimezone(contests.CENTRAL).date()
    return {'answer': contests.trivia_question_for(today)['correct_answer_id']}
'''
    (tmp_path / "inc002_test_app.py").write_text(SERVER + extra)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "PYTHONPATH": str(source),
           "DATABASE_URL": "sqlite:///" + str(tmp_path / "board.db"),
           "SESSION_SECRET": "inc002-synthetic-browser-session-secret",
           "SESSION_COOKIE_SECURE": "false", "LOGIN_RATE_LIMIT_MODE": "off",
           "LOGIN_RATE_LIMIT_REQUIRED": "false", "PYTHONDONTWRITEBYTECODE": "1",
           "XDG_CACHE_HOME": str(tmp_path / "cache"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_DATA_HOME": str(tmp_path / "data")}
    with (tmp_path / "uvicorn.log").open("w") as stream:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "inc002_test_app:app",
            "--app-dir", str(tmp_path), "--host", "127.0.0.1", "--port", str(port),
            "--no-proxy-headers", "--timeout-graceful-shutdown", "2"], cwd=source, env=env,
            stdout=stream, stderr=stream)
        try:
            for _ in range(1000):
                try:
                    with urlopen(origin + "/guest", timeout=2) as response:
                        assert response.status == 200
                    break
                except URLError:
                    assert process.poll() is None, (tmp_path / "uvicorn.log").read_text()
                    time.sleep(.05)
            else:
                pytest.fail("Disposable INC002 app did not start")
            config = {"origin": origin, "chrome": chrome, "profile": str(tmp_path / "chrome"),
                      "output": str(tmp_path)}
            result = subprocess.run(["node", str(source / "tests/inc002_browser_driver.cjs")],
                input=json.dumps(config), text=True, capture_output=True, cwd=source, env=env, timeout=220)
            (tmp_path / "browser-stdout.json").write_text(result.stdout)
            (tmp_path / "browser-stderr.txt").write_text(result.stderr)
            assert result.returncode == 0, result.stdout + "\n" + result.stderr
            proof = json.loads(result.stdout)
            assert proof["widths"] == [1440, 390]
            assert proof["checks"] >= 70
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
