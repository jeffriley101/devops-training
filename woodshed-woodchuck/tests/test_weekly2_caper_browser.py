"""Caper with real endpoints and ordinary browsers; migrated synthetic data only.
Historical charts, BOARD receipts, Thirds rounds and old clues below are seeded
projection fixtures, not assertions that those activities were played in a browser.
Claims exercised in the browser always reach the genuine authorized endpoints.
"""
from datetime import datetime, time, timedelta, timezone
import json
import hashlib
import sqlite3
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright
from test_overhaul_profile_saves_browser import save_site
from test_weekly2_game_mode_entry_browser import login, chooser, close_chooser
from test_weekly2_game_mode_browser import open_editor, save as save_mode


def seed(factory, label):
    """Called only by the evidence-local runtime, after actual migrations."""
    if label.startswith(('gate-', 'setup-')):
        return
    from app.models import WoodchuckProfile, PracticeChart, QuestCompletion, RewardGrant, ArcadePlaySession
    from app import weekly_plans as plans
    from app.weekly import CENTRAL, utc_instant
    from app.weekly_rewards import grant
    now = datetime.now(timezone.utc)
    monday = plans._week_start(now)
    with factory() as s:
        p = s.query(WoodchuckProfile).filter_by(woodchuck_id='WC-GUEST-A').one()
        p.game_mode = 'Challenge'
        s.commit()
        if label.startswith('claims-'):
            for offset in range(19, 0, -1):
                at = datetime.combine(monday-timedelta(weeks=offset),time(9),CENTRAL)
                plan = plans.issue_current_plan(s,p,'Build daily consistency',now=at)
                s.commit()
                if offset == 1:
                    plans.start_current_instrument(s,p,plans.start_context(p,plan),now=at+timedelta(seconds=1))
                    s.commit()
                    for day in range(4):
                        submitted = at+timedelta(days=day,minutes=1)
                        s.add(PracticeChart(profile_id=p.id,practice_date=submitted.date(),
                            created_at=utc_instant(submitted),instrument='Flute',source='p-book',minutes=15,
                            note='PRIVATE SEEDED NOTE',practice_details=['PRIVATE DETAIL'],
                            include_contests=False,include_team_contests=False))
                    for day in (1,2):
                        at_day=utc_instant(at+timedelta(days=day))
                        quest='synthetic-reviewed-bonus'
                        s.add(QuestCompletion(profile_id=p.id,quest_id=quest,activity_date=at_day.astimezone(CENTRAL).date(),
                            logged_minutes=1,reward_amount=5,created_at=at_day,completed_at=at_day))
                        s.add(RewardGrant(profile_id=p.id,source_key=f'bonus-challenge:{at_day.astimezone(CENTRAL).date()}:{quest}',
                            reward_type='dandelion',amount=5,category_key='board-bonus-v1:flute',created_at=at_day))
                    s.add(ArcadePlaySession(profile_id=p.id,game_key='thirds',play_token=uuid4().hex,
                        started_at=utc_instant(at+timedelta(seconds=2)),completed_at=utc_instant(at+timedelta(minutes=1)),
                        submitted_score=7,authoritative_score=7,entry_cost=0,payout=0))
                    s.commit()
        for offset in range(2,8 if label.startswith('claims-') else 16):
            grant(s,p.id,f'clover-caper:{monday-timedelta(weeks=offset)}:clue','caper_clue',now=now)
        s.commit()


def stored(database):
    with sqlite3.connect(database) as c:
        c.row_factory=sqlite3.Row
        names=('weekly_plans','weekly_instrument_plans','weekly_task_claims','reward_grants',
            'practice_charts','quest_completions','arcade_play_sessions','contest_results',
            'woodchuck_states','woodchuck_profiles','camp_point_awards','crown_awards')
        return {n:[dict(r) for r in c.execute('SELECT * FROM '+n+' ORDER BY 1')] for n in names}


def open_caper(page):
    page.locator('#mum-open-button').click()
    page.wait_for_function('WWSurfaces.current()?.id === "mum-panel"')
    page.locator('#weekly-caper-open').click()
    page.wait_for_function('document.querySelector("#weekly-caper-content").getAttribute("aria-busy") === "false"')
    assert page.locator('#mum-title').inner_text()=='Clover Caper'


def finished(page, message=None):
    page.wait_for_function('document.querySelector("#weekly-caper-content").getAttribute("aria-busy") === "false"')
    if message:
        assert message in page.locator('#weekly-caper-status').inner_text()


def mutate(page,path,body,method='PATCH'):
    result=page.evaluate('''async({path,body,method})=>{
      const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      return {status:r.status,data:await r.json()};
    }''',dict(path=path,body=body,method=method))
    assert result['status']==200,result
    return result['data']


def key(page):
    return page.evaluate('localStorage.getItem("woodshed:clover-caper:request:WC-GUEST-A")')


@pytest.fixture
def caper_site(tmp_path, request, monkeypatch):
    # Ordinary pytest use also migrates the current source; no create_all shortcut.
    import test_overhaul_profile_saves_browser as harness
    root = __import__('pathlib').Path(__file__).resolve().parents[1]
    label = request.node.callspec.params['scenario']+'-'+request.node.callspec.params['browser_name']
    migration = f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(root/'alembic.ini')!r}), 'w25weekly004')"
    source = harness.SERVER.replace('Base.metadata.create_all(engine)',migration)
    source += f"\nimport sys\nsys.path.insert(0, {str(root/'tests')!r})\nfrom app.feature_access import FEATURES, Feature\nFEATURES['weekly_plans']=Feature({not False!r} if not {label!r}.startswith('gate-') else False, 'open')\nfrom test_weekly2_caper_browser import seed\nseed(SessionLocal, {label!r})\n"
    monkeypatch.setattr(harness,'SERVER',source)
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
@pytest.mark.parametrize('scenario',['gate','setup','claims','recovery','storage','stale','race-native','race-cleared','race-changed'])
def test_caper_interface(caper_site,tmp_path,browser_name,scenario):
    origin,database=caper_site
    result={'browser':browser_name,'scenario':scenario,'checks':[], 'requests':[], 'page_errors':[],
        'physical_device':'NOT RUN','screen_reader':'NOT RUN','seeded_history':'seed() documents projection fixtures separately from genuine UI claims'}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
            context=browser.new_context(base_url=origin,viewport={'width':1440,'height':900},reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
            def request(r):
                if r.url.startswith(origin+'/weekly/'):
                    h=r.all_headers()
                    result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method,
                        'body':r.post_data,'origin':h.get('origin'),'account':h.get('x-woodshed-account'),
                        'authenticated_cookie_present':bool(h.get('cookie'))})
            context.on('request',request)
            login(page,origin)
            if scenario in ('gate','setup'):
                chooser(page);close_chooser(page)
            else:
                page.evaluate('async()=>{await WWWorldEntry.arrivalReady; await WWAccountSync.syncNow();}')
                if page.locator('#xp-panel').is_visible():page.keyboard.press('Escape')
                page.wait_for_function('!WWSurfaces.current()')
            baseline=stored(database)
            if scenario=='gate':
                page.locator('#mum-open-button').focus();page.keyboard.press('Enter')
                page.wait_for_function('WWSurfaces.current()?.id==="mum-panel"')
                assert page.locator('#weekly-caper-open').count()==0
                assert page.locator('script[src*="weekly-caper"]').count()==0
                assert page.locator('[data-mum-choice]').count()==4
                assert page.locator('[data-mum-snack-item]').count()==8
                page.locator('[data-mum-choice=water]').click()
                assert 'steady sips' in page.locator('#mum-message').inner_text()
                # Water opens the established Digital Rain surface above Mum.
                # Escape dismisses the top surface, not the whole surface stack.
                page.wait_for_function('WWSurfaces.current()?.id==="digital-rain"')
                assert page.locator('.digital-rain-stop').evaluate('(n)=>n===document.activeElement')
                page.keyboard.press('Escape')
                page.wait_for_function('!document.querySelector(".digital-rain-overlay") && WWSurfaces.current()?.id==="mum-panel"')
                assert page.locator('#mum-panel').is_visible()
                assert page.locator('[data-mum-choice=water]').evaluate('(n)=>n===document.activeElement')
                assert page.locator('.artwork-scene').evaluate('(n)=>n.inert')
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                assert not page.locator('#mum-panel').is_visible()
                assert not page.locator('.artwork-scene').evaluate('(n)=>n.inert')
                assert page.locator('#mum-open-button').evaluate('(n)=>n===document.activeElement')
                assert not result['requests']
                assert stored(database)==baseline
                result['checks'].append('Gate off: care, keyboard, focus preserved; no Caper script/request or business write')
            elif scenario=='setup':
                open_caper(page)
                assert page.locator('#weekly-caper-focus').input_value()==''
                assert 'Not selected' in page.locator('#weekly-caper-content').inner_text()
                assert page.get_by_role('button',name='Start This Week',exact=True).is_disabled()
                assert stored(database)==baseline
                page.locator('#weekly-caper-back').click()
                assert page.locator('#weekly-caper-open').evaluate('(n)=>n===document.activeElement')
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                open_editor(page);save_mode(page,'Challenge')
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                open_caper(page)
                before=stored(database)
                page.locator('#weekly-caper-focus').select_option('Improve tone and control')
                page.get_by_role('button',name='Start This Week',exact=True).click()
                page.wait_for_function('document.querySelectorAll(".weekly-caper-task").length===5')
                finished(page,'Weekly plan saved.')
                assert len(stored(database)['weekly_plans'])==1
                assert not stored(database)['weekly_instrument_plans']
                page.get_by_role('button',name='Start this week’s work',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-content").textContent.includes("Your saved Homework is below")')
                finished(page,'Instrument work saved.')
                first=stored(database)['weekly_instrument_plans'][0]
                # A legitimate instrument update from another client/context is
                # detected before the explicit start; no silent context retry.
                mutate(page,'/account/profile/instrument',{'instrument':'Trumpet'})
                page.locator('#weekly-caper-back').click();page.locator('#weekly-caper-open').click();finished(page)
                mutate(page,'/account/profile/instrument',{'instrument':'Tuba'})
                post_count=len([r for r in result['requests'] if r['method']=='POST'])
                page.get_by_role('button',name='Start this week’s work',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-status").textContent.includes("instrument changed")')
                finished(page)
                assert len([r for r in result['requests'] if r['method']=='POST'])==post_count
                page.get_by_role('button',name='Start this week’s work',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-status").textContent==="Instrument work saved."')
                finished(page)
                assert len(stored(database)['weekly_instrument_plans'])==2
                assert stored(database)['weekly_instrument_plans'][0]==first
                mutate(page,'/account/profile/instrument',{'instrument':'Trumpet'})
                page.locator('#weekly-caper-back').click();page.locator('#weekly-caper-open').click();finished(page)
                assert 'still explore and practice other instruments' in page.locator('#weekly-caper-content').inner_text()
                assert page.get_by_role('button',name='Start this week’s work',exact=True).count()==0
                assert page.locator('.weekly-caper-task').count()==5
                page.locator('[data-lane=homework] summary').first.click()
                assert 'Suggested blocks:' in page.locator('[data-lane=homework]').inner_text()
                for width,height in [(1440,900),(390,844),(844,390)]:
                    page.set_viewport_size({'width':width,'height':height})
                    diag=page.locator('#mum-panel').evaluate('(n)=>({client:n.clientWidth,scroll:n.scrollWidth,height:n.getBoundingClientRect().height,viewport:innerHeight})')
                    assert diag['scroll']<=diag['client']+1,diag
                    result.setdefault('responsive',[]).append(diag)
                    page.screenshot(path=str(tmp_path/f'caper-{width}x{height}.png'))
                page.keyboard.press('Tab')
                assert page.locator('#mum-panel').evaluate('(n)=>n.contains(document.activeElement)')
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                assert page.locator('#mum-open-button').evaluate('(n)=>n===document.activeElement')
                assert page.locator('a[data-scene-cell=L1]').get_attribute('href')=='/store'
                assert page.locator('#xp-level-control').count()==1
                after=stored(database)
                for name in ('reward_grants','practice_charts','quest_completions','contest_results','camp_point_awards','crown_awards'):
                    assert after[name]==before[name],name
                assert [r['body'] for r in result['requests'] if r['path']=='/weekly/plan' and r['method']=='POST']==['{"focus":"Improve tone and control"}']
                result['checks'].append('NULL guidance, explicit canonical mode/Focus, frozen first/second assignments, stale preflight, capacity, stored Homework, keyboard/focus, desktop/portrait/landscape, navigation and XP intact')
            elif scenario=='claims':
                open_caper(page)
                assert stored(database)==baseline
                assert 'PRIVATE' not in page.locator('#weekly-caper-content').inner_text()
                historical=page.locator('details[data-source-plan]').first
                source=historical.get_attribute('data-source-plan')
                historical.locator('summary').first.click()
                assert historical.locator('.weekly-caper-task').count()==5
                assert page.get_by_role('button',name='CLAIM CLOVER',exact=True).count()==0
                page.get_by_role('button',name='Older weeks',exact=True).click()
                page.wait_for_function('document.querySelectorAll("details[data-source-plan]").length>12')
                finished(page)
                for lane in ['logging','practice_minutes','homework','board','thirds']:
                    card=page.locator(f'details[data-source-plan="{source}"] [data-lane="{lane}"]')
                    card.get_by_role('button',name='Claim ',exact=False).click()
                    page.wait_for_function('(s)=>document.querySelector(s)?.textContent.includes("Claimed")',arg=f'details[data-source-plan="{source}"] [data-lane="{lane}"]')
                    finished(page)
                after_tasks=stored(database)
                new=[r for r in after_tasks['reward_grants'] if r not in baseline['reward_grants']]
                assert len(new)==3 and all(r['reward_type']=='pickled_carrot' and r['amount']==1 for r in new)
                assert len(after_tasks['weekly_task_claims'])==2
                assert after_tasks['woodchuck_states']==baseline['woodchuck_states']
                assert after_tasks['practice_charts']==baseline['practice_charts']
                page.locator(f'details[data-source-plan="{source}"]').get_by_role('button',name='CLAIM CLUE',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-content").textContent.includes("7 / 7 clues")')
                finished(page)
                assert not any(r['reward_type']=='white_clover' for r in stored(database)['reward_grants'])
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                finished(page)
                assert key(page)
                assert 'Four-Leaf White Clover earned' in page.locator('#weekly-caper-content').inner_text()
                assert page.get_by_role('button',name='Continue to SHED',exact=True).evaluate('(n)=>n===document.activeElement')
                assert page.locator('#weekly-caper-content').get_attribute('data-reduced-motion')=='true'
                page.screenshot(path=str(tmp_path/'clover-earned.png'))
                assert 'artwork pending approval' in page.locator('#weekly-caper-content').inner_text()
                page.keyboard.press('Enter');page.wait_for_function('!WWSurfaces.current()')
                assert key(page) is None
                after=stored(database)
                new=[r for r in after['reward_grants'] if r not in baseline['reward_grants']]
                assert sorted(r['reward_type'] for r in new)==['caper_clue','pickled_carrot','pickled_carrot','pickled_carrot','white_clover']
                for n in baseline:
                    if n not in ('reward_grants','weekly_task_claims'):assert after[n]==baseline[n],n
                result['receipts']=new
                result['checks'].append('Bounded historical pages/source IDs; genuine five task claims -> 3 jars and 2 acknowledgements -> seventh clue without automatic Clover -> explicit one Clover; no Dandelion/Activity/state/history mutation; reduced motion and missing-art fallback')
            elif scenario=='recovery':
                open_caper(page);assert key(page) is None
                assert not [r for r in result['requests'] if r['method']=='POST']
                lost=[]
                def lose(route):
                    response=route.fetch();assert response.status==200
                    lost.append(response.json());route.abort('connectionreset')
                page.route('**/weekly/clover/claim',lose)
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-content").textContent.includes("Resume Clover claim")')
                finished(page)
                first_key=key(page);assert first_key and len(lost)==1
                assert len([r for r in stored(database)['reward_grants'] if r['reward_type']=='white_clover'])==1
                page.unroute('**/weekly/clover/claim',lose)
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                open_caper(page)
                page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                assert key(page)==first_key
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function('window.WWWeeklyCaper && !document.querySelector(".app-shell").hidden')
                page.evaluate('async()=>await WWWorldEntry.arrivalReady')
                if page.locator('#xp-panel').is_visible():page.keyboard.press('Escape')
                open_caper(page);page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                assert key(page)==first_key
                posts=[r for r in result['requests'] if r['method']=='POST']
                assert len(posts)==3 and len({json.loads(r['body'])['request_key'] for r in posts})==1
                assert len([r for r in stored(database)['reward_grants'] if r['reward_type']=='white_clover'])==1
                page.get_by_role('button',name='Continue to SHED',exact=True).click();page.wait_for_function('!WWSurfaces.current()')
                assert key(page) is None
                open_caper(page)
                assert len([r for r in result['requests'] if r['method']=='POST'])==3
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                second_key=key(page);assert second_key!=first_key
                after=stored(database)
                receipts=[r for r in after['reward_grants'] if r not in baseline['reward_grants']]
                assert len(receipts)==2 and all(r['reward_type']=='white_clover' and r['amount']==1 for r in receipts)
                assert {r['source_key'] for r in receipts}=={'clover-caper:clues:7','clover-caper:clues:14'}
                for n in baseline:
                    if n!='reward_grants':assert after[n]==baseline[n],n
                result['receipts']=receipts
                result['checks'].append('Real committed response lost via route.fetch+abort; same-key close/reopen/refresh recovery; confirmation retained until Continue; second threshold requires distinct explicit action; exactly two durable Clovers')
            elif scenario=='storage':
                page.evaluate('''()=>{
                  const proto=Storage.prototype, read=proto.getItem, write=proto.setItem;
                  window.caperStorageFault='read';window.caperDeniedWrites=0;
                  proto.getItem=function(k){if(k==='woodshed:clover-caper:request:WC-GUEST-A' && window.caperStorageFault==='read')throw Error('Synthetic exact-key read denial');return read.call(this,k)};
                  proto.setItem=function(k,v){if(k==='woodshed:clover-caper:request:WC-GUEST-A' && window.caperStorageFault==='write'){window.caperDeniedWrites++;throw Error('Synthetic exact-key write denial');}return write.call(this,k,v)};
                }''')
                open_caper(page)
                assert 'storage is unavailable' in page.locator('#weekly-caper-content').inner_text()
                assert page.get_by_role('button',name='CLAIM CLOVER',exact=True).count()==0
                assert stored(database)==baseline
                page.evaluate('window.caperStorageFault="write"')
                page.locator('#weekly-caper-back').click();page.locator('#weekly-caper-open').click();finished(page)
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-status").textContent.includes("Synthetic exact-key write denial")')
                finished(page)
                assert not [r for r in result['requests'] if r['method']=='POST']
                assert stored(database)==baseline
                assert page.evaluate('window.caperDeniedWrites')==1
                page.evaluate('window.caperStorageFault=null')
                assert key(page) is None
                # After storage is restored, initiate a real recoverable action,
                # lose its committed response, and deny reads of that existing key.
                lost=[]
                def lose_storage_response(route):
                    response=route.fetch();assert response.status==200
                    lost.append(True);route.abort('connectionreset')
                page.route('**/weekly/clover/claim',lose_storage_response)
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-content").textContent.includes("Resume Clover claim")')
                finished(page)
                retained_key=key(page);assert retained_key and len(lost)==1
                saved=stored(database)
                receipts=[r for r in saved['reward_grants'] if r not in baseline['reward_grants']]
                assert len(receipts)==1 and receipts[0]['reward_type']=='white_clover' and receipts[0]['amount']==1
                page.unroute('**/weekly/clover/claim',lose_storage_response)
                page.evaluate('window.caperStorageFault="read"')
                page.locator('#weekly-caper-back').click();page.locator('#weekly-caper-open').click();finished(page)
                assert 'storage is unavailable' in page.locator('#weekly-caper-content').inner_text()
                assert page.get_by_role('button',name='CLAIM CLOVER',exact=True).count()==0
                assert len([r for r in result['requests'] if r['method']=='POST'])==1
                assert stored(database)==saved
                # Reads restored; writes still denied. Existing-key recovery must
                # not replace that key or require a new write.
                page.evaluate('window.caperStorageFault="write"')
                assert key(page)==retained_key
                page.locator('#weekly-caper-back').click();page.locator('#weekly-caper-open').click()
                page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                finished(page)
                assert key(page)==retained_key
                assert page.evaluate('window.caperDeniedWrites')==1
                posts=[r for r in result['requests'] if r['method']=='POST']
                assert len(posts)==2 and all(json.loads(r['body'])['request_key']==retained_key for r in posts)
                assert stored(database)==saved
                page.evaluate('''()=>{
                  const remove=Storage.prototype.removeItem;window.caperDeniedRemovals=0;
                  window.restoreCaperRemoval=()=>{Storage.prototype.removeItem=remove;};
                  Storage.prototype.removeItem=function(k){if(k==='woodshed:clover-caper:request:WC-GUEST-A'){window.caperDeniedRemovals++;throw Error('Synthetic exact-key removal denial');}return remove.call(this,k);};
                }''')
                page.get_by_role('button',name='Continue to SHED',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-status").textContent.includes("Synthetic exact-key removal denial")')
                finished(page)
                assert page.evaluate('window.caperDeniedRemovals')==1
                assert key(page)==retained_key and stored(database)==saved
                assert len([r for r in result['requests'] if r['method']=='POST'])==2
                page.evaluate('()=>{window.restoreCaperRemoval();window.caperStorageFault=null;}')
                page.get_by_role('button',name='Continue to SHED',exact=True).click();page.wait_for_function('!WWSurfaces.current()')
                assert key(page) is None
                open_caper(page)
                assert page.get_by_role('button',name='CLAIM CLOVER',exact=True).count()==1
                assert len([r for r in result['requests'] if r['method']=='POST'])==2
                assert stored(database)==saved
                for n in baseline:
                    if n!='reward_grants':assert saved[n]==baseline[n],n
                result['receipts']=receipts
                result['checks'].append('Exact-key read/write really fail without unsafe POST or mutation; restored storage permits real committed action; unavailable retained key is not replaced; same-key recovery works without new write; no duplicate or next-threshold settlement')
            elif scenario in ('race-native','race-cleared','race-changed'):
                # Same-account second tab uses the real existing session, never
                # a fabricated account token or successful server payload.
                other=context.new_page();other.set_default_timeout(15000)
                other.on('pageerror',lambda error:result['page_errors'].append(str(error)))
                other.goto(origin+'/home',wait_until='domcontentloaded')
                other.wait_for_function('window.WWSessionBoundary && window.WWWeeklyCaper && !document.querySelector(".app-shell").hidden')
                assert other.evaluate('async()=>await WWSessionBoundary.ready')
                other.evaluate('async()=>{await WWWorldEntry.arrivalReady;await WWAccountSync.syncNow();}')
                if other.locator('#xp-panel').is_visible():other.keyboard.press('Escape')
                other.wait_for_function('!WWSurfaces.current()')
                assert other.evaluate('WWSessionBoundary.accountId()')==page.evaluate('WWSessionBoundary.accountId()')
                baseline=stored(database)
                lock_name='woodshed:clover-caper:request:WC-GUEST-A'
                def pause_before_native(tab):
                    # Synchronization delays entry to the real named Web Lock.
                    # It neither invokes its callback directly nor replaces it.
                    tab.evaluate("""name=>{
                      const native=navigator.locks.request.bind(navigator.locks);
                      window.caperLockQueued=false;
                      navigator.locks.request=function(lock,...args){
                        if(lock!==name)return native(lock,...args);
                        window.caperLockQueued=true;
                        return new Promise((resolve,reject)=>{
                          window.releaseCaperLock=()=>{navigator.locks.request=native;native(lock,...args).then(resolve,reject)};
                        });
                      };
                    }""",lock_name)
                if scenario!='race-native':pause_before_native(other)
                open_caper(page)
                held=[]
                def hold_real_response(route):
                    response=route.fetch();assert response.status==200
                    held.append((route,response))
                page.route('**/weekly/clover/claim',hold_real_response)
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                page.wait_for_function('document.querySelector("#weekly-caper-content").getAttribute("aria-busy")==="true"')
                # The event pump waits on an actual observed response, not a sleep
                # used as evidence of concurrency.
                deadline=__import__('time').monotonic()+15
                while not held:
                    assert __import__('time').monotonic()<deadline,'real claim response missing'
                    page.wait_for_timeout(10)
                original=key(page);assert original
                other.locator('#mum-open-button').click();other.locator('#weekly-caper-open').click()
                if scenario=='race-native':
                    other.evaluate("""name=>{
                      window.caperBlockingProof=null;
                      const observe=async()=>{
                        const q=await navigator.locks.query();
                        const proof={held:q.held.filter(x=>x.name===name).length,pending:q.pending.filter(x=>x.name===name).length};
                        if(proof.held===1&&proof.pending===1)window.caperBlockingProof=proof;
                        else requestAnimationFrame(observe);
                      };
                      void observe();
                    }""",lock_name)
                    other.wait_for_function('window.caperBlockingProof!==null')
                    proof=other.evaluate('window.caperBlockingProof')
                    result['native_lock_blocking']=proof
                    assert proof['held']==1 and proof['pending']==1
                else:other.wait_for_function('window.caperLockQueued===true')
                route,response=held.pop();route.fulfill(response=response)
                page.unroute('**/weekly/clover/claim',hold_real_response)
                page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                if scenario=='race-native':
                    other.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                    posts=[r for r in result['requests'] if r['method']=='POST']
                    assert len(posts)==2 and all(json.loads(r['body'])['request_key']==original for r in posts)
                    assert len([r for r in stored(database)['reward_grants'] if r['reward_type']=='white_clover'])==1
                    # Delay A's confirmed Continue; B clears K and explicitly
                    # creates K2. A's old Continue must not delete K2.
                    pause_before_native(page)
                    page.get_by_role('button',name='Continue to SHED',exact=True).click()
                    page.wait_for_function('window.caperLockQueued===true')
                    other.get_by_role('button',name='Continue to SHED',exact=True).click()
                    other.wait_for_function('!WWSurfaces.current()');assert key(other) is None
                    open_caper(other)
                    assert len([r for r in result['requests'] if r['method']=='POST'])==2
                    other.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                    other.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                    second=key(other);assert second and second!=original
                    page.evaluate('()=>{window.releaseCaperLock();}')
                    page.wait_for_function('document.querySelector("#weekly-caper-status").textContent.includes("request changed")')
                    finished(page);assert key(page)==second
                else:
                    page.get_by_role('button',name='Continue to SHED',exact=True).click()
                    page.wait_for_function('!WWSurfaces.current()');assert key(page) is None
                    if scenario=='race-changed':
                        open_caper(page)
                        page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                        page.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                        second=key(page);assert second and second!=original
                    before_release=stored(database)
                    post_count=len([r for r in result['requests'] if r['method']=='POST'])
                    other.evaluate('()=>{window.releaseCaperLock();}')
                    other.wait_for_function('document.querySelector("#weekly-caper-status").textContent.includes("request changed")')
                    finished(other)
                    assert len([r for r in result['requests'] if r['method']=='POST'])==post_count
                    assert stored(database)==before_release
                    if scenario=='race-cleared':
                        assert key(other) is None
                        assert len([r for r in before_release['reward_grants'] if r['reward_type']=='white_clover'])==1
                        assert other.get_by_role('button',name='CLAIM CLOVER',exact=True).count()==1
                        other.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                        other.wait_for_function('document.querySelector(".weekly-caper-celebration")')
                        second=key(other);assert second and second!=original
                    else:
                        assert key(other)==second
                        assert other.get_by_role('button',name='Resume Clover claim',exact=True).count()==1
                after=stored(database)
                receipts=[r for r in after['reward_grants'] if r not in baseline['reward_grants']]
                assert len(receipts)==2 and all(r['reward_type']=='white_clover' and r['amount']==1 for r in receipts)
                assert {r['source_key'] for r in receipts}=={'clover-caper:clues:7','clover-caper:clues:14'}
                for name in baseline:
                    if name!='reward_grants':assert after[name]==baseline[name],name
                result['receipts']=receipts
                result['checks'].append('Two same-account tabs, genuine endpoints: original key recovery never creates or adopts another action; delayed Continue cannot delete newer key; fourteen clues settled only by two distinct explicit actions; no other business changes')
            elif scenario=='stale':
                held=[]
                def hold(route):
                    response=route.fetch();assert response.status==200
                    held.append((route,response))
                page.route('**/weekly/plan',hold)
                page.locator('#mum-open-button').click();page.locator('#weekly-caper-open').click()
                page.wait_for_function('document.querySelector("#weekly-caper-content").textContent.includes("Loading")')
                while not held:page.wait_for_timeout(10)
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                route,response=held.pop();route.fulfill(response=response)
                page.unroute('**/weekly/plan',hold)
                assert page.locator('#weekly-caper-content').inner_text()==''
                open_caper(page)
                held_claim=[]
                def hold_claim(route):
                    response=route.fetch();assert response.status==200
                    held_claim.append((route,response))
                page.route('**/weekly/clover/claim',hold_claim)
                page.get_by_role('button',name='CLAIM CLOVER',exact=True).click()
                while not held_claim:page.wait_for_timeout(10)
                retained=key(page);assert retained
                # Real account switch through login in a second page, not forged
                # payloads or an artificial account-generation event.
                other=context.new_page();other.on('pageerror',lambda error:result['page_errors'].append(str(error)))
                login(other,origin,'B');chooser(other);close_chooser(other)
                other.evaluate('async()=>await WWAccountSync.syncNow()')
                b_before=stored(database)
                page.wait_for_function('!WWSessionBoundary.isCurrent()')
                route,response=held_claim.pop();route.fulfill(response=response)
                assert page.locator('#weekly-caper-content').inner_text()==''
                assert other.evaluate('localStorage.getItem("woodshed:clover-caper:request:WC-GUEST-B")') is None
                assert other.locator('#weekly-caper-content').inner_text()==''
                after=stored(database)
                b_id=next(r['id'] for r in b_before['woodchuck_profiles'] if r['woodchuck_id']=='WC-GUEST-B')
                for name,records in b_before.items():
                    own=lambda row:row.get('profile_id')==b_id or (name=='woodchuck_profiles' and row.get('id')==b_id)
                    assert [r for r in after[name] if own(r)]==[r for r in records if own(r)],name
                receipts=[r for r in after['reward_grants'] if r not in baseline['reward_grants'] and r['reward_type']=='white_clover']
                assert len(receipts)==1 and receipts[0]['profile_id']!=b_id
                result['receipts']=receipts
                with sqlite3.connect(database) as c:
                    assert c.execute("SELECT count(*) FROM reward_grants r JOIN woodchuck_profiles p ON p.id=r.profile_id WHERE p.woodchuck_id='WC-GUEST-B' AND r.reward_type='white_clover'").fetchone()[0]==0
                result['checks'].append('Genuine delayed read after close ignored; delayed committed A claim after real B login cannot repaint either panel, transfer key/currency or reopen dialog')
            assert not result['page_errors'],result['page_errors']
            result.update(result='PASS',final_rows=stored(database))
            browser.close()
    finally:
        # Request keys remain available to assertions in memory, never emitted
        # into evidence. Receipts contain only the server's existing key digest.
        public=json.loads(json.dumps(result,default=str))
        for request in public['requests']:
            if request['path']=='/weekly/clover/claim' and request.get('body'):
                body=json.loads(request['body'])
                request['body']=json.dumps({'request_key_sha256':hashlib.sha256(body['request_key'].encode()).hexdigest()})
        (tmp_path/'focused-results.json').write_text(json.dumps(public,indent=2)+'\n')
