"""Real private-chart form submissions and durable first-streak receipts."""
from pathlib import Path
import json
import sqlite3

from playwright.sync_api import sync_playwright
import pytest


def server_suffix(scenario):
    assert scenario in ('timely','late','gate')
    return f'''
from app import weekly, login_streaks, practice_charts, practice_chart_routes
from app.feature_access import FEATURES, Feature
from app.models import PracticeChart
from datetime import datetime, date, timedelta, timezone
FEATURES['weekly_pants']=Feature({scenario!='gate'!r}, 'open')
class StreakClock(datetime):
    @classmethod
    def now(cls,tz=None):
        value=datetime(2026,10,{12 if scenario=='late' else 11},18,tzinfo=weekly.CENTRAL)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)
for module in (weekly,login_streaks,practice_charts,practice_chart_routes):module.datetime=StreakClock
with SessionLocal() as session:
    for offset in range(6):
        day=date(2026,10,5)+timedelta(days=offset)
        session.add(PracticeChart(profile_id=1,practice_date=day,minutes=5,instrument='Flute',
            source='p-book',created_at=datetime.combine(day,datetime.min.time(),tzinfo=weekly.CENTRAL)+timedelta(hours=12),
            include_contests=False,include_team_contests=False,credits_awarded=0))
    session.commit()
'''


def snapshot(database):
    with sqlite3.connect(database) as c:
        names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n:c.execute('SELECT * FROM "'+n+'" ORDER BY rowid').fetchall() for n in names}


def reward_state(database):
    with sqlite3.connect(database) as c:
        grants=c.execute('SELECT profile_id,source_key,reward_type,amount FROM reward_grants ORDER BY id').fetchall()
        states=[(pid,json.loads(state),revision) for pid,state,revision in c.execute('SELECT profile_id,state_json,revision FROM woodchuck_states ORDER BY profile_id')]
        charts=c.execute('SELECT profile_id,practice_date,minutes,created_at,source FROM practice_charts ORDER BY id').fetchall()
        return {'grants':grants,'states':states,'charts':charts}


@pytest.fixture
def site(tmp_path,monkeypatch,request):
    import test_overhaul_profile_saves_browser as harness
    root=Path(__file__).resolve().parents[1]
    migrate=f"from alembic import command\nfrom alembic.config import Config\ncommand.upgrade(Config({str(root/'alembic.ini')!r}),'w25weekly004')"
    source=harness.SERVER.replace('Base.metadata.create_all(engine)',migrate)+server_suffix(request.node.callspec.params['scenario'])
    monkeypatch.setattr(harness,'SERVER',source)
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.fixture
def output(tmp_path):return tmp_path


@pytest.mark.parametrize('browser_name',['chrome','firefox'])
@pytest.mark.parametrize('scenario',['timely','late','gate'])
def test_private_chart_streak(site,output,browser_name,scenario):
    origin,database=site;result={'browser':browser_name,'scenario':scenario,'page_errors':[],'requests':[]}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name=='chrome' else pw.firefox.launch()
            result['version']=browser.version
            context=browser.new_context(base_url=origin,reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda error:result['page_errors'].append(str(error)))
            page.on('request',lambda r:result['requests'].append({'method':r.method,'path':r.url.removeprefix(origin)}))
            assert context.request.post(origin+'/account/login',form={'woodchuck_id':'WC-GUEST-A','pin':'2468'},headers={'Origin':origin}).status==200
            before=snapshot(database);base=reward_state(database);result['post_login']=base
            assert not any(r[1]=='first-seven-practice-dates' for r in base['grants'])
            assert page.goto(origin+'/family/practice').status==200
            page.locator('[name="practice_date"]').fill('2026-10-11')
            page.locator('[name="minutes"]').fill('5')
            page.locator('[name="note"]').fill('Synthetic WL2-15b browser chart')
            with page.expect_navigation():page.get_by_role('button',name='Save private chart',exact=True).click()
            assert page.locator('article').filter(has_text='Synthetic WL2-15b browser chart').count()==1
            after=snapshot(database);saved=reward_state(database);result['after']=saved
            expected=[(1,'first-seven-practice-dates','dandelion',300)] if scenario=='timely' else []
            assert [r for r in saved['grants'] if r[1]=='first-seven-practice-dates']==expected
            assert saved['states'][0][1]['progress']['credits']==base['states'][0][1]['progress']['credits']+(300 if expected else 0)
            assert saved['states'][0][2]==base['states'][0][2]+bool(expected)
            assert saved['states'][1]==base['states'][1]
            assert len(saved['charts'])==7 and saved['charts'][-1][1]=='2026-10-11'
            for name in before:
                if name not in ('practice_charts','reward_grants','woodchuck_states'):assert before[name]==after[name],name
            assert page.reload().status==200
            assert context.request.post(origin+'/account/login-streak',headers={'Origin':origin}).status==200
            assert snapshot(database)==after
            assert not result['page_errors'],result['page_errors']
            page.screenshot(path=str(output/'chart-streak.png'))
            context.close();browser.close()
        result['result']='PASS'
    except BaseException as error:
        result['result']='FAIL';result['error']=repr(error);raise
    finally:(output/'focused-results.json').write_text(json.dumps(result,indent=2,default=str))
