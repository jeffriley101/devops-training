"""Prize Wheel on fresh migrated databases; real routes and durable receipts."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import sessionmaker

from app import contests, weekly_wheel as wheel
from app.economy import qualified_camp_point_clause
from app.feature_access import FEATURES, Feature
from app.models import CampPointAward, RewardGrant, WoodchuckProfile, WoodchuckState
from app.store_inventory import list_inventory_payloads, place_inventory_item
from app.weekly import CENTRAL, utc_instant
from app.weekly_models import WeeklyWheelPlay
from app.weekly_rewards import grant
from app.xp import xp_payload, level_payload
from tests.test_weekly2_plans import db, runtime, rows, NOW, config, observe_block
from tests.test_weekly2_thirds import arcade_factory

PATH = '/weekly/wheel'


@pytest.fixture
def wheel_db(runtime, monkeypatch):
    engine, factory, client = runtime
    factory.engine, factory.client = engine, client
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(False, 'open'))
    monkeypatch.setitem(FEATURES, 'weekly_wheel', Feature(True, 'open'))
    monkeypatch.setattr(wheel, 'utc_instant', lambda value=None: utc_instant(value or factory.clock[0]))
    return factory


def seed(f, jars=3, pid=1):
    # Explicit synthetic earned ledger, independent of Caper qualification.
    with f.begin() as s:
        grant(s, pid, 'synthetic-earned-jars', 'pickled_carrot', jars, now=f.clock[0]-timedelta(weeks=5))


def start(c, key='funded-action'):
    r=c.post(PATH+'/plays', json={'request_key':key});assert r.status_code==200,r.text
    return r.json()


def rotate(c, play, rotation):
    r=c.post(PATH+'/plays/'+play['id']+'/rotations',json={'rotation':rotation})
    assert r.status_code==200,r.text
    return r.json()


def assert_unchanged(before, after, allowed=()):
    for name in before:
        if name not in allowed:assert after[name]==before[name],name


def test_exact_all_buckets_and_default_gate():
    weights=(('activity',2950),('dandelion',2000),('xp',2000),('spin_again',2000),
             ('ufo',800),('dandelion_1000',200),('white_clover',50))
    assert wheel.WHEEL_WEIGHTS==weights
    assert Counter(wheel.outcome_for_bucket(i) for i in range(10000))==dict(weights)
    index=0
    for outcome,weight in weights:
        assert wheel.outcome_for_bucket(index)==wheel.outcome_for_bucket(index+weight-1)==outcome
        index+=weight
    for value in (-1,10000,True,1.5,'1'):
        with pytest.raises(ValueError):wheel.outcome_for_bucket(value)
    assert FEATURES['weekly_wheel']==Feature(False,'open')


@pytest.mark.parametrize('bucket,outcome,amount',[(0,'activity',14),(2950,'dandelion',400),
    (4950,'xp',1400),(6950,'spin_again',0),(8950,'ufo',1),(9750,'dandelion_1000',1000),(9950,'white_clover',1)])
def test_each_prize_atomic_receipts_and_retry(wheel_db,monkeypatch,bucket,outcome,amount,record_property):
    f=wheel_db;seed(f)
    monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:bucket)
    with f.client() as c:
        before=rows(f.engine);initial=c.get(PATH).json();assert rows(f.engine)==before
        result=start(c);play=result['play'];assert play['outcomes']==[outcome]
        assert result['wallet']['pickled_carrot']==0 and result['wallet']['caper_clue']==0
        assert play['complete']==(outcome!='spin_again')
        kind='dandelion' if outcome=='dandelion_1000' else outcome
        assert result['credits']==initial['credits']+(amount if kind=='dandelion' else 0)
        saved=rows(f.engine)
        assert start(c)==result
        assert c.get(PATH+'/plays/'+play['id']).json()==result
        assert c.get(PATH).json()==result
        assert rotate(c,play,1)==result
        assert rows(f.engine)==saved
        assert_unchanged(before,saved,{'weekly_wheel_plays','reward_grants','camp_point_awards','woodchuck_states'})
        with f() as s:
            grants=s.scalars(select(RewardGrant).where(RewardGrant.source_key.startswith('weekly-wheel:')).order_by(RewardGrant.source_key)).all()
            expected=[(f"weekly-wheel:{play['id']}:payment",'pickled_carrot',-3)]
            if amount:expected.insert(0,(f"weekly-wheel:{play['id']}:1",kind,amount))
            assert [(g.source_key,g.reward_type,g.amount) for g in grants]==expected
            assert all(g.profile_id==1 and g.category_key is None and g.contest_result_id is None for g in grants)
            xp=xp_payload(s,profile_id=1)
            assert xp['xp_total']==(1400 if outcome=='xp' else 14 if outcome=='activity' else 0)
            assert xp['level']==level_payload(xp['xp_total'])['level']
            assert s.get(WoodchuckProfile,1).game_mode is None
            assert s.get(WoodchuckProfile,1).level=='Advanced'
            if outcome=='activity':
                award=s.scalar(select(CampPointAward));stored=s.get(WeeklyWheelPlay,play['id'])
                assert award.id in s.scalars(select(CampPointAward.id).where(qualified_camp_point_clause()))
                assert contests.aware_utc(award.created_at)==contests.aware_utc(award.occurred_at)==contests.aware_utc(stored.completed_at)==contests.aware_utc(grants[0].created_at)
            record_property('receipt_examples',json.dumps(expected))


def test_pending_continuation_ninth_and_midnight(wheel_db,monkeypatch):
    f=wheel_db;seed(f,30)
    monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:2950)
    with f.client() as c:
        for i in range(8):start(c,str(i))
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950)
        ninth=start(c,'ninth')['play'];assert ninth['daily_ordinal']==9
        saved=rows(f.engine)
        assert c.post(PATH+'/plays',json={'request_key':'different'}).status_code==409
        assert c.post(PATH+'/plays/'+ninth['id']+'/rotations',json={'rotation':3}).status_code==409
        assert rows(f.engine)==saved
        twice=rotate(c,ninth,2);assert twice['play']['outcomes']==['spin_again','spin_again']
        saved=rows(f.engine);assert rotate(c,ninth,2)==twice;assert rows(f.engine)==saved
        f.clock[0]+=timedelta(days=1)
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:9750)
        final=rotate(c,ninth,3);assert final['play']['outcomes']==['spin_again','spin_again','dandelion_1000']
        assert final['wallet']['pickled_carrot']==3 and final['funded_plays']==0
        assert final['play']['funded_day']==ninth['funded_day']
        saved=rows(f.engine)
        assert c.post(PATH+'/plays/'+ninth['id']+'/rotations',json={'rotation':4}).status_code==409
        assert rotate(c,ninth,3)==final and rows(f.engine)==saved
        assert start(c,'new-day')['play']['daily_ordinal']==1
        with f() as s:
            assert s.scalar(select(func.count()).select_from(WeeklyWheelPlay))==10
            assert s.scalar(select(func.sum(RewardGrant.amount)).where(RewardGrant.reward_type=='pickled_carrot'))==0


@pytest.mark.parametrize('month,day,hours',[(3,8,23),(11,1,25)])
def test_daily_capacity_and_dst(wheel_db,monkeypatch,month,day,hours):
    f=wheel_db;seed(f,30);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:9950)
    begin=datetime(2026,month,day,tzinfo=CENTRAL);end=begin+timedelta(days=1)
    assert (end.astimezone(timezone.utc)-begin.astimezone(timezone.utc)).total_seconds()==hours*3600
    f.clock[0]=begin
    with f.client() as c:
        for i in range(9):assert start(c,str(i))['play']['daily_ordinal']==i+1
        f.clock[0]=end.astimezone(timezone.utc)-timedelta(seconds=1);saved=rows(f.engine)
        assert c.post(PATH+'/plays',json={'request_key':'tenth'}).status_code==409
        assert rows(f.engine)==saved
        f.clock[0]=end;assert start(c,'next')['play']['daily_ordinal']==1


def test_independent_ufo_copies(wheel_db,monkeypatch):
    f=wheel_db;seed(f,6);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:8950)
    with f.client() as c:start(c,'one');start(c,'two')
    with f.begin() as s:
        items=[i for i in list_inventory_payloads(s,profile_id=1) if i['emoji']=='🛸']
        assert len(items)==2 and items[0]['id']!=items[1]['id']
        for i,item in enumerate(items):
            value=place_inventory_item(s,profile_id=1,inventory_id=item['id'],placement_x=.2+i*.3,placement_y=.7)
            assert value['placement_x']==.2+i*.3


def test_historical_pants_and_clover_source_separation(wheel_db,monkeypatch):
    from app.weekly_caper import clover_state
    f=wheel_db;seed(f,9);old=f.clock[0]-timedelta(days=1)
    with f.begin() as s:
        s.add(WeeklyWheelPlay(id='a'*32,profile_id=1,request_key='legacy',funded_day=old.date(),
            funded_at=old,daily_ordinal=1,outcomes=['pants_choice'],completed_at=old))
        grant(s,1,'weekly-wheel:'+('a'*32)+':payment','pickled_carrot',-3,now=old)
        grant(s,1,'weekly-wheel:'+('a'*32)+':1','pants_choice',1,now=old)
        for i in range(7):grant(s,1,f'clover-caper:2026-08-{i+1:02}:clue','caper_clue',now=old)
    monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:9950)
    with f.client() as c:
        before=rows(f.engine);assert start(c,'legacy')['play']['outcomes']==['pants_choice'];assert rows(f.engine)==before
        result=start(c);assert result['wallet']=={'pickled_carrot':3,'white_clover':1,'caper_clue':7,'pants_choice':1}
        with f() as s:
            state=clover_state(s,1);assert state['next_clover_threshold']==7 and state['clover_claimable']
        # Distinct Caper endpoint still settles its own earned threshold.
        monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'))
        claim=c.post('/weekly/clover/claim',json={'request_key':'explicit-caper'})
        assert claim.status_code==200 and claim.json()['white_clover_balance']==2
        saved=rows(f.engine)
        assert c.get(PATH).json()['wallet']['caper_clue']==7
        assert rows(f.engine)==saved
        for table in ('reward_grants','weekly_wheel_plays'):
            assert all(row in saved[table] for row in before[table])


def test_gate_validation_ownership_and_readonly(wheel_db,monkeypatch):
    f=wheel_db;seed(f);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950)
    from app.main import app
    with TestClient(app) as guest:assert guest.get(PATH).status_code==401
    with f.client() as a,f.client(2) as b:
        before=rows(f.engine)
        assert b.post(PATH+'/plays',json={'request_key':'poor'}).status_code==409
        for field in ('owner','profile_id','outcome','amount','daily_ordinal','funded_day','balance','roll'):
            assert a.post(PATH+'/plays',json={'request_key':'bad',field:1}).status_code==422
        for key in ('',' '*3,'x'*65,2,True,None,[]):
            assert a.post(PATH+'/plays',json={'request_key':key}).status_code==422
        for origin in ('https://foreign.invalid','http://testserver:9999','null'):
            assert a.post(PATH+'/plays',json={'request_key':'foreign'},headers={'Origin':origin}).status_code==403
        assert a.get(PATH+'?profile_id=2').status_code==422
        assert a.post(PATH+'/plays?date=today',json={'request_key':'bad'}).status_code==422
        assert rows(f.engine)==before
        play=start(a)['play'];before=rows(f.engine)
        assert b.get(PATH+'/plays/'+play['id']).status_code==404
        assert b.post(PATH+'/plays/'+play['id']+'/rotations',json={'rotation':2}).status_code==404
        for rotation in (0,True,1.5,'2',None):
            assert a.post(PATH+'/plays/'+play['id']+'/rotations',json={'rotation':rotation}).status_code==422
        assert a.post(PATH+'/plays/'+play['id']+'/rotations',json={'rotation':2,'amount':5}).status_code==422
        for _ in range(2):assert a.get(PATH).status_code==200
        assert rows(f.engine)==before
        monkeypatch.setitem(FEATURES,'weekly_wheel',Feature(False,'open'))
        touched=[]
        def query(conn,cursor,statement,*args):
            if 'weekly_wheel_plays' in statement:touched.append(statement)
        event.listen(f.engine,'before_cursor_execute',query)
        try:
            assert a.get(PATH).status_code==403
            assert a.post(PATH+'/plays',json={'request_key':'off'}).status_code==403
        finally:event.remove(f.engine,'before_cursor_execute',query)
        assert touched==[] and rows(f.engine)==before


@pytest.mark.parametrize('stage',['payment','play','prize','state','precommit'])
def test_atomic_rollback_at_write_boundaries(wheel_db,monkeypatch,stage):
    f=wheel_db;seed(f);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:9750)
    with f.client() as c:
        before=rows(f.engine)
        def inserted(mapper,connection,row):
            if (stage=='play' and isinstance(row,WeeklyWheelPlay) or isinstance(row,RewardGrant) and
                ((stage=='payment' and row.amount==-3) or (stage=='prize' and row.amount==1000))):
                raise RuntimeError('synthetic Wheel rollback '+stage)
        def sql(conn,cursor,statement,*args):
            if stage=='state' and statement.lower().startswith('update woodchuck_states'):
                raise RuntimeError('synthetic Wheel rollback '+stage)
        def commit(s):
            if stage=='precommit':raise RuntimeError('synthetic Wheel rollback '+stage)
        event.listen(RewardGrant,'after_insert',inserted);event.listen(WeeklyWheelPlay,'after_insert',inserted)
        event.listen(f.engine,'after_cursor_execute',sql);event.listen(f.class_,'before_commit',commit)
        try:
            with pytest.raises(RuntimeError,match='synthetic Wheel rollback'):c.post(PATH+'/plays',json={'request_key':'rollback'})
        finally:
            event.remove(RewardGrant,'after_insert',inserted);event.remove(WeeklyWheelPlay,'after_insert',inserted)
            event.remove(f.engine,'after_cursor_execute',sql);event.remove(f.class_,'before_commit',commit)
        assert rows(f.engine)==before


def test_committed_response_loss_recovers_funding_and_continuation(wheel_db,monkeypatch):
    f=wheel_db;seed(f);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950)
    for name in ('fund_play','continue_play'):
        original=getattr(wheel,name)
        def marked(s,*args,_operation=original,**kwargs):
            value=_operation(s,*args,**kwargs);s.info['wheel_lost_response']=True;return value
        monkeypatch.setattr(wheel,name,marked)
    with f.client() as c:
        def lose(s):
            if s.info.get('wheel_lost_response'):raise RuntimeError('synthetic response lost after commit')
        event.listen(f.class_,'after_commit',lose)
        try:
            with pytest.raises(RuntimeError,match='synthetic response lost'):start(c)
        finally:event.remove(f.class_,'after_commit',lose)
        saved=rows(f.engine);play=start(c)['play'];assert rows(f.engine)==saved
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:0)
        event.listen(f.class_,'after_commit',lose)
        try:
            with pytest.raises(RuntimeError,match='synthetic response lost'):rotate(c,play,2)
        finally:event.remove(f.class_,'after_commit',lose)
        saved=rows(f.engine);result=rotate(c,play,2)
        assert result['play']['outcomes']==['spin_again','activity'] and rows(f.engine)==saved
        with f() as s:
            assert len(s.scalars(select(CampPointAward).where(qualified_camp_point_clause())).all())==1


def test_real_activity_producer_live_team_and_finalized_history(wheel_db,monkeypatch):
    from app.teams import create_and_join_team
    from tests.test_weekly2_wheel_activity import candidate, FINAL
    f=wheel_db;seed(f);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:0)
    with f() as s:
        # runtime seeds explicit IDs; PostgreSQL sequences do not advance for
        # those inserts. Align only this fresh fixture before auto-ID history.
        if f.engine.dialect.name=='postgresql':
            s.execute(text("SELECT setval(pg_get_serial_sequence('woodchuck_profiles','id'), (SELECT max(id) FROM woodchuck_profiles), true)"))
        # Existing synthetic historical finalization becomes immutable baseline.
        old=candidate(s)
        contests.finalize_contest_week(s,week_start=old.week.week_start,now=FINAL);s.commit()
        season,_,week=contests.ensure_current_contest_data(s,now=f.clock[0])
        team,_=create_and_join_team(s,profile=s.get(WoodchuckProfile,1),season=season,
            name='Wheel Producer Team',emblem_key='letter:Z',now=f.clock[0]);s.commit()
        team_id=team.id;week_id=week.id
    with f.client() as c:
        before=rows(f.engine);start(c)
        with f() as s:
            from app.models import ContestWeek
            week=s.get(ContestWeek,week_id)
            totals=contests.student_camp_point_totals(s,profile_id=1,now=f.clock[0],season=season,contest_week=week)
            assert totals['camp_points_this_week']==14
            scores=contests.team_leaderboards(s,season=season,contest_week=week)['team-weekly-activity-points']['open']
            assert any(row['team_id']==team_id and row['score']==14 for row in scores)
            assert s.scalar(select(CampPointAward).where(CampPointAward.profile_id==1)).team_id==team_id
        after=rows(f.engine)
        assert_unchanged(before,after,{'weekly_wheel_plays','reward_grants','camp_point_awards','woodchuck_states'})
        assert all(row in after['reward_grants'] for row in before['reward_grants'])
        assert [row for row in after['woodchuck_states'] if row['profile_id']!=1]==[row for row in before['woodchuck_states'] if row['profile_id']!=1]
        for table in ('reward_grants','camp_point_awards'):
            assert [row for row in after[table] if row['profile_id']!=1]==[row for row in before[table] if row['profile_id']!=1]


@pytest.mark.parametrize('case',['state','endpoint'])
def test_original_xp_assertions_on_migrated_database(arcade_factory,case):
    from tests import test_xp as original
    if case=='state':original.test_client_account_state_cannot_change_xp(arcade_factory)
    else:original.test_xp_endpoint_requires_authentication_and_returns_calculated_payload(arcade_factory)


@pytest.mark.parametrize('change',['account','version','deleted','nonce','unknown','under13'])
def test_ineligible_or_stale_student_cannot_spend(wheel_db,change):
    from app.main import app
    from app.age_models import AccountPrivacy
    f=wheel_db;seed(f)
    with f.client() as c:
        if change in ('version','deleted','unknown','under13'):
            with f.begin() as s:
                p=s.get(WoodchuckProfile,1)
                if change=='version':p.session_version+=1
                elif change=='deleted':p.status='deleted'
                else:s.get(AccountPrivacy,1).age_band=change
        if change=='nonce':
            with TestClient(app) as other:
                other.cookies.update(c.cookies);assert other.post('/account/logout').status_code==200
        headers={'x-woodshed-account':'WL2-PLAN-2'} if change=='account' else {}
        before=rows(f.engine)
        response=c.post(PATH+'/plays',json={'request_key':'denied'},headers=headers)
        assert response.status_code in ((401,403) if change in ('unknown','under13') else (401,)),response.text
        assert c.get(PATH,headers=headers).status_code in (401,403)
        assert_unchanged(before,rows(f.engine),{'revoked_browser_sessions'})


@pytest.mark.parametrize('stage',['prize','precommit'])
def test_free_continuation_rollback_preserves_pending_play(wheel_db,monkeypatch,stage):
    f=wheel_db;seed(f);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950)
    with f.client() as c:
        play=start(c)['play'];before=rows(f.engine)
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:9950)
        original=wheel.continue_play
        def marked(s,*args,**kwargs):
            s.info['wheel_rollback']=True
            return original(s,*args,**kwargs)
        monkeypatch.setattr(wheel,'continue_play',marked)
        def inserted(mapper,connection,row):
            if stage=='prize' and row.reward_type=='white_clover':
                raise RuntimeError('synthetic continuation rollback')
        def commit(s):
            if stage=='precommit' and s.info.get('wheel_rollback'):
                raise RuntimeError('synthetic continuation rollback')
        event.listen(RewardGrant,'after_insert',inserted);event.listen(f.class_,'before_commit',commit)
        try:
            with pytest.raises(RuntimeError,match='synthetic continuation rollback'):rotate(c,play,2)
        finally:
            event.remove(RewardGrant,'after_insert',inserted);event.remove(f.class_,'before_commit',commit)
        assert rows(f.engine)==before
        read=c.get(PATH+'/plays/'+play['id']).json()
        assert read['play']['outcomes']==['spin_again'] and read['play']['next_rotation']==2
        assert read['wallet']['pickled_carrot']==0 and read['wallet']['white_clover']==0
        assert rows(f.engine)==before
