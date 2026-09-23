"""Real Chromium, DOM handlers, cookie login and local Uvicorn/SQLite quiz flow."""
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

from app.history_mystery import history_mystery_central_date, history_mystery_questions_for_date
from tests.test_sensitive_logging import SERVER


DRIVER = r'''
const {spawn} = require('node:child_process');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const config = JSON.parse(input);
  const chrome = spawn(config.chrome, ['--headless', '--no-sandbox', '--remote-debugging-pipe',
    '--no-first-run', '--disable-background-networking', '--disable-component-update',
    '--disable-sync', '--user-data-dir=' + config.profile],
    {stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe']});
  let sequence = 0, buffer = '';
  const pending = new Map();
  const timer = setTimeout(() => {chrome.kill(); process.exit(2);}, 60000);
  chrome.stdio[4].on('data', chunk => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf('\0')) >= 0) {
      const message = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      const waiter = pending.get(message.id);
      if (waiter) {
        pending.delete(message.id);
        message.error ? waiter.reject(message.error) : waiter.resolve(message.result);
      }
    }
  });
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, {resolve, reject});
    chrome.stdio[3].write(JSON.stringify({id, method, params, sessionId}) + '\0');
  });
  try {
    const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
    const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, sessionId);
      if (result.exceptionDetails) throw result.exceptionDetails;
      return result.result.value;
    };
    const until = async expression => {
      for (let i = 0; i < 200; i++) {
        try {
          if (await evaluate(expression)) return;
        } catch (error) {
          // Navigation can destroy the old execution context before the next
          // document is ready. Only retry this CDP navigation race.
          if (error.code !== -32000 || !/navigated|context/i.test(error.message)) throw error;
        }
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      throw new Error('Timed out: ' + expression);
    };
    await send('Emulation.setDeviceMetricsOverride', {width: config.width, height: 844,
      deviceScaleFactor: 1, mobile: config.width < 500}, sessionId);
    await send('Page.navigate', {url: config.origin + '/login'}, sessionId);
    await until('document.readyState === "complete" && location.pathname === "/login"');
    const login = await evaluate(`fetch('/account/login', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'woodchuck_id=WC-LOG-TEST&pin=2468'}).then(r => r.status)`);
    if (login !== 200) throw new Error('Login failed');
    await send('Page.navigate', {url: config.origin + '/arcade/history-mystery'}, sessionId);
    await until('document.readyState === "complete" && !!window.WoodshedArcadeEconomy && document.querySelector("[data-arcade-balance]").textContent !== "—"');
    const initial = await evaluate('Number(document.querySelector("[data-arcade-balance]").textContent)');
    await evaluate('document.getElementById("history-mystery-start").click()');
    await until('document.querySelectorAll("#history-mystery-answers button:not(:disabled)").length > 0');
    const buttonHeight = await evaluate('document.querySelector("#history-mystery-answers button").getBoundingClientRect().height');
    // Lose the response after the server has saved the first answer; real shared
    // retry must send the same answer and not consume another question/reward.
    await evaluate(`(() => {const native=window.fetch; let lost=false; window.fetch=async (...args) => {
      const response=await native(...args); if(!lost && String(args[0]).endsWith('/answer')) {
        lost=true; throw new Error('synthetic lost response'); } return response; };})()`);
    for (let index = 0; index < 5; index++) {
      const choice = config.choices[index];
      await evaluate(`Array.from(document.querySelectorAll('#history-mystery-answers button')).find(b => b.textContent === ${JSON.stringify(choice)}).click()`);
      if (index === 4) {
        await until('document.getElementById("history-mystery-start").textContent === "Played Today"');
      } else {
        await until(`document.getElementById('history-mystery-progress').textContent === '${index+2}' && !!document.querySelector('#history-mystery-answers button:not(:disabled)')`);
      }
      if (index === 1) {
        await send('Page.reload', {}, sessionId);
        await until('document.readyState === "complete" && document.getElementById("history-mystery-message")?.textContent.includes("Resume")');
        await evaluate('document.getElementById("history-mystery-start").click()');
        await until('document.getElementById("history-mystery-progress").textContent === "3" && !!document.querySelector("#history-mystery-answers button:not(:disabled)")');
      }
    }
    await until(`document.getElementById('history-mystery-best').textContent === '${config.score}'`);
    const final = await evaluate(`fetch('/account/state').then(r => r.json()).then(p => ({
      credits:p.state.progress.credits, answers:p.state._history_mystery.answers.length,
      score:document.getElementById('history-mystery-score').textContent,
      best:document.getElementById('history-mystery-best').textContent,
      message:document.getElementById('history-mystery-message').textContent}))`);
    await send('Page.navigate', {url: config.origin + '/arcade'}, sessionId);
    await until('document.readyState === "complete" && document.querySelector("[data-arcade-attempts=history-mystery]")?.textContent.includes("2 purchased")');
    await until('!!window.WoodshedArcadeArt');
    await evaluate(`(async () => {
      for (const img of document.querySelectorAll('.arcade-art-image')) {
        img.scrollIntoView();
        await img.decode();
        if (img.naturalWidth !== 256 || img.naturalHeight !== 256) throw Error('Incorrect lobby tile size');
      }
    })()`);
    await until('document.querySelectorAll(".arcade-cabinet-screen.arcade-art-active").length === 6');
    await evaluate(`(() => {
      if (document.querySelector('.arcade-cabinet-marquee')) throw Error('Redundant marquee');
      for (const screen of document.querySelectorAll('.arcade-cabinet-screen')) {
        const link=screen.closest('a');
        if (!link.getAttribute('aria-label')?.startsWith('Play ')) throw Error('Missing game label');
        const fallback=screen.querySelector('.arcade-art-fallback');
        const style=getComputedStyle(screen);
        if (screen.classList.contains('arcade-art-active')) {
          const img=screen.querySelector('.arcade-art-image');
          if (getComputedStyle(fallback).display !== 'none' || img.hidden ||
              style.backgroundImage !== 'none' || style.padding !== '0px' ||
              getComputedStyle(img).objectFit !== 'contain') throw Error('Mixed artwork');
          const box=img.getBoundingClientRect();
          if (Math.abs(box.width-box.height)>1) throw Error('Non-square approved art');
        } else if (!screen.querySelector('.arcade-keeper-art') ||
                   getComputedStyle(fallback).display === 'none' ||
                   screen.querySelector('.arcade-art-image')) throw Error('Lost keeper fallback');
      }
      window.scrollTo(0,0);
    })()`);
    const metrics=await send('Page.getLayoutMetrics', {}, sessionId);
    const shot=await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:true,
      clip:{x:0,y:0,width:config.width,height:metrics.cssContentSize.height,scale:1}}, sessionId);
    require('node:fs').writeFileSync(config.artScreenshot, Buffer.from(shot.data,'base64'));
    const room = await evaluate(`({free:document.querySelector('[data-arcade-price=blue]').textContent,
      normal:document.querySelector('[data-arcade-price=thirds]').textContent,
      standingsAbove:Array.from(document.querySelectorAll('.arcade-leaderboard')).every(board => {
        const link=board.parentElement.querySelector('.arcade-cabinet-link');
        return !link.contains(board) && board.getBoundingClientRect().bottom <= link.getBoundingClientRect().top;
      }),
      injectedArt:document.querySelectorAll('.arcade-art-image').length,
      decks:document.querySelectorAll('.arcade-cabinet-control-panel[aria-hidden=true] .arcade-token-slot').length,
      reserved:document.querySelectorAll('.arcade-classroom li').length,
      classroomLinks:document.querySelectorAll('.arcade-classroom a').length,
      overflow:document.documentElement.scrollWidth > window.innerWidth})`);
    // A genuine failed request restores Scale Keyboard's original visual and background.
    await evaluate(`document.querySelector('[data-arcade-art="scale-keyboard"] .arcade-art-image').src =
      '/static/img/arcade/intentionally-missing-smoke-test.png'`);
    await until(`!document.querySelector('[data-arcade-art="scale-keyboard"]').classList.contains('arcade-art-active')`);
    await evaluate(`(() => {
      const screen=document.querySelector('[data-arcade-art="scale-keyboard"]');
      if (!screen.querySelector('.arcade-art-image').hidden ||
          getComputedStyle(screen.querySelector('.arcade-art-fallback')).display === 'none' ||
          getComputedStyle(screen).backgroundImage === 'none') throw Error('Fallback not restored');
    })()`);
    await send('Page.navigate', {url: config.origin + '/practice/skill-building'}, sessionId);
    await until('document.readyState === "complete" && document.querySelector(".arcade-classroom")');
    await until('!!window.WoodshedArcadeArt');
    await evaluate(`(async () => {
      for (const img of document.querySelectorAll('.arcade-art-image')) {
        img.scrollIntoView();
        await img.decode();
        if (img.naturalWidth !== 256 || img.naturalHeight !== 256) throw Error('Incorrect exercise tile size');
      }
    })()`);
    const exercises = await evaluate(`({
      count:document.querySelectorAll('.arcade-classroom li').length,
      tiles:document.querySelectorAll('.arcade-classroom .arcade-art-image').length,
      locked:Array.from(document.querySelectorAll('.arcade-classroom li')).every(card=>card.textContent.includes('Locked')),
      links:document.querySelectorAll('.arcade-classroom a, .arcade-classroom button').length,
      overflow:document.documentElement.scrollWidth > window.innerWidth
    })`);
    process.stdout.write(JSON.stringify({initial, buttonHeight, ...final, room, exercises}));
  } catch (error) {process.stderr.write(JSON.stringify(error, Object.getOwnPropertyNames(error))); process.exitCode=1;}
  finally {clearTimeout(timer); chrome.kill();}
});
'''


@pytest.mark.parametrize('width,score', [(390, 0), (1440, 5)])
def test_history_browser_loss_refresh_and_completion(tmp_path, width, score):
    chrome = shutil.which('google-chrome')
    if not chrome or not shutil.which('node'):
        pytest.skip('Chromium and Node required for real browser validation')
    # The lobby requires the same completed setup state as a real account.
    seed = SERVER.replace('"credits":20', '"credits":120').replace(
        'state_json={"progress"',
        'state_json={"profile":{"instrument":"Flute","level":"Beginner","goal":"Practice"},"progress"')
    (tmp_path / 'browser_app.py').write_text(seed)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(Path.cwd()),
           'DATABASE_URL': 'sqlite:///' + str(tmp_path / 'browser.db'),
           'SESSION_SECRET': 'synthetic-browser-session', 'SESSION_COOKIE_SECURE': 'false',
           'PYTHONPYCACHEPREFIX': os.environ.get('PYTHONPYCACHEPREFIX', str(tmp_path / 'pycache'))}
    questions = history_mystery_questions_for_date(history_mystery_central_date())
    choices = [q['answer'] if score == 5 else next(c for c in q['choices'] if c != q['answer']) for q in questions]
    with (tmp_path / 'uvicorn.log').open('w') as stream:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'browser_app:app', '--app-dir', str(tmp_path),
            '--host', '127.0.0.1', '--port', str(port), '--timeout-graceful-shutdown', '2'],
            env=env, stdout=stream, stderr=stream)
        try:
            for _ in range(1000):
                try:
                    with urlopen(origin + '/login', timeout=5) as response:
                        assert response.status == 200
                    break
                except URLError:
                    assert process.poll() is None
                    time.sleep(.05)
            else:
                pytest.fail('Local Uvicorn did not start')
            result = subprocess.run(['node', '-e', DRIVER], input=json.dumps({
                'chrome': chrome, 'profile': str(tmp_path / 'chrome'), 'origin': origin,
                'width': width, 'score': score, 'choices': choices,
                'artScreenshot': str(tmp_path / f'arcade-{width}.png'),
            }), text=True, capture_output=True, timeout=75)
            assert result.returncode == 0, result.stderr
            payload = json.loads(result.stdout)
            assert payload['answers'] == 5
            assert payload['score'] == payload['best'] == str(score)
            assert payload['credits'] == payload['initial'] - 100 + (5 if score == 5 else 0)
            assert payload['buttonHeight'] >= 48
            assert f'Final score: {score} / 5' in payload['message']
            assert payload['room'] == {'free': 'Always free', 'normal': '100 Dandelions · 3 attempts',
                                       'reserved': 0, 'classroomLinks': 0, 'overflow': False,
                                       'standingsAbove': True, 'injectedArt': 6, 'decks': 9}
            assert payload['exercises'] == {'count': 5, 'tiles': 5, 'locked': True, 'links': 0, 'overflow': False}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
