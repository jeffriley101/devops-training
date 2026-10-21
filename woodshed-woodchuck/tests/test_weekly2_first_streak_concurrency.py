"""Current request authorization and observed PostgreSQL milestone contention."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
import re

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, text

from app.main import app
from app.models import WoodchuckProfile, WoodchuckState, PracticeChart, RewardGrant
from app.feature_access import FEATURES, Feature
from tests.test_weekly2_first_streak import (db, streak_db, seed, seven, chart, grants,
    rows, NOW, END, START, SOURCE, datetime, timedelta, timezone, weekly, apply_daily_login)
from tests.test_weekly2_plans import observe_block
from tests.test_weekly2_thirds import arcade_factory


def client(pid=1, wid=None):
    c=TestClient(app)
    assert c.post('/account/login',data={'woodchuck_id':wid or f'WC-STREAK-{pid}','pin':'2468'}).status_code==200
    return c


def post(c, route='book', key='seventh', *, day=END):
    if route=='login':return c.post('/account/login-streak')
    if route=='credentials':return c.post('/account/login',data={'woodchuck_id':'WC-STREAK-1','pin':'2468'})
    if route=='family':
        page=c.get('/family/practice');assert page.status_code==200
        csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
        return c.post('/family/practice',data={'csrf':csrf,'practice_date':str(day),'minutes':'5','submission_key':key})
    if route=='pristine':return c.post('/practice-charts/pristine',json={'detected_playing_seconds':300,'submission_key':key,'include_contests':False,'include_team_contests':False})
    return c.post('/practice-charts',json={'practice_date':str(day),'minutes':5,'submission_key':key,'include_contests':False,'include_team_contests':False})


def snapshot(f):
    with f() as s:return rows(s)


def milestone(f,pid=1):
    with f() as s:return [(r.source_key,r.reward_type,r.amount) for r in grants(s,pid)]


@pytest.mark.parametrize('route',['book','pristine','family','login','credentials'])
def test_current_http_paths_atomic_receipt_retry_and_owner(streak_db,route,record_property):
    f=streak_db
    with client() as c:
        seed(f,seven() if route in ('login','credentials') else seven()[:-1])
        before=snapshot(f)
        r=post(c,route);assert r.status_code in (200,201),r.text
        assert milestone(f)==[(SOURCE,'dandelion',300)]
        after=snapshot(f)
        allowed={'reward_grants','woodchuck_states'}|({'practice_charts'} if route not in ('login','credentials') else set())
        for name in before:
            if name not in allowed:assert before[name]==after[name],name
        with f() as s:
            assert s.get(WoodchuckState,1).state_json['progress']['credits']==341
            assert s.get(WoodchuckState,2).state_json['progress']['credits']==40
        r=post(c,route);assert r.status_code in (200,201),r.text
        assert snapshot(f)==after
        record_property('receipt',json.dumps(milestone(f)))


def test_unrelated_and_historical_pants_and_two_instruments(streak_db):
    f=streak_db;history=seven()
    for offset,item in enumerate(history):item.instrument='Flute' if offset%2 else 'Tuba'
    seed(f,history)
    with f.begin() as s:
        s.add(RewardGrant(profile_id=1,source_key='synthetic-historical-other-pants',reward_type='pants_choice',amount=1))
        state=s.get(WoodchuckState,1)
        state.state_json=dict(state.state_json,appearance={'pants':'historical-equipped'},
            legacy_pants={'owned':['historical-owned'],'pending':['historical-pending']})
    from app.weekly_rewards import award_first_practice_streak
    with f.begin() as s:
        before=rows(s);assert award_first_practice_streak(s,1,now=NOW);s.flush();after=rows(s)
        for name in before:
            if name not in ('reward_grants','woodchuck_states'):assert before[name]==after[name]
        assert s.get(WoodchuckState,1).state_json['appearance']=={'pants':'historical-equipped'}
        assert s.get(WoodchuckState,1).state_json['legacy_pants']=={'owned':['historical-owned'],'pending':['historical-pending']}
        assert len(s.scalars(select(RewardGrant).where(RewardGrant.reward_type=='pants_choice')).all())==1
        assert not award_first_practice_streak(s,1,now=NOW)


@pytest.mark.parametrize('route',['book','pristine','login'])
def test_foreign_origin_account_revoked_and_gate_off(streak_db,monkeypatch,route):
    f=streak_db
    with client() as c:
        seed(f,seven() if route=='login' else seven()[:-1]);before=snapshot(f)
        url='/account/login-streak' if route=='login' else '/practice-charts'+('/pristine' if route=='pristine' else '')
        body={} if route=='login' else {'submission_key':'denied','detected_playing_seconds':300} if route=='pristine' else {'submission_key':'denied','minutes':5,'practice_date':str(END)}
        assert c.post(url,json=body,headers={'Origin':'https://foreign.invalid'}).status_code==403
        assert c.post(url,json=body,headers={'x-woodshed-account':'WC-STREAK-2'}).status_code==401
        assert snapshot(f)==before
        monkeypatch.setitem(FEATURES,'weekly_pants',Feature(False,'open'))
        assert post(c,route).status_code in (200,201)
        assert milestone(f)==[]
        cookie=c.cookies.get('session');assert c.post('/account/logout').status_code==200
        before=snapshot(f)
        with TestClient(app) as stale:
            stale.cookies.set('session',cookie)
            assert stale.post(url,json=body).status_code==401
        assert snapshot(f)==before


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('pair',[('book','book'),('book','login'),('login','login')])
def test_overlapping_milestone_requests(streak_db,pair,record_property):
    f=streak_db;a,b=client(),client()
    seed(f,seven() if pair[0]=='login' else seven()[:-1]);before=snapshot(f)
    held,release=Event(),Event();pids={}
    def hold(s):
        if not held.is_set():
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15)
    def query(c,cur,statement,*args):
        if held.is_set() and 'FOR UPDATE' in statement:
            pid=c.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(f.class_,'before_commit',hold);event.listen(f.kw['bind'],'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(post,a,pair[0]);assert held.wait(10)
            second=pool.submit(post,b,pair[1])
            try:observe_block(f.kw['bind'],pids,record_property,'streak-'+ '-'.join(pair))
            finally:release.set()
            assert first.result(timeout=15).status_code in (200,201)
            assert second.result(timeout=15).status_code in (200,201)
    finally:
        release.set();event.remove(f.class_,'before_commit',hold);event.remove(f.kw['bind'],'before_cursor_execute',query)
    assert milestone(f)==[(SOURCE,'dandelion',300)]
    with f() as s:assert s.get(WoodchuckState,1).state_json['progress']['credits']==341
    after=snapshot(f)
    for name in before:
        if name not in ('practice_charts','reward_grants','woodchuck_states'):assert before[name]==after[name]
    # A lost completed response, followed by the same request, cannot award again.
    assert post(a,pair[0]).status_code in (200,201) and snapshot(f)==after
    a.close();b.close()


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('route,change',[('book','version'),('pristine','nonce'),('family','version'),('login','consent'),('credentials','version')])
def test_authorization_changes_while_waiting(streak_db,route,change,record_property):
    from app.age_models import AccountPrivacy
    f=streak_db;c=client();seed(f,seven() if route in ('login','credentials') else seven()[:-1])
    before=snapshot(f);held,release=Event(),Event();pids={}
    # Family GET does not take a profile lock while the holder runs; submission does.
    def holder():
        with f() as s:
            p=s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            if change=='version':p.session_version+=1
            if change=='consent':s.get(AccountPrivacy,1).age_band='under13'
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cur,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(f.kw['bind'],'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(holder);assert held.wait(10)
            second=pool.submit(post,c,route)
            try:
                observe_block(f.kw['bind'],pids,record_property,'streak-'+route+'-'+change)
                if change=='nonce':
                    with TestClient(app) as logout:
                        logout.cookies.update(c.cookies);assert logout.post('/account/logout').status_code==200
            finally:release.set()
            first.result(timeout=15);r=second.result(timeout=15)
            assert r.status_code==(403 if change=='consent' else 401),r.text
    finally:release.set();event.remove(f.kw['bind'],'before_cursor_execute',query);c.close()
    assert milestone(f)==[]
    after=snapshot(f)
    for name in before:
        if name not in ('woodchuck_profiles','account_privacy_rules','revoked_browser_sessions'):assert before[name]==after[name],name


def test_authorized_child_then_withdrawn_child(arcade_factory,monkeypatch):
    from app import tester_enrollments as testers, child_authorization as consent, login_streaks, practice_charts
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    f=arcade_factory;monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    monkeypatch.setitem(FEATURES,'weekly_pants',Feature(True,'open'))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    for module in (weekly,login_streaks,practice_charts):monkeypatch.setattr(module,'datetime',Clock)
    with f() as s:p=s.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    history=seven(pid=pid);seed(f,history[:-1])
    with client(wid=wid) as c:
        assert post(c,'family').status_code==200
        assert milestone(f,pid)==[(SOURCE,'dandelion',300)]
        with f.begin() as s:consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
        before=snapshot(f)
        assert c.post('/account/login-streak').status_code==401
        after=snapshot(f)
        for name in before:
            if name!='revoked_browser_sessions':assert before[name]==after[name]


def test_default_off_gate_and_monday_unchanged():
    assert FEATURES['weekly_pants']==Feature(False,'open')
    assert FEATURES['weekly_monday_bonus']==Feature(False,'open')


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_actual_child_consent_withdrawn_during_wait(arcade_factory,monkeypatch,record_property):
    from app import tester_enrollments as testers, child_authorization as consent, login_streaks
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    f=arcade_factory;monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    monkeypatch.setitem(FEATURES,'weekly_pants',Feature(True,'open'))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    for module in (weekly,login_streaks):monkeypatch.setattr(module,'datetime',Clock)
    with f() as s:p=s.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    c=client(wid=wid);seed(f,seven(pid=pid));before=snapshot(f)
    held,release=Event(),Event();pids={}
    def holder():
        with f() as s:
            s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==pid).with_for_update())
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cur,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            backend=cn.connection.driver_connection.info.backend_pid
            if backend!=pids['first']:pids['second']=backend
    event.listen(f.kw['bind'],'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(holder);assert held.wait(10)
            second=pool.submit(post,c,'login')
            try:observe_block(f.kw['bind'],pids,record_property,'streak-actual-child-withdrawal')
            finally:release.set()
            first.result(timeout=15);response=second.result(timeout=15)
            assert response.status_code==403,response.text
    finally:release.set();event.remove(f.kw['bind'],'before_cursor_execute',query);c.close()
    assert milestone(f,pid)==[]
    after=snapshot(f)
    for name in before:
        if name not in ('woodchuck_profiles','child_consent_evidence','revoked_browser_sessions'):
            assert before[name]==after[name],name
