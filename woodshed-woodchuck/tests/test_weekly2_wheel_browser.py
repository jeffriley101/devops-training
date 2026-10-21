"""Ordinary browsers and real Wheel routes. Initial jars/old plays are seeded history."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import time

import pytest
from playwright.sync_api import sync_playwright
from test_weekly2_game_mode_entry_browser import login
from test_weekly2_pantry_browser import ready, shop, hit_targets
from test_weekly2_caper_browser import stored as business_rows


def seed(factory, label):
    from types import SimpleNamespace
    from app import weekly_wheel as wheel
    from app.models import WoodchuckProfile, RewardGrant
    from app.weekly_models import WeeklyWheelPlay
    now = datetime.now(timezone.utc)
    scenario = label.rsplit('-', 1)[0]
    sequence = iter([7000, 7000, 2950] if scenario in ('free', 'lost') else [2950] * 20)
    # Test server RNG only. Do not replace shared secrets/session randomness.
    wheel.secrets = SimpleNamespace(randbelow=lambda n: next(sequence))
    with factory() as s:
        for p in s.query(WoodchuckProfile):
            p.game_mode = 'Challenge'
        a = s.query(WoodchuckProfile).filter_by(woodchuck_id='WC-GUEST-A').one()
        if scenario not in ('gate', 'empty'):
            s.add(RewardGrant(profile_id=a.id, source_key='synthetic-wheel-earned-jars', reward_type='pickled_carrot', amount=30))
        if scenario == 'free':
            for i in range(8):
                s.add(WeeklyWheelPlay(id=f'synthetic-prior-{i}', profile_id=a.id, request_key=f'synthetic-prior-{i}',
                    funded_day=now.astimezone(wheel.CENTRAL).date(), daily_ordinal=i+1,
                    funded_at=now-timedelta(minutes=10-i), completed_at=now-timedelta(minutes=10-i), outcomes=['ufo']))
                s.add(RewardGrant(profile_id=a.id, source_key=f'weekly-wheel:synthetic-prior-{i}:payment', reward_type='pickled_carrot', amount=-3))
        if scenario == 'history':
            s.add(WeeklyWheelPlay(id='synthetic-pants', profile_id=a.id, request_key='synthetic-pants',
                funded_day=(now-timedelta(days=7)).astimezone(wheel.CENTRAL).date(), daily_ordinal=1,
                funded_at=now-timedelta(days=7), completed_at=now-timedelta(days=7), outcomes=['pants_choice']))
            s.add(RewardGrant(profile_id=a.id, source_key='weekly-wheel:synthetic-pants:1', reward_type='pants_choice', amount=1))
        s.commit()


def stored(db):
    result = business_rows(db)
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        result['weekly_wheel_plays'] = [dict(r) for r in c.execute('SELECT * FROM weekly_wheel_plays ORDER BY id')]
    return result


def open_wheel(page):
    page.locator('[data-shop-panel="practice-room"]').click()
    page.locator('#weekly-wheel-open').click()
    finished(page)


def finished(page):
    page.wait_for_function('document.querySelector("#weekly-wheel-content").getAttribute("aria-busy")==="false"')
    assert page.locator('#weekly-wheel-view').is_visible()


def confirmed(page, text):
    page.wait_for_function('(text)=>document.querySelector("#weekly-wheel-status").textContent.includes("Confirmed: "+text)', arg=text)
    finished(page)


def clear(page):
    page.get_by_role('button', name='Continue', exact=True).click()
    page.wait_for_function('document.querySelector("#weekly-wheel-status").textContent.startsWith("Wheel loaded")')
    finished(page)


def key(page):
    return page.evaluate("""()=>new Promise((resolve,reject)=>{
      const request=indexedDB.open('woodshed-prize-wheel-recovery',1);
      request.onerror=()=>reject(request.error);
      request.onsuccess=()=>{const db=request.result;
        const tx=db.transaction('pending-actions','readonly'),read=tx.objectStore('pending-actions').get(document.body.dataset.accountId);
        let result;read.onsuccess=()=>{result=read.result??null;};
        tx.oncomplete=()=>{db.close();resolve(result);};tx.onabort=()=>{db.close();reject(tx.error);};};
    })""")


def wait_held(page, held):
    deadline = time.monotonic()+15
    while not held:
        assert time.monotonic()<deadline, 'Expected real server response was not observed'
        page.wait_for_timeout(10)


@pytest.fixture
def wheel_site(tmp_path, request, monkeypatch):
    import test_overhaul_profile_saves_browser as harness
    root = Path(__file__).resolve().parents[1]
    label = request.node.callspec.params['scenario']+'-'+request.node.callspec.params['browser_name']
    migration = f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(root/'alembic.ini')!r}), 'w25weekly004')"
    source = harness.SERVER.replace('Base.metadata.create_all(engine)', migration)
    source += f"\nimport sys\nsys.path.insert(0,{str(root/'tests')!r})\nfrom app.feature_access import FEATURES,Feature\nFEATURES['weekly_wheel']=Feature(not {label!r}.startswith('gate-'),'open')\nFEATURES['weekly_pantry']=Feature(True,'open')\nFEATURES['weekly_plans']=Feature(True,'open')\nfrom test_weekly2_wheel_browser import seed\nseed(SessionLocal,{label!r})\n"
    monkeypatch.setattr(harness, 'SERVER', source)
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
@pytest.mark.parametrize('scenario', ['gate','empty','basic','free','history','lost','storage','race-a-first','race-b-first','newer','isolation'])
def test_wheel_interface(wheel_site, tmp_path, browser_name, scenario):
    origin, database = wheel_site
    result = {'scenario':scenario,'browser':browser_name,'requests':[],'errors':[], 'geometry':[],
        'history':'Initial jars and any synthetic-prior/synthetic-pants plays are seeded. Other Wheel receipts are real endpoint writes.',
        'physical_device':'NOT RUN','screen_reader':'NOT RUN'}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
            result['version'] = browser.version
            context = browser.new_context(base_url=origin, viewport={'width':1440,'height':900}, reduced_motion='reduce')
            page = context.new_page(); page.set_default_timeout(15000)
            context.on('page', lambda p:p.on('pageerror',lambda e:result['errors'].append(str(e))))
            page.on('pageerror', lambda e:result['errors'].append(str(e)))
            def record(r):
                if r.url.startswith(origin+'/weekly/'):
                    row={'path':r.url.removeprefix(origin),'method':r.method}
                    if r.post_data:
                        body=json.loads(r.post_data)
                        row['body']={k:(hashlib.sha256(v.encode()).hexdigest() if k=='request_key' else v) for k,v in body.items()}
                    result['requests'].append(row)
            context.on('request',record)
            def posts():return [r for r in result['requests'] if r['method']=='POST' and r['path'].startswith('/weekly/wheel')]
            login(page,origin);ready(page);shop(page)
            baseline=stored(database)
            result['baseline_rows']=baseline
            own_id=next(r['id'] for r in baseline['woodchuck_profiles'] if r['woodchuck_id']=='WC-GUEST-A')
            starting_credits=json.loads(next(r['state_json'] for r in baseline['woodchuck_states'] if r['profile_id']==own_id))['progress']['credits']
            if scenario=='gate':
                assert page.locator('#weekly-wheel-open').count()==0
                assert page.locator('script[src*="weekly-wheel"]').count()==0
                page.locator('[data-shop-panel="practice-room"]').click()
                assert page.locator('.practice-room-door').count()==4
                page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                assert not result['requests'] and stored(database)==baseline
            else:
                open_wheel(page)
                if scenario in ('empty','history'):
                    assert not posts()
                    if scenario=='empty':
                        assert '0 Pickled Carrot jars' in page.locator('#weekly-wheel-content').inner_text()
                        assert page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).is_disabled()
                    else:
                        assert 'Saved result: Historical pants-choice prize' in page.locator('#weekly-wheel-content').inner_text()
                        assert 'pants' not in page.locator('.weekly-wheel-odds').inner_text().lower()
                    assert stored(database)==baseline
                elif scenario=='basic':
                    assert '30 Pickled Carrot jars' in page.locator('#weekly-wheel-content').inner_text()
                    assert page.locator('.weekly-wheel-odds li').all_text_contents()==[
                        '29.5% — 14 Activity points','20% — 400 Dandelions','20% — 1,400 XP','20% — Spin Again',
                        '8% — UFO emoji','2% — 1,000 Dandelions','0.5% — Four-Leaf White Clover']
                    for w,h in [(1440,900),(390,844),(844,390)]:
                        page.set_viewport_size({'width':w,'height':h})
                        assert page.locator('#weekly-wheel-view').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        page.screenshot(path=str(tmp_path/f'wheel-{w}x{h}.png'))
                        page.locator('#weekly-wheel-back').click()
                        assert page.locator('#weekly-wheel-open').evaluate('(el)=>el===document.activeElement')
                        page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                        assert page.locator('[data-shop-panel="practice-room"]').evaluate('(el)=>el===document.activeElement')
                        result['geometry'].append({'viewport':[w,h],'targets':hit_targets(page,'#shop-hotspots')})
                        page.locator('[data-shop-panel="practice-room"]').focus();page.keyboard.press('Enter')
                        page.locator('#weekly-wheel-open').focus();page.keyboard.press('Enter');finished(page)
                    assert stored(database)==baseline
                    page.emulate_media(reduced_motion='no-preference')
                    # Hold real committed response, then deliver a second normal pointer click
                    # while the first action is in flight (no forced click).
                    held=[]
                    def hold(route):
                        response=route.fetch();assert response.status==200;held.append((route,response))
                    page.route('**/weekly/wheel/plays',hold)
                    pay=page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True)
                    pay.scroll_into_view_if_needed();box=pay.bounding_box()
                    assert pay.evaluate('(el)=>{const r=el.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el;}')
                    result['payment_hit_target']=box
                    pay.click()
                    wait_held(page,held)
                    page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
                    assert len(posts())==1
                    route,response=held.pop();route.fulfill(response=response);page.unroute('**/weekly/wheel/plays',hold)
                    confirmed(page,'400 Dandelions')
                    assert page.locator('.weekly-prize-wheel').get_attribute('data-outcome')=='dandelion'
                    assert float(page.locator('.weekly-prize-wheel').get_attribute('data-target-angle'))==pytest.approx(142.2,abs=1e-10)
                    assert '27 Pickled Carrot jars' in page.locator('#weekly-wheel-content').inner_text()
                    after=stored(database);new=[r for r in after['reward_grants'] if r not in baseline['reward_grants']]
                    assert sorted((r['reward_type'],r['amount']) for r in new)==[('dandelion',400),('pickled_carrot',-3)]
                    result['receipts']=new
                    clear(page);assert key(page) is None
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                    page.reload();ready(page)
                    # Native hydration restores the protected Dandelions; no local wallet writer.
                    assert int(page.locator('#credits-value').inner_text())==starting_credits+400
                    from test_weekly2_pantry_browser import open_pantry,closed
                    open_pantry(page);assert '27 Pickled Carrot jars' in page.locator('#weekly-pantry-content').inner_text()
                    page.locator('#weekly-pantry-close').click();closed(page)
                    page.locator('[data-shop-panel="gear"]').click();assert page.locator('[data-shop-panel-content="gear"]').is_visible()
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                    page.locator('#shop-hotspots a[href="/home"]').click();page.wait_for_url('**/home');ready(page)
                    from test_weekly2_caper_browser import open_caper
                    open_caper(page);page.locator('#weekly-caper-back').click();page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                    assert len(posts())==1
                elif scenario=='free':
                    assert '1 of 9 funded plays' in page.locator('#weekly-wheel-content').inner_text()
                    page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click();confirmed(page,'Spin Again');clear(page)
                    assert '0 of 9 funded plays' in page.locator('#weekly-wheel-content').inner_text()
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()');open_wheel(page)
                    assert len(posts())==1
                    page.get_by_role('button',name='SPIN AGAIN — FREE',exact=True).click();confirmed(page,'Spin Again');clear(page)
                    page.reload();ready(page);open_wheel(page);assert len(posts())==2
                    page.get_by_role('button',name='SPIN AGAIN — FREE',exact=True).click();confirmed(page,'400 Dandelions');clear(page)
                    assert page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).is_disabled()
                    after=stored(database);new=[r for r in after['reward_grants'] if r not in baseline['reward_grants']]
                    assert sorted((r['reward_type'],r['amount']) for r in new)==[('dandelion',400),('pickled_carrot',-3)]
                    assert len(after['weekly_wheel_plays'])==9
                    actual=next(r for r in after['weekly_wheel_plays'] if not r['id'].startswith('synthetic-'))
                    assert json.loads(actual['outcomes'])==['spin_again','spin_again','dandelion']
                    assert [r['body'].get('rotation') for r in posts()]==[None,2,3]
                    result['receipts']=new
                elif scenario=='lost':
                    def lose(route):
                        response=route.fetch();assert response.status==200;route.abort('connectionreset')
                    page.route('**/weekly/wheel/plays',lose)
                    page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click()
                    page.get_by_role('button',name='Recover saved action',exact=True).wait_for()
                    original=key(page);assert original
                    page.unroute('**/weekly/wheel/plays',lose)
                    page.reload();ready(page);open_wheel(page);confirmed(page,'Spin Again')
                    assert key(page)==original and len(posts())==2
                    assert posts()[0]['body']==posts()[1]['body'];clear(page)
                    page.route('**/weekly/wheel/plays/*/rotations',lose)
                    page.get_by_role('button',name='SPIN AGAIN — FREE',exact=True).click()
                    page.get_by_role('button',name='Recover saved action',exact=True).wait_for()
                    rotation_key=key(page);assert json.loads(rotation_key)['rotation']==2
                    page.unroute('**/weekly/wheel/plays/*/rotations',lose)
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()');open_wheel(page);confirmed(page,'Spin Again')
                    assert key(page)==rotation_key and len(posts())==3 # recovery GET proves index2 already saved
                    clear(page);page.get_by_role('button',name='SPIN AGAIN — FREE',exact=True).click();confirmed(page,'400 Dandelions')
                    after=stored(database);new=[r for r in after['reward_grants'] if r not in baseline['reward_grants']]
                    assert sorted((r['reward_type'],r['amount']) for r in new)==[('dandelion',400),('pickled_carrot',-3)]
                    result['receipts']=new
                elif scenario=='storage':
                    page.evaluate("""()=>{window.wheelDenied=0;window.wheelDeny='';
                      for(const [method,kind] of [['get','read'],['add','write'],['delete','remove']]){
                        const original=IDBObjectStore.prototype[method];
                        IDBObjectStore.prototype[method]=function(...args){
                          if(this.name==='pending-actions'&&this.transaction.db.name==='woodshed-prize-wheel-recovery'){
                            if(window.wheelDeny===kind){window.wheelDenied++;throw new DOMException('Synthetic exact Wheel storage denial','SecurityError');}
                            if(method==='add'&&window.wheelDeny==='abort'){
                              const request=original.apply(this,args);window.wheelDenied++;this.transaction.abort();return request;
                            }
                          }
                          return original.apply(this,args);
                        };
                      }
                      const open=IDBFactory.prototype.open;
                      IDBFactory.prototype.open=function(name,...args){
                        if(name==='woodshed-prize-wheel-recovery'&&window.wheelDeny==='open'){
                          window.wheelDenied++;throw new DOMException('Synthetic Wheel IndexedDB unavailable','SecurityError');
                        }
                        return open.call(this,name,...args);
                      };
                    }""")
                    for operation in ['read','write','abort','open']:
                        page.evaluate('(op)=>window.wheelDeny=op',operation)
                        page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click()
                        page.wait_for_function('() => /storage|retained/i.test(document.querySelector("#weekly-wheel-status").textContent) && document.querySelector("#weekly-wheel-content").getAttribute("aria-busy")==="false"')
                        finished(page);assert not posts() and stored(database)==baseline
                        page.evaluate('()=>window.wheelDeny=""')
                        page.get_by_role('button',name='Refresh Wheel',exact=True).first.click();finished(page)
                    assert page.evaluate('window.wheelDenied')>=4
                    page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click();confirmed(page,'400 Dandelions')
                    original=key(page);page.evaluate('()=>window.wheelDeny="remove"')
                    page.get_by_role('button',name='Continue',exact=True).click()
                    page.wait_for_function('document.querySelector("#weekly-wheel-status").textContent.includes("could not be cleared")')
                    assert key(page)==original and len(posts())==1
                    page.evaluate('()=>window.wheelDeny=""')
                    page.get_by_role('button',name='Recover saved action',exact=True).click();confirmed(page,'400 Dandelions')
                    assert key(page)==original and posts()[0]['body']==posts()[1]['body']
                    clear(page);assert key(page) is None
                    assert len(posts())==2
                    new=[r for r in stored(database)['reward_grants'] if r not in baseline['reward_grants']]
                    assert len(new)==2;result['receipts']=new
                elif scenario in ('race-a-first','race-b-first'):
                    page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click();confirmed(page,'400 Dandelions')
                    original=key(page)
                    original_fingerprint=hashlib.sha256(json.loads(original)['key'].encode()).hexdigest()
                    result['original_request_key_sha256']=original_fingerprint
                    other=context.new_page();other.set_default_timeout(15000);other.goto(origin+'/store');ready(other)
                    lock='woodshed:prize-wheel:request:WC-GUEST-A'
                    trace=[];responses=[]
                    result['race_trace']=trace
                    def observe_console(message):
                        if message.text.startswith('WHEEL_RACE '):
                            trace.append(json.loads(message.text.removeprefix('WHEEL_RACE ')))
                    context.on('console',observe_console)
                    context.on('response',lambda response:responses.append(response)
                        if response.url==origin+'/weekly/wheel/plays' else None)
                    # Observe actual native callbacks and actual storage operations.
                    # No extra reads, waits, callback replacement result or fault injection.
                    observer = """({name,original,tab})=>{
                      let serial=0;
                      const note=(event,extra={})=>console.debug('WHEEL_RACE '+JSON.stringify({tab,
                        serial:++serial,time:performance.timeOrigin+performance.now(),event,...extra}));
                      const identity=value=>value===null?'absent':value===original?'original':'different';
                      const request=navigator.locks.request.bind(navigator.locks);
                      navigator.locks.request=function(nameArg,...args){
                        if(nameArg!==name)return request(nameArg,...args);
                        const callback=args.pop();note('requested');
                        return request(nameArg,...args,function(handle){
                          note('acquired');
                          const result=callback(handle);
                          // Return the exact application promise without delaying it.
                          Promise.resolve(result).then(()=>note('callback-ended'),()=>note('callback-rejected'));
                          return result;
                        });
                      };
                      const seen=new WeakSet();
                      for(const operation of ['get','add','delete']){
                        const method=IDBObjectStore.prototype[operation];
                        IDBObjectStore.prototype[operation]=function(...args){
                          const request=method.apply(this,args);
                          if(this.name==='pending-actions'&&this.transaction.db.name==='woodshed-prize-wheel-recovery'){
                            const tx=this.transaction;
                            if(!seen.has(tx)){
                              seen.add(tx);tx.addEventListener('complete',()=>note('transaction-complete',{mode:tx.mode}));
                              tx.addEventListener('abort',()=>note('transaction-abort',{mode:tx.mode}));
                            }
                            if(operation==='get')request.addEventListener('success',()=>note('get',{value:identity(request.result??null),mode:tx.mode}));
                            else note(operation,operation==='add'?{value:identity(args[0]),mode:tx.mode}:{mode:tx.mode});
                          }
                          return request;
                        };
                      }
                    }"""
                    page.evaluate(observer,{'name':lock,'original':original,'tab':'A'})
                    other.evaluate(observer,{'name':lock,'original':original,'tab':'B'})
                    # Hold a real native lock only while arranging request submission.
                    # Queue the first request completely before the second UI action.
                    blocker=context.new_page();blocker.goto(origin+'/store');ready(blocker)
                    blocker.evaluate("""name=>{window.lockHeld=false;void navigator.locks.request(name,()=>new Promise(resolve=>{window.releaseWheelLock=resolve;window.lockHeld=true;}));}""",lock)
                    blocker.wait_for_function('window.lockHeld')
                    def native_queue(pending,held=1):
                        blocker.evaluate("""({name,pending,held})=>{
                          window.wheelQueueProof=null;
                          async function observe(){
                            const q=await navigator.locks.query();
                            const proof={held:q.held.filter(x=>x.name===name),pending:q.pending.filter(x=>x.name===name)};
                            if(proof.held.length===held && proof.pending.length===pending)window.wheelQueueProof=proof;
                            else requestAnimationFrame(observe);
                          }
                          void observe();
                        }""",{'name':lock,'pending':pending,'held':held})
                        blocker.wait_for_function('window.wheelQueueProof!==null')
                        return blocker.evaluate('window.wheelQueueProof')
                    def a_continue():page.get_by_role('button',name='Continue',exact=True).click()
                    def b_recover():
                        other.locator('[data-shop-panel="practice-room"]').click();other.locator('#weekly-wheel-open').click()
                    first,second=(a_continue,b_recover) if scenario=='race-a-first' else (b_recover,a_continue)
                    first();result['first_queued']=native_queue(1)
                    second();result['both_queued']=native_queue(2)
                    before=stored(database);count=len(posts())
                    blocker.evaluate('()=>window.releaseWheelLock()')
                    result['locks_after_recovery']=native_queue(0,0)
                    finished(other);finished(page)
                    acquisitions=[e['tab'] for e in trace if e['event']=='acquired']
                    result['observed_acquisition_order']=acquisitions
                    expected=['A','B'] if scenario=='race-a-first' else ['B','A']
                    assert acquisitions==expected,trace
                    b_acquire=next(i for i,e in enumerate(trace) if e['tab']=='B' and e['event']=='acquired')
                    b_read=next(e for e in trace[b_acquire+1:] if e['tab']=='B' and e['event']=='get')
                    # IndexedDB reads follow committed transitions. The original
                    # localStorage failure remains historical evidence, not a pass.
                    assert b_read['value']==('absent' if scenario=='race-a-first' else 'original'),trace
                    assert not any(e['event']=='add' for e in trace),trace
                    if scenario=='race-a-first':
                        assert 'pending action changed' in other.locator('#weekly-wheel-status').inner_text()
                        assert len(posts())==count
                    else:
                        assert 'Confirmed: 400 Dandelions' in other.locator('#weekly-wheel-status').inner_text()
                        assert len(posts())==count+1
                    assert 'Checking saved action' not in other.locator('#weekly-wheel-status').inner_text()
                    assert 'Saved result: 400 Dandelions' in other.locator('#weekly-wheel-content').inner_text()
                    assert all(r['body']=={'request_key':original_fingerprint} for r in posts())
                    assert key(other) is None and stored(database)==before
                    plays=before['weekly_wheel_plays'];assert len(plays)==1
                    assert plays[0]['profile_id']==own_id and plays[0]['outcomes']=='["dandelion"]'
                    receipts=[r for r in before['reward_grants'] if r not in baseline['reward_grants']]
                    assert len(receipts)==2
                    assert {(r['reward_type'],r['amount']) for r in receipts}=={('pickled_carrot',-3),('dandelion',400)}
                    assert all(r['profile_id']==own_id for r in receipts)
                    result['recovery_receipts']=receipts
                    result['recovered_play_id']=plays[0]['id']
                    result['recovery_responses']=[{'status':r.status,'play_id':r.json()['play']['id']} for r in responses]
                    assert all(r['status']==200 and r['play_id']==plays[0]['id'] for r in result['recovery_responses'])
                    # Refresh reads server truth after the completed clear. It cannot
                    # buy automatically. Native document reload is covered separately.
                    posts_before_refresh=len(posts())
                    other.get_by_role('button',name='Refresh Wheel',exact=True).click();finished(other)
                    assert len(posts())==posts_before_refresh
                    assert 'Saved result: 400 Dandelions' in other.locator('#weekly-wheel-content').inner_text()
                    assert stored(database)==before
                    result['pre_second_explicit_click_posts']=len(posts())
                    other.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click();confirmed(other,'400 Dandelions')
                    assert key(other)!=original and len(posts())==posts_before_refresh+1
                    assert posts()[-1]['body']['request_key']!=original_fingerprint
                    new=[r for r in stored(database)['reward_grants'] if r not in baseline['reward_grants']]
                    assert len(new)==4 and sum(r['amount'] for r in new if r['reward_type']=='pickled_carrot')==-6
                    assert sum(r['amount'] for r in new if r['reward_type']=='dandelion')==800
                    assert len(stored(database)['weekly_wheel_plays'])==2
                    result['receipts']=new
                    clear(other);result['final_native_locks']=native_queue(0,0)
                elif scenario=='newer':
                    # Independent same-origin documents, separate IDB connections;
                    # real shared storage and native locks, not a JavaScript Map.
                    other=context.new_page();other.set_default_timeout(15000);other.goto(origin+'/store');ready(other)
                    page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click();confirmed(page,'400 Dandelions')
                    original=key(page);open_wheel(other);confirmed(other,'400 Dandelions')
                    assert key(other)==original and posts()[0]['body']==posts()[1]['body']
                    clear(other);assert key(other) is None
                    events=[];result['atomic_trace']=events
                    def observe(message):
                        if message.text.startswith('ATOMIC_WHEEL '):events.append(json.loads(message.text.removeprefix('ATOMIC_WHEEL ')))
                    context.on('console',observe)
                    observer="""({tab,original})=>{
                      const seen=new WeakMap();let counter=0;
                      const note=(event,extra={})=>console.debug('ATOMIC_WHEEL '+JSON.stringify({tab,event,time:performance.timeOrigin+performance.now(),...extra}));
                      for(const operation of ['get','add','delete']){
                        const method=IDBObjectStore.prototype[operation];
                        IDBObjectStore.prototype[operation]=function(...args){
                          const request=method.apply(this,args);
                          if(this.name==='pending-actions'&&this.transaction.db.name==='woodshed-prize-wheel-recovery'){
                            const tx=this.transaction;let id=seen.get(tx);
                            if(!id){id=++counter;seen.set(tx,id);tx.addEventListener('complete',()=>note('commit',{id,mode:tx.mode}));}
                            if(operation==='get')request.addEventListener('success',()=>note('get',{id,mode:tx.mode,value:request.result==null?'absent':request.result===original?'K':'K2'}));
                            else note(operation,{id,mode:tx.mode});
                          }
                          return request;
                        };
                      }
                    }"""
                    for tab,document in [('A',page),('B',other)]:document.evaluate(observer,{'tab':tab,'original':original})
                    # B's distinct explicit action commits K2 and a genuine prize.
                    # Only its response is lost; its native lock is then released.
                    def lose(route):
                        response=route.fetch();assert response.status==200;route.abort('connectionreset')
                    other.route('**/weekly/wheel/plays',lose)
                    other.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click()
                    other.get_by_role('button',name='Recover saved action',exact=True).wait_for();finished(other)
                    replacement=key(other);assert replacement and replacement!=original
                    before=stored(database);assert len(before['weekly_wheel_plays'])==2
                    mark=len(events);result['before_old_continue_trace_count']=mark
                    page.get_by_role('button',name='Continue',exact=True).click()
                    page.wait_for_function('document.querySelector("#weekly-wheel-status").textContent.includes("action changed")');finished(page)
                    assert key(page)==replacement and key(other)==replacement
                    assert stored(database)==before
                    old_clear=events[mark:]
                    assert any(e['tab']=='A' and e['event']=='get' and e['mode']=='readwrite' and e['value']=='K2' for e in old_clear)
                    assert not any(e['tab']=='A' and e['event']=='delete' for e in old_clear)
                    assert any(e['tab']=='B' and e['event']=='add' for e in events[:mark])
                    assert any(e['tab']=='B' and e['event']=='commit' and e['mode']=='readwrite' for e in events[:mark])
                    assert len(posts())==3
                    other.unroute('**/weekly/wheel/plays',lose)
                    other.keyboard.press('Escape');other.wait_for_function('!WWSurfaces.current()')
                    open_wheel(other);confirmed(other,'400 Dandelions')
                    assert key(other)==replacement and posts()[-1]['body']==posts()[-2]['body']
                    assert len(posts())==4 and stored(database)==before
                    assert 'Checking saved action' not in page.locator('#weekly-wheel-status').inner_text()
                    clear(other);assert key(other) is None
                    new=[r for r in stored(database)['reward_grants'] if r not in baseline['reward_grants']]
                    assert len(new)==4
                    assert sum(r['amount'] for r in new if r['reward_type']=='pickled_carrot')==-6
                    assert sum(r['amount'] for r in new if r['reward_type']=='dandelion')==800
                    result['receipts']=new
                    result['request_fingerprints']=[hashlib.sha256(json.loads(raw)['key'].encode()).hexdigest() for raw in [original,replacement]]
                    result['locks_after']=other.evaluate("""async()=>{
                      const q=await navigator.locks.query();return {held:q.held.filter(x=>x.name.startsWith('woodshed:prize-wheel:')),
                        pending:q.pending.filter(x=>x.name.startsWith('woodshed:prize-wheel:'))};
                    }""")
                    assert result['locks_after']=={'held':[],'pending':[]}
                else:
                    held=[];hold_requests=True
                    def hold(route):
                        if not hold_requests:
                            route.continue_();return
                        response=route.fetch();assert response.status==200;held.append((route,response))
                    page.route('**/weekly/wheel',hold)
                    page.get_by_role('button',name='Refresh Wheel',exact=True).click();wait_held(page,held)
                    page.keyboard.press('Escape');page.wait_for_function('!WWSurfaces.current()')
                    hold_requests=False
                    open_wheel(page)
                    before=page.locator('#weekly-wheel-content').inner_text()
                    route,response=held.pop();route.fulfill(response=response)
                    assert page.locator('#weekly-wheel-content').inner_text()==before
                    page.unroute('**/weekly/wheel',hold)
                    hold_requests=True
                    page.route('**/weekly/wheel/plays',hold)
                    page.get_by_role('button',name='Exchange 3 jars — PLAY',exact=True).click();wait_held(page,held)
                    original=key(page);assert original
                    other=context.new_page();other.set_default_timeout(15000);login(other,origin,'B');ready(other);shop(other)
                    page.wait_for_function('!WWSessionBoundary.isCurrent()')
                    baseline_b=stored(database)
                    route,response=held.pop();route.fulfill(response=response)
                    assert page.locator('#weekly-wheel-content').inner_text()==''
                    open_wheel(other);assert '0 Pickled Carrot jars' in other.locator('#weekly-wheel-content').inner_text()
                    assert 'Saved result' not in other.locator('#weekly-wheel-content').inner_text() and key(other) is None
                    assert stored(database)==baseline_b
                    assert len(posts())==1
            assert not result['errors'],result['errors']
            final=stored(database)
            result.update(result='PASS',final_rows=final,post_count=len(posts()))
            # All non-economy histories are preserved; native login/sync effects
            # on profiles/state are verified separately above and not labelled prizes.
            for table in baseline:
                if table not in ('woodchuck_profiles','woodchuck_states','reward_grants','weekly_wheel_plays'):
                    assert final[table]==baseline[table],table
            browser.close()
    finally:
        # Request-key values never enter artifacts; durable rows are redacted too.
        for rows in (result.get('baseline_rows',{}),result.get('final_rows',{})):
            for play in rows.get('weekly_wheel_plays',[]):
                play['request_key_sha256']=hashlib.sha256(play.pop('request_key').encode()).hexdigest()
        (tmp_path/'focused-results.json').write_text(json.dumps(result,indent=2)+'\n')
