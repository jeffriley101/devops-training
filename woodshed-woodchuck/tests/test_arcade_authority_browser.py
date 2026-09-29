"""Real Chromium: DOM answers, lost responses, shared Top 5 and score injection."""
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
from sqlalchemy import create_engine, select
from app.models import ArcadePlaySession
from tests.test_history_browser import DRIVER as HISTORY_DRIVER
from tests.test_sensitive_logging import SERVER

# Reuse just the CDP transport/login setup; gameplay below uses real DOM handlers.
DRIVER = HISTORY_DRIVER.split("    await send('Page.navigate', {url: config.origin + '/arcade/history-mystery'}, sessionId);")[0]
DRIVER = DRIVER.replace('awaitPromise: true, returnByValue: true', 'awaitPromise: true, returnByValue: true, userGesture: true').replace('60000', '65000').replace('document.readyState === \"complete\"', 'document.readyState !== \"loading\"') + r'''
    await send('Page.navigate', {url: config.origin + '/arcade/' + config.game}, sessionId);
    await until('document.readyState !== "loading" && !!window.WoodshedArcadeEconomy');
    await evaluate(`(() => {const native=window.fetch; window.lastChallenge=null; window.actionCount=0;
      window.fetch=async (...args) => {
        const response=await native(...args);
        if (String(args[0]) === '/arcade/plays' && response.ok) window.openedPlay=await response.clone().json();
        if (String(args[0]).endsWith('/action') && response.ok) {
          window.lastChallenge=(await response.clone().json()).challenge;
          window.actionCount++;
          if(window.actionCount===1) throw new Error('synthetic lost action response');
        }
        return response;
      };})()`);
    await evaluate(`document.getElementById('${config.prefix}-start').click()`);
    await until('!!window.openedPlay');
    // A valid token cannot turn a forged total into a completion.
    const forged = await evaluate(`fetch('/arcade/plays/'+window.openedPlay.play_token+'/complete', {
      method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify({score:2147483647})}).then(r=>r.status)`);
    if(forged !== 409) throw Error('Forged completion accepted');
    await new Promise(resolve=>setTimeout(resolve,1000));
    const answer = await evaluate(`(() => {
      const q=window.openedPlay.challenge.question;
      if('${config.game}'==='thirds') return {'C Major':'E','D Minor':'F','E Minor':'G','F Major':'A','G Major':'B','A Minor':'C','B Minor':'D'}[q.chord];
      if('${config.game}'==='dressed-to-the-nines') return {'C':'D','D':'E','F':'G','G':'A','A':'B','Bb':'C','Eb':'F'}[q.start];
      if('${config.game}'==='interval-basic-training') return {60:'Unison',62:'2nd',64:'3rd',65:'4th',67:'5th',69:'6th',71:'7th',72:'Octave',74:'9th'}[q.secondMidi];
      return q.rootMidi;
    })()`);
    if(config.game==='thirds') {
      await evaluate(`document.getElementById('thirds-answer').value=${JSON.stringify(answer)};
        document.getElementById('thirds-answer-form').requestSubmit()`);
    } else if(config.game==='scale-keyboard') {
      await evaluate(`document.querySelector('#scale-keyboard-keys button[data-scale-midi="${answer}"]').click()`);
    } else {
      await until(`!document.querySelector('[data-${config.prefix}-answer=${JSON.stringify(answer)}]').disabled`);
      await evaluate(`document.querySelector('[data-${config.prefix}-answer=${JSON.stringify(answer)}]').click()`);
    }
    await until('window.actionCount === 2');
    const expected = config.game==='scale-keyboard' ? 100 : 1;
    await until(`document.getElementById('${config.prefix}-score').textContent === '${expected}'`);
    if(await evaluate('window.lastChallenge.action_index') !== 1) throw Error('Retry counted twice');
    // Wait for the real server/browser duration, with no fake timing or seeded score.
    await new Promise(resolve=>setTimeout(resolve,30000));
    await until(`document.getElementById('${config.prefix}-best').textContent === '${expected}'`);
    const token=await evaluate('window.openedPlay.play_token');
    const replay=await evaluate(`fetch('/arcade/plays/'+${JSON.stringify(token)}+'/complete', {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({score:2147483647})}).then(r=>r.status)`);
    if(replay!==409) throw Error('Completed score changed');
    const relogin = await evaluate(`fetch('/account/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:'woodchuck_id=WC-OBSERVER&pin=2468'}).then(r=>r.status)`);
    if(relogin!==200) throw Error('Observer login failed');
    await send('Page.navigate', {url: config.origin + '/arcade/' + config.game}, sessionId);
    await until(`document.querySelector('[data-arcade-leaderboard]')?.textContent.includes('Synthetic — ${expected}')`);
    const leaderboard=await evaluate(`fetch('/arcade/scores/${config.game}').then(r=>r.json())`);
    if(leaderboard.leaderboard[0].score!==expected || leaderboard.leaderboard[0].is_current_user) throw Error('Shared board mismatch');
    process.stdout.write(JSON.stringify({score:expected, leaderboard, forged, replay}));
  } catch (error) {process.stderr.write(JSON.stringify(error, Object.getOwnPropertyNames(error))); process.exitCode=1;}
  finally {clearTimeout(timer); chrome.kill();}
});
'''


@pytest.mark.parametrize('game,prefix', [('thirds','thirds'), ('dressed-to-the-nines','nines'),
    ('interval-basic-training','interval'), ('scale-keyboard','scale-keyboard')])
def test_browser_verified_run_visible_to_second_account(tmp_path, game, prefix):
    chrome = shutil.which('google-chrome')
    if not chrome or not shutil.which('node'):
        pytest.skip('Chromium and Node required')
    seed = SERVER.replace('"credits":20', '"credits":500').replace('state_json={"progress"',
        'state_json={"profile":{"instrument":"Flute","level":"Beginner","goal":"Practice"},"progress"')
    seed += '''
with SessionLocal() as s:
    p=WoodchuckProfile(woodchuck_id='WC-OBSERVER',display_name='Observer',pin_hash=hash_pin('2468'),
                      instrument='Flute',level='Beginner',goal='Practice')
    s.add(p); s.flush(); declare_age(s,p.id,'adult')
    s.add(WoodchuckState(profile_id=p.id,state_json={'profile':{'instrument':'Flute','level':'Beginner','goal':'Practice'},'progress':{'credits':500}},revision=0))
    s.commit()
'''
    (tmp_path/'browser_app.py').write_text(seed)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    db_url='sqlite:///'+str(tmp_path/'browser.db')
    env={'PATH':os.environ['PATH'],'PYTHONPATH':str(Path.cwd()),'DATABASE_URL':db_url,
         'SESSION_SECRET':'synthetic-browser-top5-session','SESSION_COOKIE_SECURE':'false',
         'LOGIN_RATE_LIMIT_MODE':'off','PYTHONDONTWRITEBYTECODE':'1'}
    with (tmp_path/'uvicorn.log').open('w') as stream:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','browser_app:app','--app-dir',str(tmp_path),
            '--host','127.0.0.1','--port',str(port),'--timeout-graceful-shutdown','2'],env=env,stdout=stream,stderr=stream)
        try:
            for _ in range(1000):
                try:
                    with urlopen(origin+'/login',timeout=5): break
                except URLError:
                    assert process.poll() is None, (tmp_path/'uvicorn.log').read_text()
                    time.sleep(.05)
            else:
                pytest.fail('Local Uvicorn did not start: '+(tmp_path/'uvicorn.log').read_text())
            result=subprocess.run(['node','-e',DRIVER],input=json.dumps({'chrome':chrome,'profile':str(tmp_path/'chrome'),
                'origin':origin,'width':390,'game':game,'prefix':prefix}),text=True,capture_output=True,timeout=75)
            assert result.returncode==0,result.stderr
            payload=json.loads(result.stdout)
            assert payload['forged']==payload['replay']==409
            engine=create_engine(db_url)
            with engine.connect() as connection:
                assert connection.scalar(select(ArcadePlaySession.authoritative_score))==payload['score']
            engine.dispose()
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
