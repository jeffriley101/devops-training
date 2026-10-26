"""Automatic opening using real routes; only server clock/history are synthetic."""
from datetime import datetime
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_weekly2_game_mode_entry_browser import login
from test_weekly2_newsletter_browser import business, finished, opened


def seed(factory, scenario):
    from app import weekly_newsletter as news, weekly_routes
    from app.weekly import CENTRAL, utc_instant
    from app.feature_access import FEATURES, Feature
    from app.models import WoodchuckProfile
    from app.age_models import AccountPrivacy
    clock=datetime(2026,10,19,13,29,59,tzinfo=CENTRAL) if scenario=='auto-before' else datetime(2026,10,20,14,tzinfo=CENTRAL)
    news.utc_instant=lambda value=None:utc_instant(value or clock)
    weekly_routes.utc_instant=lambda value=None:utc_instant(value or clock)
    FEATURES['weekly_newsletter']=Feature(scenario!='auto-gate','open')
    with factory.begin() as s:
        for p in s.query(WoodchuckProfile):
            p.game_mode=None if scenario=='auto-order' else 'Challenge'
            if scenario=='auto-adult':s.get(AccountPrivacy,p.id).age_band='adult'


def ready(page):
    page.wait_for_function('() => window.WWGameModeEntry && window.WWShedEntry && !document.querySelector(".app-shell").hidden')
    assert page.evaluate('async()=>await WWSessionBoundary.ready') is True
    page.wait_for_function('() => !document.querySelector("#xp-panel").hidden')


def test_auto(site, output, engine, scenario):
    origin,database=site
    result={'scenario':scenario,'engine':engine,'requests':[],'errors':[],'seeded':'Server clock only; presentation rows created by actual endpoint.'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if engine=='chrome' else pw.firefox.launch()
        ctx=browser.new_context(base_url=origin,reduced_motion='reduce',viewport={'width':1280,'height':900})
        page=ctx.new_page();page.set_default_timeout(15000)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        ctx.on('request',lambda r:result['requests'].append({'method':r.method,'path':r.url.removeprefix(origin)}) if '/weekly/' in r.url else None)
        intercepted=[]
        if scenario=='auto-lost':
            def lost(route):
                response=route.fetch();assert response.status==200
                intercepted.append(response.json());route.abort()
            page.route('**/weekly/newsletter/presentation',lost)
        login(page,origin);ready(page)
        page.evaluate("""() => {window.__entryObservation={};for(const [name,p] of Object.entries({session:WWSessionBoundary.ready,arrival:WWWorldEntry.arrivalReady,xp:WWShedEntry.ready,mode:WWGameModeEntry.ready}))Promise.resolve(p).then(value=>{window.__entryObservation[name]=value===undefined?'complete':value;});}""")
        baseline=business(database)
        assert not baseline['weekly_newsletter_presentations']
        assert page.locator('#weekly-newsletter-view').is_hidden()
        if scenario=='auto-lost':
            with page.expect_event('requestfailed',predicate=lambda r:r.url.endswith('/weekly/newsletter/presentation')):
                page.keyboard.press('Escape')
        else:page.keyboard.press('Escape')
        if scenario=='auto-order':
            page.wait_for_function('() => document.querySelector("#game-mode-entry").open')
            assert business(database)['weekly_newsletter_presentations']==[]
            page.keyboard.press('Escape')
        if scenario in ('auto-gate','auto-before'):
            page.evaluate('async()=>{await WWShedEntry.ready;await WWGameModeEntry.ready;}')
            if scenario=='auto-before':
                page.wait_for_event('response',predicate=lambda r:r.url.endswith('/weekly/newsletter')) if not any(r['path']=='/weekly/newsletter' for r in result['requests']) else None
            assert page.locator('#weekly-newsletter-view').is_hidden()
            assert not any(r['method']=='POST' for r in result['requests'])
            assert business(database)==baseline
        elif scenario=='auto-lost':
            page.wait_for_function('() => !WWSurfaces.current()')
            # A manual entry remains usable even after a committed response is lost.
            opened(page)
            assert intercepted and intercepted[0]['status']=='reserved'
            assert len(business(database)['weekly_newsletter_presentations'])==1
        else:
            page.wait_for_function('() => !document.querySelector("#weekly-newsletter-view").hidden')
            finished(page)
            assert page.locator('#weekly-newsletter-content').get_attribute('data-stage')=='1'
            after=business(database)
            assert len(after['weekly_newsletter_presentations'])==1
            for key in baseline:
                if key!='weekly_newsletter_presentations':assert baseline[key]==after[key],key
            page.screenshot(path=str(output/'auto-desktop.png'))
            page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
            assert page.locator('#mum-open-button').evaluate('(el)=>el===document.activeElement')
            opened(page);finished(page)
            assert business(database)==after
            page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
            with page.expect_response(lambda r:r.url.endswith('/weekly/newsletter')) as reread:
                page.reload()
            assert reread.value.status==200
            page.wait_for_function('() => window.WWGameModeEntry && window.WWShedEntry && !document.querySelector(".app-shell").hidden')
            page.evaluate('async()=>{await WWShedEntry.ready;await WWGameModeEntry.ready;}')
            # Explicit manual reopening can read while the auto duplicate finishes; never a second marker.
            opened(page);finished(page)
            assert business(database)['weekly_newsletter_presentations']==after['weekly_newsletter_presentations']
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(output/'auto-portrait.png'))
        if scenario=='auto-devices':
            page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
            original=business(database)['weekly_newsletter_presentations']
            other=browser.new_context(base_url=origin,reduced_motion='reduce')
            device=other.new_page();device.set_default_timeout(15000)
            login(device,origin);ready(device)
            with device.expect_response(lambda r:r.url.endswith('/weekly/newsletter/presentation')) as duplicate:
                device.keyboard.press('Escape')
            assert duplicate.value.json()['status']=='already_reserved'
            assert device.locator('#weekly-newsletter-view').is_hidden()
            tab=ctx.new_page();tab.set_default_timeout(15000)
            result['tab_before_foreground']={'hidden':tab.evaluate('document.hidden')}
            tab.bring_to_front()
            with tab.expect_response(lambda r:r.url.endswith('/weekly/newsletter/presentation')) as duplicate:
                tab.goto(origin+'/home')
            assert duplicate.value.json()['status']=='already_reserved'
            assert business(database)['weekly_newsletter_presentations']==original
            other.close()
        result['rows']=business(database)['weekly_newsletter_presentations']
        result['no_reward_change']=business(database)['reward_grants']==baseline['reward_grants']
        assert result['no_reward_change'] and not result['errors']
        assert not any('/claim' in r['path'] or '/wheel' in r['path'] for r in result['requests'])
        result['result']='PASS';result['version']=browser.version
        (output/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
        browser.close()


def test_delayed_account(site,output,engine,scenario):
    origin,database=site
    result={'scenario':scenario,'engine':engine,'errors':[],'held_response':'real server response, never fabricated'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if engine=='chrome' else pw.firefox.launch()
        context=browser.new_context(base_url=origin,reduced_motion='reduce')
        a=context.new_page();a.set_default_timeout(15000)
        a.on('pageerror',lambda e:result['errors'].append(str(e)))
        held=[];path='/weekly/newsletter/presentation' if scenario.endswith('write') else '/weekly/newsletter'
        def hold(route):held.append((route,route.fetch()))
        a.route('**'+path,hold)
        login(a,origin);ready(a);baseline=business(database)
        a.keyboard.press('Escape')
        import time
        deadline=time.monotonic()+15
        while not held:
            assert time.monotonic()<deadline,'real held response not observed'
            a.wait_for_timeout(10)  # bounded event-loop polling for the captured response
        assert held[0][1].status==200
        committed=business(database)
        assert len(committed['weekly_newsletter_presentations'])==int(scenario.endswith('write'))
        b=context.new_page();b.set_default_timeout(15000)
        b.on('pageerror',lambda e:result['errors'].append(str(e)))
        login(b,origin,'B');ready(b);b.keyboard.press('Escape')
        b.wait_for_function('() => !document.querySelector("#weekly-newsletter-view").hidden');finished(b)
        after_login=business(database)
        a.wait_for_function('() => !WWSessionBoundary.isCurrent()')
        route,response=held.pop();route.fulfill(response=response)
        a.unroute('**'+path,hold)
        assert a.locator('#mum-panel').is_hidden()
        assert a.locator('#weekly-newsletter-content').inner_text()==''
        assert 'No saved plan' in b.locator('#weekly-newsletter-content').inner_text()
        assert business(database)==after_login
        assert len(after_login['weekly_newsletter_presentations'])==1+int(scenario.endswith('write'))
        for name in ('weekly_plans','weekly_instrument_plans','contest_results'):
            assert baseline[name]==after_login[name],name
        import sqlite3
        with sqlite3.connect(database) as db:
            grants=db.execute('SELECT profile_id,source_key,reward_type,amount FROM reward_grants ORDER BY profile_id').fetchall()
        assert len(grants)==2 and grants[0][0]==1 and grants[1][0]==2
        assert grants[0][1]==grants[1][1] and grants[0][1].startswith('login-streak:')
        assert all(row[2:]==('dandelion',1) for row in grants)
        result['independent_native_login_grants']=grants
        assert not result['errors']
        result.update(result='PASS',version=browser.version,markers=after_login['weekly_newsletter_presentations'],
                      unchanged_after_stale_response=True,login_effects_separately_baselined=True)
        b.screenshot(path=str(output/'account-b.png'))
        (output/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
        browser.close()


def test_active_tool(site,output,engine,scenario):
    origin,database=site;result={'scenario':scenario,'engine':engine,'errors':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if engine=='chrome' else pw.firefox.launch()
        context=browser.new_context(base_url=origin,reduced_motion='reduce')
        page=context.new_page();page.set_default_timeout(15000)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        held=[];posts=[]
        context.on('request',lambda r:posts.append(r.url) if r.method=='POST' and '/weekly/' in r.url else None)
        def hold(route):held.append((route,route.fetch()))
        page.route('**/weekly/newsletter',hold)
        login(page,origin);ready(page);page.keyboard.press('Escape')
        import time
        deadline=time.monotonic()+15
        while not held:
            assert time.monotonic()<deadline
            page.wait_for_timeout(10)
        baseline=business(database)
        page.locator('#metronome-open-button').click()
        page.locator('#metronome-start-button').click()
        page.wait_for_function('() => document.querySelector("#metronome-start-button").classList.contains("metronome-stop-button")')
        route,response=held.pop()
        with page.expect_response(lambda r:r.url.endswith('/weekly/newsletter')):
            route.fulfill(response=response)
        page.unroute('**/weekly/newsletter',hold)
        page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        assert page.locator('#metronome-panel').is_visible()
        assert page.locator('#metronome-start-button').inner_text()=='Stop'
        assert page.locator('#weekly-newsletter-view').is_hidden()
        assert not posts and business(database)==baseline
        page.screenshot(path=str(output/'practice-undisturbed.png'))
        page.locator('#metronome-start-button').click();page.keyboard.press('Escape')
        page.wait_for_function('() => !WWSurfaces.current()')
        page.reload()
        page.wait_for_function('() => !document.querySelector("#weekly-newsletter-view").hidden');finished(page)
        assert len(business(database)['weekly_newsletter_presentations'])==1
        assert business(database)['reward_grants']==baseline['reward_grants']
        assert not result['errors']
        result.update(result='PASS',version=browser.version,undisturbed=True,markers=business(database)['weekly_newsletter_presentations'])
        (output/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
        browser.close()
