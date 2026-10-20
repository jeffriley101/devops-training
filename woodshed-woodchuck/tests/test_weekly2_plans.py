"""Saved plan contracts on actual migrated SQLite/PostgreSQL; no live services."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import sys
from threading import Event
import time

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import models as m, weekly_plans as plans, weekly_routes as routes
from app.age_privacy import declare_age
from app.db import Base
from app.feature_access import FEATURES, Feature
from app.security import hash_pin
from app.weekly import CENTRAL, utc_instant
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan
from tests.test_weekly2_foundation_migration import (
    db, config, revision, rows, profile, seed_history, schema_contract, play,
)
from tests.test_weekly2_game_mode import assert_only_session_retirement

OLD, NEW = 'w25weekly002', 'w25weekly003'
FOCI = ('Build daily consistency', 'Improve tone and control', 'Learn songs faster', 'Strengthen improvisation')
NOW = datetime(2026, 10, 12, 13, 30, tzinfo=CENTRAL)
PATH = '/weekly/plan'


def at(day=12, hour=9, minute=0, *, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=CENTRAL)


def plan_values(**changes):
    return dict(id=1, profile_id=1, week_start=date(2026, 10, 12), focus=FOCI[0],
        issued_game_mode='Challenge', generator_version='weekly_plan_v1', issued_at=NOW,
        recent_results_cutoff=at(hour=0), recent_results={'weeks': []}, logging_days_target=4,
        practice_minutes_target=60, homework_minutes_target=15, thirds_target=7) | changes


def slot_values(**changes):
    return dict(plan_id=1, slot_no=1, instrument='Flute', started_at=NOW,
                homework_spec={'synthetic_schema_only': True}) | changes


def old_rows(engine):
    return {k:v for k,v in rows(engine).items() if k not in ('weekly_plans','weekly_instrument_plans')}


def test_ancestry_catalog_and_disabled_gate():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == [NEW] and script.get_revision(NEW).down_revision == OLD
    assert script.get_revision(OLD).down_revision == 'w25weekly001'
    assert len(list(script.walk_revisions())) == 65
    from app.content import GOAL_OPTIONS
    assert tuple(GOAL_OPTIONS) == FOCI
    assert plans.TARGETS == {'Beginner': (3,30,10,3), 'Challenge': (4,60,15,7), 'Performance': (5,90,20,15)}
    assert FEATURES['weekly_plans'] == Feature(False, 'open')


def test_populated_upgrade_history_parity_and_empty_roundtrip(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    with db.begin() as c:
        c.execute(m.WeeklyWheelPlay.__table__.insert().values(**play()))
        c.execute(text("UPDATE woodchuck_profiles SET game_mode='Performance' WHERE id=1"))
    before, structure = rows(db), schema_contract(db)
    command.upgrade(config(), NEW)
    assert revision(db) == NEW and old_rows(db) == before
    current, triggers = schema_contract(db)
    assert {k:v for k,v in current.items() if k not in ('weekly_plans','weekly_instrument_plans')} == structure[0]
    assert triggers == structure[1]
    with db.connect() as c:
        assert compare_metadata(MigrationContext.configure(c, opts={'compare_type':True}), Base.metadata) == []
        assert c.scalar(text('SELECT count(*) FROM weekly_plans')) == 0
        assert c.scalar(text('SELECT count(*) FROM weekly_instrument_plans')) == 0
        from app.operator_schema_compatibility import installed_revision
        with pytest.raises(RuntimeError, match='revision_not_approved'):
            installed_revision(c, RuntimeError)
    command.downgrade(config(), OLD)
    assert revision(db) == OLD and rows(db) == before and schema_contract(db) == structure
    command.upgrade(config(), NEW)
    assert revision(db) == NEW and old_rows(db) == before
    # Existing populated Team/Classroom/contest/Wheel history also survives
    # issuance, not merely the additive DDL. No Classroom authority is inferred.
    with Session(db) as s:
        declare_age(s, 1, 'adult')
        s.commit()
    before_issue = old_rows(db)
    from unittest.mock import patch
    with patch.dict(FEATURES, {'weekly_plans': Feature(True, 'open')}), Session(db) as s:
        plans.issue_current_plan(s, s.get(m.WoodchuckProfile, 1), FOCI[0], now=NOW)
        s.commit()
    assert old_rows(db) == before_issue


def test_fresh_schema_constraints_cascades_and_used_downgrade(db):
    command.upgrade(config(), 'head')
    assert revision(db) == NEW
    with db.connect() as c:
        assert compare_metadata(MigrationContext.configure(c, opts={'compare_type':True}), Base.metadata) == []
    profile(db, 1)
    invalids=[{'focus':'Practice'},{'issued_game_mode':'Advanced'},{'logging_days_target':99},
              {'practice_minutes_target':None},{'generator_version':' '},
              {'recent_results_cutoff':NOW+timedelta(seconds=1)}, {'recent_results':None}, {'profile_id':999}]
    for changes in invalids:
        before=rows(db)
        with pytest.raises(IntegrityError), db.begin() as c:
            c.execute(WeeklyPlan.__table__.insert().values(**plan_values(**changes)))
        assert rows(db)==before
    with pytest.raises(ValueError):WeeklyPlan(**plan_values(week_start=date(2026,10,13)))
    with db.begin() as c:c.execute(WeeklyPlan.__table__.insert().values(**plan_values()))
    before=rows(db)
    with pytest.raises(IntegrityError), db.begin() as c:
        c.execute(WeeklyPlan.__table__.insert().values(**plan_values(id=2)))
    assert rows(db)==before
    with pytest.raises(RuntimeError,match='weekly_plans_in_use'):command.downgrade(config(),OLD)
    assert revision(db)==NEW and rows(db)==before
    for changes in [{'slot_no':0},{'slot_no':3},{'instrument':' '},{'plan_id':999},{'homework_spec':None}]:
        with pytest.raises(IntegrityError), db.begin() as c:
            c.execute(WeeklyInstrumentPlan.__table__.insert().values(**slot_values(**changes)))
        assert rows(db)==before
    with db.begin() as c:
        c.execute(WeeklyInstrumentPlan.__table__.insert().values(**slot_values()))
        c.execute(WeeklyInstrumentPlan.__table__.insert().values(**slot_values(slot_no=2,instrument='Tuba')))
    used=rows(db)
    for changes in [{'slot_no':1,'instrument':'Trumpet'},{'slot_no':2,'instrument':'Flute'}]:
        with pytest.raises(IntegrityError),db.begin() as c:
            c.execute(WeeklyInstrumentPlan.__table__.insert().values(**slot_values(**changes)))
        assert rows(db)==used
    with pytest.raises(RuntimeError,match='weekly_plans_in_use'):command.downgrade(config(),OLD)
    assert revision(db)==NEW and rows(db)==used
    with db.begin() as c:
        c.execute(text('DELETE FROM woodchuck_profiles WHERE id=1'))
        assert c.scalar(text('SELECT count(*) FROM weekly_plans'))==0
        assert c.scalar(text('SELECT count(*) FROM weekly_instrument_plans'))==0
    command.downgrade(config(),OLD)
    assert revision(db)==OLD


@pytest.fixture
def runtime(db,monkeypatch):
    command.upgrade(config(), NEW)
    from app.main import app
    factory=sessionmaker(db,expire_on_commit=False,autoflush=False)
    for name,module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module,'SessionLocal'):monkeypatch.setattr(module,'SessionLocal',factory)
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'))
    factory.clock=[NOW]
    monkeypatch.setattr(plans,'utc_instant',lambda value=None:utc_instant(value or factory.clock[0]))
    monkeypatch.setattr(routes,'utc_instant',lambda value=None:utc_instant(value or factory.clock[0]))
    with factory() as s:
        for pid in (1,2):
            s.add(m.WoodchuckProfile(id=pid,woodchuck_id=f'WL2-PLAN-{pid}',display_name=f'Synthetic {pid}',
                pin_hash=hash_pin('2468'),instrument='Flute',level='Advanced',game_mode=None,goal='Original Focus'))
        s.flush()
        for pid in (1,2):
            declare_age(s,pid,'adult',at=at(1))
            s.add(m.WoodchuckState(profile_id=pid,revision=9,state_json={'profile':{'level':'Advanced'},
                '_weekly_preferences':{'game_mode':'Performance'},'progress':{'credits':23,'xp':321}}))
        s.commit()
    def client(pid=1,**kwargs):
        c=TestClient(app,**kwargs)
        assert c.post('/account/login',data={'woodchuck_id':f'WL2-PLAN-{pid}','pin':'2468'}).status_code==200
        return c
    return db,factory,client


def choose(c,mode='Challenge'):
    r=c.patch('/account/profile/game-mode',json={'game_mode':mode});assert r.status_code==200,r.text


def chart(factory,practice_day,submitted,*,minutes=20,seconds=None,pid=1,source=None):
    with factory() as s:
        s.add(m.PracticeChart(profile_id=pid,practice_date=practice_day,minutes=minutes,instrument='Flute',
            source=source or ('pristine' if seconds is not None else 'p-book'),detected_playing_seconds=seconds,
            created_at=submitted.astimezone(timezone.utc),note='SYNTHETIC PRIVATE NOTE',practice_details=['private text'],
            include_contests=False,include_team_contests=False))
        s.commit()


def test_readonly_null_gate_and_no_extra_routes(runtime,monkeypatch):
    db,f,c=runtime;c=c();before=rows(db)
    assert c.get('/account/me').status_code==200
    assert c.get(PATH).json()=={'plan':None,'progress':{'practice_seconds':0,'practice_days':0,'logging_days':0}}
    assert c.post(PATH,json={'focus':FOCI[0]}).status_code==409
    assert rows(db)==before
    for p in ['/weekly/wheel','/weekly/claims']:
        assert c.post(p,json={}).status_code==404
    assert c.post('/weekly/plan/instruments/current/start',json={}).status_code==404
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(False,'open'))
    assert c.get(PATH).status_code==403 and c.post(PATH,json={'focus':FOCI[0]}).status_code==403
    assert rows(db)==before


@pytest.mark.parametrize('mode,targets',[('Beginner',(3,1800,10,3)),('Challenge',(4,3600,15,7)),('Performance',(5,5400,20,15))])
def test_explicit_focus_targets_retry_and_frozen_mode(runtime,mode,targets):
    db,f,client=runtime;c=client();choose(c,mode)
    for number,focus in enumerate(FOCI):
        f.clock[0]=NOW+timedelta(weeks=number)
        before=old_rows(db)
        r=c.post(PATH,json={'focus':focus});assert r.status_code==200,r.text
        saved=r.json()['plan'];assert saved['focus']==focus and saved['issued_game_mode']==mode
        assert saved['targets']==dict(zip(('logging_days','practice_seconds','homework_minutes','thirds'),targets))
        assert r.headers['cache-control']=='no-store' and 'set-cookie' not in r.headers
        assert c.get(PATH).json()['plan']==saved
        assert c.post(PATH,json={'focus':focus}).json()['plan']==saved
        assert c.post(PATH,json={'focus':FOCI[(number+1)%4]}).status_code==409
        assert old_rows(db)==before
    choose(c,'Performance' if mode!='Performance' else 'Beginner')
    after=rows(db)
    assert c.get(PATH).json()['plan']==saved
    assert c.post(PATH,json={'focus':FOCI[-1]}).json()['plan']==saved
    assert rows(db)==after
    with f() as s:
        assert len(s.scalars(select(WeeklyPlan)).all())==4
        assert not s.scalars(select(WeeklyInstrumentPlan)).all()
        assert s.get(m.WoodchuckProfile,1).level=='Advanced'
        assert s.get(m.WoodchuckProfile,1).goal=='Original Focus'
        assert s.get(m.WoodchuckProfile,2).game_mode is None
        assert all(x.generator_version=='weekly_plan_v1' for x in s.scalars(select(WeeklyPlan)))


def test_invalid_input_origin_account_and_revoked_session(runtime):
    db,f,client=runtime;c=client();choose(c)
    before=rows(db)
    for focus in [None,True,3,[],{},'', 'Practice',FOCI[0]+' ']:
        assert c.post(PATH,json={'focus':focus}).status_code==422
    for field in ['profile_id','week_start','game_mode','issued_game_mode','targets','generator_version','progress','completion','goal']:
        assert c.post(PATH,json={'focus':FOCI[0],field:'forged'}).status_code==422
        assert c.get(PATH,params={field:'2'}).status_code==422
        assert c.post(PATH,params={field:'2'},json={'focus':FOCI[0]}).status_code==422
    for headers in [{'Origin':'http://foreign.invalid'},{'Origin':'http://testserver:9999'},{'Origin':''},{'Origin':'null'},{'Sec-Fetch-Site':'same-site'}]:
        assert c.post(PATH,headers=headers,json={'focus':FOCI[0]}).status_code==403
    with TestClient(__import__('app.main',fromlist=['app']).app) as anon:
        assert anon.get(PATH).status_code==401
        assert anon.post(PATH,json={'focus':FOCI[0]}).status_code==401
    assert c.get(PATH,headers={'x-woodshed-account':'WL2-PLAN-2'}).status_code==401
    assert c.post(PATH,headers={'x-woodshed-account':'WL2-PLAN-2'},json={'focus':FOCI[0]}).status_code==401
    assert rows(db)==before
    # Missing Origin follows the existing sensitive-mutation policy.
    request=c.build_request('POST',PATH,json={'focus':FOCI[0]});del request.headers['origin']
    assert c.send(request).status_code==403 and rows(db)==before
    cookie=c.cookies.get('session');assert c.post('/account/logout').status_code==200
    before=rows(db)
    stale=TestClient(__import__('app.main',fromlist=['app']).app);stale.cookies.set('session',cookie)
    assert stale.get(PATH).status_code==401 and stale.post(PATH,json={'focus':FOCI[0]}).status_code==401
    assert rows(db)==before


def test_progress_snapshot_cutoff_privacy_and_account_scope(runtime):
    db,f,client=runtime;c=client();choose(c)
    for day,submitted,minutes,seconds in [
        (date(2026,9,28),at(28,month=9),10,None),(date(2026,9,28),at(28,10,month=9),5,None),
        (date(2026,10,5),at(5),20,None),(date(2026,10,6),at(6),1,61),
        (date(2026,10,11),at(hour=0)-timedelta(seconds=1),8,None),
        (date(2026,10,11),at(hour=0),9,None),(date(2026,10,12),at(),25,None),
        (date(2026,9,21),at(21,month=9),99,None)]:
        chart(f,day,submitted,minutes=minutes,seconds=seconds)
    chart(f,date(2026,10,5),at(5),minutes=999,pid=2)
    chart(f,date(2026,10,13),at(),minutes=99) # future practice excluded
    chart(f,date(2026,10,12),at(14),minutes=99) # future submission excluded
    chart(f,date(2026,10,12),at(),minutes=99,source='client-forged')
    before=old_rows(db)
    r=c.post(PATH,json={'focus':FOCI[0]});assert r.status_code==200,r.text
    assert r.json()['progress']=={'practice_seconds':1500,'practice_days':1,'logging_days':1}
    expected={'weeks':[{'week_start':'2026-09-28','practice_seconds':900,'logging_days':1},
                       {'week_start':'2026-10-05','practice_seconds':1741,'logging_days':3}]}
    with f() as s:
        p=s.scalar(select(WeeklyPlan));assert p.recent_results==expected
        cutoff=p.recent_results_cutoff
        if cutoff.tzinfo is None:cutoff=cutoff.replace(tzinfo=timezone.utc)
        assert utc_instant(cutoff)==at(hour=0).astimezone(timezone.utc)
    assert old_rows(db)==before
    for secret in ['PRIVATE','private text','recent_results','generator_version','rating']:
        assert secret not in r.text
    chart(f,date(2026,10,11),at(12,10),minutes=33)
    assert c.post(PATH,json={'focus':FOCI[0]}).json()['plan']==r.json()['plan']
    with f() as s:assert s.scalar(select(WeeklyPlan)).recent_results==expected
    f.clock[0]=at(13,18)
    chart(f,date(2026,10,12),at(13),minutes=1,seconds=61)
    chart(f,date(2026,10,12),at(13),minutes=2)
    # Intentional BOOK + Pristine credit; late prior-date logs count only as logs here.
    assert c.get(PATH).json()['progress']=={'practice_seconds':7621,'practice_days':2,'logging_days':2}
    c2=client(2);assert c2.get(PATH).json()['plan'] is None
    assert 'PRIVATE' not in c2.get(PATH).text


def test_server_week_boundaries_and_dst(runtime):
    db,f,client=runtime;c=client();choose(c)
    for instant,start,cutoff in [
        ('2026-10-12T04:59:59+00:00','2026-10-05','2026-10-05T05:00:00+00:00'),
        ('2026-10-12T05:00:00+00:00','2026-10-12','2026-10-12T05:00:00+00:00'),
        ('2026-03-09T05:00:00+00:00','2026-03-09','2026-03-09T05:00:00+00:00'),
        ('2026-11-02T05:59:59+00:00','2026-10-26','2026-10-26T05:00:00+00:00'),
        ('2026-11-02T06:00:00+00:00','2026-11-02','2026-11-02T06:00:00+00:00')]:
        f.clock[0]=datetime.fromisoformat(instant)
        response=c.post(PATH,json={'focus':FOCI[0]});assert response.status_code==200,response.text
        assert response.json()['plan']['week_start']==start
        with f() as s:
            p=s.get(WeeklyPlan,response.json()['plan']['id']);value=p.recent_results_cutoff
            if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
            assert value.astimezone(timezone.utc).isoformat()==cutoff


def test_atomic_rollback_lost_response_and_narrow_retry(runtime,monkeypatch):
    db,f,client=runtime;c=client();choose(c);before=rows(db)
    original=f.class_.commit
    def fail_commit(s):
        assert s.scalar(select(WeeklyPlan)) is not None
        raise RuntimeError('synthetic precommit failure')
    with monkeypatch.context() as patch:
        patch.setattr(f.class_,'commit',fail_commit)
        with pytest.raises(RuntimeError,match='synthetic precommit failure'):c.post(PATH,json={'focus':FOCI[0]})
    assert rows(db)==before
    def lost_response(s):
        original(s)
        raise RuntimeError('synthetic lost response after commit')
    with monkeypatch.context() as patch:
        patch.setattr(f.class_,'commit',lost_response)
        with pytest.raises(RuntimeError,match='synthetic lost response'):c.post(PATH,json={'focus':FOCI[0]})
    committed=rows(db);assert len(committed['weekly_plans'])==1 and old_rows(db)=={k:v for k,v in before.items() if k not in ('weekly_plans','weekly_instrument_plans')}
    result=c.post(PATH,json={'focus':FOCI[0]});assert result.status_code==200 and rows(db)==committed
    assert c.post(PATH,json={'focus':FOCI[1]}).status_code==409 and rows(db)==committed
    issue=plans.issue_current_plan
    calls=[]
    def unique_race(s,p,focus):
        calls.append(p.id)
        if len(calls)==1:
            row=s.scalar(select(WeeklyPlan));values={col.name:getattr(row,col.name) for col in WeeklyPlan.__table__.c if col.name!='id'}
            s.execute(WeeklyPlan.__table__.insert().values(**values))
        return issue(s,p,focus)
    with monkeypatch.context() as patch:
        patch.setattr(plans,'issue_current_plan',unique_race)
        assert c.post(PATH,json={'focus':FOCI[0]}).status_code==200
    assert calls==[1,1] and rows(db)==committed
    calls.clear()
    def invalid(s,p,focus):
        calls.append(p.id);s.execute(text("UPDATE weekly_plans SET focus='bad'"))
    with monkeypatch.context() as patch:
        patch.setattr(plans,'issue_current_plan',invalid)
        with pytest.raises(IntegrityError):c.post(PATH,json={'focus':FOCI[0]})
    assert calls==[1] and rows(db)==committed


def observe_block(engine,pids,record_property,case):
    blockers=[];deadline=time.monotonic()+8
    with engine.connect() as observer:
        while time.monotonic()<deadline:
            if 'second' in pids:
                blockers=observer.scalar(text('SELECT pg_blocking_pids(:p)'),{'p':pids['second']})
                if pids['first'] in blockers:break
            time.sleep(.01)
    assert pids['first'] in blockers,'No genuine PostgreSQL blocking observed'
    record_property('postgres_blocking',json.dumps({'case':case,'pids':pids,'blockers':blockers,'isolation':'READ COMMITTED'}))


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('case',['same-focus','different-focus','mode-change','rotated','revoked'])
def test_postgres_issuance_waits_and_rechecks(runtime,case,record_property):
    db,f,client=runtime;c1,c2=client(),client();choose(c1)
    if case=='revoked':c2.cookies.update(c1.cookies)
    before=old_rows(db);pids={};held=Event();release=Event()
    def hold(s):
        if not held.is_set():
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            held.set();assert release.wait(15)
    def query(c,cursor,statement,parameters,context,many):
        if 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement and held.is_set():pids['second']=c.connection.driver_connection.info.backend_pid
    event.listen(db,'before_cursor_execute',query)
    event.listen(f.class_,'before_commit',hold)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            if case in ('mode-change','rotated','revoked'):
                def first_write():
                    with f() as s:
                        p=s.scalar(select(m.WoodchuckProfile).where(m.WoodchuckProfile.id==1).with_for_update())
                        if case=='mode-change':
                            # Use the actual atomic endpoint in this case below.
                            raise AssertionError('unreachable')
                        if case=='rotated':p.session_version+=1
                        s.commit()
                first=pool.submit(c1.patch,'/account/profile/game-mode',json={'game_mode':'Performance'}) if case=='mode-change' else pool.submit(first_write)
            else:first=pool.submit(c1.post,PATH,json={'focus':FOCI[0]})
            assert held.wait(10)
            pending=pool.submit(c2.post,PATH,json={'focus':FOCI[1] if case=='different-focus' else FOCI[0]})
            try:
                observe_block(db,pids,record_property,case)
                if case=='revoked':assert c1.post('/account/logout').status_code==200
            finally:release.set()
            first_result=first.result(timeout=15)
            if first_result is not None:assert first_result.status_code==200
            result=pending.result(timeout=15)
            assert result.status_code=={'same-focus':200,'different-focus':409,'mode-change':200,'rotated':401,'revoked':401}[case],result.text
    finally:
        release.set();event.remove(f.class_,'before_commit',hold);event.remove(db,'before_cursor_execute',query)
    with f() as s:
        saved=s.scalars(select(WeeklyPlan)).all()
        assert len(saved)==(0 if case in ('rotated','revoked') else 1)
        if saved:assert saved[0].issued_game_mode==('Performance' if case=='mode-change' else 'Challenge')
        assert not s.scalars(select(WeeklyInstrumentPlan)).all()
    after=old_rows(db)
    allowed={'woodchuck_profiles','woodchuck_states'} if case=='mode-change' else {'woodchuck_profiles','revoked_browser_sessions'} if case=='rotated' else {'revoked_browser_sessions'} if case=='revoked' else set()
    for name in before:
        if name not in allowed:assert after[name]==before[name],name


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_c002_child_withdrawal_after_lock_wait(db,monkeypatch,record_property):
    command.upgrade(config(),NEW)
    from app.main import app
    from app import tester_enrollments as testers,child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry,CLAIMED
    f=sessionmaker(db,expire_on_commit=False,autoflush=False)
    for name,module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module,'SessionLocal'):monkeypatch.setattr(module,'SessionLocal',f)
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'))
    monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    with f() as s:
        child=s.scalar(select(m.WoodchuckProfile));ident,wid=child.id,child.woodchuck_id
        enrollment=s.scalar(select(m.TesterEnrollment));assert enrollment.cohort_key=='C002'
    c=TestClient(app);assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
    choose(c,'Beginner')
    clock=[NOW];monkeypatch.setattr(plans,'utc_instant',lambda value=None:utc_instant(value or clock[0]));monkeypatch.setattr(routes,'utc_instant',lambda value=None:utc_instant(value or clock[0]))
    valid=c.post(PATH,json={'focus':FOCI[0]});assert valid.status_code==200,valid.text
    historical=rows(db)['weekly_plans'];clock[0]+=timedelta(weeks=1)
    pids={};held=Event();release=Event()
    def withdraw():
        with f() as s:
            s.scalar(select(m.WoodchuckProfile).where(m.WoodchuckProfile.id==ident).with_for_update())
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cursor,statement,parameters,context,many):
        if 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement and held.is_set():pids['second']=cn.connection.driver_connection.info.backend_pid
    event.listen(db,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(withdraw);assert held.wait(10)
            pending=pool.submit(c.post,PATH,json={'focus':FOCI[0]})
            try:observe_block(db,pids,record_property,'C002-consent-withdrawal')
            finally:release.set()
            first.result(timeout=15);response=pending.result(timeout=15);assert response.status_code==401,response.text
    finally:release.set();event.remove(db,'before_cursor_execute',query)
    assert rows(db)['weekly_plans']==historical
    assert c.get(PATH).status_code==401
    with f() as s:
        assert s.scalar(select(ConsentEvidence)).withdrawn_at is not None
        assert not s.scalars(select(WeeklyInstrumentPlan)).all()
        assert not s.scalars(select(m.ClassroomStudentMembership)).all()
