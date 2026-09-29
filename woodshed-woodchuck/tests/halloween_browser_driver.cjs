// Only test_halloween_browser.py's local disposable app is supported.
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
  let seq = 0, buffer = '', checks = 0;
  const screenshots = [];
  let activePage;
  const timer = setTimeout(() => { chrome.kill(); process.exit(2); }, 150000);
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
    await send('Network.enable', {}, sessionId);
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true }, sessionId);
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const until = async expression => {
      for (let n = 0; n < 200; n++) {
        if (await evaluate(expression)) return;
        await new Promise(resolve => setTimeout(resolve, 40));
      }
      throw new Error('Timed out: ' + expression);
    };
    const navigate = async path => {
      await send('Page.navigate', { url: config.origin + path }, sessionId);
      await until(`location.pathname === ${JSON.stringify(path)} && document.readyState === 'complete' && window.WWSessionBoundary?.isCurrent()`);
      await evaluate('window.WWSessionBoundary.ready');
    };
    const check = async (expression, label) => { assert.equal(await evaluate(expression), true, label); checks++; };
    const click = async selector => {
      const point = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.scrollIntoView({block:'center'}); const r = el.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 }, sessionId);
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 }, sessionId);
    };
    const key = async (key, code, modifiers = 0) => {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code: key, windowsVirtualKeyCode: code, modifiers }, sessionId);
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code: key, windowsVirtualKeyCode: code, modifiers }, sessionId);
    };
    const shot = async name => {
      const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
      fs.writeFileSync(config.output + '/' + name + '.png', Buffer.from(data, 'base64'));
      screenshots.push(name + '.png');
    };
    return { sessionId, evaluate, until, navigate, check, click, key, shot };
  };
  try {
    const page = await tab();
    activePage = page;
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] }, page.sessionId);
    await page.navigate('/guest/login');
    await page.evaluate(`fetch('/account/login', {method:'POST', body:new URLSearchParams({woodchuck_id:'WC-GUEST-A',pin:'2468'})}).then(r => {if(!r.ok) throw Error('Login failed');})`);
    let fallbackHTML;
    for (const width of [1440, 390, 320]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height: width === 1440 ? 1000 : 844, deviceScaleFactor: 1, mobile: false }, page.sessionId);
      await page.navigate('/quest');
      await page.until(`document.querySelector('[data-season-art-open]')?.getAttribute('aria-haspopup') === 'dialog'`);
      await page.evaluate(`(() => {const image=new Image();image.src=document.querySelector('.season-polaroid-image').data;return image.decode();})()`);
      await page.check(`document.querySelector('.season-polaroid-image .season-image-fallback').getBoundingClientRect().height === 0`, 'Loaded thumbnail hides fallback');
      await page.check(`document.documentElement.scrollWidth <= innerWidth`, 'BOARD overflow at ' + width);
      await page.check(`!document.querySelector('.back-to-school-streamers')`, 'No school streamers');
      await page.check(`document.querySelector('.halloween-decor--board').getAttribute('aria-hidden') === 'true'`, 'Decor hidden from assistive technology');
      await page.check(`[...document.querySelectorAll('.halloween-decor, .halloween-decor *')].every(el => getComputedStyle(el).pointerEvents === 'none' && getComputedStyle(el).animationName === 'none')`, 'Static click-through decoration');
      await page.check(`(() => {const s=getComputedStyle(document.querySelector('.season-polaroid-image'));return Math.abs(parseFloat(s.width)/parseFloat(s.height)-4/3)<.01 && s.objectFit==='contain';})()`, 'Complete 4:3 image');
      if (width !== 320) await page.shot('board-' + width);
      await page.evaluate(`document.querySelector('[data-season-art-open]').focus()`);
      await page.key('Enter', 13);
      await page.until(`document.getElementById('season-art-dialog').open`);
      await page.check(`document.activeElement.matches('#season-art-dialog button')`, 'Close button receives focus');
      await page.check(`location.pathname === '/quest'`, 'No page navigation');
      const ax = await send('Accessibility.getFullAXTree', {}, page.sessionId);
      assert(ax.nodes.some(n => n.role?.value === 'dialog' && n.name?.value === 'Halloween at the Woodshed')); checks++;
      await page.key('Tab', 9);
      await page.check(`document.getElementById('season-art-dialog').contains(document.activeElement) || document.activeElement === document.body`, 'Native modal focus containment');
      await page.key('Tab', 9, 8);
      await page.check(`document.getElementById('season-art-dialog').contains(document.activeElement)`, 'Reverse Tab stays in dialog');
      await page.evaluate(`(() => {const image=new Image();image.src=document.querySelector('.season-master-image').data;return image.decode();})()`);
      if (width !== 320) await page.shot('lightbox-' + width);
      await page.key('Escape', 27);
      await page.until(`!document.getElementById('season-art-dialog').open`);
      await page.check(`document.activeElement.matches('[data-season-art-open]')`, 'Escape returns focus');
      await page.click('[data-season-art-open]');
      await page.until(`document.getElementById('season-art-dialog').open`);
      await page.click('#season-art-dialog button');
      await page.until(`!document.getElementById('season-art-dialog').open`);
      await page.check(`document.activeElement.matches('[data-season-art-open]')`, 'Close returns focus');
      fallbackHTML = await page.evaluate(`document.querySelector('.season-polaroid').outerHTML + document.querySelector('.season-art-dialog').outerHTML`);

      await page.navigate('/home');
      await page.until(`document.querySelector('[data-student-woodchuck]')?.hasAttribute('data-appearance-ready')`);
      await page.until(`!!document.querySelector('.shed-decoration')`);
      // Dismiss the existing daily XP presentation if this is the first visit.
      await page.evaluate(`if (!document.getElementById('xp-panel').hidden) document.getElementById('xp-panel-close').click()`);
      await page.evaluate(`document.activeElement?.blur()`);
      await page.check(`document.documentElement.scrollWidth <= innerWidth`, 'SHED overflow at ' + width);
      await page.check(`document.querySelector('.halloween-decor--shed').getAttribute('aria-hidden') === 'true'`, 'SHED decorative semantics');
      await page.check(`(() => {const a=document.querySelector('.artwork-scene').getBoundingClientRect(),b=document.querySelector('.halloween-decor--shed').getBoundingClientRect();return ['x','y','width','height'].every(k=>Math.abs(a[k]-b[k])<1);})()`, 'Overlay shares artwork rectangle');
      await page.check(`[...document.querySelectorAll('#woodshed-hotspots button')].every(el => {const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;})`, 'All ten room controls receive clicks');
      if (width !== 320) await page.shot('shed-' + width);
      await page.click('#shed-secret-button');
      await page.until(`!document.getElementById('shed-secret-panel').hidden`);
      await page.check(`document.getElementById('shed-secret-panel').getBoundingClientRect().height > 0`, 'Secret Symbol opens');
      await page.key('Escape', 27);
      await page.until(`document.getElementById('shed-secret-panel').hidden`);
      await page.check(`document.activeElement.id === 'shed-secret-button'`, 'Secret focus returns');
      await page.click('#instrument-object');
      await page.until(`!document.getElementById('your-woodchuck').hidden`);
      await page.key('Escape', 27);
      await page.click('#shed-decorate-button');
      await page.until(`document.querySelector('.artwork-scene').classList.contains('is-decorating')`);
      await page.until(`!!document.querySelector('.shed-decoration')`);
      await page.click('#shed-decoration-view-room');
      await page.check(`(() => {const el=document.querySelector('.shed-decoration');const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})()`, 'Owned decoration remains reachable for dragging');
      await page.click('#shed-decorate-close');
    }
    // The existing Guest tools remain functional and contain no account overlay.
    const guest = await tab();
    await send('Network.clearBrowserCookies', {}, guest.sessionId);
    await guest.navigate('/guest');
    await guest.evaluate(`document.getElementById('guest-instrument').value='Flute';document.getElementById('guest-level').value='Beginner';document.getElementById('guest-goal').selectedIndex=1;document.getElementById('guest-setup-form').requestSubmit()`);
    await guest.until(`!document.getElementById('guest-tools').hidden`);
    await guest.click('#shed-secret-button');
    await guest.until(`!document.getElementById('shed-secret-panel').hidden`);
    await guest.check(`!document.querySelector('.halloween-decor')`, 'Separate Guest template preserved');
    await guest.key('Escape', 27);
    await guest.click('#metronome-open-button');
    await guest.until(`!document.getElementById('metronome-panel').hidden`);
    checks++;

    // Component-level progressive enhancement, independent of account boot scripts.
    const fallback = await tab();
    activePage = fallback;
    const html = '<html><head><link rel="stylesheet" href="/static/css/styles.css"><link rel="stylesheet" href="/static/css/seasonal-presentation.css"></head><body>' + fallbackHTML + '</body></html>';
    const fallbackDocument = async () => {
      await send('Page.navigate', { url: config.origin + '/static/css/seasonal-presentation.css' }, fallback.sessionId);
      await fallback.until(`location.pathname === '/static/css/seasonal-presentation.css' && document.readyState === 'complete'`);
      const { frameTree } = await send('Page.getFrameTree', {}, fallback.sessionId);
      await send('Page.setDocumentContent', { frameId: frameTree.frame.id, html }, fallback.sessionId);
      await fallback.until(`!!document.querySelector('[data-season-art-open]')`);
    };
    await send('Emulation.setScriptExecutionDisabled', { value: true }, fallback.sessionId);
    await fallbackDocument();
    await fallback.click('[data-season-art-open]');
    await fallback.until(`location.pathname === '/static/img/seasonal/harvest/master_2x1.png' && document.readyState === 'complete' && !document.querySelector('[data-season-art-open]')`);
    checks++;
    await send('Emulation.setScriptExecutionDisabled', { value: false }, fallback.sessionId);
    await fallbackDocument();
    await fallback.evaluate(`HTMLDialogElement.prototype.showModal = undefined`);
    await fallback.evaluate(await fetch(config.origin + '/static/js/visible-art.js').then(r => r.text()));
    await fallback.click('[data-season-art-open]');
    await fallback.until(`location.pathname === '/static/img/seasonal/harvest/master_2x1.png' && document.readyState === 'complete' && !document.querySelector('[data-season-art-open]')`);
    checks++;
    await send('Network.setBlockedURLs', { urls: ['*board_polaroid_1200x900.jpg', '*master_2x1.png'] }, fallback.sessionId);
    await send('Network.setCacheDisabled', { cacheDisabled: true }, fallback.sessionId);
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: false }, fallback.sessionId);
    await fallbackDocument();
    await fallback.until(`document.querySelector('.season-polaroid-image .season-image-fallback').getBoundingClientRect().height > 0`);
    await fallback.check(`!document.querySelector('.season-polaroid-image').contentDocument`, 'Failed thumbnail uses native text fallback');
    await fallback.evaluate(await fetch(config.origin + '/static/js/visible-art.js').then(r => r.text()));
    await fallback.click('[data-season-art-open]');
    await fallback.until(`document.getElementById('season-art-dialog').open`);
    await fallback.check(`document.querySelector('.season-master-image .season-image-fallback').getBoundingClientRect().height > 0`, 'Failed master uses text fallback');
    await fallback.check(`document.documentElement.scrollWidth <= innerWidth`, 'Image fallback has no mobile overflow');
    await fallback.shot('image-fallback');

    activePage = page;
    await fetch(config.origin + '/test/season/school', { method: 'POST' });
    await page.navigate('/guest/login');
    await page.evaluate(`fetch('/account/login', {method:'POST', body:new URLSearchParams({woodchuck_id:'WC-GUEST-A',pin:'2468'})})`);
    for (const path of ['/quest', '/home']) {
      await page.navigate(path);
      await page.check(`!document.querySelector('.halloween-decor, .season-polaroid, #season-art-dialog')`, 'No Halloween presentation on ' + path);
    }
    console.log(JSON.stringify({ widths: [1440, 390, 320], checks, screenshots }));
  } catch (error) {
    if (activePage) await activePage.shot('failure');
    console.error(error.stack || JSON.stringify(error));
    process.exitCode = 1;
  } finally {
    clearTimeout(timer);
    chrome.kill();
  }
});
