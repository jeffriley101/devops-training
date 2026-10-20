"""Ordinary first-party browser entry; no CORS assistance or synthetic scoring."""
import json
import sqlite3

import pytest
from playwright.sync_api import sync_playwright
from test_overhaul_profile_saves_browser import save_site
from test_weekly2_game_mode_entry_browser import login, chooser, close_chooser


def ledger(database):
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
            for table in ('woodchuck_profiles', 'woodchuck_states', 'weekly_plans',
                          'weekly_instrument_plans', 'arcade_attempt_packs',
                          'reward_grants', 'camp_point_awards')
        }


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
def test_free_label_and_first_party_start(save_site, tmp_path, browser_name):
    origin, database = save_site
    # Owned synthetic fixture only. Login and gameplay use ordinary browser paths.
    with sqlite3.connect(database) as connection:
        for pid, raw in connection.execute('SELECT profile_id,state_json FROM woodchuck_states'):
            state = json.loads(raw)
            state.setdefault('progress', {})['credits'] = 0
            connection.execute('UPDATE woodchuck_states SET state_json=? WHERE profile_id=?',
                               (json.dumps(state), pid))
    result = {'browser': browser_name, 'checks': [], 'page_errors': []}
    try:
        with sync_playwright() as playwright:
            browser = (playwright.chromium.launch(executable_path='/opt/google/chrome/chrome')
                       if browser_name == 'chrome' else playwright.firefox.launch())
            context = browser.new_context(base_url=origin, viewport={'width': 1440, 'height': 900})
            page = context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
            login(page, origin)
            chooser(page)
            close_chooser(page)
            page.goto(origin + '/arcade', wait_until='domcontentloaded')
            assert page.locator('[data-arcade-price=thirds]').inner_text() == 'Always free'
            assert page.locator('[data-arcade-price=dressed-to-the-nines]').inner_text() == '100 Dandelions · 3 attempts'
            page.screenshot(path=str(tmp_path / 'arcade-free-label.png'))
            page.goto(origin + '/arcade/thirds', wait_until='domcontentloaded')
            page.wait_for_function('window.WWSessionBoundary && window.WWAccountSync')
            assert page.evaluate('async()=>await WWSessionBoundary.ready') is True
            page.evaluate('async()=>await WWAccountSync.syncNow()')
            before = ledger(database)
            with page.expect_response(lambda response:
                    response.url == origin + '/arcade/plays' and response.request.method == 'POST') as waiting:
                page.locator('#thirds-start').click()
            response = waiting.value
            assert response.status == 200
            payload = response.json()
            assert payload['charged_now'] == 0 and payload['free_reason'] == 'always_free'
            assert payload['result_authority'] == 'server_scored'
            assert set(payload['challenge']['question']) == {'chord'}
            headers = response.request.all_headers()
            assert headers['origin'] == origin and headers.get('cookie')
            result['request'] = {
                'origin': headers['origin'], 'content_type': headers.get('content-type'),
                'authenticated_cookie_present': bool(headers.get('cookie')), 'status': response.status,
                'charged_now': payload['charged_now'], 'free_reason': payload['free_reason'],
            }
            assert ledger(database) == before
            with sqlite3.connect(database) as connection:
                assert connection.execute(
                    'SELECT game_key,entry_cost,pack_id,completed_at,authoritative_score '
                    'FROM arcade_play_sessions').fetchall() == [('thirds', 0, None, None, None)]
            assert not result['page_errors'], result['page_errors']
            page.screenshot(path=str(tmp_path / 'thirds-started.png'))
            result['checks'] = [
                'Thirds free label; unrelated paid label retained',
                'zero-balance first-party UI start with ordinary browser security',
                'one server-generated uncompleted play; no pack, debit or reward',
                'profile/state/plan/assignment and reward ledger unchanged',
            ]
            result['result'] = 'PASS'
            browser.close()
    finally:
        (tmp_path / 'focused-results.json').write_text(json.dumps(result, indent=2) + '\n')
