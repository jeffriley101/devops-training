"""Rendered Arcade visibility and real gameplay initialization in Chromium."""
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

from tests.test_history_browser import DRIVER as HISTORY_DRIVER
from tests.test_sensitive_logging import SERVER


# Reuse the existing Chromium transport and real cookie login.
DRIVER = HISTORY_DRIVER.split(
    "    await send('Page.navigate', {url: config.origin + '/arcade/history-mystery'}, sessionId);"
)[0].replace("60000", "120000").replace(
    "awaitPromise: true, returnByValue: true",
    "awaitPromise: true, returnByValue: true, userGesture: true",
) + r'''
    const personal = [
      {key:'blue', path:'/arcade/blue', best:'#arcade-game-best', score:17,
       start:'#arcade-game-start', running:'!document.querySelector("[data-blue-action=right]").disabled'},
      {key:'radio-tuner', path:'/arcade/radio-tuner', best:'#arcade-game-best', score:23,
       start:'#arcade-game-start', running:'!document.getElementById("radio-game-tap").disabled'},
      {key:'wheel-of-woodchuck', path:'/arcade/wheel-of-woodchuck', best:'#wheel-best', score:31,
       start:'#wheel-start', running:'!document.getElementById("wheel-spin").disabled && document.querySelectorAll("[data-wheel-letter]").length === 26'},
      {key:'plunge-burrow', path:'/plunge-burrow', best:'#plunge-best', score:41,
       start:'#plunge-start', running:'document.getElementById("plunge-state").textContent === "Playing" && !document.getElementById("plunge-pause").disabled'},
    ];
    const shared = ['thirds', 'dressed-to-the-nines', 'interval-basic-training', 'scale-keyboard'];
    const navigate = async path => {
      await send('Page.navigate', {url:config.origin + path}, sessionId);
      await until(`location.pathname === ${JSON.stringify(path)} && document.readyState !== 'loading'
        && !!window.WoodshedArcadeEconomy && document.querySelector('[data-arcade-balance]')?.textContent !== '—'`);
    };
    const checkPersonal = async (bestSelector, listSelector, scopeSelector) => {
      await evaluate(`(() => {
        const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
        const best = document.querySelector(${JSON.stringify(bestSelector)});
        const list = document.querySelector(${JSON.stringify(listSelector)});
        if (!visible(best) || !best.parentElement.innerText.startsWith('Personal Best')) throw Error('Personal Best missing');
        if (!list || visible(list) || visible(list.closest('section'))) throw Error('Top 5 visible or required DOM missing');
        const scope = document.querySelector(${JSON.stringify(scopeSelector)});
        if (/Top 5/i.test(scope.innerText)) throw Error('Visible Top 5 advertising');
      })()`);
    };
    const checkShared = async key => {
      await evaluate(`(() => {
        const list = document.querySelector('[data-arcade-leaderboard="${key}"]');
        const panel = list?.closest('section');
        if (!panel || !panel.getClientRects().length || getComputedStyle(panel).visibility === 'hidden'
            || !panel.innerText.includes('Top 5')) throw Error('Shared Top 5 hidden: ${key}');
      })()`);
    };
    await navigate('/arcade');
    for (const game of personal) {
      const best = `[data-arcade-personal-best="${game.key}"]`;
      await until(`document.querySelector('${best}').textContent === '${game.score}'`);
      await checkPersonal(best, `[data-arcade-leaderboard="${game.key}"]`,
        `.arcade-cabinet:has(${best})`);
    }
    for (const key of shared) await checkShared(key);
    for (const game of personal) {
      await navigate(game.path);
      await until(`document.querySelector('${game.best}')?.textContent === '${game.score}'`);
      const list = game.key === 'plunge-burrow' ? '#plunge-leaderboard' : `[data-arcade-leaderboard="${game.key}"]`;
      await checkPersonal(game.best, list, 'main');
      await evaluate(`document.querySelector('${game.start}').click()`);
      await until(game.running);
      // Existing score rendering can update the hidden list during play.
      await checkPersonal(game.best, list, 'main');
    }
    for (const key of shared) {
      await navigate('/arcade/' + key);
      await checkShared(key);
    }
    process.stdout.write(JSON.stringify({personal:personal.map(g => g.key), shared}));
  } catch (error) {
    process.stderr.write(JSON.stringify(error, Object.getOwnPropertyNames(error)));
    process.exitCode = 1;
  } finally {clearTimeout(timer); chrome.kill();}
});
'''


@pytest.mark.parametrize("width", [390, 1280])
def test_personal_scores_visible_shared_panels_hidden_and_games_start(tmp_path, width):
    chrome = shutil.which("google-chrome")
    if not chrome or not shutil.which("node"):
        pytest.skip("Chromium and Node required")
    seed = SERVER.replace('"credits":20', '"credits":500').replace(
        'state_json={"progress"',
        'state_json={"profile":{"instrument":"Flute","level":"Beginner","goal":"Practice"},"progress"',
    ) + '''
from app.models import ArcadeHighScore
from sqlalchemy import select
with SessionLocal() as session:
    profile = session.scalar(select(WoodchuckProfile))
    profile.plunge_best_score = 41
    for game, score in [('blue', 17), ('radio-tuner', 23), ('wheel-of-woodchuck', 31)]:
        session.add(ArcadeHighScore(profile_id=profile.id, game_key=game, best_score=score))
    session.commit()
'''
    (tmp_path / "browser_app.py").write_text(seed)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {
        "PATH": os.environ["PATH"], "PYTHONPATH": str(Path.cwd()),
        "DATABASE_URL": "sqlite:///" + str(tmp_path / "browser.db"),
        "SESSION_SECRET": "synthetic-personal-arcade-ui-session",
        "SESSION_COOKIE_SECURE": "false", "LOGIN_RATE_LIMIT_MODE": "off",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    log = tmp_path / "uvicorn.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "browser_app:app", "--app-dir", str(tmp_path),
             "--host", "127.0.0.1", "--port", str(port), "--timeout-graceful-shutdown", "2"],
            env=env, stdout=stream, stderr=stream,
        )
        try:
            for _ in range(1000):
                try:
                    with urlopen(origin + "/login", timeout=5):
                        break
                except URLError:
                    assert process.poll() is None, log.read_text()
                    time.sleep(.05)
            else:
                pytest.fail("Local Uvicorn did not start: " + log.read_text())
            result = subprocess.run(
                ["node", "-e", DRIVER], input=json.dumps({
                    "chrome": chrome, "profile": str(tmp_path / "chrome"),
                    "origin": origin, "width": width,
                }), text=True, capture_output=True, timeout=130,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {
                "personal": ["blue", "radio-tuner", "wheel-of-woodchuck", "plunge-burrow"],
                "shared": ["thirds", "dressed-to-the-nines", "interval-basic-training", "scale-keyboard"],
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
