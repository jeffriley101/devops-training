"""Migrated synthetic reservation records; no rewards or presentation leases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import json

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import event, select, text

from app import weekly_routes
from app.age_models import AccountPrivacy
from app.db import Base
from app.feature_access import FEATURES, Feature
from app.models import WoodchuckProfile, WeeklyNewsletterPresentation
from tests.test_weekly2_newsletter import db, runtime, NOW, rows, config, arcade_factory
from tests.test_weekly2_plans import observe_block

PATH = '/weekly/newsletter/presentation'


@pytest.fixture
def student(runtime):
    with runtime.begin() as s:
        for pid in (1, 2):
            s.get(AccountPrivacy, pid).age_band = '13to17'
    return runtime


def post(client, week='2026-10-19', **kwargs):
    return client.post(PATH, json={'expected_week': week}, **kwargs)


@pytest.mark.parametrize('day,hour,minute,second,eligible', [
    (0,13,29,59,False),(0,13,30,0,True),(1,0,0,0,True),
    (2,12,0,0,True),(6,23,59,59,True),(7,0,0,0,False),(7,13,29,59,False),(7,13,30,0,True)])
def test_publication(student, day,hour,minute,second,eligible):
    f=student;f.clock[0]=(NOW+timedelta(days=day)).replace(hour=hour,minute=minute,second=second)
    week='2026-10-26' if day==7 else '2026-10-19'
    with f.client() as c:
        before=rows(f.engine);r=post(c,week);assert r.status_code==200,r.text
        assert r.json()['status']==('reserved' if eligible else 'unavailable')
        after=rows(f.engine)
        for key in before:
            if key!='weekly_newsletter_presentations':assert before[key]==after[key],key
        assert len(after['weekly_newsletter_presentations'])==int(eligible)
        assert r.headers['cache-control']=='private, no-store'


@pytest.mark.parametrize('age_band', ['13to17', 'adult'])
def test_retry_other_profile_new_week_and_reads(student, age_band):
    f=student
    with f.begin() as s:
        for pid in (1, 2):s.get(AccountPrivacy,pid).age_band=age_band
    with f.client() as a, f.client(2) as b:
        assert '/static/js/weekly-newsletter-auto.js' in a.get('/home').text
        before=rows(f.engine)
        for _ in range(2):assert a.get('/weekly/newsletter').status_code==200
        assert rows(f.engine)==before
        assert post(a).json()['status']=='reserved'
        after=rows(f.engine)
        for key in before:
            if key!='weekly_newsletter_presentations':assert before[key]==after[key],key
        assert post(a).json()['status']=='already_reserved'
        assert a.get('/weekly/newsletter').status_code==200
        assert rows(f.engine)==after
        assert post(b).json()['status']=='reserved'
        with f.begin() as s:s.get(WoodchuckProfile,1).instrument='Tuba'
        assert post(a).json()['status']=='already_reserved'
        f.clock[0]=NOW+timedelta(days=7)
        assert post(a,'2026-10-19').json()['status']=='unavailable'
        assert post(a,'2026-10-26').json()['status']=='reserved'
        with f() as s:assert s.query(WeeklyNewsletterPresentation).count()==3


@pytest.mark.parametrize('age_band', ['13to17', 'adult'])
@pytest.mark.parametrize('denial',['gate','pending','foreign','origin','selector','extra','revoked'])
def test_denial(student,monkeypatch,denial,age_band):
    f=student
    with f.begin() as s:s.get(AccountPrivacy,1).age_band=age_band
    with f.client() as c:
        if denial=='gate':monkeypatch.setitem(FEATURES,'weekly_newsletter',Feature(False,'open'))
        if denial=='pending':
            with f.begin() as s:s.get(AccountPrivacy,1).age_band='under13'
        if denial=='revoked':
            with f.begin() as s:s.get(WoodchuckProfile,1).session_version+=1
        cookie=c.cookies.get('session')
        before=rows(f.engine)
        if denial=='foreign':r=post(c,headers={'x-woodshed-account':'WL2-NEWS-2'})
        elif denial=='origin':r=post(c,headers={'Origin':'https://foreign.invalid'})
        elif denial=='selector':r=c.post(PATH+'?profile_id=2',json={'expected_week':'2026-10-19'})
        elif denial=='extra':r=c.post(PATH,json={'expected_week':'2026-10-19','profile_id':2})
        else:r=post(c)
        assert r.status_code in (401,403,422),r.text
        after=rows(f.engine)
        # Native revocation may retire the browser session; never presentation/business writes.
        if denial=='revoked':
            from tests.test_weekly2_game_mode import assert_only_session_retirement
            assert_only_session_retirement(before,after,cookie)
        else:assert before==after


def test_rollback(student):
    f=student
    with f.client() as c:
        before=rows(f.engine)
        def fail(s):raise RuntimeError('synthetic precommit failure')
        event.listen(f.class_,'before_commit',fail)
        try:
            with pytest.raises(RuntimeError,match='synthetic precommit'):post(c)
        finally:event.remove(f.class_,'before_commit',fail)
        assert rows(f.engine)==before
        assert post(c).json()['status']=='reserved'
        assert post(c).json()['status']=='already_reserved'


def test_migration_empty_and_populated_refusal(student):
    f=student
    with f.engine.connect() as c:
        differences=compare_metadata(MigrationContext.configure(c),Base.metadata)
        assert not [d for d in differences if 'weekly_newsletter_presentations' in repr(d)],differences
    command.downgrade(config(),'w25weekly004')
    command.upgrade(config(),'w25weekly005')
    with f.client() as c:assert post(c).json()['status']=='reserved'
    before=rows(f.engine)
    with pytest.raises(RuntimeError,match='newsletter_presentations_in_use'):
        command.downgrade(config(),'w25weekly004')
    assert rows(f.engine)==before


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('change',['duplicate','version','consent'])
def test_real_lock_wait(student,change,record_property):
    f=student;a,b=f.client(),f.client();held,release=Event(),Event();pids={}
    def holder():
        with f.begin() as s:
            p=s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            if change=='version':p.session_version+=1
            elif change=='consent':s.get(AccountPrivacy,1).age_band='under13'
            else:s.add(WeeklyNewsletterPresentation(profile_id=1,week_start=NOW.date(),reserved_at=NOW))
            s.flush();held.set();assert release.wait(15)
    def capture(conn,cur,statement,*args):
        if held.is_set() and 'FOR UPDATE' in statement:
            pid=conn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    def hold_commit(session):
        if not held.is_set():
            pids['first']=session.scalar(text('SELECT pg_backend_pid()'));held.set();assert release.wait(15)
    if change=='duplicate':event.listen(f.class_,'before_commit',hold_commit)
    event.listen(f.engine,'before_cursor_execute',capture)
    before=rows(f.engine)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(post,a) if change=='duplicate' else pool.submit(holder)
            assert held.wait(10)
            two=pool.submit(post,b)
            try:observe_block(f.engine,pids,record_property,'newsletter-'+change)
            finally:release.set()
            first=one.result(timeout=15);r=two.result(timeout=15)
            if change=='duplicate':assert first.json()['status']=='reserved'
    finally:
        release.set();event.remove(f.engine,'before_cursor_execute',capture);a.close();b.close()
        if change=='duplicate':event.remove(f.class_,'before_commit',hold_commit)
    assert r.status_code==(200 if change=='duplicate' else 401 if change=='version' else 403),r.text
    if change=='duplicate':assert r.json()['status']=='already_reserved'
    after=rows(f.engine)
    for name in ('reward_grants','woodchuck_states','weekly_plans','weekly_instrument_plans','contest_results'):
        assert before[name]==after[name],name
    assert len(after['weekly_newsletter_presentations'])==int(change=='duplicate')


def test_authorized_child_withdrawal_and_privacy(arcade_factory,monkeypatch):
    from app import tester_enrollments as testers,child_authorization as consent
    from app.child_models import ConsentEvidence
    from app.main import app
    from fastapi.testclient import TestClient
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry,CLAIMED
    from tests.test_weekly2_game_mode import assert_only_session_retirement
    monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(arcade_factory,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    monkeypatch.setitem(FEATURES,'weekly_newsletter',Feature(True,'open'))
    monkeypatch.setattr(weekly_routes,'utc_instant',lambda:NOW)
    with arcade_factory() as s:wid=s.scalar(select(WoodchuckProfile)).woodchuck_id
    with TestClient(app) as c:
        assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
        assert '/static/js/weekly-newsletter-auto.js' in c.get('/home').text
        before=rows(arcade_factory.engine)
        response=post(c);assert response.status_code==200,response.text
        assert response.json()['status']=='reserved' and wid not in response.text
        after=rows(arcade_factory.engine)
        for key in before:
            if key!='weekly_newsletter_presentations':assert before[key]==after[key]
        assert post(c).json()['status']=='already_reserved'
        assert c.get('/weekly/newsletter').status_code==200
        assert rows(arcade_factory.engine)==after
        with arcade_factory.begin() as s:consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
        before=rows(arcade_factory.engine);cookie=c.cookies.get('session')
        assert post(c).status_code in (401,403)
        assert_only_session_retirement(before,rows(arcade_factory.engine),cookie)


@pytest.mark.parametrize('age_band', ['13to17', 'adult'])
def test_director_and_management_session_context(student, age_band):
    from base64 import b64decode,b64encode
    from itsdangerous import TimestampSigner
    from app.main import SESSION_SECRET
    from app.models import ProfileCapability
    f=student
    with f.begin() as s:s.get(AccountPrivacy,1).age_band=age_band
    with f.client() as c:
        cookie=c.cookies.get('session')
        # Negative fixtures add adult authority; never used to authorize a success.
        for key in ('trusted_verifier_id','site_admin_fingerprint','contest_admin_token_fingerprint','child_parent_consent','child_director_permission'):
            facts=json.loads(b64decode(cookie.split('.')[0]));facts[key]='synthetic-adult-context'
            c.cookies.clear();c.cookies.set('session',TimestampSigner(SESSION_SECRET).sign(b64encode(json.dumps(facts).encode())).decode())
            before=rows(f.engine);assert post(c).status_code==403;assert rows(f.engine)==before
            assert '/static/js/weekly-newsletter-auto.js' not in c.get('/home').text
        c.cookies.clear();c.cookies.set('session',cookie)
        with f.begin() as s:s.add(ProfileCapability(profile_id=1,capability='band_director'))
        before=rows(f.engine);assert post(c).status_code==403;assert rows(f.engine)==before
        assert '/static/js/weekly-newsletter-auto.js' not in c.get('/home').text


def test_two_students_one_payer(student):
    from app.models import BillingAccount,Membership,MembershipSeat
    f=student
    with f.begin() as s:
        payer=BillingAccount(profile_id=1);s.add(payer);s.flush()
        member=Membership(billing_account_id=payer.id,source='manual',starts_at=NOW);s.add(member);s.flush()
        s.add_all([MembershipSeat(membership_id=member.id,profile_id=p,slot_number=p) for p in (1,2)])
    with f.client() as a,f.client(2) as b:
        before=rows(f.engine)
        assert post(a).json()['status']=='reserved'
        assert post(b).json()['status']=='reserved'
        after=rows(f.engine)
        assert len(after['weekly_newsletter_presentations'])==2
        for name in before:
            if name!='weekly_newsletter_presentations':assert before[name]==after[name],name


@pytest.mark.parametrize('db',['postgresql'],indirect=True)
@pytest.mark.parametrize('change',['nonce','rollover'])
def test_wait_rechecks_nonce_and_publication(student,change,record_property):
    from base64 import b64decode
    from app.session_revocations import RevokedBrowserSession,NONCE,digest
    f=student;c=f.client();held,release=Event(),Event();pids={}
    nonce=json.loads(b64decode(c.cookies.get('session').split('.')[0]))[NONCE]
    def holder():
        with f.begin() as s:
            s.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
            pids['first']=s.scalar(text('SELECT pg_backend_pid()'))
            if change=='nonce':s.add(RevokedBrowserSession(token_hash=digest(nonce),revoked_at=NOW,expires_at=NOW+timedelta(days=14)))
            s.flush();held.set();assert release.wait(15)
    def capture(conn,cur,statement,*args):
        if held.is_set() and 'FOR UPDATE' in statement:
            pid=conn.connection.driver_connection.info.backend_pid
            if pid!=pids['first']:pids['second']=pid
    before=rows(f.engine);event.listen(f.engine,'before_cursor_execute',capture)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(holder);assert held.wait(10);two=pool.submit(post,c)
            try:
                observe_block(f.engine,pids,record_property,'newsletter-'+change)
                if change=='rollover':f.clock[0]=(NOW+timedelta(days=7)).replace(hour=0)
            finally:release.set()
            one.result(timeout=15);r=two.result(timeout=15)
    finally:release.set();event.remove(f.engine,'before_cursor_execute',capture);c.close()
    if change=='nonce':assert r.status_code==401
    else:assert r.status_code==200 and r.json()['status']=='unavailable'
    after=rows(f.engine)
    for name in before:
        if name!='revoked_browser_sessions':assert before[name]==after[name],name
