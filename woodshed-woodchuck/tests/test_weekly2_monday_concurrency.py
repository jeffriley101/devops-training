"""PostgreSQL openings with observed waits and preloaded authenticated sessions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, text

from app.models import WoodchuckProfile
from tests.test_weekly2_monday import (db, runtime, monday_db, rows, receipt, balance,
    unchanged, observe_block, MONDAY, arcade_factory)


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_two_sessions_and_lost_response(monday_db,record_property):
    f=monday_db;a,b=f.client(),f.client();base=balance(f);before=rows(f.engine)
    held,release=Event(),Event();pids={}
    def hold(s):
        if not held.is_set():
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            held.set();assert release.wait(15)
    def query(c,cur,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=c.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(f.class_,'before_commit',hold);event.listen(f.engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(a.get,'/home');assert held.wait(10)
            second=pool.submit(b.get,'/family/practice')
            try:observe_block(f.engine,pids,record_property,'monday-two-sessions')
            finally:release.set()
            assert first.result(timeout=15).status_code==second.result(timeout=15).status_code==200
    finally:
        release.set();event.remove(f.class_,'before_commit',hold);event.remove(f.engine,'before_cursor_execute',query)
    assert balance(f)==(base[0]+75,base[1]+1) and len(receipt(f))==1
    saved=rows(f.engine);unchanged(before,saved,('reward_grants','woodchuck_states'))
    # The completed response can be discarded; the next opening returns durable truth.
    assert a.get('/store').status_code==200 and rows(f.engine)==saved
    a.close();b.close()


@pytest.mark.parametrize('change',['version','deleted','nonce','consent','tuesday','week-expiry','new-week-release'])
@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_reauthorize_after_wait(monday_db,change,monkeypatch,record_property):
    from app.main import app
    from app.age_models import AccountPrivacy
    from datetime import datetime, timedelta
    f=monday_db;c=f.client();before=rows(f.engine);held,release=Event(),Event();pids={}
    if change in ('week-expiry','new-week-release'):
        f.clock[0]=datetime.fromisoformat('2026-10-25T23:59:59-05:00')
    base=balance(f)
    def holder():
        with f() as s:
            p=s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            if change=='version':p.session_version+=1
            if change=='deleted':p.status='deleted'
            if change=='consent':s.get(AccountPrivacy,1).age_band='under13'
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cur,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            pid=cn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    event.listen(f.engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(holder);assert held.wait(10)
            second=pool.submit(c.get,'/store')
            try:
                observe_block(f.engine,pids,record_property,'monday-'+change)
                if change=='nonce':
                    with TestClient(app) as logout:
                        logout.cookies.update(c.cookies);assert logout.post('/account/logout').status_code==200
                if change=='tuesday':f.clock[0]+=timedelta(days=1)
                if change=='week-expiry':f.clock[0]=datetime.fromisoformat('2026-10-26T00:00:00-05:00')
                if change=='new-week-release':f.clock[0]=datetime.fromisoformat('2026-10-26T13:30:00-05:00')
            finally:release.set()
            first.result(timeout=15);r=second.result(timeout=15)
            assert r.status_code==(200 if change in ('tuesday','week-expiry','new-week-release') else 403 if change=='consent' else 401),r.text
    finally:release.set();event.remove(f.engine,'before_cursor_execute',query);c.close()
    if change in ('tuesday','new-week-release'):
        week='2026-10-19' if change=='tuesday' else '2026-10-26'
        assert receipt(f)==[('weekly-monday:'+week,75,'dandelion',None,None)]
        assert balance(f)==(base[0]+75,base[1]+1)
        unchanged(before,rows(f.engine),('reward_grants','woodchuck_states'))
    else:
        assert receipt(f)==[]
        unchanged(before,rows(f.engine),('woodchuck_profiles','account_privacy_rules','revoked_browser_sessions'))
    record_property('post_wait_receipt',json.dumps(receipt(f)))


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
def test_actual_child_consent_withdrawal_after_wait(arcade_factory,monkeypatch,record_property):
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent, weekly
    from app.child_models import ConsentEvidence
    from app.feature_access import FEATURES, Feature
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    f=arcade_factory;monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    original=weekly.utc_instant;f.clock=[MONDAY]
    monkeypatch.setattr(weekly,'utc_instant',lambda value=None:original(value or f.clock[0]))
    monkeypatch.setitem(FEATURES,'weekly_monday_bonus',Feature(True,'open'))
    with f() as s:p=s.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    c=TestClient(app);assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
    # Authorized private child control, no plan or Game Mode required.
    assert c.get('/family/practice').status_code==200 and len(receipt(f,pid))==1
    from datetime import timedelta
    f.clock[0]+=timedelta(days=7)
    before=rows(f.engine);held,release=Event(),Event();pids={}
    def withdraw():
        with f() as s:
            s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==pid).with_for_update())
            consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15);s.commit()
    def query(cn,cur,statement,*args):
        if held.is_set() and 'woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            backend=cn.connection.driver_connection.info.backend_pid
            if backend!=pids['first']:pids['second']=backend
    event.listen(f.engine,'before_cursor_execute',query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(withdraw);assert held.wait(10)
            second=pool.submit(c.get,'/family/practice')
            try:observe_block(f.engine,pids,record_property,'monday-child-withdrawal')
            finally:release.set()
            first.result(timeout=15);assert second.result(timeout=15).status_code==401
    finally:release.set();event.remove(f.engine,'before_cursor_execute',query);c.close()
    after=rows(f.engine)
    for name in before:
        if name not in ('woodchuck_profiles','child_consent_evidence','revoked_browser_sessions'):
            assert after[name]==before[name],name
