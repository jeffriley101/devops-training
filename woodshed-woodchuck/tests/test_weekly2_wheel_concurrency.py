"""Observed PostgreSQL lock waits with preloaded real student credentials."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text

from app import weekly_wheel as wheel
from app.feature_access import FEATURES, Feature
from app.models import RewardGrant, WoodchuckProfile
from app.weekly_models import WeeklyWheelPlay
from app.weekly import utc_instant
from tests.test_weekly2_wheel import (PATH, seed, start, rotate, rows, assert_unchanged,
    wheel_db, runtime, db, arcade_factory, observe_block, NOW)


@pytest.mark.parametrize('kind',['same-key','overspend','daily-cap','continuation'])
@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_postgres_overlapping_settlement(wheel_db,monkeypatch,record_property,kind):
    from app.main import app
    f=wheel_db;seed(f,30 if kind=='daily-cap' else 3)
    monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:9750)
    a=f.client();b=TestClient(app);b.cookies.update(a.cookies)
    path=PATH+'/plays';body={'request_key':'first'};other={'request_key':'second' if kind in ('overspend','daily-cap') else 'first'}
    if kind=='daily-cap':
        for i in range(8):start(a,'prior-'+str(i))
    if kind=='continuation':
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950)
        pending=start(a)['play'];path+=f"/{pending['id']}/rotations";body=other={'rotation':2}
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:0)
    before=rows(f.engine);held=Event();release=Event();pids={}
    def hold(s):
        if not held.is_set():
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            assert s.connection().get_isolation_level()=='READ COMMITTED'
            held.set();assert release.wait(15)
    def query(cn,cursor,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(f.class_,'before_commit',hold);event.listen(f.engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(a.post,path,json=body);assert held.wait(10)
            second=pool.submit(b.post,path,json=other)
            try:observe_block(f.engine,pids,record_property,'wheel-'+kind)
            finally:release.set()
            x,y=first.result(timeout=15),second.result(timeout=15)
            assert x.status_code==200,x.text
            assert y.status_code==(409 if kind in ('overspend','daily-cap') else 200),y.text
            if kind in ('same-key','continuation'):assert x.json()==y.json()
    finally:
        release.set();event.remove(f.class_,'before_commit',hold);event.remove(f.engine,'before_cursor_execute',query);a.close();b.close()
    after=rows(f.engine)
    assert_unchanged(before,after,{'weekly_wheel_plays','reward_grants','woodchuck_states','camp_point_awards'})
    with f() as s:
        assert s.scalar(select(func.count()).select_from(WeeklyWheelPlay))==(9 if kind=='daily-cap' else 1)
        assert wheel.wallet(s,1)['pickled_carrot']==(3 if kind=='daily-cap' else 0)
        if kind=='continuation':
            assert s.scalar(select(WeeklyWheelPlay)).outcomes==['spin_again','activity']
            assert s.scalar(select(func.count()).select_from(RewardGrant).where(RewardGrant.reward_type=='activity'))==1
        else:
            assert len(after['reward_grants'])==len(before['reward_grants'])+2


@pytest.mark.parametrize('change',['version','deleted','nonce'])
@pytest.mark.parametrize('operation',['fund','continue'])
@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_postgres_revocation_after_wait(wheel_db,monkeypatch,record_property,change,operation):
    from app.main import app
    f=wheel_db;seed(f);monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950)
    c=f.client();path=PATH+'/plays';body={'request_key':'blocked'}
    if operation=='continue':
        pending=start(c)['play'];path+=f"/{pending['id']}/rotations";body={'rotation':2}
    monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:0)
    before=rows(f.engine);held=Event();release=Event();pids={}
    def holder():
        with f() as s:
            p=s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            if change=='version':p.session_version+=1
            if change=='deleted':p.status='deleted'
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cursor,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(f.engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(holder);assert held.wait(10)
            second=pool.submit(c.post,path,json=body)
            try:
                observe_block(f.engine,pids,record_property,'wheel-'+operation+'-'+change)
                if change=='nonce':
                    with TestClient(app) as logout:
                        logout.cookies.update(c.cookies);assert logout.post('/account/logout').status_code==200
            finally:release.set()
            first.result(timeout=15);result=second.result(timeout=15);assert result.status_code==401,result.text
            assert c.get(PATH).status_code==401
    finally:release.set();event.remove(f.engine,'before_cursor_execute',query);c.close()
    assert_unchanged(before,rows(f.engine),{'woodchuck_profiles','revoked_browser_sessions'})


@pytest.mark.parametrize('operation',['fund','continue'])
@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_postgres_parent_consent_withdrawal_after_wait(arcade_factory,monkeypatch,record_property,operation):
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    f=arcade_factory;monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    with f() as s:p=s.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    monkeypatch.setitem(FEATURES,'weekly_wheel',Feature(True,'open'));f.clock=[NOW]
    monkeypatch.setattr(wheel,'utc_instant',lambda value=None:utc_instant(value or f.clock[0]))
    monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:6950);seed(f,6,pid)
    c=TestClient(app);assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
    # An ordinary authorized child can fund privately before withdrawal.
    pending=start(c)['play'];assert c.get(PATH).status_code==200
    path=PATH+'/plays';body={'request_key':'blocked'}
    if operation=='continue':path+=f"/{pending['id']}/rotations";body={'rotation':2}
    else:
        monkeypatch.setattr(wheel.secrets,'randbelow',lambda n:0);rotate(c,pending,2)
    before=rows(f.engine);held=Event();release=Event();pids={}
    def withdraw():
        with f() as s:
            s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==pid).with_for_update())
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cursor,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            backend=cn.connection.driver_connection.info.backend_pid
            if backend!=pids['first']:pids['second']=backend
    event.listen(f.engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(withdraw);assert held.wait(10)
            second=pool.submit(c.post,path,json=body)
            try:observe_block(f.engine,pids,record_property,'wheel-consent-'+operation)
            finally:release.set()
            first.result(timeout=15);r=second.result(timeout=15);assert r.status_code==401,r.text
    finally:release.set();event.remove(f.engine,'before_cursor_execute',query)
    after=rows(f.engine)
    for name in ('weekly_wheel_plays','reward_grants','woodchuck_states','camp_point_awards','contest_results',
                 'practice_charts','weekly_plans','weekly_instrument_plans'):
        assert after[name]==before[name],name
    assert c.get(PATH).status_code==401;c.close()
