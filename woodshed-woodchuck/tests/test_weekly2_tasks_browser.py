"""Existing BOARD button with ordinary Chromium/Firefox security and synthetic data."""
import json
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_overhaul_profile_saves_browser import save_site
from test_weekly2_game_mode_entry_browser import login, chooser, close_chooser


def stored(database):
    with sqlite3.connect(database) as connection:
        connection.row_factory=sqlite3.Row
        tables=('woodchuck_profiles','woodchuck_states','quest_completions','reward_grants',
                'practice_charts','camp_point_awards','crown_awards','weekly_plans','weekly_instrument_plans')
        return {name:[dict(row) for row in connection.execute(f'SELECT * FROM {name} ORDER BY 1')]
                for name in tables}


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
def test_existing_bonus_button_and_zero_activity_message(save_site,tmp_path,browser_name):
    origin,database=save_site
    result={'browser':browser_name,'checks':[],'page_errors':[],'physical_device':'NOT RUN'}
    try:
        with sync_playwright() as playwright:
            browser=(playwright.chromium.launch(executable_path='/opt/google/chrome/chrome')
                     if browser_name=='chrome' else playwright.firefox.launch())
            context=browser.new_context(base_url=origin,viewport={'width':1440,'height':900})
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda error:result['page_errors'].append(str(error)))
            requests=[]
            page.on('request',lambda r:requests.append(r.method+' '+r.url) if '/bonus-challenge/progress' in r.url else None)
            login(page,origin);chooser(page);close_chooser(page)
            response=page.goto(origin+'/quest',wait_until='domcontentloaded')
            assert response.status==200
            page.wait_for_function('window.WWSessionBoundary && window.WWAccountSync')
            assert page.evaluate('async()=>await WWSessionBoundary.ready') is True
            page.evaluate('async()=>await WWAccountSync.syncNow()')
            page.wait_for_function('!document.querySelector("#complete-quest-btn").disabled && document.querySelector("#quest-text").textContent!=="Loading quest..."')
            assert 'Bonus Challenge earns +5 Dandelions once daily and no Activity' in page.locator('.notice-card').first.inner_text()
            assert page.locator('script[src="/static/js/app.js?v=wl2-09"]').count()==1
            before=stored(database)
            page.screenshot(path=str(tmp_path/'bonus-before.png'))
            with page.expect_response(lambda r:r.url==origin+'/contests/bonus-challenge/progress' and r.request.method=='POST') as pending:
                page.locator('#complete-quest-btn').click()
            response=pending.value;assert response.status==200
            payload=response.json()
            assert payload['created'] is True and payload['dandelions_awarded']==5
            assert payload['camp_points_awarded']==0 and payload['camp_point_created'] is False
            headers=response.request.all_headers()
            assert headers['origin']==origin and headers.get('cookie')
            page.wait_for_function('document.querySelector("#quest-feedback").textContent==="Challenge complete: +5 Dandelions."')
            assert page.locator('#complete-quest-btn').is_disabled()
            after=stored(database)
            assert len(after['quest_completions'])==len(before['quest_completions'])+1
            grants=[row for row in after['reward_grants'] if row not in before['reward_grants']]
            assert len(grants)==1 and grants[0]['amount']==5 and grants[0]['reward_type']=='dandelion'
            completion=after['quest_completions'][0]
            assert grants[0]['source_key']==f"bonus-challenge:{completion['activity_date']}:{completion['quest_id']}"
            assert grants[0]['category_key']=='board-bonus-v1:flute'
            assert grants[0]['created_at']==completion['completed_at']
            state_before=next(row for row in before['woodchuck_states'] if row['profile_id']==completion['profile_id'])
            state_after=next(row for row in after['woodchuck_states'] if row['profile_id']==completion['profile_id'])
            assert json.loads(state_after['state_json'])['progress']['credits']==json.loads(state_before['state_json'])['progress']['credits']+5
            assert state_after['revision']==state_before['revision']+1
            assert all(row in after['woodchuck_states'] for row in before['woodchuck_states'] if row['profile_id']!=completion['profile_id'])
            for name in ('practice_charts','camp_point_awards','crown_awards','weekly_plans','weekly_instrument_plans','woodchuck_profiles'):
                assert after[name]==before[name],name
            assert requests==['POST '+origin+'/contests/bonus-challenge/progress']
            # A repeated control action uses existing saved success, without a new POST.
            page.locator('#practice-form').evaluate('(form)=>form.requestSubmit()')
            page.wait_for_timeout(100)
            assert requests==['POST '+origin+'/contests/bonus-challenge/progress']
            assert stored(database)==after
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('document.querySelector("#quest-status").textContent.includes("Completed") || document.querySelector("#quest-status").textContent.includes("Complete")')
            assert page.locator('#complete-quest-btn').is_disabled()
            assert stored(database)==after
            assert not result['page_errors'],result['page_errors']
            page.screenshot(path=str(tmp_path/'bonus-saved.png'))
            result.update(result='PASS',request={'origin':headers['origin'],'content_type':headers.get('content-type'),
                'authenticated_cookie_present':True,'status':response.status},
                receipt={'source_key':grants[0]['source_key'],'category_key':grants[0]['category_key'],
                         'amount':5,'timestamp_matches_completion':True},
                checks=['existing first-party I Played It button commits +5 exactly once / zero Activity',
                        'readable success and help copy; cached asset version updated',
                        'no Practice Chart, Verified credit, Crown or plan allocation',
                        'matching completion/receipt timestamps; owner balance/revision +1',
                        'duplicate control and refresh preserve saved success without another award',
                        'other account and historical business rows preserved'])
            browser.close()
    finally:
        (tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
