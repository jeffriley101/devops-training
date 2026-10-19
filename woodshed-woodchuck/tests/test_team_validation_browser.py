"""Real-browser regression for combined Name/Team validation and recovery."""
import json
import sqlite3

import pytest
from playwright.sync_api import sync_playwright
from test_overhaul_profile_saves_browser import save_site, stored


def team_records(database):
    with sqlite3.connect(database) as db:
        return {table: db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                for table in ('teams', 'team_memberships', 'team_name_claims')}


def enter_shed(page, origin):
    assert page.goto(origin + '/home', wait_until='domcontentloaded').status == 200
    page.wait_for_function('window.WWSessionBoundary && window.WWWorldEntry')
    assert page.evaluate('async()=>await WWSessionBoundary.ready') is True
    page.evaluate('async()=>await WWWorldEntry.arrivalReady')
    if page.evaluate('WWSurfaces.current()?.id === "xp-panel"'):
        page.keyboard.press('Escape')
        page.wait_for_function('!WWSurfaces.current()')


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
def test_name_team_validation_is_readable_atomic_and_recovers(save_site, tmp_path, browser_name):
    origin, database = save_site
    checks, errors = [], []
    try:
        with sync_playwright() as p:
            browser = (p.chromium.launch(executable_path='/opt/google/chrome/chrome')
                       if browser_name == 'chrome' else p.firefox.launch())
            context = browser.new_context(viewport={'width': 390, 'height': 844},
                                          reduced_motion='reduce', base_url=origin)
            assert context.request.post(origin + '/account/login',
                form={'woodchuck_id': 'WC-GUEST-A', 'pin': '2468'},
                headers={'Origin': origin}).status == 200
            page = context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            enter_shed(page, origin)
            before_b = stored(database, 'WC-GUEST-B')
            page.locator('#shed-team-button').focus()
            page.keyboard.press('Enter')
            page.wait_for_function('WWSurfaces.current()?.id === "shed-team-panel"')
            page.wait_for_function('document.querySelector("#shed-team-emblem-choice").options.length > 1')
            assert page.locator('.artwork-scene').evaluate('(n)=>n.inert')

            # Keep Name form validity, outage recovery and cooldown contracts.
            page.locator('#change-name-input').fill('')
            assert not page.locator('#change-name-form').evaluate('(f)=>f.checkValidity()')
            saved_name = 'D13 ' + browser_name
            page.locator('#change-name-input').fill(saved_name)
            calls = []

            def unavailable(route):
                calls.append(route.request.method)
                if len(calls) == 1:
                    route.fulfill(status=503, content_type='application/json',
                                  body='{"detail":"Synthetic temporary outage"}')
                else:
                    route.continue_()

            page.route('**/account/profile/name', unavailable)
            page.locator('#change-name-form button[type=submit]').click()
            page.wait_for_function('document.querySelector("#change-name-feedback").classList.contains("error-text")')
            assert page.locator('#change-name-input').input_value() == saved_name
            assert page.locator('#woodchuck-name-value').inner_text() == 'Synthetic A'
            assert page.locator('#change-name-form button[type=submit]').is_enabled()
            assert calls == ['PATCH']
            page.locator('#change-name-form button[type=submit]').click()
            page.wait_for_function('document.querySelector("#change-name-feedback").textContent.includes("successfully")')
            assert calls == ['PATCH', 'PATCH']
            assert page.locator('#woodchuck-name-value').inner_text() == saved_name
            page.locator('#change-name-input').fill('Another name')
            page.locator('#change-name-form button[type=submit]').click()
            page.wait_for_function('document.querySelector("#change-name-feedback").classList.contains("error-text")')
            assert page.locator('#woodchuck-name-value').inner_text() == saved_name
            page.locator('#change-name-input').fill(saved_name)
            checks.append({'name_validity_outage_retry_cooldown': 'PASS'})

            available = page.evaluate("""async()=>{
                const data = await (await fetch('/teams')).json();
                const used = new Set(data.teams.map(t=>t.emblem.key));
                return data.approved_emblems.find(e=>!used.has(e.key)).key;
            }""")
            initial_teams = team_records(database)
            for name, emblem, guidance in [
                ('', '', ['Enter a Team name.', 'Choose a Team emblem.']),
                ('Valid draft', '', ['Choose a Team emblem.']),
                ('', available, ['Enter a Team name.']),
            ]:
                page.locator('#shed-team-name').fill(name)
                page.locator('#shed-team-emblem-choice').select_option(emblem)
                with page.expect_response(lambda r: r.url == origin + '/teams' and r.request.method == 'POST') as response:
                    page.locator('#shed-team-create').click()
                assert response.value.status == 422
                page.wait_for_function('(expected)=>expected.every(t=>document.querySelector("#shed-team-feedback").textContent.includes(t))', arg=guidance)
                feedback = page.locator('#shed-team-feedback').inner_text()
                assert '[object Object]' not in feedback
                assert page.locator('#woodchuck-name-value').inner_text() == saved_name
                assert team_records(database) == initial_teams, 'Rejected Team validation must not partially save'
                assert stored(database, 'WC-GUEST-B') == before_b
                checks.append({'team_validation': response.value.json(), 'text': feedback, 'no_partial_save': True})
            page.screenshot(path=str(tmp_path / 'team-validation-readable.png'))

            # Exercise error presentation only; actual validation remains tested above.
            # HTML-shaped server text must stay text, including per-field messages.
            for detail, expected in [
                ('This Team name is unavailable.', 'This Team name is unavailable.'),
                ([{'loc': ['body', 'name'], 'msg': '<img id="unsafe-team-message" src=x>', 'type': 'value_error'}],
                 'Team name: <img id="unsafe-team-message" src=x>'),
                ([{}], 'Your Team could not be created. Check the name and emblem and try again.'),
            ]:
                def presentation_error(route):
                    route.fulfill(status=422, content_type='application/json', body=json.dumps({'detail': detail}))
                page.route('**/teams', presentation_error)
                page.locator('#shed-team-create').click()
                page.wait_for_function('(text)=>document.querySelector("#shed-team-feedback").textContent === text', arg=expected)
                assert page.locator('#unsafe-team-message').count() == 0
                assert team_records(database) == initial_teams
                checks.append({'synthetic_presentation_detail': detail, 'safe_text': expected})
                page.unroute('**/teams', presentation_error)

            # Correct both fields and save through the real endpoint, by keyboard.
            team_name = 'D13 ' + browser_name + ' Team'
            page.locator('#shed-team-name').fill(team_name)
            page.locator('#shed-team-emblem-choice').select_option(available)
            page.locator('#shed-team-create').focus()
            page.keyboard.press('Tab')
            page.keyboard.press('Shift+Tab')
            assert page.locator('#shed-team-create').evaluate('(n)=>n.matches(":focus-visible") && getComputedStyle(n).outlineStyle !== "none"')
            page.keyboard.press('Enter')
            page.wait_for_function('(name)=>document.querySelector("#shed-team-current").textContent.includes(name)', arg=team_name)
            assert 'created and selected' in page.locator('#shed-team-feedback').inner_text().lower()
            for key in ['Tab'] * 18 + ['Shift+Tab'] * 18:
                page.keyboard.press(key)
                assert page.locator('#shed-team-panel').evaluate('(n)=>n.contains(document.activeElement)')
            page.keyboard.press('Escape')
            page.wait_for_function('!WWSurfaces.current()')
            assert page.locator('#shed-team-button').evaluate('(n)=>document.activeElement === n')
            enter_shed(page, origin)
            assert page.locator('#woodchuck-name-value').inner_text() == saved_name
            page.locator('#shed-team-button').click()
            page.wait_for_function('(name)=>document.querySelector("#shed-team-current").textContent.includes(name)', arg=team_name)
            assert stored(database, 'WC-GUEST-B') == before_b
            page.screenshot(path=str(tmp_path / 'name-team-saved.png'))
            checks.append({'real_team_creation_persistence_keyboard_focus': 'PASS'})

            # Leave a real validation error on A; switch through native session
            # transitions and verify neither that error nor A's identity reaches B.
            page.locator('#shed-team-name').fill('')
            page.locator('#shed-team-emblem-choice').select_option('')
            page.locator('#shed-team-create').click()
            page.wait_for_function('document.querySelector("#shed-team-feedback").textContent.includes("Enter a Team name.")')
            before_switch = team_records(database)
            next_page = context.new_page()
            next_page.on('pageerror', lambda error: errors.append(str(error)))
            enter_shed(next_page, origin)
            statuses = next_page.evaluate('''async()=>{
                const out = await fetch('/account/logout', {method:'POST'});
                const inside = await fetch('/account/login', {method:'POST', body:new URLSearchParams({woodchuck_id:'WC-GUEST-B', pin:'2468'})});
                return [out.status, inside.status];
            }''')
            assert statuses == [200, 200]
            enter_shed(next_page, origin)
            page.wait_for_function('!WWSessionBoundary.isCurrent() && document.querySelector(".app-shell").hidden')
            assert next_page.evaluate('WWState.getState().account.woodchuckId') == 'WC-GUEST-B'
            assert next_page.locator('#woodchuck-name-value').inner_text() == 'Synthetic B'
            assert saved_name not in next_page.locator('body').inner_text()
            next_page.locator('#shed-team-button').click()
            next_page.wait_for_function('WWSurfaces.current()?.id === "shed-team-panel"')
            next_page.wait_for_function('document.querySelector("#shed-team-current").textContent.includes("No team selected.")')
            assert next_page.locator('#shed-team-feedback').inner_text() == ''
            assert next_page.locator('#change-name-feedback').inner_text() == ''
            assert team_name not in next_page.locator('#shed-team-current').inner_text()
            assert team_records(database) == before_switch
            assert_team_belongs_only_to_a(database, initial_teams, team_name)
            next_page.screenshot(path=str(tmp_path / 'account-b-clean-feedback.png'))
            checks.append({'account_switch_native_transitions': statuses, 'A_closed_B_clean': True})
            assert errors == []
            context.close()
            browser.close()
    finally:
        (tmp_path / 'focused-results.json').write_text(json.dumps({'browser': browser_name, 'checks': checks, 'page_errors': errors}, indent=2))


def assert_team_belongs_only_to_a(database, initial, team_name):
    """Assert the new Team belongs only to A and no rejected drafts persisted."""
    current = team_records(database)
    assert len(current['teams']) == len(initial['teams']) + 1
    assert len(current['team_memberships']) == len(initial['team_memberships']) + 1
    with sqlite3.connect(database) as db:
        members = db.execute('SELECT p.woodchuck_id FROM team_memberships m '
                             'JOIN teams t ON t.id=m.team_id JOIN woodchuck_profiles p ON p.id=m.profile_id '
                             'WHERE t.display_name=?', (team_name,)).fetchall()
    assert members == [('WC-GUEST-A',)]
