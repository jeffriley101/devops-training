"""Newsletter journeys through real endpoints and migrated synthetic databases.
Historical receipts are explicitly seeded; UI writes are inspected separately.
"""
import json
import sqlite3
from datetime import datetime, timedelta, time, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_weekly2_game_mode_entry_browser import login, chooser, close_chooser
from test_weekly2_game_mode_browser import save as save_mode


def server_source(original, migration, scenario):
    source = original.replace('Base.metadata.create_all(engine)', migration)
    return source + f'''\nimport sys\nsys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\nfrom test_weekly2_newsletter_browser import seed\nseed(SessionLocal, {scenario!r})\n'''


def seed(factory, scenario):
    from app.feature_access import FEATURES, Feature
    from app import weekly_newsletter as news, weekly_plans as plans
    from app.weekly import CENTRAL, utc_instant
    from app.models import WoodchuckProfile, RewardGrant, PracticeChart
    FEATURES['weekly_newsletter'] = Feature(scenario != 'gate', 'open')
    FEATURES['weekly_plans'] = Feature(True, 'open')
    now = datetime.now(timezone.utc)
    monday = plans._week_start(now)
    if scenario == 'before':
        before = datetime.combine(monday, time(13, 29, 59), CENTRAL)
        news.utc_instant = lambda value=None: utc_instant(before)
    with factory() as s:
        profiles = s.query(WoodchuckProfile).order_by(WoodchuckProfile.id).all()
        for p in profiles:
            p.game_mode = None if scenario == 'setup' else 'Challenge'
        s.commit()
        p = profiles[0]
        if scenario in ('reading', 'read-only', 'recovery', 'isolation', 'finalized'):
            old_at = datetime.combine(monday-timedelta(days=7), time(14), CENTRAL)
            old = plans.issue_current_plan(s, p, 'Build daily consistency', now=old_at)
            s.commit()
            plans.start_current_instrument(s, p, plans.start_context(p, old), now=old_at)
            s.add(RewardGrant(profile_id=p.id,source_key=f'weekly-monday:{monday-timedelta(days=7)}',reward_type='dandelion',amount=75,created_at=utc_instant(old_at)))
            s.add(RewardGrant(profile_id=p.id,source_key=f'clover-caper:{monday-timedelta(days=7)}:jar:1',reward_type='pickled_carrot',amount=1,created_at=utc_instant(old_at)))
            s.add(PracticeChart(profile_id=p.id,practice_date=monday-timedelta(days=7),created_at=utc_instant(old_at),instrument='Flute',source='pristine',minutes=0,detected_playing_seconds=59,note='PRIVATE NOTE NEVER RENDER',practice_details=['PRIVATE DETAIL'],include_contests=False,include_team_contests=False))
            s.commit()
        if scenario in ('reading','read-only','finalized'):
            plan=plans.issue_current_plan(s,p,'Improve tone and control',now=now);s.commit()
            plans.start_current_instrument(s,p,plans.start_context(p,plan),now=now);s.commit()

        if scenario == 'finalized':
            from app.models import Season, Contest, ContestWeek, ContestResult
            at=utc_instant(now-timedelta(hours=1))
            season=Season(key='newsletter-synthetic',name='Synthetic honors',starts_on=monday-timedelta(days=90),status='closed')
            contest=Contest(key='newsletter-owned-practice',name='Own practice honor',metric_type='practice_minutes',subject_type='student')
            s.add_all([season,contest]);s.flush()
            week=ContestWeek(season_id=season.id,week_start=monday-timedelta(days=7),week_end=monday,
                verification_deadline_at=at,finalize_after=at,status='finalized',finalized_at=at,
                finalizer_rules_version='contest_finalizer_v3',practice_scoring_mode='submitted_v2')
            s.add(week);s.flush()
            honor=ContestResult(contest_week_id=week.id,contest_id=contest.id,division='open',subject_type='student',
                subject_key=str(p.id),profile_id=p.id,display_name_snapshot='PRIVATE IDENTITY',score=999,rank=1,medal='gold',created_at=at)
            s.add(honor);s.flush()
            s.add(RewardGrant(profile_id=p.id,contest_result_id=honor.id,source_key='synthetic-honor',reward_type='dandelion',amount=50,created_at=at));s.commit()
    # Manual-only regression precondition: this edition's auto chance was used.
    # Automatic reservation and no-prior-marker behavior have their own tests.
    if not scenario.startswith('auto-') and scenario not in ('gate', 'before'):
        from app.weekly_models import WeeklyNewsletterPresentation
        with factory.begin() as s:
            for p in s.query(WoodchuckProfile):
                s.add(WeeklyNewsletterPresentation(profile_id=p.id,week_start=monday,reserved_at=now))
    FEATURES['weekly_plans'] = Feature(scenario != 'read-only', 'open')

def business(database):
    with sqlite3.connect(database) as db:
        names=[row[0] for row in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        return {n: sorted([list(row) for row in db.execute('SELECT * FROM "'+n+'"')],key=repr) for n in names}


def opened(page):
    page.locator('#mum-open-button').click()
    page.wait_for_function('() => WWSurfaces.current()?.id === "mum-panel"')
    page.locator('#weekly-newsletter-open').click()
    finished(page)


def finished(page):
    page.wait_for_function('() => document.querySelector("#weekly-newsletter-content").getAttribute("aria-busy") === "false"')


def settle_entry(page, scenario):
    page.wait_for_function('() => window.WWSessionBoundary && window.WWWorldEntry && window.WWAccountSync && !document.querySelector(".app-shell").hidden')
    assert page.evaluate('async()=>await WWSessionBoundary.ready') is True
    if scenario == 'setup':
        chooser(page);close_chooser(page)
    else:
        page.evaluate('async()=>{await WWWorldEntry.arrivalReady; await WWAccountSync.syncNow();}')
        if page.locator('#xp-panel').is_visible():page.keyboard.press('Escape')
        page.wait_for_function('() => !WWSurfaces.current()')


def edit_instrument(page, instrument):
    page.get_by_role('button',name='Review Instrument / Game Mode in profile',exact=True).click()
    page.wait_for_function('() => WWSurfaces.current()?.id === "your-woodchuck"')
    page.locator('#woodchuck-instrument').select_option(instrument)
    page.locator('#woodchuck-editor-form button[type=submit]').click()
    page.wait_for_function('() => document.querySelector("#woodchuck-editor-status").textContent === "Your Woodchuck is saved."')
    page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')


@pytest.fixture
def newsletter_site(tmp_path, request, monkeypatch):
    import test_overhaul_profile_saves_browser as harness
    root=Path(__file__).resolve().parents[1]
    migration=f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(root/'alembic.ini')!r}), 'w25weekly005')"
    monkeypatch.setattr(harness, 'SERVER', server_source(harness.SERVER, migration, request.node.callspec.params['scenario']))
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
@pytest.mark.parametrize('scenario',['gate','before','setup','reading','read-only','recovery','isolation','finalized'])
def test_newsletter_interface(newsletter_site,tmp_path,browser_name,scenario):
    origin,database=newsletter_site
    result={'scenario':scenario,'browser':browser_name,'requests':[],'page_errors':[],
            'seeded_history':'seed() supplies prior plan, chart, and receipts; no UI claim is inferred.',
            'physical_device':'NOT RUN','screen_reader':'NOT RUN'}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
            context=browser.new_context(base_url=origin,viewport={'width':1440,'height':900},reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
            context.on('request',lambda r:result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method,'account_binding':r.headers.get('x-woodshed-account')}) if r.url.startswith(origin+'/weekly/') else None)
            if scenario in ('reading','read-only','isolation','finalized'):
                # Observe before login: automatic startup may finish before
                # settle_entry returns. Manual reads begin only after its no-op.
                with page.expect_response(lambda r:r.url==origin+'/weekly/newsletter/presentation' and r.request.method=='POST') as reservation:
                    login(page,origin);settle_entry(page,scenario)
                automatic=reservation.value
                assert automatic.status==200
                result['automatic_reservation_response']=automatic.json()
                assert result['automatic_reservation_response']['status']=='already_reserved'
                if scenario in ('isolation','finalized'):
                    result['account_a']=page.evaluate('WWSessionBoundary.accountId()')
                    assert automatic.request.frame==page.main_frame
                    assert automatic.request.headers.get('x-woodshed-account')==result['account_a']
            else:
                login(page,origin);settle_entry(page,scenario)
            baseline=business(database)
            manual_request_start=len(result['requests'])
            result['manual_request_start']=manual_request_start
            if scenario=='gate':
                page.locator('#mum-open-button').click()
                assert page.locator('#weekly-newsletter-open').count()==0
                assert page.locator('[data-mum-choice]').count()==4
                assert page.locator('#weekly-caper-open').is_visible()
                page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                assert not result['requests'];assert business(database)==baseline
            else:
                opened(page)
                assert business(database)==baseline
                body=page.locator('#weekly-newsletter-content')
                if scenario=='before':
                    assert '13:30' in body.inner_text();assert page.locator('#newsletter-focus').count()==0
                elif scenario=='setup':
                    assert 'Not selected' in body.inner_text()
                    page.locator('#newsletter-focus').select_option('Build daily consistency')
                    assert page.get_by_role('button',name='Confirm Focus and start this week').is_disabled()
                    page.get_by_role('button',name='Review Instrument / Game Mode in profile').click()
                    page.wait_for_function('() => WWSurfaces.current()?.id === "your-woodchuck"')
                    save_mode(page,'Challenge');page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                    opened(page);page.locator('#newsletter-focus').select_option('Build daily consistency')
                    page.get_by_role('button',name='Confirm Focus and start this week').click();finished(page)
                    assert body.get_attribute('data-stage')=='2'
                    after_plan=business(database);assert len(after_plan['weekly_plans'])==1;assert len(after_plan['weekly_instrument_plans'])==0
                    page.get_by_role('button',name='Start work for Flute',exact=True).click();finished(page)
                    first=business(database);assert len(first['weekly_instrument_plans'])==1
                    edit_instrument(page,'Trumpet');opened(page)
                    page.get_by_role('button',name='View Your New Week').click();finished(page)
                    page.get_by_role('button',name='Start work for Trumpet',exact=True).click();finished(page)
                    second=business(database);assert len(second['weekly_instrument_plans'])==2
                    assert second['weekly_instrument_plans'][0]==first['weekly_instrument_plans'][0]
                    assert second['weekly_plans']==first['weekly_plans']
                    assert 'Both instrument slots' in body.inner_text()
                    page.get_by_text('Week notes & saved rewards',exact=True).click()
                    page.get_by_role('button',name='Refresh saved Newsletter').click();finished(page)
                    assert business(database)==second
                    assert second['reward_grants']==first['reward_grants']
                elif scenario in ('reading','read-only','finalized'):
                    page.get_by_text('Last week’s goals, rewards & results',exact=True).click()
                    assert ('results are ready to read' if scenario=='finalized' else 'results are still being wrapped up') in body.inner_text()
                    if scenario=='finalized':
                        assert 'gold · rank 1' in body.inner_text()
                        assert '50 dandelion earned' in body.inner_text()
                    assert 'aren’t available here' in body.text_content()
                    assert 'PRIVATE' not in body.text_content()
                    assert '75 dandelion earned' in body.inner_text()
                    page.get_by_role('button',name='View Your New Week').click();finished(page)
                    assert 'saved Homework' in body.inner_text()
                    if scenario=='read-only':assert 'can’t start weekly work here right now' in body.inner_text()
                    for width,height in [(390,844),(844,390),(1440,900)]:
                        page.set_viewport_size({'width':width,'height':height})
                        assert body.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        page.screenshot(path=str(tmp_path/f'{width}x{height}.png'))
                    assert business(database)==baseline
                    page.get_by_text('Week notes & saved rewards',exact=True).click()
                    page.get_by_role('button',name='Back to Last Week').click();finished(page)
                    if scenario=='finalized':
                        page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                        assert not [r for r in result['requests'][manual_request_start:] if r['method']=='POST' and r['path'] not in ('/weekly/plan','/weekly/plan/instruments/current/start')]
                        result['manual_phase_before_reload']=[manual_request_start,len(result['requests'])]
                        with page.expect_response(lambda r:r.url==origin+'/weekly/newsletter/presentation' and r.request.method=='POST') as reservation_reload:
                            page.reload();settle_entry(page,'reading')
                        automatic_reload=reservation_reload.value
                        assert automatic_reload.status==200
                        result['automatic_reservation_response_reload']=automatic_reload.json()
                        assert result['automatic_reservation_response_reload']['status']=='already_reserved'
                        assert automatic_reload.request.frame==page.main_frame
                        assert automatic_reload.request.headers.get('x-woodshed-account')==result['account_a']
                        manual_request_start=len(result['requests'])
                        result['manual_phase_after_reload_start']=manual_request_start
                        after_navigation=business(database)
                        result['post_reload_business']=after_navigation
                        assert after_navigation['weekly_plans']==baseline['weekly_plans']
                        assert after_navigation['weekly_instrument_plans']==baseline['weekly_instrument_plans']
                        assert after_navigation['reward_grants']==baseline['reward_grants']
                        assert after_navigation['weekly_newsletter_presentations']==baseline['weekly_newsletter_presentations']
                        opened(page)
                        assert 'Your Focus: Improve tone and control' in body.inner_text()
                        assert business(database)==after_navigation

                elif scenario=='recovery':
                    def lost(route):
                        response=route.fetch();assert response.status==200
                        result['committed_plan_response']=response.json();route.abort('failed')
                    page.route('**/weekly/plan',lost)
                    page.locator('#newsletter-focus').select_option('Learn songs faster')
                    page.get_by_role('button',name='Confirm Focus and start this week').click();finished(page)
                    page.unroute('**/weekly/plan',lost)
                    assert 'Your Focus: Learn songs faster' in body.inner_text()
                    assert len(business(database)['weekly_plans'])==2 # one seeded prior plan
                    page.get_by_role('button',name='View Your New Week').click();finished(page)
                    # Change the authorized account through its existing endpoint after display.
                    response=page.evaluate('''async()=>{const r=await fetch('/account/appearance',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({instrument:'Trumpet',hoodie:'blue',hat:'green'})});return r.status;}''')
                    assert response==200
                    page.get_by_role('button',name='Start work for Flute',exact=True).click();finished(page)
                    assert 'changed' in page.locator('#weekly-newsletter-status').inner_text()
                    assert len(business(database)['weekly_instrument_plans'])==1 # seeded prior assignment only
                elif scenario=='isolation':
                    page.locator('#weekly-newsletter-back').click()
                    held=[]
                    def delay(route):held.append((route,route.fetch()))
                    page.route('**/weekly/newsletter',delay)
                    page.locator('#weekly-newsletter-open').click()
                    page.wait_for_function('() => document.querySelector("#weekly-newsletter-content").getAttribute("aria-busy")==="true"')
                    deadline=__import__('time').monotonic()+15
                    while not held:
                        assert __import__('time').monotonic()<deadline, 'actual Newsletter response not observed'
                        page.wait_for_timeout(10)
                    page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                    assert not page.locator('#mum-panel').is_visible()
                    # Normal native login transition in a second tab retires A's page.
                    assert not [r for r in result['requests'][manual_request_start:] if r['method']=='POST' and r['path'] not in ('/weekly/plan','/weekly/plan/instruments/current/start')]
                    result['manual_phase_a']=[manual_request_start,len(result['requests'])]
                    page2=context.new_page()
                    with page2.expect_response(lambda r:r.url==origin+'/weekly/newsletter/presentation' and r.request.method=='POST') as reservation_b:
                        login(page2,origin,'B');settle_entry(page2,'reading')
                    automatic_b=reservation_b.value
                    assert automatic_b.status==200
                    result['automatic_reservation_response_b']=automatic_b.json()
                    assert result['automatic_reservation_response_b']['status']=='already_reserved'
                    result['account_b']=page2.evaluate('WWSessionBoundary.accountId()')
                    assert result['account_b']!=result['account_a']
                    assert automatic_b.request.frame==page2.main_frame
                    assert automatic_b.request.headers.get('x-woodshed-account')==result['account_b']
                    manual_request_start=len(result['requests'])
                    result['manual_phase_b_start']=manual_request_start
                    base_b=business(database)
                    result['post_b_login_business']=base_b
                    page.wait_for_function('() => !WWSessionBoundary.isCurrent()')
                    route,response=held.pop();route.fulfill(response=response)
                    page.unroute('**/weekly/newsletter',delay)
                    assert page.locator('#weekly-newsletter-content').inner_text()==''
                    opened(page2)
                    assert 'No saved plan' in page2.locator('#weekly-newsletter-content').inner_text()
                    assert '75 dandelion earned' not in page2.locator('#weekly-newsletter-content').inner_text()
                    assert business(database)==base_b
                    assert not page.locator('#mum-panel').is_visible()
                if scenario!='isolation':
                    before_close=business(database)
                    page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                    assert not page.locator('#mum-panel').is_visible()
                    assert page.locator('#mum-open-button').evaluate('(e)=>e===document.activeElement')
                    assert business(database)==before_close
            assert not result['page_errors'],result['page_errors']
            assert not [r for r in result['requests'][manual_request_start:] if r['method']=='POST' and r['path'] not in ('/weekly/plan','/weekly/plan/instruments/current/start')]
            result['final_business']=business(database)
            result['baseline_business']=baseline
            result['version']=browser.version;result['result']='PASS'
            browser.close()
    except BaseException as error:
        result['result']='FAIL';result['error']=repr(error)
        try:page.screenshot(path=str(tmp_path/'failure.png'));result['dom']=page.content()
        except Exception:pass
        raise
    finally:(tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2,default=str))


def verify_mum_redesign(origin, database, output, browser_name):
    """Focused real-endpoint UX journey; called by the contained evidence runner.

    Fresh fixtures: A has saved current/prior work; B has neither. Automatic
    startup is observed before protected manual-read baselines, never suppressed.
    """
    result={'browser':browser_name,'checks':[],'errors':[],'requests':[],'screenshots':[],'business_comparisons':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
        result['version']=browser.version
        try:
            for who in ('B','A'):
                ctx=browser.new_context(base_url=origin,reduced_motion='reduce',viewport={'width':1440,'height':1000})
                ctx.tracing.start(screenshots=True,snapshots=True,sources=True)
                page=ctx.new_page();page.set_default_timeout(15000)
                page.on('pageerror',lambda e:result['errors'].append(str(e)))
                ctx.on('request',lambda r:result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method}) if r.url.startswith(origin+'/weekly/') else None)
                try:
                    from test_weekly2_newsletter_auto_browser import ready as auto_ready
                    login(page,origin,who);auto_ready(page)
                    with page.expect_response(lambda r:r.url==origin+'/weekly/newsletter/presentation' and r.request.method=='POST') as reservation:
                        page.keyboard.press('Escape')
                    assert reservation.value.status==200 and reservation.value.json()['status']=='reserved'
                    page.locator('#weekly-newsletter-view').wait_for(state='visible');finished(page)
                    body=page.locator('#weekly-newsletter-content')
                    before=business(database);offset=len(result['requests'])
                    assert body.get_attribute('data-stage')=='1'
                    assert 'PRIVATE' not in body.text_content()
                    assert body.locator('img[src="/static/img/characters/mum.png"]').count()==1
                    assert page.locator('#mum-panel > .mum-artwork').is_hidden()
                    for viewport,label in [({'width':1440,'height':1000},'desktop'),({'width':390,'height':844},'mobile')]:
                        page.set_viewport_size(viewport)
                        assert body.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        assert page.locator('#mum-panel').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        file=f'{who}-stage1-{label}.png';page.screenshot(path=str(output/file));result['screenshots'].append(file)
                    assert business(database)==before
                    assert not [r for r in result['requests'][offset:] if r['method']=='POST']
                    result['business_comparisons'].append(who+' Stage1 reads unchanged')
                    if who=='B':
                        assert 'No plan last week?' in body.inner_text()
                        assert body.locator('#newsletter-focus').count()==1
                        confirm=page.get_by_role('button',name='Confirm Focus and start this week',exact=True)
                        assert confirm.is_disabled()
                        body.locator('#newsletter-focus').select_option('Learn songs faster')
                        with page.expect_response(lambda r:r.url==origin+'/weekly/plan' and r.request.method=='POST') as issued:
                            confirm.click()
                        assert issued.value.status==200
                        assert issued.value.request.post_data_json=={'focus':'Learn songs faster'}
                        finished(page);assert body.get_attribute('data-stage')=='2'
                        after=business(database);assert len(after['weekly_plans'])==len(before['weekly_plans'])+1
                        assert after['weekly_instrument_plans']==before['weekly_instrument_plans']
                        assert after['reward_grants']==before['reward_grants']
                        assert after['weekly_plans'][:-1]==before['weekly_plans']
                        page.get_by_role('button',name='Start work for Flute',exact=True).click();finished(page)
                        work=business(database);assert len(work['weekly_instrument_plans'])==len(after['weekly_instrument_plans'])+1
                        assert work['weekly_plans']==after['weekly_plans'];assert work['reward_grants']==after['reward_grants']
                        result['checks'].append('B explicit Focus-only issuance and separate real instrument start')
                    else:
                        assert body.locator('#newsletter-focus').count()==0
                        assert 'Your Focus: Improve tone and control' in body.inner_text()
                        page.get_by_role('button',name='View Your New Week',exact=True).click();finished(page)
                        assert business(database)==before
                        result['checks'].append('A saved Focus/targets/Homework preserved without issuance')
                    assert body.get_attribute('data-stage')=='2'
                    assert body.locator('[data-lane]').count()==5
                    assert 'Thirds' in body.inner_text();assert 'BOARD Bonus Challenges' in body.inner_text()
                    assert body.locator('.newsletter-finale p').inner_text()=='Now go pick up your horn!'
                    assert body.locator('img').count()==1
                    baseline=business(database);offset=len(result['requests'])
                    for viewport,label in [({'width':1440,'height':1000},'desktop'),({'width':390,'height':844},'mobile')]:
                        page.set_viewport_size(viewport)
                        assert body.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        file=f'{who}-stage2-{label}.png';page.screenshot(path=str(output/file));result['screenshots'].append(file)
                        body.locator('.newsletter-finale').scroll_into_view_if_needed()
                        file=f'{who}-stage2-{label}-finale.png';page.screenshot(path=str(output/file));result['screenshots'].append(file)
                    # The final action uses normal keyboard activation and the
                    # existing surface close/focus restoration machinery.
                    page.get_by_role('button',name='Back to SHED',exact=True).focus();page.keyboard.press('Enter')
                    page.wait_for_function('() => !WWSurfaces.current()')
                    assert page.locator('#mum-open-button').evaluate('(e)=>e===document.activeElement')
                    opened(page);page.get_by_role('button',name='View Your New Week',exact=True).click();finished(page)
                    assert business(database)==baseline
                    assert not [r for r in result['requests'][offset:] if r['method']=='POST']
                    result['business_comparisons'].append(who+' manual reopen/read/dismiss unchanged')
                    assert page.locator('#weekly-newsletter-content [data-lane]').count()==5
                    assert not result['errors']
                    result['checks'].append(who+' desktop/mobile, exact finale, keyboard close/focus, saved manual reopening')
                    # Caper remains a separate controller, opened only by an
                    # explicit navigation action, never mounted by rendering.
                    page.get_by_role('button',name='Explore Clover Caper',exact=True).click()
                    page.wait_for_function('() => document.querySelector("#weekly-caper-content").getAttribute("aria-busy")==="false"')
                    assert page.locator('#mum-title').inner_text()=='Clover Caper'
                    assert business(database)==baseline
                    page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                    result['checks'].append(who+' explicit Caper navigation without claims')
                except BaseException:
                    page.screenshot(path=str(output/f'{who}-failure.png'));raise
                finally:
                    ctx.tracing.stop(path=str(output/f'{who}-trace.zip'));ctx.close()
            assert not [r for r in result['requests'] if r['method']=='POST' and r['path'] not in ('/weekly/plan','/weekly/plan/instruments/current/start','/weekly/newsletter/presentation')]
            result['result']='PASS'
        except BaseException as ex:
            result['result']='FAIL';result['error']=repr(ex);raise
        finally:
            (output/'ux-results.json').write_text(json.dumps(result,indent=2));browser.close()
    return result



def verify_mum_caper_unavailable(origin, database, output, browser_name):
    """Real saved-plan Newsletter with its Caper/plan gate OFF, not a fake response."""
    result={'browser':browser_name,'case':'saved-plan-caper-gate-off','errors':[],'requests':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
        result['version']=browser.version
        ctx=browser.new_context(base_url=origin,reduced_motion='reduce',viewport={'width':390,'height':844})
        ctx.tracing.start(screenshots=True,snapshots=True,sources=True)
        page=ctx.new_page();page.set_default_timeout(15000)
        page.on('pageerror',lambda error:result['errors'].append(str(error)))
        ctx.on('request',lambda r:result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method}) if r.url.startswith(origin+'/weekly/') else None)
        try:
            from test_weekly2_newsletter_auto_browser import ready as auto_ready
            login(page,origin);auto_ready(page)
            with page.expect_response(lambda r:r.url==origin+'/weekly/newsletter/presentation' and r.request.method=='POST') as reservation:
                page.keyboard.press('Escape')
            assert reservation.value.status==200 and reservation.value.json()['status']=='reserved'
            page.locator('#weekly-newsletter-view').wait_for(state='visible');finished(page)
            before=business(database);offset=len(result['requests'])
            page.get_by_role('button',name='View Your New Week',exact=True).click();finished(page)
            body=page.locator('#weekly-newsletter-content')
            assert body.get_attribute('data-stage')=='2'
            payload=page.evaluate("async()=>await(await fetch('/weekly/newsletter')).json()")
            assert payload['current']['plan_actions_available'] is False
            assert page.locator('#weekly-caper-open').count()==0 and page.locator('#weekly-caper-view').count()==0
            button=page.get_by_role('button',name='Explore Clover Caper',exact=True)
            assert button.is_disabled()
            assert 'Clover Caper isn’t available here right now' in body.inner_text()
            assert page.locator('#mum-title').evaluate('(e)=>e===document.activeElement')
            page.keyboard.press('Enter')
            assert body.get_attribute('data-stage')=='2' and page.locator('#weekly-newsletter-view').is_visible()
            assert page.locator('#mum-title').evaluate('(e)=>e===document.activeElement')
            assert business(database)==before
            button.scroll_into_view_if_needed();page.screenshot(path=str(output/'caper-unavailable-mobile.png'))
            page.get_by_role('button',name='Back to SHED',exact=True).focus();page.keyboard.press('Enter')
            page.wait_for_function('() => !WWSurfaces.current()')
            assert page.locator('#mum-open-button').evaluate('(e)=>e===document.activeElement')
            opened(page);page.get_by_role('button',name='View Your New Week',exact=True).click();finished(page)
            assert page.get_by_role('button',name='Explore Clover Caper',exact=True).is_disabled()
            assert business(database)==before
            assert not [r for r in result['requests'][offset:] if r['method']=='POST' or r['path'] in ('/weekly/claims','/weekly/plan')]
            assert not result['errors']
            result.update(result='PASS',stage_preserved=True,focus_preserved=True,business_rows_unchanged=True,unavailable_feedback=True,no_caper_request=True)
        except BaseException as ex:
            result.update(result='FAIL',error=repr(ex));page.screenshot(path=str(output/'failure.png'));raise
        finally:
            (output/'unavailable-results.json').write_text(json.dumps(result,indent=2));ctx.tracing.stop(path=str(output/'trace.zip'));browser.close()
    return result
