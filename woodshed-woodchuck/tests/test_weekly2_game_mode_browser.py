"""WL2-03 real-browser profile selector; synthetic migrated databases only."""
import json
import sqlite3
import time

import pytest
from playwright.sync_api import sync_playwright
from test_team_validation_browser import enter_shed
from test_overhaul_profile_saves_browser import save_site

MODE_PATH = '/account/profile/game-mode'


def snapshot(database):
    with sqlite3.connect(database) as c:
        return {t: c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in (
            'woodchuck_profiles', 'woodchuck_states', 'reward_grants', 'contest_results',
            'camp_point_awards', 'owned_item_copies')}


def account(database, who='A'):
    with sqlite3.connect(database) as c:
        r = c.execute('SELECT p.game_mode,p.level,p.level_changed_at,s.revision,s.state_json '
                      'FROM woodchuck_profiles p JOIN woodchuck_states s ON s.profile_id=p.id '
                      'WHERE p.woodchuck_id=?', ('WC-GUEST-'+who,)).fetchone()
    return {'mode': r[0], 'level': r[1], 'level_changed_at': r[2], 'revision': r[3], 'state': json.loads(r[4])}


def ready(page, origin):
    enter_shed(page, origin)
    page.wait_for_function('window.WWAccountSync && !document.querySelector(".app-shell").hidden')


def open_editor(page):
    page.locator('#instrument-object').focus()
    page.keyboard.press('Enter')
    page.wait_for_function('WWSurfaces.current()?.id === "your-woodchuck"')
    assert page.locator('.artwork-scene').evaluate('(n)=>n.inert')


def wait_finished(page):
    page.wait_for_function('document.querySelector("#change-level-form").getAttribute("aria-busy") !== "true"')


def save(page, value):
    page.locator('#change-level-select').select_option(value)
    page.locator('#change-level-form button[type=submit]').click()
    page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("successfully")')
    wait_finished(page)
    assert page.locator('#change-level-select').input_value() == value
    assert page.locator('#level-value').inner_text() == value


def consistent(page, database, who='A', mode=None):
    stored = account(database, who)
    state = page.evaluate('WWState.getState()')
    assert stored['mode'] == mode
    assert state['account']['woodchuckId'] == 'WC-GUEST-'+who
    assert state['profile']['game_mode'] == state['_weekly_preferences']['game_mode'] == mode
    assert state['account']['serverRevision'] == stored['revision']
    if stored['state'].get('_weekly_preferences'):
        assert stored['state']['_weekly_preferences']['game_mode'] == mode
    return stored


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
@pytest.mark.parametrize('scenario', ['selection', 'recovery', 'delayed'])
def test_game_mode_interface(save_site, tmp_path, browser_name, scenario):
    origin, database = save_site
    result = {'browser': browser_name, 'scenario': scenario, 'checks': [], 'requests': [], 'page_errors': []}
    baseline = None
    try:
        with sync_playwright() as p:
            browser = (p.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name == 'chrome' else p.firefox.launch())
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce', base_url=origin)
            assert context.request.post(origin+'/account/login', form={'woodchuck_id': 'WC-GUEST-A', 'pin': '2468'}, headers={'Origin': origin}).status == 200
            page = context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            def request(r):
                if r.url.startswith(origin) and (r.url.endswith(MODE_PATH) or r.url.endswith('/account/state') or r.url.endswith('/account/profile/level')):
                    headers = r.all_headers()
                    result['requests'].append({'url': r.url, 'method': r.method, 'origin': headers.get('origin'),
                        'content_type': headers.get('content-type'), 'account': headers.get('x-woodshed-account')})
            page.on('request', request)
            ready(page, origin)
            baseline = snapshot(database)
            initial = account(database)
            b_before = account(database, 'B')
            assert initial['mode'] is None
            open_editor(page)
            assert page.locator('#change-level-title').inner_text() == 'Game Mode'
            assert page.locator('#change-level-select').input_value() == ''
            assert page.locator('#level-value').inner_text() == 'Not selected'
            assert page.locator('#change-level-select option').evaluate_all('(xs)=>xs.map(x=>x.value)') == ['', 'Beginner', 'Challenge', 'Performance']
            assert not any(r['method']=='PATCH' for r in result['requests'])

            if scenario == 'selection':
                # Browser JSON cannot override canonical NULL supplied on refresh.
                page.evaluate('''()=>{
                    const state=JSON.parse(localStorage.getItem(WWState.STORAGE_KEY));
                    state.profile.game_mode='Performance';state._weekly_preferences={game_mode:'Performance'};
                    localStorage.setItem(WWState.STORAGE_KEY,JSON.stringify(state));
                }''')
                ready(page, origin)
                open_editor(page)
                assert page.locator('#change-level-select').input_value() == ''
                consistent(page, database, mode=None)
                for index, mode in enumerate(['Beginner', 'Challenge', 'Performance']):
                    page.set_viewport_size({'width': 390, 'height': 844} if index % 2 else {'width': 1440, 'height': 900})
                    before = account(database)
                    page.locator('#change-level-select').select_option(mode)
                    page.locator('#change-level-select').focus()
                    page.keyboard.press('Tab')
                    assert page.locator('#change-level-form button[type=submit]').evaluate('(n)=>n===document.activeElement && n.matches(":focus-visible") && getComputedStyle(n).outlineStyle!=="none"')
                    page.keyboard.press('Enter')
                    page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("successfully")')
                    wait_finished(page)
                    saved = consistent(page, database, mode=mode)
                    assert saved['revision'] == before['revision']+1
                    assert saved['level'] == initial['level'] and saved['level_changed_at'] == initial['level_changed_at']
                    assert page.locator('#level-value').inner_text() == mode
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(tmp_path/(mode.lower()+'-saved.png')))
                    page.keyboard.press('Escape')
                    page.wait_for_function('!WWSurfaces.current()')
                    assert page.locator('#instrument-object').evaluate('(n)=>n===document.activeElement')
                    ready(page, origin)
                    open_editor(page)
                    assert page.locator('#change-level-select').input_value() == mode
                    consistent(page, database, mode=mode)
                    result['checks'].append({'mode': mode, 'saved_and_refreshed': True, 'keyboard_focus': True, 'revision': saved['revision']})
                # Existing Name/Appearance siblings still work in the combined panel.
                page.locator('#woodchuck-instrument').select_option('Trumpet')
                page.locator('#woodchuck-editor-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#woodchuck-editor-status").textContent.includes("saved")')
                assert account(database)['mode'] == 'Performance'
                page.keyboard.press('Escape')
                page.wait_for_function('!WWSurfaces.current()')
                assert page.locator('a[data-scene-cell=L1]').get_attribute('href') == '/store'
                assert page.locator('a[data-scene-cell=L2]').get_attribute('href') == '/p-book'
                page.locator('a[data-scene-cell=L1]').click()
                page.wait_for_url(origin+'/store')
                page.wait_for_function('window.WWSessionBoundary && !document.querySelector(".app-shell").hidden')
                assert page.locator('a[data-scene-cell=L1]').get_attribute('href') == '/home'
                assert page.locator('a[data-scene-cell=R3]').get_attribute('href') == '/quest'
                assert page.locator('.main-nav').count() == 0
                page.locator('a[data-scene-cell=L1]').click()
                page.wait_for_url(origin+'/home')
                page.wait_for_function('window.WWSessionBoundary && !document.querySelector(".app-shell").hidden')
                assert page.locator('.main-nav').count() == 0
                result['checks'].append({'combined_appearance_save_and_shed_shop_navigation': True})

            elif scenario == 'recovery':
                # Invalid select value is readable without issuing a mutation.
                page.evaluate('document.querySelector("#change-level-form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))')
                assert 'Choose Beginner, Challenge or Performance.' in page.locator('#change-level-feedback').inner_text()
                assert account(database) == initial
                def unavailable(route):
                    route.fulfill(status=503, content_type='application/json', body='{"detail":"Synthetic temporary outage"}')
                page.route('**'+MODE_PATH, unavailable)
                page.locator('#change-level-select').select_option('Challenge')
                page.locator('#change-level-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("try again")')
                wait_finished(page)
                assert 'Synthetic temporary outage' in page.locator('#change-level-feedback').inner_text()
                assert page.locator('#change-level-select').input_value() == 'Challenge'
                assert page.locator('#change-level-form button[type=submit]').is_enabled()
                assert account(database) == initial
                page.unroute('**'+MODE_PATH, unavailable)
                save(page, 'Challenge')
                consistent(page, database, mode='Challenge')
                # Actual server commit, then lose only its browser response.
                committed = []
                def lose_response(route):
                    response = route.fetch()
                    assert response.status == 200
                    committed.append(response.json())
                    route.abort('failed')
                page.route('**'+MODE_PATH, lose_response)
                page.locator('#change-level-select').select_option('Performance')
                page.locator('#change-level-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("interrupted response has been recovered")')
                wait_finished(page)
                assert len(committed) == 1
                consistent(page, database, mode='Performance')
                page.unroute('**'+MODE_PATH, lose_response)
                result['checks'].append({'invalid_no_write_503_no_write_retry': True, 'lost_response_after_actual_commit_recovered': committed})
                # If confirmation also fails, explicit Save safely retries the already
                # committed mode, preserving its revision and all other state.
                page.route('**'+MODE_PATH, lose_response)
                def lost_confirmation(route):
                    if route.request.method == 'GET':
                        route.fulfill(status=503, content_type='application/json', body='{}')
                    else:route.continue_()
                page.route('**/account/state', lost_confirmation)
                page.locator('#change-level-select').select_option('Beginner')
                page.locator('#change-level-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("Reload or try again")')
                wait_finished(page)
                persisted = account(database)
                assert persisted['mode'] == persisted['state']['profile']['game_mode'] == 'Beginner'
                page.unroute('**'+MODE_PATH, lose_response)
                page.unroute('**/account/state', lost_confirmation)
                save(page, 'Beginner')
                assert account(database) == persisted
                consistent(page, database, mode='Beginner')
                result['checks'].append({'lost_response_and_confirmation_explicit_retry': True, 'no_duplicate_revision': persisted['revision']})

            else:
                held = []
                def wait_held():
                    deadline = time.monotonic()+8
                    while not held and time.monotonic()<deadline:
                        page.wait_for_timeout(10)  # Pump browser protocol until actual response capture.
                    assert len(held) == 1
                def delay(route):
                    response = route.fetch()
                    assert response.status == 200
                    held.append((route, response))
                page.route('**'+MODE_PATH, delay)
                page.locator('#change-level-select').select_option('Beginner')
                page.locator('#change-level-form button[type=submit]').click()
                page.wait_for_function('document.querySelector("#change-level-form").getAttribute("aria-busy")==="true"')
                wait_held()
                other = context.new_page()
                other.on('pageerror', lambda e: result['page_errors'].append(str(e)))
                ready(other, origin)
                open_editor(other)
                save(other, 'Performance')
                newer = account(database)
                held[0][0].fulfill(response=held[0][1])
                page.wait_for_function('document.querySelector("#change-level-feedback").textContent.includes("successfully")')
                wait_finished(page)
                consistent(page, database, mode='Performance')
                assert account(database) == newer
                page.unroute('**'+MODE_PATH, delay)
                result['checks'].append({'older_same_account_response_cannot_replace_newer_revision': True})
                other.close()
                held.clear()
                page.route('**'+MODE_PATH, delay)
                page.locator('#change-level-select').select_option('Challenge')
                page.locator('#change-level-form button[type=submit]').click()
                wait_held()
                next_page = context.new_page()
                next_page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
                ready(next_page, origin)
                statuses = next_page.evaluate('''async()=>{
                    const a=await fetch('/account/logout',{method:'POST'});
                    const b=await fetch('/account/login',{method:'POST',body:new URLSearchParams({woodchuck_id:'WC-GUEST-B',pin:'2468'})});
                    return [a.status,b.status];
                }''')
                assert statuses == [200, 200]
                ready(next_page, origin)
                b_after_login = account(database, 'B')
                after_legitimate_b_login = snapshot(database)
                page.wait_for_function('!WWSessionBoundary.isCurrent() && document.querySelector(".app-shell").hidden')
                held[0][0].fulfill(response=held[0][1])
                page.wait_for_function('document.querySelector("#change-level-form").getAttribute("aria-busy")!=="true"')
                open_editor(next_page)
                assert next_page.locator('#change-level-select').input_value() == ''
                assert next_page.locator('#level-value').inner_text() == 'Not selected'
                assert next_page.locator('#change-level-feedback').inner_text() == ''
                assert account(database, 'B') == b_after_login
                assert snapshot(database) == after_legitimate_b_login, 'Late response must not mutate any account, grant, history or ownership record'
                assert next_page.evaluate('WWState.getState().account.woodchuckId') == 'WC-GUEST-B'
                consistent(next_page, database, who='B', mode=None)
                result['checks'].append({'native_switch': statuses, 'late_A_cannot_update_B': True})
                next_page.screenshot(path=str(tmp_path/'account-b-null-clean.png'))
                next_page.close()
                page.unroute('**'+MODE_PATH, delay)

            final = account(database)
            assert final['level'] == initial['level'] and final['level_changed_at'] == initial['level_changed_at']
            after = snapshot(database)
            for table in ('reward_grants', 'contest_results', 'camp_point_awards', 'owned_item_copies'):
                # B's normal login may issue its already-existing login award in
                # the switch scenario; the mode requests themselves grant nothing.
                if scenario != 'delayed':assert after[table] == baseline[table], table
            if scenario != 'delayed':assert account(database, 'B') == b_before
            assert not any(r['method']=='PATCH' and r['url'].endswith('/account/profile/level') for r in result['requests'])
            assert not any(r['method']=='PUT' for r in result['requests']), 'Mode selector must not issue a second state PUT'
            assert result['page_errors'] == []
            result['final_account'] = final
            result['result'] = 'PASS'
            context.close()
            browser.close()
    finally:
        (tmp_path/'focused-results.json').write_text(json.dumps(result, indent=2, default=str))
