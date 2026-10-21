"""Accepted Pantry contracts on fresh migrated storage; reads never settle rewards."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import models as m, weekly_plans as plans
from app.feature_access import FEATURES, Feature
from app.weekly import CENTRAL, utc_instant
from tests.test_weekly2_plans import runtime, db, config, FOCI, at, choose, chart, rows
from tests.test_weekly2_tasks import plan_db
from tests.test_weekly2_thirds import assert_student_safe, arcade_factory

PATH = '/weekly/pantry'


@pytest.fixture
def pantry_db(plan_db, monkeypatch):
    monkeypatch.setitem(FEATURES, 'weekly_pantry', Feature(True, 'open'))
    return plan_db


def snapshot(factory):
    return rows(factory.engine)


def test_no_plan_progress_and_repeated_reads_are_readonly(pantry_db):
    f=pantry_db
    chart(f, date(2026,10,12), at(), minutes=20)
    with f.client() as c:
        before=snapshot(f)
        for _ in range(3):
            r=c.get(PATH);assert r.status_code==200,r.text
            assert r.headers['cache-control']=='no-store'
            body=r.json();assert body['targets'] is None
            assert body['progress']=={'logging_days':1,'practice_seconds':1200,'practice_days':1}
            assert_student_safe(body)
            assert 'PRIVATE' not in str(body) and 'recent_results' not in str(body)
        assert snapshot(f)==before


@pytest.mark.parametrize('mode,days,seconds',[('Beginner',3,1800),('Challenge',4,3600),('Performance',5,5400)])
def test_frozen_targets_and_over_goal_actual_progress(pantry_db,mode,days,seconds):
    f=pantry_db
    chart(f,date(2026,10,12),at(),minutes=100)
    with f.client() as c:
        choose(c,mode)
        assert c.post('/weekly/plan',json={'focus':FOCI[0]}).status_code==200
        choose(c,'Beginner' if mode!='Beginner' else 'Performance')
        before=snapshot(f)
        body=c.get(PATH).json()
        assert body['targets']=={'logging_days':days,'practice_seconds':seconds}
        assert body['progress']=={'logging_days':1,'practice_seconds':6000,'practice_days':1}
        assert c.get(PATH).json()==body and snapshot(f)==before
        assert_student_safe(body)


def test_submission_days_practice_dates_book_and_pristine_seconds(pantry_db):
    f=pantry_db;f.clock[0]=at(13,16)
    chart(f,date(2026,10,11),at(12),minutes=10)
    chart(f,date(2026,10,12),at(13),minutes=20)
    chart(f,date(2026,10,12),at(13),minutes=1,seconds=118)
    chart(f,date(2026,10,12),at(13),minutes=3)
    chart(f,date(2026,10,14),at(13),minutes=99)
    chart(f,date(2026,10,12),at(14),minutes=99)
    chart(f,date(2026,10,12),at(13),minutes=99,source='bonus')
    with f.client() as c:
        before=snapshot(f);body=c.get(PATH).json()
        assert body['progress']=={'logging_days':2,'practice_seconds':1498,'practice_days':1}
        assert snapshot(f)==before


@pytest.mark.parametrize('monday',[date(2026,3,2),date(2026,10,26)])
def test_chicago_dst_monday_rollover_preserves_late_chart_rules(pantry_db,monday):
    f=pantry_db;end=monday+timedelta(days=7)
    midnight=datetime.combine(end,datetime.min.time(),CENTRAL)
    f.clock[0]=midnight-timedelta(seconds=1)
    sunday=end-timedelta(days=1)
    chart(f,sunday,f.clock[0]-timedelta(seconds=1),minutes=1)
    with f.client() as c:
        first=c.get(PATH).json();assert first['context']['week_start']==str(monday)
        assert first['progress']['practice_seconds']==60 and first['progress']['logging_days']==1
        f.clock[0]=midnight
        empty=c.get(PATH).json();assert empty['progress']=={'logging_days':0,'practice_seconds':0,'practice_days':0}
        chart(f,sunday,midnight,minutes=2)
        before=snapshot(f);late=c.get(PATH).json()
        assert late['context']['week_start']==str(end)
        assert late['progress']=={'logging_days':1,'practice_seconds':0,'practice_days':0}
        assert snapshot(f)==before


def test_signed_earned_and_spent_jars_are_not_weekly_earnable_count(pantry_db):
    f=pantry_db
    with f() as s:
        for i,amount in enumerate([1,1,1,1,1,-3]):
            s.add(m.RewardGrant(profile_id=1,source_key=f'synthetic-reviewed-ledger:{i}',reward_type='pickled_carrot',amount=amount))
        s.add(m.RewardGrant(profile_id=2,source_key='other-owner',reward_type='pickled_carrot',amount=99))
        s.commit()
    with f.client() as c:
        before=snapshot(f);r=c.get(PATH);assert r.status_code==200
        assert r.json()['wallet']['pickled_carrot']==2
        assert snapshot(f)==before
        assert len([x for x in before['reward_grants'] if x['reward_type']=='pickled_carrot' and x['profile_id']==1])==6


@pytest.mark.parametrize('pantry_enabled',[True,False])
def test_independent_gates_fail_closed_without_loading_plan_storage(pantry_db,monkeypatch,pantry_enabled):
    f=pantry_db
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(False,'open'))
    monkeypatch.setitem(FEATURES,'weekly_pantry',Feature(pantry_enabled,'open'))
    def forbidden(*args,**kwargs):raise AssertionError('Disabled plan storage queried')
    monkeypatch.setattr(plans,'load_current_plan',forbidden)
    with f.client() as c:
        before=snapshot(f);r=c.get(PATH)
        assert r.status_code==(200 if pantry_enabled else 403)
        if pantry_enabled:assert r.json()['targets'] is None
        assert snapshot(f)==before


def test_account_scope_stale_credentials_and_no_mutation_routes(pantry_db):
    f=pantry_db
    chart(f,date(2026,10,12),at(),minutes=7,pid=1)
    chart(f,date(2026,10,12),at(),minutes=99,pid=2)
    with f.client(1) as a,f.client(2) as b:
        before=snapshot(f)
        assert a.get(PATH).json()['progress']['practice_seconds']==420
        assert b.get(PATH).json()['progress']['practice_seconds']==5940
        assert a.get(PATH,params={'profile_id':2}).status_code==422
        assert a.request('GET',PATH,json={'profile_id':2}).json()['progress']['practice_seconds']==420
        assert a.get(PATH,headers={'x-woodshed-account':'WL2-PLAN-2'}).status_code==401
        assert snapshot(f)==before
        assert a.post(PATH,json={}).status_code==405
        for path in ['/weekly/wheel','/weekly/pantry/claim','/weekly/pantry/spend']:
            assert a.post(path,json={}).status_code==404
        assert a.post('/account/logout').status_code==200
        retired=snapshot(f);assert a.get(PATH).status_code==401;assert snapshot(f)==retired


def test_parent_authorized_child_reads_privately_and_withdrawal_revokes(arcade_factory,monkeypatch):
    f=arcade_factory
    from app import tester_enrollments as testers,child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry as activate,CLAIMED
    monkeypatch.setitem(FEATURES,'weekly_pantry',Feature(True,'open'))
    monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    # Existing reviewed synthetic parent-activation facility. No real KWS/email.
    activate(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    with f() as s:
        p=s.scalar(select(m.WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    from app.main import app
    with TestClient(app) as c:
        assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
        before=snapshot(f);r=c.get(PATH);assert r.status_code==200,r.text
        assert_student_safe(r.json());assert wid not in str(r.json())
        assert snapshot(f)==before
        with f() as s:
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)));s.commit()
        before_denial=snapshot(f);denied=c.get(PATH);assert denied.status_code in (401,403)
        # The canonical authorization path may retire a browser nonce; business
        # rows, balances and historical evidence must remain unchanged.
        after=snapshot(f)
        for name in before_denial:
            if name!='revoked_browser_sessions':assert after[name]==before_denial[name],name
