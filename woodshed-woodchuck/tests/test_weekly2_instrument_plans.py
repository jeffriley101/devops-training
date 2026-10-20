"""Explicit assignments on migrated databases, with real profile-lock contention."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
from threading import Event
import sys

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import models as m, weekly_homework as homework, weekly_plans as plans, weekly_routes as routes
from app.feature_access import FEATURES, Feature
from app.weekly import utc_instant
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan
from tests.test_weekly2_plans import (
    db, runtime, config, rows, old_rows, seed_history, FOCI, NOW, PATH, choose, observe_block,
)

START = '/weekly/plan/instruments/current/start'


def issue(c, mode='Challenge'):
    choose(c, mode)
    r=c.post(PATH,json={'focus':FOCI[0]});assert r.status_code==200,r.text
    return r.json()


def change(c, instrument):
    r=c.patch('/account/profile/instrument',json={'instrument':instrument});assert r.status_code==200,r.text


def start(c, context=None):
    if context is None:context=c.get(PATH).json()['start_context']
    return c.post(START,json={'start_context':context})


def protected(engine):
    return {k:v for k,v in rows(engine).items() if k!='weekly_instrument_plans'}


def test_readonly_gate_missing_plan_and_exploration(runtime,monkeypatch):
    engine,f,client=runtime;c=client();before=rows(engine)
    assert c.post(START,json={}).status_code==404
    assert c.get(PATH).json()['plan'] is None and rows(engine)==before
    saved=issue(c);assert 'instruments' not in saved['plan']
    change(c,'Tuba');before=rows(engine)
    assert c.get(PATH).status_code==200 and rows(engine)==before
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(False,'open'))
    assert start(c,saved['start_context']).status_code==403
    assert c.get(PATH).status_code==403 and rows(engine)==before
    assert c.get('/account/me').status_code==200
    change(c,'Violin')
    assert not rows(engine)['weekly_instrument_plans']


def test_two_slots_retry_frozen_specs_and_exploration(runtime,monkeypatch):
    engine,f,client=runtime;c=client();issued=issue(c);before=protected(engine)
    r=start(c);assert r.status_code==200,r.text
    a=r.json()['assignment'];assert a['slot_no']==1 and a['instrument']=='flute'
    assert protected(engine)==before
    assert start(c).json()['assignment']==a
    change(c,'Tuba');before=protected(engine)
    b=start(c).json()['assignment'];assert b['slot_no']==2 and b['instrument']=='tuba'
    assert protected(engine)==before
    assert a['task_ref']!=b['task_ref']
    assert a['homework_reward_ref']==b['homework_reward_ref']=='clover-caper:2026-10-12:jar:3'
    assert a['completion_status']==b['completion_status']=='verification-unavailable'
    for instrument in ['Violin','Guitar']:
        change(c,instrument);before=rows(engine)
        assert start(c).status_code==409 and rows(engine)==before
        assert c.get('/account/appearance').json()['instrument']==instrument
    change(c,'Tuba');context=c.get(PATH).json()['start_context']
    choose(c,'Performance');before=rows(engine)
    def forbidden(*args):raise AssertionError('Saved Homework must never regenerate')
    monkeypatch.setattr(homework,'homework_spec',forbidden)
    result=c.get(PATH).json();assert result['start_context']==context
    assert result['plan']['instruments']==[a,b] and result['plan']['targets']==issued['plan']['targets']
    assert result['plan']['issued_game_mode']=='Challenge' and set(result['tasks']) == {'thirds'}
    assert start(c,context).json()['assignment']==b and rows(engine)==before
    other=client();after_login=rows(engine)
    assert start(other).json()['assignment']==b and rows(engine)==after_login


def test_aliases_unsupported_and_stale_context(runtime):
    engine,f,client=runtime;c=client();issue(c)
    # Accepted legacy aliases live in historical storage, not the selectable-label API.
    with f() as s:s.get(m.WoodchuckProfile,1).instrument='Piano';s.commit()
    context=c.get(PATH).json()['start_context'];a=start(c,context).json()['assignment']
    assert a['instrument']=='piano-keyboard'
    with f() as s:s.get(m.WoodchuckProfile,1).instrument='Keyboard';s.commit()
    assert c.get(PATH).json()['start_context']==context
    before=rows(engine);assert start(c,context).json()['assignment']==a and rows(engine)==before
    for value in ['','unknown','Accordion','Drum Major']:
        with f() as s:s.get(m.WoodchuckProfile,1).instrument=value;s.commit()
        before=rows(engine)
        assert c.get(PATH).json()['start_context'] is None
        assert start(c,context).status_code==409 and rows(engine)==before
    change(c,'Flute');before=rows(engine)
    assert start(c,context).status_code==409 and rows(engine)==before
    context=c.get(PATH).json()['start_context']
    choose(c,'Beginner');assert c.get(PATH).json()['start_context']==context
    f.clock[0]+=timedelta(weeks=1);before=rows(engine)
    assert start(c,context).status_code==409 and rows(engine)==before
    assert c.post(START,json={}).status_code==404
    issue(c);before=rows(engine)
    assert start(c,context).status_code==409 and rows(engine)==before
    assert start(c).status_code==200


@pytest.mark.parametrize('mode',['Beginner','Challenge','Performance'])
def test_saved_examples_all_modes_and_foci(runtime,mode,record_property):
    engine,f,client=runtime;c=client();choose(c,mode)
    examples=[]
    for offset,focus in enumerate(FOCI):
        f.clock[0]=NOW+timedelta(weeks=offset)
        r=c.post(PATH,json={'focus':focus});assert r.status_code==200,r.text
        context=r.json()['start_context']
        choose(c,'Performance' if mode!='Performance' else 'Beginner')
        before=protected(engine);response=start(c,context);assert response.status_code==200,response.text
        a=response.json()['assignment'];assert protected(engine)==before
        with f() as s:
            plan=s.get(WeeklyPlan,r.json()['plan']['id']);row=s.get(WeeklyInstrumentPlan,(plan.id,1))
            assert a['homework']==row.homework_spec==homework.homework_spec(plan,'Flute')
            assert plan.issued_game_mode==mode
        assert a['homework']['presentation']=={'Beginner':'numbered-steps','Challenge':'repetition-metronome','Performance':'continuous-attempt'}[mode]
        blocks=a['homework']['suggested_blocks'];assert blocks['count']*blocks['minutes_each']==r.json()['plan']['targets']['homework_minutes']
        assert c.get(PATH).json()['plan']['instruments']==[a]
        assert a['completion_status']=='verification-unavailable'
        assert all(secret not in json.dumps(a) for secret in ['recent_results','Practice Rating','PRIVATE'])
        examples.append({'mode':mode,'focus':focus,'assignment':a})
        choose(c,mode)
    record_property('saved_homework_examples',json.dumps(examples))


def test_selectors_origin_account_and_revocation(runtime):
    engine,f,client=runtime;c=client();context=issue(c)['start_context']
    other=client(2);other_context=issue(other)['start_context'];before=rows(engine)
    for key in ['account','profile_id','instrument','week','slot','targets','homework','completion','focus','game_mode']:
        assert c.post(START,json={'start_context':context,key:'forged'}).status_code==422
        assert c.post(START,params={key:'forged'},json={'start_context':context}).status_code==422
    for invalid in [True,1,[],{},'x'*101]:
        assert c.post(START,json={'start_context':invalid}).status_code==422
    assert c.post(START,json={}).status_code==422
    for origin in ['http://foreign.invalid','http://testserver:9999','','null']:
        assert c.post(START,json={'start_context':context},headers={'Origin':origin}).status_code==403
    req=c.build_request('POST',START,json={'start_context':context});del req.headers['origin']
    assert c.send(req).status_code==403
    assert start(c,other_context).status_code==409
    assert c.post(START,json={'start_context':context},headers={'x-woodshed-account':'WL2-PLAN-2'}).status_code==401
    anon=TestClient(__import__('app.main',fromlist=['app']).app)
    assert anon.post(START,json={}).status_code==401
    assert rows(engine)==before
    cookie=c.cookies.get('session');assert c.post('/account/logout').status_code==200
    stale=TestClient(__import__('app.main',fromlist=['app']).app);stale.cookies.set('session',cookie)
    before=rows(engine);assert start(stale,context).status_code==401 and rows(engine)==before
    assert start(other,other_context).status_code==200
    assert not any(x['plan_id']==1 for x in rows(engine)['weekly_instrument_plans'])


@pytest.mark.parametrize('failure',['generator','flush','precommit','lost-response'])
def test_atomic_failures_and_lost_response_retry(runtime,monkeypatch,failure):
    engine,f,client=runtime;c=client();context=issue(c)['start_context'];before=rows(engine)
    original=f.class_.commit
    def generator(*args):raise RuntimeError('synthetic generator failure')
    def flush(session,ctx,instances):
        if any(isinstance(x,WeeklyInstrumentPlan) for x in session.new):raise RuntimeError('synthetic flush failure')
    def commit(session):
        assert session.scalar(select(WeeklyInstrumentPlan)) is not None
        if failure=='lost-response':original(session)
        raise RuntimeError('synthetic '+failure+' failure')
    with monkeypatch.context() as patch:
        if failure=='generator':patch.setattr(homework,'homework_spec',generator)
        elif failure=='flush':event.listen(f.class_,'before_flush',flush)
        else:patch.setattr(f.class_,'commit',commit)
        try:
            with pytest.raises(RuntimeError,match='synthetic'):start(c,context)
        finally:
            if failure=='flush':event.remove(f.class_,'before_flush',flush)
    after=rows(engine)
    if failure=='lost-response':
        assert len(after['weekly_instrument_plans'])==1
        assert protected(engine)=={k:v for k,v in before.items() if k!='weekly_instrument_plans'}
        result=start(c,context);assert result.status_code==200 and rows(engine)==after
    else:assert after==before


def test_unique_retry_is_narrow_and_reauthorizes(runtime,monkeypatch):
    engine,f,client=runtime;c=client();context=issue(c)['start_context'];assert start(c,context).status_code==200
    before=rows(engine);original=plans.start_current_instrument;calls=[];authorizations=[]
    auth=routes._authorized
    def checked(*args,**kwargs):authorizations.append(True);return auth(*args,**kwargs)
    def race(s,p,context,**kwargs):
        calls.append(p.id)
        if len(calls)==1:
            row=s.scalar(select(WeeklyInstrumentPlan));s.execute(WeeklyInstrumentPlan.__table__.insert().values(**{col.name:getattr(row,col.name) for col in WeeklyInstrumentPlan.__table__.c}))
        return original(s,p,context,**kwargs)
    monkeypatch.setattr(routes,'_authorized',checked)
    with monkeypatch.context() as patch:
        patch.setattr(plans,'start_current_instrument',race)
        assert start(c,context).status_code==200
    assert calls==[1,1] and len(authorizations)>=4 and rows(engine)==before
    calls.clear()
    def invalid(s,p,context,**kwargs):
        calls.append(p.id);s.execute(text('UPDATE weekly_instrument_plans SET slot_no=3'))
    with monkeypatch.context() as patch:
        patch.setattr(plans,'start_current_instrument',invalid)
        with pytest.raises(IntegrityError):start(c,context)
    assert calls==[1] and rows(engine)==before


def test_populated_history_and_reward_records_unchanged(db,monkeypatch):
    command.upgrade(config(),'head');seed_history(db)
    from app.age_privacy import declare_age
    with Session(db) as s:
        p=s.get(m.WoodchuckProfile,1);p.game_mode='Challenge';declare_age(s,1,'adult');s.commit()
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'))
    with Session(db) as s:plans.issue_current_plan(s,s.get(m.WoodchuckProfile,1),FOCI[0],now=NOW);s.commit()
    before=protected(db)
    with Session(db) as s:
        p=s.get(m.WoodchuckProfile,1);plan=plans.load_current_plan(s,1,now=NOW)
        a=plans.start_current_instrument(s,p,plans.start_context(p,plan),now=NOW);s.commit()
    assert a['completion_status']=='verification-unavailable' and protected(db)==before
    assert len(rows(db)['weekly_instrument_plans'])==1


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('case',['same-instrument','remaining-slot','stale-instrument','week-rollover','mode-change','rotated','revoked'])
def test_postgres_start_waits_and_rechecks(runtime,case,record_property):
    engine,f,client=runtime;c1,c2=client(),client();context=issue(c1)['start_context']
    if case in ('remaining-slot','stale-instrument'):
        assert start(c1,context).status_code==200;change(c1,'Tuba');context=c1.get(PATH).json()['start_context']
    if case=='revoked':c2.cookies.update(c1.cookies)
    before=protected(engine);pids={};held=Event();release=Event()
    def hold(s):
        if not held.is_set():
            s.flush()  # Instrument endpoint's real UPDATE acquires its row lock.
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            held.set();assert release.wait(15)
    def query(c,cursor,statement,parameters,ctx,many):
        if 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement and held.is_set():pids['second']=c.connection.driver_connection.info.backend_pid
    def fence():
        with f() as s:
            p=s.scalar(select(m.WoodchuckProfile).where(m.WoodchuckProfile.id==1).with_for_update())
            if case=='rotated':p.session_version+=1
            s.commit()
    event.listen(engine,'before_cursor_execute',query);event.listen(f.class_,'before_commit',hold)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            if case=='stale-instrument':first=pool.submit(c1.patch,'/account/profile/instrument',json={'instrument':'Violin'})
            elif case=='mode-change':first=pool.submit(c1.patch,'/account/profile/game-mode',json={'game_mode':'Performance'})
            elif case in ('rotated','revoked','week-rollover'):first=pool.submit(fence)
            else:first=pool.submit(start,c1,context)
            assert held.wait(10)
            pending=pool.submit(start,c2,context)
            try:
                observe_block(engine,pids,record_property,case)
                if case=='revoked':assert c1.post('/account/logout').status_code==200
                if case=='week-rollover':f.clock[0]+=timedelta(weeks=1)
            finally:release.set()
            r=first.result(timeout=15)
            if r is not None:assert r.status_code==200,r.text
            result=pending.result(timeout=15)
            assert result.status_code=={'stale-instrument':409,'week-rollover':409,'rotated':401,'revoked':401}.get(case,200),result.text
    finally:
        release.set();event.remove(engine,'before_cursor_execute',query);event.remove(f.class_,'before_commit',hold)
    data=rows(engine);assigned=data['weekly_instrument_plans']
    assert len(assigned)=={'same-instrument':1,'remaining-slot':2,'stale-instrument':1,'mode-change':1}.get(case,0)
    if case in ('same-instrument','remaining-slot'):assert result.json()['assignment']==r.json()['assignment']
    if case=='mode-change':assert result.json()['assignment']['homework']['presentation']=='repetition-metronome'
    allowed={'woodchuck_profiles','woodchuck_states'} if case in ('stale-instrument','mode-change') else {'woodchuck_profiles'} if case=='rotated' else {'revoked_browser_sessions'} if case=='revoked' else set()
    for name,value in before.items():
        if name not in allowed:assert data[name]==value,name
    if case=='remaining-slot':
        change(c1,'Violin');after=rows(engine)
        assert start(c1).status_code==409 and rows(engine)==after
    if case=='stale-instrument':
        result=start(c1);assert result.status_code==200 and result.json()['assignment']['instrument']=='violin'
        assert len(rows(engine)['weekly_instrument_plans'])==2


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_c002_child_withdrawal_blocks_start_after_wait(db,monkeypatch,record_property):
    command.upgrade(config(),'head')
    from app.main import app
    from app import tester_enrollments as testers,child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry,CLAIMED
    f=sessionmaker(db,expire_on_commit=False,autoflush=False)
    for name,module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module,'SessionLocal'):monkeypatch.setattr(module,'SessionLocal',f)
    monkeypatch.setitem(FEATURES,'weekly_plans',Feature(True,'open'));monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    with f() as s:p=s.scalar(select(m.WoodchuckProfile));ident,wid=p.id,p.woodchuck_id
    c=TestClient(app);assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
    monkeypatch.setattr(plans,'utc_instant',lambda value=None:utc_instant(value or NOW));monkeypatch.setattr(routes,'utc_instant',lambda value=None:utc_instant(value or NOW))
    context=issue(c,'Beginner')['start_context'];valid=start(c,context);assert valid.status_code==200,valid.text
    historical=rows(db)['weekly_instrument_plans'];change(c,'Tuba');context=c.get(PATH).json()['start_context']
    pids={};held=Event();release=Event()
    def withdraw():
        with f() as s:
            s.scalar(select(m.WoodchuckProfile).where(m.WoodchuckProfile.id==ident).with_for_update())
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cursor,statement,parameters,ctx,many):
        if 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement and held.is_set():pids['second']=cn.connection.driver_connection.info.backend_pid
    event.listen(db,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(withdraw);assert held.wait(10)
            pending=pool.submit(start,c,context)
            try:observe_block(db,pids,record_property,'C002-consent-withdrawal')
            finally:release.set()
            first.result(timeout=15);r=pending.result(timeout=15);assert r.status_code==401,r.text
    finally:release.set();event.remove(db,'before_cursor_execute',query)
    assert rows(db)['weekly_instrument_plans']==historical
    assert c.get(PATH).status_code==401 and start(c,context).status_code==401
    with f() as s:
        assert s.scalar(select(ConsentEvidence)).withdrawn_at is not None
        assert not s.scalars(select(m.ClassroomStudentMembership)).all()
