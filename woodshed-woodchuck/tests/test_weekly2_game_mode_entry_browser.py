"""Focused ordinary-browser entry flows; test-owned migrated fixtures only."""
import json
from pathlib import Path
import sqlite3
import time

import pytest
from playwright.sync_api import sync_playwright
from test_overhaul_profile_saves_browser import save_site
from test_weekly2_game_mode_browser import snapshot, account, open_editor


def authenticated(page):
    page.wait_for_function('window.WWSessionBoundary && !document.querySelector(".app-shell").hidden')
    page.evaluate('WWSessionBoundary.ready')


def login(page, origin, who='A'):
    page.goto(origin+'/login')
    authenticated(page) if page.locator('body').get_attribute('data-authenticated') == 'true' else None
    page.locator('#login-woodchuck-id').fill('WC-GUEST-'+who)
    page.locator('#login-pin').fill('2468')
    page.locator('#account-login-form button[type=submit]').click()
    page.wait_for_url(origin+'/home')
    authenticated(page)


def chooser(page):
    # Natural first-entry XP presentation must finish before the mode dialog.
    page.wait_for_function('document.querySelector("#xp-panel")?.hidden === false || document.querySelector("#game-mode-entry")?.open')
    if page.locator('#xp-panel').is_visible():
        assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
        page.locator('#xp-panel-close').click()
    page.wait_for_function('document.querySelector("#game-mode-entry").open')
    page.wait_for_function('WWSurfaces.current()?.id === "game-mode-entry"')
    assert page.locator('#game-mode-entry-select').input_value() == ''
    assert page.locator('#game-mode-entry-current').inner_text() == 'Not selected'
    assert page.locator('#game-mode-entry-select').evaluate('(n)=>n===document.activeElement')


def saved(page):
    page.wait_for_function('document.querySelector("#game-mode-entry-feedback").textContent.includes("saved") && document.querySelector("#game-mode-entry-form").getAttribute("aria-busy") !== "true"')
    assert 'could not' not in page.locator('#game-mode-entry-feedback').inner_text()


def choose(page, mode):
    page.locator('#game-mode-entry-select').select_option(mode)
    page.locator('#game-mode-entry-select').focus()
    page.keyboard.press('Tab')
    assert page.locator('#game-mode-entry-form button[type=submit]').evaluate('(n)=>n===document.activeElement && n.matches(":focus-visible")')
    page.keyboard.press('Enter')
    saved(page)


def close_chooser(page):
    page.locator('#game-mode-entry [data-surface-close]').click()
    page.wait_for_function('!document.querySelector("#game-mode-entry").open && !WWSurfaces.current()')


def count(database):
    with sqlite3.connect(database) as c:
        return c.execute('SELECT count(*) FROM woodchuck_profiles').fetchone()[0]


def stored_mode(database, wid):
    with sqlite3.connect(database) as c:
        return c.execute('SELECT game_mode FROM woodchuck_profiles WHERE woodchuck_id=?', (wid,)).fetchone()[0]


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
@pytest.mark.parametrize('scenario', ['returning', 'signup', 'guest', 'hydration', 'delayed'])
def test_explicit_mode_entry(save_site, tmp_path, browser_name, scenario):
    origin, database = save_site
    result = {'browser': browser_name, 'scenario': scenario, 'checks': [], 'requests': [], 'page_errors': []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name == 'chrome' else p.firefox.launch()
            context = browser.new_context(base_url=origin, viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
            page = context.new_page();page.set_default_timeout(15000)
            page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())  # Normal unsaved-form confirmation only.
            def request(r):
                if r.url.startswith(origin) and '/account/' in r.url:
                    headers = r.all_headers()
                    result['requests'].append({'path': r.url.removeprefix(origin), 'method': r.method,
                        'origin': headers.get('origin'), 'account': headers.get('x-woodshed-account')})
            context.on('request', request)

            if scenario == 'returning':
                login(page, origin);before = snapshot(database)
                chooser(page)
                assert snapshot(database) == before
                assert not any(r['path']=='/account/profile/game-mode' for r in result['requests'])
                other = context.new_page()
                other.goto(origin+'/home');authenticated(other)
                other.evaluate('WWGameModeEntry.start()')
                other.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert not other.locator('#game-mode-entry').evaluate('(d)=>d.open')
                assert snapshot(database) == before
                other.close();page.bring_to_front()
                page.keyboard.press('Escape')
                page.wait_for_function('!document.querySelector("#game-mode-entry").open')
                assert account(database)['mode'] is None
                page.reload();authenticated(page)
                page.wait_for_function('window.WWGameModeEntry')
                # Await the real hydration requests, not a fixed delay.
                page.evaluate('WWGameModeEntry.start()')
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
                assert snapshot(database) == before
                assert page.evaluate('fetch("/account/logout",{method:"POST"}).then(r=>r.ok)')
                login(page, origin);chooser(page)
                choose(page, 'Beginner');close_chooser(page)
                for mode in ['Challenge', 'Performance']:
                    open_editor(page)
                    page.locator('#change-level-select').select_option(mode)
                    page.locator('#change-level-form button[type=submit]').click()
                    page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("successfully")')
                    assert account(database)['mode'] == mode
                    page.keyboard.press('Escape')
                    page.wait_for_function('!WWSurfaces.current()')
                assert page.evaluate('fetch("/account/logout",{method:"POST"}).then(r=>r.ok)')
                login(page, origin)
                page.evaluate('WWGameModeEntry.start()')
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
                assert account(database)['mode'] == 'Performance' and account(database)['level'] == 'Beginner'
                assert page.locator('#xp-level-control').is_visible()
                assert page.locator('a[data-scene-cell=L1]').get_attribute('href') == '/store'
                assert page.locator('a[data-scene-cell=L2]').get_attribute('href') == '/p-book'
                result['checks'].append('Natural XP/modal sequencing; no write on open/dismiss; same-login suppression; later-login retry; all modes and profile changes persist; XP/navigation retained')

            elif scenario == 'signup':
                page.goto(origin+'/prebeta/C002')
                page.goto(origin+'/setup?age=adult')
                assert page.locator('#setup-game-mode').input_value() == ''
                page.locator('#woodchuck-name').fill('Synthetic new student')
                page.locator('#instrument').select_option('Flute')
                page.locator('#goal').select_option(index=1)
                page.locator('#student-pin').fill('2468')
                page.locator('#account-create-form button[type=submit]').click()
                assert page.locator('#setup-error').inner_text() == 'Please choose a Game Mode.'
                assert not any(r['path']=='/account/create' for r in result['requests'])
                page.locator('#setup-game-mode').select_option('Challenge')
                page.set_viewport_size({'width':390,'height':844})
                attempts = []
                def interrupt(route):
                    attempts.append(1)
                    if len(attempts)==1:route.fulfill(status=503, content_type='application/json', body='{"detail":"Synthetic mode-save failure"}')
                    else:
                        response = route.fetch()
                        assert response.status == 200
                        route.abort('failed')  # Actual commit; lose only its response.
                page.route('**/account/profile/game-mode', interrupt)
                n = count(database)
                page.locator('#account-create-form button[type=submit]').click()
                page.wait_for_function('!document.querySelector("#account-created-panel").hidden && document.querySelector("#game-mode-entry").open')
                page.wait_for_function('document.querySelector("#game-mode-entry-feedback").textContent.includes("Synthetic mode-save failure") && document.querySelector("#game-mode-entry-form").getAttribute("aria-busy") !== "true"')
                wid = page.locator('#created-woodchuck-id').inner_text()
                assert count(database) == n+1 and stored_mode(database, wid) is None
                assert page.locator('#account-create-form').is_hidden()
                assert 'saved successfully' not in page.locator('#game-mode-entry-feedback').inner_text()
                page.locator('#game-mode-entry-form button[type=submit]').click();saved(page)
                assert 'recovered' in page.locator('#game-mode-entry-feedback').inner_text()
                assert stored_mode(database, wid) == 'Challenge' and count(database) == n+1
                assert len([r for r in result['requests'] if r['path']=='/account/create' and r['method']=='POST']) == 1
                with sqlite3.connect(database) as c:
                    assert c.execute('SELECT cohort_key,source FROM tester_enrollments e JOIN woodchuck_profiles p ON p.id=e.profile_id WHERE p.woodchuck_id=?',(wid,)).fetchall()==[('C002','DIRECTOR1')]
                    assert c.execute('SELECT level FROM woodchuck_profiles WHERE woodchuck_id=?',(wid,)).fetchone()==('Beginner',)
                page.unroute('**/account/profile/game-mode', interrupt);close_chooser(page)
                page.locator('a[href="/home"]').first.click();page.wait_for_url(origin+'/home');authenticated(page)
                page.evaluate('WWGameModeEntry.start()')
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
                assert page.evaluate('WWState.getState().profile.game_mode') == 'Challenge'
                result['checks'].append('C002 signup explicit selection; 503 leaves one recoverable account; lost committed response recovers authoritatively; no duplicate creation; attribution/history kept')

            elif scenario == 'guest':
                before = snapshot(database)
                page.goto(origin+'/guest')
                page.wait_for_function('window.WWGuest && !document.querySelector("#guest-setup-fields").disabled')
                assert page.locator('#guest-game_mode').input_value() == ''
                page.locator('#guest-instrument').select_option('Flute')
                page.locator('#guest-game_mode').select_option('Performance')
                page.locator('#guest-goal').select_option(index=1)
                page.locator('#guest-setup-form button[type=submit]').click()
                assert page.locator('#guest-tools').is_visible() and snapshot(database) == before
                assert not result['requests']
                page.reload()
                page.wait_for_function('window.WWGuest && !document.querySelector("#guest-setup-fields").disabled')
                assert page.locator('#guest-game_mode').input_value() == '' and page.locator('#guest-tools').is_hidden()
                page.locator('#guest-instrument').select_option('Flute');page.locator('#guest-game_mode').select_option('Challenge');page.locator('#guest-goal').select_option(index=1)
                page.locator('#guest-setup-form button[type=submit]').click()
                page.locator('a[data-guest-account-entry]').first.click();page.wait_for_url('**/setup?from=guest')
                page.locator('#registration-age').select_option('adult');page.locator('form[action="/setup"] button').click()
                assert page.locator('#setup-game-mode').input_value() == ''
                assert snapshot(database) == before
                page.goto(origin+'/guest/login')
                page.locator('#login-woodchuck-id').fill('WC-GUEST-A');page.locator('#login-pin').fill('2468')
                page.locator('#account-login-form button').click();page.wait_for_url(origin+'/home');authenticated(page);chooser(page)
                assert account(database)['mode'] is None
                assert not any(r['path']=='/account/profile/game-mode' for r in result['requests'])
                assert page.evaluate('WWState.getState().profile.game_mode') is None
                result['checks'].append('Guest choices memory-only; reload/exit resets; no account writes/rewards; setup and login do not import Guest choice')

            elif scenario == 'hydration':
                def fail(route):route.fulfill(status=503, content_type='application/json', body='{"detail":"Synthetic unavailable"}')
                page.route('**/account/state', fail)
                page.goto(origin+'/login');page.locator('#login-woodchuck-id').fill('WC-GUEST-A');page.locator('#login-pin').fill('2468')
                page.locator('#account-login-form button').click()
                page.wait_for_function('document.querySelector("#login-error").textContent.includes("Synthetic unavailable")')
                assert page.url == origin+'/login' and account(database)['mode'] is None
                assert not any(r['path']=='/account/profile/game-mode' for r in result['requests'])
                page.unroute('**/account/state', fail)
                page.route('**/account/me', fail)
                page.goto(origin+'/home')
                page.wait_for_selector('.shared-recovery[data-recovery=server]')
                assert page.locator('.app-shell').is_hidden()
                assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
                page.unroute('**/account/me', fail)
                page.locator('.shared-recovery button', has_text='Retry').click();authenticated(page);chooser(page)
                assert account(database)['mode'] is None
                result['checks'].append('Failed saved-state login and failed profile preflight never treated as NULL or saved; authorized recovery opens unselected chooser')

            else:
                login(page, origin);chooser(page)
                captured = []
                def hold(route):
                    response = route.fetch()
                    assert response.status == 200
                    captured.append((route,response))
                page.route('**/account/profile/game-mode', hold)
                page.locator('#game-mode-entry-select').select_option('Challenge')
                page.locator('#game-mode-entry-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#game-mode-entry-form").getAttribute("aria-busy") === "true"')
                deadline = time.monotonic()+10
                while not captured and time.monotonic()<deadline:page.wait_for_timeout(10)
                assert captured, 'Actual committed response was not captured'
                other = context.new_page()
                assert other.request.post(origin+'/account/logout',headers={'Origin':origin}).status==200
                login(other, origin, 'B');chooser(other)
                before = snapshot(database)
                captured[0][0].fulfill(response=captured[0][1])
                page.wait_for_function('document.querySelector(".app-shell").hidden')
                assert snapshot(database)==before and account(database,'B')['mode'] is None
                assert other.locator('#game-mode-entry-select').input_value()==''
                assert other.locator('#game-mode-entry-feedback').inner_text()==''
                assert account(database)['mode']=='Challenge'
                result['checks'].append('Committed A response delayed across real logout/login B; stale tab closes; B mode, feedback and all DB records unchanged')

            assert not result['page_errors'],result['page_errors']
            page.screenshot(path=str(tmp_path/(scenario+'-'+browser_name+'.png')))
            result['result']='PASS';context.close();browser.close()
    finally:
        (tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')


RECOVERY_SCENARIOS = ['state-null', 'state-selected', 'save-gap', 'restore-gap',
                      'cache', 'locks', 'extra-read', 'idempotent-gap', 'mode-order', 'partial-gap']


def pump_until(page, predicate):
    deadline = time.monotonic() + 10
    while not predicate() and time.monotonic() < deadline:
        page.wait_for_timeout(10)  # Pump held network callbacks, not a race ordering delay.
    assert predicate(), 'Expected bounded browser event was not observed'


def remote_update(context, origin, label):
    headers = {'Origin': origin, 'X-Woodshed-Account': 'WC-GUEST-A'}
    response = context.request.get(origin+'/account/state', headers=headers)
    assert response.status == 200
    payload = response.json();state = payload['state']
    state.setdefault('account', {})['serverRevision'] = payload['revision']
    state['entry_remote_note'] = label
    response = context.request.put(origin+'/account/state', data=state, headers=headers)
    assert response.status == 200
    return response.json()['revision']


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
@pytest.mark.parametrize('scenario', RECOVERY_SCENARIOS)
def test_entry_recovery(save_site, tmp_path, browser_name, scenario):
    origin, database = save_site
    result = {'browser': browser_name, 'scenario': scenario, 'checks': [], 'page_errors': []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name == 'chrome' else p.firefox.launch()
            context = browser.new_context(base_url=origin, viewport={'width':1440,'height':900}, reduced_motion='reduce')
            page = context.new_page();page.set_default_timeout(15000)
            page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            page.on('dialog', lambda d: d.accept())
            if scenario in ('cache', 'locks'):
                # Limit the fault to presentation coordination. Native session
                # caches/authentication locks remain real and unmodified.
                if scenario == 'cache':
                    context.add_init_script('''(()=>{
                      const key='woodshed:game-mode-entry:session:v1';
                      for(const method of ['getItem','setItem']) {
                        const original=Storage.prototype[method];
                        Storage.prototype[method]=function(k,...args){
                          if(k===key) {window.presentationFaults=(window.presentationFaults||0)+1;throw new DOMException('Synthetic presentation cache unavailable','SecurityError');}
                          return original.call(this,k,...args);
                        };
                      }
                    })()''')
                else:
                    context.add_init_script('''(()=>{
                      const native=navigator.locks.request.bind(navigator.locks);
                      // Only the presentation code sees a missing capability;
                      // the unchanged native boundary still uses real locks.
                      Object.defineProperty(navigator.locks,'request',{get(){
                        if(new Error().stack.includes('game-mode-entry.js')) {
                          window.presentationFaults=(window.presentationFaults||0)+1;return undefined;
                        }
                        return native;
                      }});
                    })()''')
                page.goto(origin+'/prebeta/C002');page.goto(origin+'/setup?age=adult')
                page.locator('#woodchuck-name').fill('Synthetic fallback student')
                page.locator('#instrument').select_option('Flute');page.locator('#goal').select_option(index=1)
                page.locator('#student-pin').fill('2468');page.locator('#setup-game-mode').select_option('Performance')
                initial_count = count(database);attempts = []
                def fail_once(route):
                    attempts.append(1)
                    if len(attempts)==1:route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic retry required"}')
                    else:route.continue_()
                page.route('**/account/profile/game-mode', fail_once)
                page.locator('#account-create-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#game-mode-entry").open && document.querySelector("#game-mode-entry-feedback").textContent.includes("Synthetic retry required") && document.querySelector("#game-mode-entry-form").getAttribute("aria-busy")!=="true"')
                wid=page.locator('#created-woodchuck-id').inner_text()
                assert stored_mode(database,wid) is None and count(database)==initial_count+1
                assert page.locator('#game-mode-entry-select').input_value()=='Performance'
                assert page.evaluate('presentationFaults > 0 && WWSessionBoundary.isCurrent()')
                page.locator('#game-mode-entry-form button[type=submit]').click();saved(page)
                assert stored_mode(database,wid)=='Performance' and count(database)==initial_count+1
                assert len(attempts)==2 and page.locator('#account-create-form').is_hidden()
                result['checks'].append('Explicit Signup choice retained through presentation failure and real 503/retry; one account; native boundary remains authorized')
                if scenario=='locks':
                    # A genuinely missing native capability must independently
                    # fail closed; the presentation fallback cannot authorize it.
                    blocked=context.new_page()
                    blocked.add_init_script("Object.defineProperty(navigator,'locks',{value:undefined})")
                    before=snapshot(database);blocked.goto(origin+'/home')
                    blocked.wait_for_selector('.shared-recovery')
                    assert blocked.locator('.app-shell').is_hidden()
                    assert snapshot(database)==before
                    result['checks'].append('Complete native Web Locks loss independently blocks rendered account access without mutation')
            else:
                login(page,origin);chooser(page)
                if scenario=='state-selected':choose(page,'Beginner')
                if scenario=='idempotent-gap':choose(page,'Challenge')
                close_chooser(page)
                before=snapshot(database);balance_before=account(database)['state']['progress']['credits'];b_before=account(database,'B');base=page.evaluate('WWState.getState().account.serverRevision')
                if scenario=='mode-order':
                    held=[]
                    def hold_mode_read(route):
                        response=route.fetch();assert response.status==200
                        held.append((route,response))
                    page.route('**/account/state',hold_mode_read)
                    page.evaluate('window.oldModeDone=false;void WWGameModeEntry.start().then(()=>{window.oldModeDone=true})')
                    pump_until(page,lambda:len(held)==1)
                    response=context.request.patch(origin+'/account/profile/game-mode',data={'game_mode':'Challenge'},headers={'Origin':origin,'X-Woodshed-Account':'WC-GUEST-A'})
                    assert response.status==200
                    page.evaluate('window.newModeDone=false;void WWGameModeEntry.start().then(()=>{window.newModeDone=true})')
                    pump_until(page,lambda:len(held)==2)
                    held[1][0].fulfill(response=held[1][1]);page.wait_for_function('window.newModeDone')
                    newest=page.evaluate('WWState.getState()')
                    assert newest['profile']['game_mode']=='Challenge' and newest['account']['serverRevision']==base
                    held[0][0].fulfill(response=held[0][1]);page.wait_for_function('window.oldModeDone')
                    assert page.evaluate('WWState.getState()')==newest
                    assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
                    assert account(database)['mode']=='Challenge' and account(database,'B')==b_before
                    result['checks'].append('Older NULL projection delivered after newer selected-mode projection is rejected without adopting a content revision or prompting')
                elif scenario=='extra-read':
                    assert page.evaluate('WWSessionBoundary.isCurrent()')
                    initial=page.evaluate('WWState.getState()')
                    page.route('**/account/state',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic entry-only read failure"}'))
                    assert page.evaluate('WWGameModeEntry.start()') is None
                    assert page.evaluate('WWState.getState()')==initial
                    assert not page.locator('#game-mode-entry').evaluate('(d)=>d.open')
                    assert snapshot(database)==before
                    result['checks'].append('Entry-only state read fails after real native preflight; no revision/mode/state write, dialog or false success')
                else:
                    # Pending edits stay at their old revision until conflict
                    # recovery; the server update is made by an authorized client.
                    page.evaluate('()=>{const s=WWState.getState();s.entry_pending_note="pending-local";WWState.saveState(s,{sync:false})}')
                    if scenario.startswith('state-'):
                        held=[]
                        page.route('**/account/state',lambda route:held.append(route))
                        page.evaluate('window.entryReadDone=false;void WWGameModeEntry.start().then(()=>{window.entryReadDone=true})')
                        pump_until(page,lambda:bool(held))
                        revision=remote_update(context,origin,'other-client-update')
                        response=held[0].fetch();assert response.status==200
                        held[0].fulfill(response=response)
                        page.wait_for_function('window.entryReadDone')
                        page.unroute('**/account/state')
                    else:
                        if scenario=='partial-gap':
                            # Existing partial readers may know a newer economy
                            # revision without confirming mode at that revision.
                            # Use actual authorized server updates, not forged
                            # revisions or rewards, then the existing projector.
                            response=context.request.patch(origin+'/account/profile/game-mode',data={'game_mode':'Beginner'},headers={'Origin':origin,'X-Woodshed-Account':'WC-GUEST-A'})
                            assert response.status==200
                            remote_update(context,origin,'other-client-update')
                            response=context.request.get(origin+'/account/state',headers={'Origin':origin,'X-Woodshed-Account':'WC-GUEST-A'})
                            assert response.status==200
                            remote=response.json()
                            page.evaluate('(p)=>{const s=WWState.getState();WWState.applyEconomy(s,p);WWState.saveState(s,{sync:false})}', {'revision':remote['revision'],'credits':remote['state']['progress']['credits']})
                            base=page.evaluate('WWState.getState().account.serverRevision')
                            assert not page.evaluate('(revision)=>WWState.gameModeAtRevision(WWState.getState(),revision)',base)
                        open_editor(page)
                        held=[]
                        def gap(route):
                            revision=base if scenario=='partial-gap' else remote_update(context,origin,'other-client-update')
                            response=route.fetch();assert response.status==200
                            held.append(revision)
                            if scenario=='restore-gap':route.abort('failed')
                            else:route.fulfill(response=response)
                        page.route('**/account/profile/game-mode',gap)
                        page.locator('#change-level-select').select_option('Challenge')
                        page.locator('#change-level-form button[type=submit]').click()
                        page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("saved") && document.querySelector("#change-level-form").getAttribute("aria-busy")!=="true"')
                        assert held and account(database)['mode']=='Challenge'
                        if scenario=='restore-gap':assert 'recovered' in page.locator('#change-level-feedback').inner_text()
                        page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                    local=page.evaluate('WWState.getState()')
                    assert local['account']['serverRevision']==base
                    assert local['entry_pending_note']=='pending-local'
                    assert 'entry_remote_note' not in local
                    expected_mode='Beginner' if scenario=='state-selected' else 'Challenge' if scenario.endswith('gap') else None
                    assert local['profile']['game_mode']==expected_mode
                    statuses=[]
                    page.on('response',lambda r:statuses.append(r.status) if r.url==origin+'/account/state' and r.request.method=='PUT' else None)
                    with page.expect_navigation(wait_until='domcontentloaded'):
                        page.evaluate('void WWAccountSync.syncNow()')
                    authenticated(page)
                    page.wait_for_function('window.WWState && WWState.getState().entry_remote_note==="other-client-update" && WWSessionBoundary.isCurrent()')
                    assert 409 in statuses
                    backups=page.evaluate('Object.keys(localStorage).filter(k=>k.startsWith("woodshedWoodchuckConflictBackup.")).map(k=>JSON.parse(localStorage.getItem(k)))')
                    assert any(s.get('entry_pending_note')=='pending-local' for s in backups)
                    # The restored full revision now safely permits ordinary save.
                    page.evaluate('()=>{const s=WWState.getState();s.entry_followup_note="after-recovery";WWState.saveState(s,{sync:false})}')
                    assert page.evaluate('WWAccountSync.syncNow()') is True
                    final=account(database)
                    assert final['state']['entry_remote_note']=='other-client-update'
                    assert final['state']['entry_followup_note']=='after-recovery'
                    assert final['mode']==expected_mode
                    after=snapshot(database)
                    for table in ('reward_grants','contest_results','camp_point_awards','owned_item_copies'):
                        assert after[table]==before[table]
                    assert final['state']['progress']['credits']==balance_before
                    assert account(database,'B')==b_before
                    result['checks'].append('Newer revision not attached to stale content; local edits backed up; actual 409 full recovery then successful save preserves other-client update, mode, rewards and balances')
            assert not result['page_errors'],result['page_errors']
            page.screenshot(path=str(tmp_path/(scenario+'-'+browser_name+'.png')))
            result['result']='PASS';context.close();browser.close()
    finally:
        (tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
