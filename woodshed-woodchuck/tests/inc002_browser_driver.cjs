// Only test_inc002_browser.py's disposable localhost app is supported.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const config = JSON.parse(input);
  assert.match(config.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
  const chrome = spawn(config.chrome, ['--headless', '--no-sandbox', '--remote-debugging-pipe',
    '--no-first-run', '--disable-background-networking', '--disable-component-update', '--disable-sync',
    '--disable-extensions', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
    '--autoplay-policy=no-user-gesture-required', '--user-data-dir=' + config.profile],
    { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
  const pending = new Map();
  let seq = 0, buffer = '', checks = 0, activePage;
  const screenshots = [];
  const timer = setTimeout(() => { chrome.kill(); process.exit(2); }, 210000);
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + '\0');
  });
  chrome.stdio[4].on('data', chunk => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf('\0')) >= 0) {
      const message = JSON.parse(buffer.slice(0, end));
      buffer = buffer.slice(end + 1);
      const callback = pending.get(message.id);
      if (callback) {
        pending.delete(message.id);
        message.error ? callback.reject(message.error) : callback.resolve(message.result);
      }
    }
  });
  const tab = async () => {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `
      const RealDate = Date;
      window.Date = class extends RealDate {
        constructor(...args) {super(...(args.length ? args : ['2026-09-29T18:00:00Z']));}
        static now() {return new RealDate('2026-09-29T18:00:00Z').getTime();}
      };
    ` }, sessionId);
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true }, sessionId);
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const until = async expression => {
      for (let n = 0; n < 300; n++) {
        if (await evaluate(expression)) return;
        await new Promise(resolve => setTimeout(resolve, 40));
      }
      throw new Error('Timed out: ' + expression);
    };
    const navigate = async () => {
      await send('Page.navigate', { url: config.origin + '/quest' }, sessionId);
      await until(`location.pathname === '/quest' && document.readyState === 'complete' && window.WWSessionBoundary?.isCurrent()`);
      await evaluate('window.WWSessionBoundary.ready');
      await until(`!document.getElementById('complete-quest-btn').textContent.includes('Loading') && document.querySelector('#trivia-options input') && !document.getElementById('contest-standings-loading').offsetHeight`);
    };
    const check = async (expression, label) => { assert.equal(await evaluate(expression), true, label); checks++; };
    const shot = async name => {
      const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
      fs.writeFileSync(config.output + '/' + name + '.png', Buffer.from(data, 'base64'));
      screenshots.push(name + '.png');
    };
    return { sessionId, evaluate, until, navigate, check, shot };
  };
  try {
    const page = await tab();
    activePage = page;
    for (const [width, account] of [[1440, 'A'], [390, 'B']]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height: width === 1440 ? 1000 : 844, deviceScaleFactor: 1, mobile: false }, page.sessionId);
      await send('Page.navigate', { url: config.origin + '/guest/login' }, page.sessionId);
      await page.until(`location.pathname === '/guest/login' && document.readyState === 'complete'`);
      await page.evaluate(`fetch('/account/login', {method:'POST', body:new URLSearchParams({woodchuck_id:'WC-GUEST-${account}',pin:'2468'})}).then(r => {if(!r.ok) throw Error('Login failed');})`);
      await page.navigate();
      const before = await page.evaluate(`fetch('/test/snapshot').then(r=>r.json())`);
      const creditsBefore = before.states['WC-GUEST-' + account].progress.credits;
      let points = 0, credits = creditsBefore;
      await page.check(`document.documentElement.scrollWidth <= innerWidth`, 'Initial BOARD overflow at ' + width);
      await page.check(`!!document.querySelector('.halloween-decor--board') && !!document.querySelector('.season-polaroid-image')`, 'Halloween and Polaroid retained');
      await page.check(`document.querySelector('.notice-card').textContent.includes('do not create verified practice minutes')`, 'Accurate BOARD copy');
      // Old browser completion flags are preferences, not daily eligibility.
      await page.evaluate(`const s=window.WWState.getState();s.bandCamp.daily.careComplete=true;
        s.bandCamp.daily.marchingComplete=true;window.WWState.saveState(s,{sync:false});`);
      // Simulate a failed request and prove controls remain usable.
      await page.evaluate(`window.fetch=(previousFetch=>async (...args)=>{
        if(window.failCare && args[0]==='/contests/camp-points/awards' && JSON.parse(args[1].body).activity_type==='care') {
          window.failCare=false;return new Response(JSON.stringify({detail:'Please try instrument care again.'}),{status:503});
        }
        if(window.failBonus && args[0]==='/contests/bonus-challenge/progress') {
          window.failBonus=false;return new Response(JSON.stringify({detail:'Please try Bonus Challenge again.'}),{status:503});
        }
        return previousFetch(...args);
      })(window.fetch);window.failCare=true;document.getElementById('instrument-care-activity').open=true;document.getElementById('instrument-care-button').click();`);
      await page.until(`document.getElementById('board-feedback').textContent.includes('Please try instrument care again')`);
      await page.check(`!document.getElementById('instrument-care-button').disabled && !document.getElementById('instrument-care-button').textContent.includes('Saving')`, 'Care failure restores control');
      for (const [activity, button, details] of [
        ['hours', 'camp-hours-checkbox', 'camp-hours-activity'],
        ['care', 'instrument-care-button', 'instrument-care-activity'],
        ['marching', 'marching-challenge-button', 'marching-activity'],
      ]) {
        await page.evaluate(`window.failActivity=true;window.fetch=(previousFetch=>async(...args)=>{
          if(window.failActivity && args[0]==='/contests/camp-points/awards' && JSON.parse(args[1].body).activity_type==='${activity}') {
            window.failActivity=false;return new Response(JSON.stringify({detail:'Retry ${activity}.'}),{status:503});
          }
          return previousFetch(...args);
        })(window.fetch);document.getElementById('${details}').open=true;document.getElementById('${button}').click();`);
        await page.until(`document.getElementById('board-feedback').textContent.includes('Retry ${activity}.')`);
        await page.check(`!document.getElementById('${button}').disabled && document.getElementById('${details}').dataset.serverComplete !== 'true'`, activity + ' failure remains retryable');
        if (activity === 'hours') await page.check(`!document.getElementById('${button}').checked`, 'Hours failure unchecks control');
        await page.evaluate(`document.getElementById('${details}').open=true;const b=document.getElementById('${button}');b.click();b.click();`);
        points++; credits++;
        await page.until(`document.getElementById('${details}').dataset.serverComplete === 'true'`);
        await page.check(`document.getElementById('${button}').disabled`, activity + ' completes visibly');
        await page.check(`document.getElementById('board-player-weekly-points').textContent === '${points}'`, activity + ' weekly points');
        await page.check(`document.getElementById('board-player-season-points').textContent === '${points}'`, activity + ' season points');
        await page.until(`window.WWState.getState().progress.credits === ${credits}`);
        await page.check(`document.getElementById('board-feedback').textContent.includes('+1 Board Activity Point and +1 dandelion')`, activity + ' reward feedback');
        await page.navigate();
        await page.until(`document.getElementById('${details}').dataset.serverComplete === 'true'`);
        await page.check(`document.getElementById('${button}').disabled`, activity + ' persists after refresh');
        if (activity === 'hours') await page.check(`document.getElementById('${button}').checked`, 'Hours remains checked');
      }
      await page.evaluate(`window.failBonus=true;window.fetch=(previousFetch=>async(...args)=>{
        if(window.failBonus && args[0]==='/contests/bonus-challenge/progress') {window.failBonus=false;return new Response(JSON.stringify({detail:'Please try Bonus Challenge again.'}),{status:503});}
        return previousFetch(...args);
      })(window.fetch);document.getElementById('complete-quest-btn').click();`);
      await page.until(`document.getElementById('practice-error').textContent.includes('Please try Bonus Challenge again')`);
      await page.check(`!document.getElementById('complete-quest-btn').disabled && document.getElementById('complete-quest-btn').textContent==='I Played It'`, 'Bonus failure restores control');
      await page.evaluate(`const b=document.getElementById('complete-quest-btn');b.click();b.click();`);
      points += 2; credits += 5;
      await page.until(`document.getElementById('complete-quest-btn').textContent==='Quest Complete'`);
      await page.check(`document.getElementById('complete-quest-btn').disabled`, 'Bonus visibly complete');
      await page.check(`document.getElementById('quest-feedback').textContent.includes('+5 dandelions and +2 Board Activity Points')`, 'Bonus exact reward');
      await page.check(`document.getElementById('board-player-weekly-points').textContent==='${points}'`, 'Bonus weekly points');
      await page.until(`window.WWState.getState().progress.credits === ${credits}`);
      await page.navigate();
      await page.until(`document.getElementById('complete-quest-btn').textContent==='Quest Complete'`);
      await page.check(`document.getElementById('complete-quest-btn').disabled`, 'Bonus persists after refresh');
      const answer = await page.evaluate(`fetch('/test/trivia-answer').then(r=>r.json()).then(j=>j.answer)`);
      await page.evaluate(`document.getElementById('trivia-activity').open=true;document.querySelector('#trivia-options input[value=${JSON.stringify(answer)}]').click();document.getElementById('trivia-button').click();`);
      points++; credits++;
      await page.until(`document.getElementById('trivia-summary').textContent.includes('+1 Board Activity Point')`);
      await page.check(`document.getElementById('trivia-button').disabled && document.getElementById('trivia-button').textContent==='Correct ✓'`, 'Trivia still works');
      await page.check(`document.getElementById('board-player-weekly-points').textContent==='${points}'`, 'Trivia points update');
      await page.until(`window.WWState.getState().progress.credits === ${credits}`);
      await page.navigate();
      await page.until(`document.getElementById('trivia-summary').textContent.includes('+1 Board Activity Point')`);
      await page.check(`document.getElementById('trivia-button').disabled`, 'Trivia attempt persists');
      // Direct requests from a second tab must return canonical zero new value.
      const other = await tab();
      await other.navigate();
      const retries = await other.evaluate(`(async()=>{
        const challenge=(await (await fetch('/contests/bonus-challenge/current')).json()).challenge;
        const day=challenge.activity_date;
        const data=[...['hours','care','marching'].map(activity_type=>['/contests/camp-points/awards',{activity_type,activity_date:day}]),
          ['/contests/bonus-challenge/progress',{activity_date:day,challenge_instance:challenge.instance_key}]];
        return Promise.all(data.flatMap(([path,body])=>[1,2].map(async()=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,...await r.json()};})));
      })()`);
      assert(retries.every(r => r.status === 200 && r.created === false && r.credits === credits)); checks++;
      await page.check(`document.documentElement.scrollWidth <= innerWidth`, 'Completed BOARD overflow at ' + width);
      await page.check(`!!document.querySelector('.halloween-decor--board') && !!document.querySelector('.season-polaroid-image')`, 'Halloween after interactions');
      const after = await page.evaluate(`fetch('/test/snapshot').then(r=>r.json())`);
      assert.equal(after.counts.camp_point_awards - before.counts.camp_point_awards, 5); checks++;
      assert.equal(after.counts.reward_grants - before.counts.reward_grants, 5); checks++;
      assert.equal(after.states['WC-GUEST-' + account].progress.credits, creditsBefore + 9); checks++;
      for (const table of ['practice_charts', 'practice_chart_verifications', 'quest_completions', 'contest_results']) {
        assert.equal(after.counts[table], before.counts[table], table + ' unchanged'); checks++;
      }
      await page.shot('inc002-board-' + width);
    }
    process.stdout.write(JSON.stringify({ widths: [1440, 390], checks, screenshots }));
  } catch (error) {
    if (activePage) {
      try {
        await activePage.shot('inc002-failure');
        fs.writeFileSync(config.output + '/inc002-failure.html', await activePage.evaluate('document.documentElement.outerHTML'));
      } catch (_) {}
    }
    process.stderr.write(error.stack || JSON.stringify(error));
    process.exitCode = 1;
  } finally {
    clearTimeout(timer);
    chrome.kill();
  }
});
