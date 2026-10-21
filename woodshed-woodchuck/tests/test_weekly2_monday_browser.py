"""Normal student entry; server clock controlled in disposable test runtime only."""
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright
import pytest
from test_weekly2_game_mode_entry_browser import login


def clock_source(path):
    # Test-owned server clock file, not an HTTP endpoint or browser clock.
    return ("\nfrom app import weekly\nfrom datetime import datetime\nfrom pathlib import Path\n"
            "_original_utc=weekly.utc_instant\n"
            f"weekly.utc_instant=lambda value=None:_original_utc(value or datetime.fromisoformat(Path({str(path)!r}).read_text()))\n")


def saved(database):
    with sqlite3.connect(database) as c:
        return {
            'grants':c.execute("SELECT profile_id,source_key,reward_type,amount FROM reward_grants ORDER BY id").fetchall(),
            'states':[(p,json.loads(s),r) for p,s,r in c.execute('SELECT profile_id,state_json,revision FROM woodchuck_states ORDER BY profile_id')],
            'profiles':c.execute('SELECT id,woodchuck_id,game_mode,level FROM woodchuck_profiles ORDER BY id').fetchall(),
        }


def monday_rows(snapshot):
    return [r for r in snapshot['grants'] if r[1].startswith('weekly-monday:')]


@pytest.fixture
def site(tmp_path, monkeypatch, request):
    import test_overhaul_profile_saves_browser as harness
    root=Path(__file__).resolve().parents[1]
    migration=f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(root/'alembic.ini')!r}),'w25weekly004')"
    source=harness.SERVER.replace('Base.metadata.create_all(engine)',migration)
    enabled=request.node.callspec.params['scenario']!='gate'
    clock=tmp_path/'monday-clock.txt'
    clock.write_text('2026-10-19T09:00:00-05:00' if request.node.callspec.params['scenario']=='window' else '2026-10-21T12:00:00-05:00')
    source+=f"\nfrom app.feature_access import FEATURES,Feature\nFEATURES['weekly_monday_bonus']=Feature({enabled!r},'open')\n"+clock_source(clock)
    monkeypatch.setattr(harness,'SERVER',source)
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.fixture
def output(tmp_path):
    return tmp_path


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
@pytest.mark.parametrize('scenario',['normal','private','lost','gate','window'])
def test_monday_entry(site, output, browser_name, scenario):
    assert browser_name in ('chrome','firefox') and scenario in ('normal','private','lost','gate','window')
    origin,database=site
    result={'browser':browser_name,'scenario':scenario,'requests':[],'page_errors':[]}
    try:
        with sync_playwright() as pw:
            b=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
            result['version']=b.version
            context=b.new_context(base_url=origin,reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
            page.on('request',lambda r:result['requests'].append({'path':r.url.removeprefix(origin),'method':r.method}))
            initial=saved(database);result['initial']=initial
            if scenario=='normal':
                login(page,origin)
                page.wait_for_function('() => window.WWAccountSync && window.WWSessionBoundary')
                page.evaluate('async()=>{await WWSessionBoundary.ready;await WWAccountSync.syncNow();}')
                after=saved(database);assert monday_rows(after)==[(1,'weekly-monday:2026-10-19','dandelion',75)]
                daily=[r for r in after['grants'] if r[1].startswith('login-streak:') and r[0]==1]
                assert len(daily)==1 and daily[0][3]==1
                assert after['states'][0][1]['progress']['credits']==initial['states'][0][1]['progress']['credits']+76
                for path in ('/store','/home','/family/practice','/store'):
                    assert page.goto(origin+path).status==200
                page.reload()
                result['after']=saved(database);assert result['after']['grants']==after['grants']
                assert result['after']['states'][0][1]['progress']['credits']==after['states'][0][1]['progress']['credits']
            else:
                # Authenticate first without navigating to a qualifying page.
                response=context.request.post(origin+'/account/login',form={'woodchuck_id':'WC-GUEST-A','pin':'2468'},headers={'Origin':origin})
                assert response.status==200
                base=saved(database);assert monday_rows(base)==[]
                result['post_login']=base
                if scenario=='gate':
                    for path in ('/home','/store','/family/practice'):
                        assert page.goto(origin+path).status==200
                    assert monday_rows(saved(database))==[]
                    assert saved(database)['states'][0][1]['progress']['credits']==base['states'][0][1]['progress']['credits']
                elif scenario=='lost':
                    def lose(route):
                        response=route.fetch();assert response.status==200
                        result['committed_before_abort']=saved(database)
                        assert len(monday_rows(result['committed_before_abort']))==1
                        route.abort()
                    page.route('**/store',lose,times=1)
                    from playwright.sync_api import Error
                    with pytest.raises(Error):page.goto(origin+'/store')
                    # The aborted document can still be navigating to the browser's
                    # error page. Recover in a fresh tab of the SAME signed context.
                    page.close()
                    page=context.new_page();page.set_default_timeout(15000)
                    page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
                    assert page.goto(origin+'/store').status==200
                    assert saved(database)['grants']==result['committed_before_abort']['grants']
                    assert saved(database)['states'][0][1]['progress']['credits']==result['committed_before_abort']['states'][0][1]['progress']['credits']
                elif scenario=='window':
                    visits=[('2026-10-19T09:00:00-05:00',0,'/home'),
                            ('2026-10-19T13:29:59-05:00',0,'/store'),
                            ('2026-10-19T13:30:00-05:00',1,'/store'),
                            ('2026-10-21T12:00:00-05:00',1,'/family/practice'),
                            ('2026-10-25T23:59:59-05:00',1,'/home'),
                            ('2026-10-26T00:00:00-05:00',1,'/store'),
                            ('2026-10-26T13:29:00-05:00',1,'/family/practice'),
                            ('2026-10-26T13:30:00-05:00',2,'/home')]
                    result['visits']=[]
                    for instant,count,path in visits:
                        (Path(database).parent/'monday-clock.txt').write_text(instant)
                        assert page.goto(origin+path).status==200
                        after=saved(database)
                        assert monday_rows(after)==[(1,'weekly-monday:'+w,'dandelion',75)
                            for w in ('2026-10-19','2026-10-26')[:count]]
                        assert after['states'][0][1]['progress']['credits']==base['states'][0][1]['progress']['credits']+75*count
                        assert after['states'][1]==base['states'][1]
                        assert after['profiles']==base['profiles']
                        result['visits'].append({'instant':instant,'path':path,'snapshot':after})
                elif scenario=='private':
                    assert page.goto(origin+'/family/practice').status==200
                    after=saved(database);assert monday_rows(after)==[(1,'weekly-monday:2026-10-19','dandelion',75)]
                    assert after['states'][0][1]['progress']['credits']==base['states'][0][1]['progress']['credits']+75
                    assert page.reload().status==200
                    assert saved(database)['grants']==after['grants']
                result['after']=saved(database)
            assert not result['page_errors'],result['page_errors']
            assert all(p[2] is None for p in result['after']['profiles'])
            page.screenshot(path=str(output/'opening.png'))
            context.close();b.close()
        result['result']='PASS'
    except BaseException as e:
        result['result']='FAIL';result['error']=repr(e);raise
    finally:(output/'focused-results.json').write_text(json.dumps(result,indent=2,default=str))
