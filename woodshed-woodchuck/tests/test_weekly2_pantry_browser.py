"""Real migrated Pantry reads; seeded charts/ledger are labelled synthetic history."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json

import pytest
from playwright.sync_api import sync_playwright
from test_weekly2_caper_browser import stored
from test_weekly2_game_mode_entry_browser import login,chooser,close_chooser


def seed(factory,label):
    if label.startswith('gate-'):return
    from app.models import WoodchuckProfile,PracticeChart,RewardGrant
    from app import weekly_plans as plans
    now=datetime.now(timezone.utc);monday=plans._week_start(now)
    with factory() as s:
        a=s.query(WoodchuckProfile).filter_by(woodchuck_id='WC-GUEST-A').one()
        a.game_mode='Challenge'
        s.flush()
        if not label.startswith('noplan-'):
            plans.issue_current_plan(s,a,'Build daily consistency',now=now-timedelta(minutes=5))
        for p in s.query(WoodchuckProfile).all():
            s.add(PracticeChart(profile_id=p.id,practice_date=monday,created_at=now-timedelta(minutes=1),
                instrument='Flute',minutes=100 if p.id==a.id else 59,source='p-book',
                note='PRIVATE SYNTHETIC PANTRY NOTE',practice_details=['PRIVATE DETAIL'],
                include_contests=False,include_team_contests=False))
        s.add(PracticeChart(profile_id=a.id,practice_date=monday,created_at=now-timedelta(minutes=1),
            instrument='Flute',minutes=1,source='pristine',detected_playing_seconds=1,
            include_contests=False,include_team_contests=False))
        for i,amount in enumerate([1,1,1,1,1,-3]):
            s.add(RewardGrant(profile_id=a.id,source_key=f'synthetic-reviewed-pantry-ledger:{i}',reward_type='pickled_carrot',amount=amount))
        s.commit()


def ready(page):
    page.wait_for_function('window.WWSessionBoundary && window.WWAccountSync && !document.querySelector(".app-shell").hidden')
    assert page.evaluate('async()=>await WWSessionBoundary.ready')
    page.evaluate('async()=>{await WWWorldEntry.arrivalReady;await WWAccountSync.syncNow();}')
    if page.locator('#xp-panel').is_visible():page.keyboard.press('Escape')
    page.wait_for_function('!WWSurfaces.current()')


def shop(page):
    page.locator('#woodshed-hotspots a[href="/store"]').click()
    page.wait_for_url('**/store');ready(page)


def opened(page):
    page.wait_for_function('WWSurfaces.current()?.id==="weekly-pantry"')
    page.wait_for_function('document.querySelector("#weekly-pantry-status").textContent==="Tap to dismiss."')


def open_pantry(page,keyboard=False):
    control=page.locator('#dandelion-object')
    if keyboard:control.focus();page.keyboard.press('Enter')
    else:control.click()
    opened(page)


def closed(page):
    page.wait_for_function('!WWSurfaces.current()')
    assert not page.locator('#weekly-pantry').is_visible()
    assert page.locator('#dandelion-object').evaluate('(el)=>el===document.activeElement')


def hit_targets(page,selector):
    # Normal closed-room geometry: each calibrated center reaches its own target.
    # This is distinct from intentional native-modal isolation while Pantry opens.
    rows=page.locator(selector+' > .scene-hotspot').evaluate_all("""nodes=>nodes.map(el=>{
      const r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
      const hit=document.elementFromPoint(x,y)?.closest('.scene-hotspot');
      return {id:el.id,cell:el.dataset.sceneCell,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
        visible:r.width>0&&r.height>0,center_in_view:x>=0&&y>=0&&x<innerWidth&&y<innerHeight,
        hit_cell:hit?.dataset.sceneCell,correct:hit===el};
    })""")
    assert len(rows)==10
    assert all(r['visible'] and r['center_in_view'] and r['correct'] for r in rows),rows
    return rows


@pytest.fixture
def pantry_site(tmp_path,request,monkeypatch):
    import test_overhaul_profile_saves_browser as harness
    app_root=Path(__file__).resolve().parents[1]
    label=request.node.callspec.params['scenario']+'-'+request.node.callspec.params['browser_name']
    migration=f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(app_root/'alembic.ini')!r}), 'w25weekly004')"
    source=harness.SERVER.replace('Base.metadata.create_all(engine)',migration)
    source+=f"\nimport sys\nsys.path.insert(0,{str(app_root/'tests')!r})\nfrom app.feature_access import FEATURES,Feature\nFEATURES['weekly_plans']=Feature(not {label!r}.startswith('gate-'),'open')\nFEATURES['weekly_pantry']=Feature(not {label!r}.startswith('gate-'),'open')\nfrom test_weekly2_pantry_browser import seed\nseed(SessionLocal,{label!r})\n"
    monkeypatch.setattr(harness,'SERVER',source)
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
@pytest.mark.parametrize('scenario',['gate','noplan','saved','isolation'])
def test_pantry_interface(pantry_site,tmp_path,browser_name,scenario):
    origin,database=pantry_site
    result={'browser':browser_name,'scenario':scenario,'checks':[],'requests':[],'errors':[],
        'fixtures':'seeded synthetic charts and signed earned/spent ledger; no UI claims/spending',
        'physical_device':'NOT RUN','screen_reader':'NOT RUN','geometry':[],'native_state_writes':[]}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
            context=browser.new_context(base_url=origin,viewport={'width':1440,'height':900},reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:result['errors'].append(str(e)))
            context.on('request',lambda r:result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method}) if r.url.startswith(origin+'/weekly/') else None)
            def native_state_write(response):
                if response.url==origin+'/account/state' and response.request.method=='PUT':
                    body=response.json()
                    result['native_state_writes'].append({'status':response.status,'revision':body.get('revision')})
            page.on('response',native_state_write)
            login(page,origin)
            if scenario=='gate':chooser(page);close_chooser(page)
            ready(page)
            assert page.locator('#weekly-pantry-open').count()==0
            assert page.locator('script[src*="weekly-pantry"]').count()==0
            shop(page)
            baseline=stored(database)
            assert page.locator('#dandelion-object').count()==1
            assert not page.locator('#weekly-pantry').is_visible()
            page.evaluate('''()=>{window.pantryEffects=0;window.addEventListener('woodshed:celebrate',()=>window.pantryEffects++);}''')
            if scenario=='gate':
                assert page.locator('script[src*="weekly-pantry"]').count()==0
                assert page.locator('#dandelion-object').get_attribute('data-weekly-pantry') is None
                for keyboard in [False,True]:
                    before=page.evaluate('window.pantryEffects')
                    if keyboard:page.locator('#dandelion-object').focus();page.keyboard.press('Enter')
                    else:page.locator('#dandelion-object').click()
                    assert page.locator('.shop-dandelion-balance-burst').count()==1
                    assert page.evaluate('window.pantryEffects')==before+1
                assert not result['requests'] and stored(database)==baseline
                result['geometry'].append({'viewport':[1440,900],'room':'SHOP','targets':hit_targets(page,'#shop-hotspots')})
                result['checks'].append('Gate OFF: original single balance burst/reaction on pointer and keyboard; no Pantry script/request/business write')
            elif scenario in ('saved','noplan'):
                assert not result['requests']
                for width,height in [(1440,900),(390,844),(844,390)]:
                    page.set_viewport_size({'width':width,'height':height})
                    geometry=hit_targets(page,'#shop-hotspots')
                    before=len(result['requests']);open_pantry(page)
                    assert len(result['requests'])==before+1
                    assert page.locator('.shop-dandelion-balance-burst').count()==0
                    assert page.locator('#weekly-pantry-close').evaluate('(el)=>el===document.activeElement')
                    text=page.locator('#weekly-pantry-content').inner_text()
                    assert 'Chart-logging days' in text and 'Practice Minutes' in text and 'Verified' not in text
                    assert '2 Pickled Carrot jars available' in text and 'statistics' in text
                    assert 'PRIVATE' not in text
                    if scenario=='saved':
                        assert '1h 40m 1s / 1h' in text
                        assert page.locator('.weekly-pantry-fill').nth(1).evaluate('(el)=>el.style.height')=='100%'
                    else:
                        assert 'goal not set' in text and '1h 40m 1s' in text
                        assert page.locator('.weekly-pantry-fill').nth(1).evaluate('(el)=>el.style.height')=='0%'
                    assert page.locator('#credits-value').inner_text() in text
                    assert page.locator('#weekly-pantry').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert page.locator('#weekly-pantry button').count()==1
                    assert page.locator('#weekly-pantry details').count()==0
                    page.screenshot(path=str(tmp_path/f'pantry-{width}x{height}.png'))
                    # Clicking an arbitrary visible part of the display dismisses;
                    # no detail/hold/reward action is attached to this tap.
                    page.locator('#weekly-pantry-close').click();closed(page)
                    assert page.evaluate('window.pantryEffects')==0
                    result['geometry'].append({'viewport':[width,height],'room':'SHOP','targets':geometry})
                    assert hit_targets(page,'#shop-hotspots')==geometry
                open_pantry(page,keyboard=True);page.keyboard.press('Escape');closed(page)
                open_pantry(page,keyboard=True);page.keyboard.press('Space');closed(page)
                assert stored(database)==baseline
                # A failed actual read is not a no-plan/NULL-mode inference.
                def fail(route):route.abort('connectionreset')
                page.route('**/weekly/pantry',fail);page.locator('#dandelion-object').click()
                page.wait_for_function('document.querySelector("#weekly-pantry-status").textContent.includes("tap Dandelions to retry")')
                assert page.locator('#weekly-pantry-content').inner_text()==''
                page.locator('#weekly-pantry-close').click();closed(page)
                page.unroute('**/weekly/pantry',fail)
                open_pantry(page);page.locator('#weekly-pantry-close').click();closed(page)
                # Observe the actual brief lifecycle; no clock/timer override.
                open_pantry(page)
                page.wait_for_function('document.querySelector("#weekly-pantry-content strong").hidden')
                assert page.locator('#weekly-pantry').is_visible()
                page.wait_for_function('!document.querySelector("#weekly-pantry").open');closed(page)
                assert page.evaluate('window.pantryEffects')==0
                if scenario=='saved':
                    page.emulate_media(reduced_motion='no-preference')
                    open_pantry(page)
                    page.wait_for_function('!document.querySelector("#weekly-pantry").open');closed(page)
                    assert page.evaluate('window.pantryEffects')==1
                assert stored(database)==baseline
                # Unchanged neighboring SHOP control remains normally clickable.
                page.locator('[data-shop-panel="contact"]').click()
                page.wait_for_function('WWSurfaces.current()?.id==="shop-feature-dialog"')
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                # Use actual navigation; all preexisting SHED hit areas must be
                # reachable, especially Mum at the exact failing844x390 viewport.
                before_navigation=stored(database)
                writes_before=len(result['native_state_writes'])
                page.locator('#shop-hotspots a[href="/home"]').click();page.wait_for_url('**/home');ready(page)
                shed_baseline=stored(database)
                sync_writes=result['native_state_writes'][writes_before:]
                assert sync_writes and all(x['status']==200 for x in sync_writes)
                for table in before_navigation:
                    if table!='woodchuck_states':assert shed_baseline[table]==before_navigation[table],table
                old_states={x['profile_id']:x for x in before_navigation['woodchuck_states']}
                new_states={x['profile_id']:x for x in shed_baseline['woodchuck_states']}
                own_id=next(x['id'] for x in before_navigation['woodchuck_profiles'] if x['woodchuck_id']=='WC-GUEST-A')
                for pid in old_states:
                    if pid!=own_id:assert new_states[pid]==old_states[pid]
                assert new_states[own_id]['revision']==old_states[own_id]['revision']+len(sync_writes)
                assert new_states[own_id]['revision']==sync_writes[-1]['revision']
                assert json.loads(new_states[own_id]['state_json'])['progress']==json.loads(old_states[own_id]['state_json'])['progress']
                result['navigation_accounting']={'before_rows':before_navigation,'after_rows':shed_baseline,'explicit_native_sync_writes':sync_writes}
                assert page.locator('#weekly-pantry-open').count()==0
                for width,height in [(1440,900),(390,844),(844,390)]:
                    page.set_viewport_size({'width':width,'height':height})
                    result['geometry'].append({'viewport':[width,height],'room':'SHED','targets':hit_targets(page,'#woodshed-hotspots')})
                    page.locator('#mum-open-button').click();page.wait_for_function('WWSurfaces.current()?.id==="mum-panel"')
                    page.locator('#weekly-caper-open').click()
                    page.wait_for_function('document.querySelector("#weekly-caper-content").getAttribute("aria-busy")==="false"')
                    page.locator('#weekly-caper-back').click();page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                    assert page.locator('#mum-open-button').evaluate('(el)=>el===document.activeElement')
                assert stored(database)==shed_baseline
                result['checks'].append('Existing SHOP R4 only; desktop/portrait844x390 landscape hit geometry; real pointer/keyboard/tap dismissal and focus; brief1400/4500ms timing/reduced motion; error/dismiss/reopen retry; precise progress/signed balance; GET-only; SHED/Mum/Caper intact')
            else:
                held=[];pause_reads=True
                def hold(route):
                    if not pause_reads:
                        route.continue_();return
                    response=route.fetch();assert response.status==200
                    held.append((route,response))
                def wait_held():
                    deadline=__import__('time').monotonic()+15
                    while not held:
                        assert __import__('time').monotonic()<deadline,'authorized Pantry response not observed'
                        page.wait_for_timeout(10)
                page.route('**/weekly/pantry',hold)
                page.locator('#dandelion-object').click();wait_held()
                page.keyboard.press('Escape');closed(page)
                pause_reads=False
                open_pantry(page)
                page.evaluate('window.pantryCurrentContent=document.querySelector("#weekly-pantry-content").firstElementChild')
                route,response=held.pop();route.fulfill(response=response)
                page.unroute('**/weekly/pantry',hold)
                assert page.evaluate('window.pantryCurrentContent===document.querySelector("#weekly-pantry-content").firstElementChild')
                assert '1h 40m 1s' in page.locator('#weekly-pantry-content').inner_text()
                page.locator('#weekly-pantry-close').click();closed(page)
                assert stored(database)==baseline
                pause_reads=True
                page.route('**/weekly/pantry',hold)
                page.locator('#dandelion-object').click();wait_held()
                other=context.new_page();other.on('pageerror',lambda e:result['errors'].append(str(e)))
                login(other,origin,'B');chooser(other);close_chooser(other);ready(other);shop(other)
                before_release=stored(database)
                page.wait_for_function('!WWSessionBoundary.isCurrent()')
                route,response=held.pop();route.fulfill(response=response)
                assert page.locator('#weekly-pantry-content').inner_text()==''
                assert page.locator('#weekly-pantry-close').get_attribute('aria-label')=='Pantry. Tap to dismiss.'
                assert other.locator('#weekly-pantry-content').inner_text()==''
                open_pantry(other)
                text=other.locator('#weekly-pantry-content').inner_text()
                assert '59m' in text and '1h 40m 1s' not in text
                assert '0 Pickled Carrot jars available' in text
                assert stored(database)==before_release
                result['checks'].append('Actual held A response cannot repaint reopened panel; real B login retires A; delayed A response clears old labels and cannot expose data or change B rows; B reads only own progress/zero jars')
            assert all(r['method']=='GET' for r in result['requests'])
            assert not result['errors'],result['errors']
            result.update(result='PASS',final_rows=stored(database));browser.close()
    finally:(tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
