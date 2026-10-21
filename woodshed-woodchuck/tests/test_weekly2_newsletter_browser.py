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
    migration=f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(root/'alembic.ini')!r}), 'w25weekly004')"
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
            context.on('request',lambda r:result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method}) if r.url.startswith(origin+'/weekly/') else None)
            login(page,origin);settle_entry(page,scenario)
            baseline=business(database)
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
                    page.get_by_role('button',name='Refresh saved Newsletter').click();finished(page)
                    assert business(database)==second
                    assert second['reward_grants']==first['reward_grants']
                elif scenario in ('reading','read-only','finalized'):
                    assert ('Results: finalized' if scenario=='finalized' else 'Results: pending') in body.inner_text()
                    if scenario=='finalized':
                        assert 'gold · rank 1' in body.inner_text()
                        assert '50 dandelion recorded' in body.inner_text()
                    assert 'unavailable' in body.inner_text()
                    assert 'PRIVATE' not in body.inner_text()
                    assert '75 dandelion recorded' in body.inner_text()
                    page.get_by_role('button',name='View Your New Week').click();finished(page)
                    assert 'saved Homework' in body.inner_text()
                    if scenario=='read-only':assert 'plan actions are unavailable' in body.inner_text()
                    for width,height in [(390,844),(844,390),(1440,900)]:
                        page.set_viewport_size({'width':width,'height':height})
                        assert body.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                        page.screenshot(path=str(tmp_path/f'{width}x{height}.png'))
                    assert business(database)==baseline
                    page.get_by_role('button',name='Back to Last Week').click();finished(page)
                    if scenario=='finalized':
                        page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                        page.reload();settle_entry(page,'reading')
                        after_navigation=business(database)
                        assert after_navigation['weekly_plans']==baseline['weekly_plans']
                        assert after_navigation['weekly_instrument_plans']==baseline['weekly_instrument_plans']
                        assert after_navigation['reward_grants']==baseline['reward_grants']
                        opened(page)
                        assert 'Saved Focus: Improve tone and control' in body.inner_text()
                        assert business(database)==after_navigation

                elif scenario=='recovery':
                    def lost(route):
                        response=route.fetch();assert response.status==200
                        result['committed_plan_response']=response.json();route.abort('failed')
                    page.route('**/weekly/plan',lost)
                    page.locator('#newsletter-focus').select_option('Learn songs faster')
                    page.get_by_role('button',name='Confirm Focus and start this week').click();finished(page)
                    page.unroute('**/weekly/plan',lost)
                    assert 'Saved Focus: Learn songs faster' in body.inner_text()
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
                    page2=context.new_page();login(page2,origin,'B');settle_entry(page2,'reading')
                    base_b=business(database)
                    page.wait_for_function('() => !WWSessionBoundary.isCurrent()')
                    route,response=held.pop();route.fulfill(response=response)
                    page.unroute('**/weekly/newsletter',delay)
                    assert page.locator('#weekly-newsletter-content').inner_text()==''
                    opened(page2)
                    assert 'No saved plan' in page2.locator('#weekly-newsletter-content').inner_text()
                    assert '75 dandelion recorded' not in page2.locator('#weekly-newsletter-content').inner_text()
                    assert business(database)==base_b
                    assert not page.locator('#mum-panel').is_visible()
                if scenario!='isolation':
                    before_close=business(database)
                    page.keyboard.press('Escape');page.wait_for_function('() => !WWSurfaces.current()')
                    assert not page.locator('#mum-panel').is_visible()
                    assert page.locator('#mum-open-button').evaluate('(e)=>e===document.activeElement')
                    assert business(database)==before_close
            assert not result['page_errors'],result['page_errors']
            assert not [r for r in result['requests'] if r['method']=='POST' and r['path'] not in ('/weekly/plan','/weekly/plan/instruments/current/start')]
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
