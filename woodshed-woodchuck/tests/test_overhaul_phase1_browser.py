"""Phase 1 on real Chrome and Firefox; synthetic disposable accounts only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright
from test_r4_browser import SERVER

LAYOUTS = [(320,568),(390,844),(430,932),(768,1024),(1024,768),(844,390),(1440,900),(740,320)]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def local_site(tmp_path_factory):
    temp = tmp_path_factory.mktemp('overhaul-browser')
    # Seed the already-approved calendar only in this disposable test database.
    source = SERVER.replace('    session.commit()', '    from app.seasons import bootstrap_canonical_seasons\n    bootstrap_canonical_seasons(session)\n    session.commit()')
    (temp/'local_app.py').write_text(source)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    env={'PATH':os.environ['PATH'], 'LANG':'C.UTF-8', 'PYTHONPATH':str(ROOT),
         'DATABASE_URL':'sqlite:///'+str(temp/'local.db'), 'SESSION_SECRET':'phase1-disposable-browser-secret-long',
         'SESSION_COOKIE_SECURE':'false','LOGIN_RATE_LIMIT_MODE':'off','LOGIN_RATE_LIMIT_REQUIRED':'false',
         'PYTHONDONTWRITEBYTECODE':'1'}
    with (temp/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','uvicorn','local_app:app','--app-dir',str(temp),
            '--host','127.0.0.1','--port',str(port),'--no-proxy-headers'],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(600):
                try:
                    with urlopen(origin+'/guest',timeout=2) as response: assert response.status==200
                    break
                except URLError:
                    assert server.poll() is None,(temp/'server.log').read_text()
                    time.sleep(.1)
            else: pytest.fail('Local test server did not start')
            yield origin
        finally:
            server.terminate(); server.wait(timeout=15)


@pytest.mark.parametrize('browser_name', ['chrome','firefox'])
def test_phase1_visual_and_interaction_contract(local_site, tmp_path, browser_name):
    evidence=Path(os.getenv('WW_OVERHAUL_EVIDENCE_DIR', str(tmp_path))) / browser_name
    evidence.mkdir(parents=True, exist_ok=True)
    errors=[]; results=[]
    with sync_playwright() as playwright:
        browser=(playwright.chromium.launch(executable_path='/opt/google/chrome/chrome',headless=True)
                 if browser_name=='chrome' else playwright.firefox.launch(headless=True))
        context=browser.new_context(reduced_motion='reduce',base_url=local_site,has_touch=True)
        page=context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        def goto(path):
            page.goto(local_site+path)
            page.wait_for_function('window.WWState && window.WWWorldEntry && !document.querySelector(".app-shell").hidden')
            if path == '/home':
                page.evaluate('async()=>{await Promise.all([WWSessionBoundary.ready, WWWorldEntry.arrivalReady])}')
                page.wait_for_timeout(150)
                if page.evaluate('WWSurfaces.current()?.id==="xp-panel"'):
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        def open_panel(selector, panel):
            page.locator(selector).evaluate('(node)=>{node.focus();node.click()}')
            page.wait_for_function('(id)=>window.WWSurfaces.current()?.id===id',arg=panel)
            assert page.locator('.artwork-scene').evaluate('(node)=>node.inert')
            assert page.locator(panel_selector(panel)).evaluate('(node)=>node.querySelectorAll("form form").length')==0
        def close_panel(selector):
            page.keyboard.press('Escape')
            page.wait_for_function('!window.WWSurfaces.current()')
            assert page.locator(selector).evaluate('(node)=>document.activeElement===node')
        def panel_selector(panel): return '#'+panel
        goto('/login')
        response=context.request.post(local_site+'/account/login',form={'woodchuck_id':'WC-GUEST-A','pin':'2468'},headers={'Origin':local_site})
        assert response.status==200
        for width,height in LAYOUTS:
            page.set_viewport_size({'width':width,'height':height})
            for path in ['/home','/store','/p-book','/quest']:
                goto(path)
                assert page.locator('.main-nav').count()==0
                assert page.locator('a[href="/family/practice"], a[href="/family/parent-access"]').count()==0
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(path,width,height)
                assert page.evaluate('(()=>{const ids=[...document.querySelectorAll("[id]")].map(n=>n.id);return ids.length===new Set(ids).size})()'),path
                if path in ['/home','/store']:
                    page.wait_for_function('document.querySelector("[data-student-woodchuck]")?.hasAttribute("data-appearance-ready")')
                    proof=page.locator('.artwork-scene').evaluate('''scene=>{
                      const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}};
                      const art=scene.querySelector('.room-scene-art'),grid=scene.querySelector('.scene-hotspots');
                      return {scene:rect(scene),art:rect(art),grid:rect(grid),ratio:art.naturalWidth/art.naturalHeight,
                        cells:[...grid.children].map(n=>({cell:n.dataset.sceneCell,rect:rect(n),href:n.getAttribute('href'),id:n.id,key:n.dataset.shopPanel}))};}''')
                    r=proof['scene']
                    assert abs(r['w']/r['h']-proof['ratio'])<.003
                    assert abs(r['h']-min(height,width/proof['ratio']))<1
                    assert r['x']>=0 and r['y']>=0 and r['y']+r['h']<=height+1
                    for key in ['x','y','w','h']:
                        assert abs(r[key]-proof['grid'][key])<1 and abs(r[key]-proof['art'][key])<1
                    assert len(proof['cells'])==10
                    for i,cell in enumerate(proof['cells']):
                        assert cell['cell']==('R' if i%2 else 'L')+str(i//2+1)
                        assert cell['rect']['w']>=44 and cell['rect']['h']>=44
                        assert abs(cell['rect']['w']-r['w']/2)<1 and abs(cell['rect']['h']-r['h']/5)<1
                    page.locator('[data-scene-cell="L1"]').focus();page.keyboard.press('Tab')
                    assert page.locator('[data-scene-cell="R1"]').evaluate('(n)=>document.activeElement===n'),page.evaluate('({active:document.activeElement.outerHTML,current:WWSurfaces.current()?.id,inert:document.querySelector(".artwork-scene").inert})')
                    assert page.locator(':focus-visible').evaluate('(n)=>getComputedStyle(n).outlineStyle')=='solid'
                    results.append({'path':path,'viewport':[width,height],'geometry':proof})
                elif path=='/p-book':
                    assert page.locator('.room-return').get_attribute('href')=='/home'
                    assert page.evaluate('(()=>{const controls=[...document.querySelectorAll("#p-book-form input, #p-book-form select, #p-book-form textarea")];return controls[0].id==="p-book-minutes" && document.querySelector("#practice-timer-toggle-btn").compareDocumentPosition(controls[0]) & Node.DOCUMENT_POSITION_FOLLOWING})()')
                else:
                    assert page.locator('.room-return').get_attribute('href')=='/store'
                    assert page.locator('.board-definition-card').evaluate('(n)=>n.nextElementSibling.classList.contains("bonus-challenge-section")')
                if path=='/p-book':
                    assert page.locator('#practice-timer-display').evaluate('(n)=>n.getBoundingClientRect().right<=n.closest("article").getBoundingClientRect().right')
                page.evaluate('document.activeElement?.blur()')
                page.screenshot(path=str(evidence/f'{path[1:]}-{width}x{height}.png'),full_page=True)
            if width in [390,768,1440,844,740]:
                goto('/home')
                for selector,panel in [('#shed-team-button','shed-team-panel'),('#instrument-object','your-woodchuck')]:
                    open_panel(selector,panel)
                    rect=page.locator('#'+panel).bounding_box()
                    assert rect['y']>=0 and rect['y']+rect['height']<=height+1
                    assert page.locator('#'+panel).evaluate('(n)=>n.scrollWidth<=n.clientWidth')
                    page.screenshot(path=str(evidence/f'{panel}-{width}x{height}.png'))
                    close_panel(selector)
                goto('/store');open_panel('[data-shop-panel="contact"]','shop-feature-dialog')
                assert page.locator('[data-shop-panel-content="contact"] a[href="/account/privacy"]').count()==1
                assert page.locator('[data-shop-panel-content="contact"] #authenticated-logout').count()==1
                page.screenshot(path=str(evidence/f'contact-{width}x{height}.png'))
                close_panel('[data-shop-panel="contact"]')
        # Each form retains its own dirty state, even after the sibling saves.
        goto('/home');open_panel('#instrument-object','your-woodchuck')
        page.locator('#change-level-select').select_option('Intermediate')
        page.locator('#woodchuck-hoodie').select_option('red')
        page.locator('#woodchuck-editor-form button[type="submit"]').click()
        page.wait_for_function('document.querySelector("#woodchuck-editor-status").textContent.includes("saved")')
        dialogs=[]
        acceptance=[False]
        def answer(dialog):
            dialogs.append(dialog.message)
            dialog.accept() if acceptance[0] else dialog.dismiss()
        page.on('dialog',answer)
        page.keyboard.press('Escape');assert dialogs==['Discard unsaved changes?']
        assert page.locator('#your-woodchuck').evaluate('(n)=>n.open')
        acceptance[0]=True
        page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        open_panel('#shed-team-button','shed-team-panel')
        page.locator('#change-name-input').fill('Unsubmitted name')
        acceptance[0]=False
        page.evaluate('history.back()');page.wait_for_timeout(200)
        assert page.locator('#shed-team-panel').is_visible()
        assert page.locator('#change-name-input').input_value()=='Unsubmitted name'
        acceptance[0]=True
        page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        goto('/p-book')
        goto('/home#shed-team-panel')
        page.wait_for_function('WWSurfaces.current()?.id==="shed-team-panel"')
        assert page.locator('#change-name-input').input_value()=='Synthetic A'
        page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        # Alpha-tested pointer reactions, transparent-space pass-through, keyboard,
        # reduced-motion feedback and no server mutation on either room.
        for path in ['/home','/store']:
            goto(path);page.wait_for_function('document.querySelector("[data-student-woodchuck]").hasAttribute("data-appearance-ready")')
            page.wait_for_timeout(800)
            points=page.locator('[data-student-woodchuck]').evaluate('''img=>{
                const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;
                const ctx=c.getContext('2d');ctx.drawImage(img,0,0);const data=ctx.getImageData(0,0,c.width,c.height).data;
                const r=img.getBoundingClientRect(),scale=Math.min(r.width/c.width,r.height/c.height);
                const x0=r.x+(r.width-c.width*scale)/2,y0=r.y+(r.height-c.height*scale)/2;
                const points={};
                for(let y=Math.floor(c.height*.35);y<c.height*.7;y+=4)for(let x=4;x<c.width-4;x+=4){
                    const a=data[(y*c.width+x)*4+3];
                    if(a>240 && x>c.width*.35 && x<c.width*.65 && y>c.height*.45 && !points.opaque)points.opaque={x:x0+x*scale,y:y0+y*scale};
                    if(a===0&&!points.transparent)points.transparent={x:x0+x*scale,y:y0+y*scale};}
                return points;}''')
            before=context.request.get(local_site+'/test/snapshot').json()
            page.evaluate('window.clicks=0;document.querySelector(".scene-hotspots").addEventListener("click",()=>window.clicks++)')
            page.mouse.click(**points['opaque'])
            reaction=page.evaluate('(()=>{const art=document.querySelector("[data-student-woodchuck]");return {active:art.classList.contains("is-tap-wiggle"),filter:getComputedStyle(art).filter,clicks:window.clicks,panel:WWSurfaces.current()?.id||null}})()')
            assert reaction['active'] and 'brightness' in reaction['filter'],reaction
            assert reaction['clicks']==0 and reaction['panel'] is None
            page.locator('.character-reaction-target').focus();page.keyboard.press('Enter')
            assert page.evaluate('clicks')==0
            page.touchscreen.tap(**points['opaque'])
            assert page.evaluate('clicks')==0 and page.evaluate('!WWSurfaces.current()')
            after=context.request.get(local_site+'/test/snapshot').json();assert before==after
            page.mouse.click(**points['transparent']);assert page.evaluate('clicks')==1
            if page.evaluate('!!WWSurfaces.current()'):
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        # Nested destinations preserve shared return navigation; welcome retains Parent Access.
        for path in ['/practice/skill-building','/practice/pristine','/account/privacy','/membership?as_account=student','/plunge-burrow']:
            page.goto(local_site+path)
            returns=page.locator('a[href="/home"],a[href="/store"]')
            assert returns.count()>=1
            returns.first.wait_for(state='visible')
        goto('/');assert page.locator('a[href="/family/parent-access"]').count()==1
        # Switch real sessions and navigate through browser history. Old identity is absent.
        context.request.post(local_site+'/account/logout',headers={'Origin':local_site})
        context.request.post(local_site+'/account/login',form={'woodchuck_id':'WC-GUEST-B','pin':'2468'},headers={'Origin':local_site})
        goto('/home');assert page.locator('#woodchuck-name-value').inner_text()=='Synthetic B'
        assert page.evaluate('WWState.getState().account.woodchuckId')=='WC-GUEST-B'
        assert 'Synthetic A' not in page.locator('body').inner_text()
        assert errors==[],errors
        (evidence/'results.json').write_text(json.dumps({'browser':browser.version,'layouts':LAYOUTS,'results':results,'page_errors':errors},indent=2))
        context.close();browser.close()


@pytest.mark.parametrize('browser_name', ['chrome','firefox'])
def test_normal_motion_and_profile_form_saves(local_site, tmp_path, browser_name):
    evidence=Path(os.getenv('WW_OVERHAUL_EVIDENCE_DIR', str(tmp_path)))/browser_name
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else p.firefox.launch()
        context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='no-preference',has_touch=True)
        account='WC-GUEST-A' if browser_name=='chrome' else 'WC-GUEST-B'
        assert context.request.post(local_site+'/account/login',form={'woodchuck_id':account,'pin':'2468'},headers={'Origin':local_site}).status==200
        page=context.new_page()
        def room(path):
            page.goto(local_site+path)
            page.wait_for_function('window.WWWorldEntry && document.querySelector(".ww-motion-ready") && document.querySelector("[data-student-woodchuck]").hasAttribute("data-appearance-ready")')
            page.wait_for_timeout(200)
            if page.evaluate('WWSurfaces.current()?.id==="xp-panel"'):
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        for path in ['/home','/store']:
            room(path)
            target=page.locator('.character-reaction-target')
            target.focus();page.keyboard.press('Space')
            assert page.locator('[data-student-woodchuck]').evaluate('(n)=>getComputedStyle(n).animationName')=='ww-woodchuck-tap-wiggle'
            assert page.evaluate('!WWSurfaces.current()')
            page.screenshot(path=str(evidence/(path[1:]+'-normal-wiggle.png')))
            page.wait_for_function('!document.querySelector("[data-student-woodchuck]").classList.contains("is-tap-wiggle")')
            assert page.locator('[data-student-woodchuck]').evaluate('(n)=>getComputedStyle(n).animationName')=='ww-woodchuck-idle'
        room('/home')
        page.locator('#shed-team-button').evaluate('(n)=>n.click()')
        page.wait_for_function('WWSurfaces.current()?.id==="shed-team-panel"')
        page.locator('#change-name-input').fill('Phase 1 '+browser_name)
        page.locator('#change-name-form button[type="submit"]').click()
        page.wait_for_function('document.querySelector("#change-name-feedback").textContent.includes("successfully")')
        assert page.locator('#woodchuck-name-value').inner_text()=='Phase 1 '+browser_name
        page.locator('#change-name-input').fill('Second name')
        page.locator('#change-name-form button[type="submit"]').click()
        page.wait_for_function('document.querySelector("#change-name-feedback").classList.contains("error-text")')
        assert page.locator('#woodchuck-name-value').inner_text()=='Phase 1 '+browser_name
        page.on('dialog',lambda dialog:dialog.accept());page.keyboard.press('Escape')
        page.wait_for_function('!WWSurfaces.current()')
        page.locator('#instrument-object').evaluate('(n)=>n.click()')
        page.wait_for_function('WWSurfaces.current()?.id==="your-woodchuck"')
        page.locator('#change-level-select').select_option('Intermediate')
        page.locator('#change-level-form button[type="submit"]').click()
        page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("successfully")')
        assert page.locator('#level-value').inner_text()=='Intermediate'
        page.locator('#change-level-select').select_option('Advanced')
        page.locator('#change-level-form button[type="submit"]').click()
        page.wait_for_function('document.querySelector("#change-level-feedback").classList.contains("error-text")')
        assert page.locator('#level-value').inner_text()=='Intermediate'
        page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        assert page.locator('#instrument-object').evaluate('(n)=>document.activeElement===n')
        context.close();browser.close()


def test_chrome_safe_area_overrides(local_site, tmp_path):
    """Actual env() insets via the installed Chrome protocol, including a side notch."""
    evidence=Path(os.getenv('WW_OVERHAUL_EVIDENCE_DIR', str(tmp_path)))/'safe-area'
    evidence.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/opt/google/chrome/chrome')
        context=browser.new_context(reduced_motion='reduce')
        assert context.request.post(local_site+'/account/login',form={'woodchuck_id':'WC-GUEST-A','pin':'2468'},headers={'Origin':local_site}).status==200
        page=context.new_page();cdp=context.new_cdp_session(page)
        for width,height,insets in [(390,844,{'top':44,'bottom':34,'left':0,'right':0}),
                                    (844,390,{'top':0,'bottom':21,'left':44,'right':0})]:
            page.set_viewport_size({'width':width,'height':height})
            cdp.send('Emulation.setSafeAreaInsetsOverride',{'insets':insets})
            def safe(selector):
                r=page.locator(selector).bounding_box()
                assert r['x']>=insets['left']-1 and r['y']>=insets['top']-1,(selector,r,insets)
                assert r['x']+r['width']<=width-insets['right']+1 and r['y']+r['height']<=height-insets['bottom']+1,(selector,r,insets)
            for path in ['/home','/store','/p-book','/quest']:
                page.goto(local_site+path)
                page.wait_for_function('window.WWWorldEntry && !document.querySelector(".app-shell").hidden')
                page.wait_for_timeout(200)
                if page.evaluate('WWSurfaces.current()?.id==="xp-panel"'):
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                safe('.artwork-scene' if path in ['/home','/store'] else '.room-return')
                if path in ['/p-book','/quest']:
                    safe('#sound-effects-button')
                page.screenshot(path=str(evidence/f'{path[1:]}-{width}.png'))
                pairs=([('#shed-team-button','shed-team-panel'),('#instrument-object','your-woodchuck')]
                       if path=='/home' else [('[data-shop-panel="contact"]','shop-feature-dialog')] if path=='/store' else [])
                for control,panel in pairs:
                    page.locator(control).evaluate('(n)=>n.click()')
                    page.wait_for_function('(id)=>WWSurfaces.current()?.id===id',arg=panel)
                    safe('#'+panel)
                    page.screenshot(path=str(evidence/f'{panel}-{width}.png'))
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
        context.close();browser.close()
