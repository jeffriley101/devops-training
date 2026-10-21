"""Monday opening rewards through real routes and fresh migrated databases."""
from datetime import datetime, timedelta
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from app import login_streaks, weekly
from app.feature_access import FEATURES, Feature
from app.models import RewardGrant, WoodchuckProfile, WoodchuckState
from tests.test_weekly2_plans import db, runtime, rows, observe_block
from tests.test_weekly2_thirds import arcade_factory

MONDAY = datetime(2026, 10, 19, 13, 30, tzinfo=weekly.CENTRAL)
PATHS = ('/home', '/store', '/family/practice')


@pytest.fixture
def monday_db(runtime, monkeypatch):
    engine, f, client = runtime
    f.engine, f.client, f.clock = engine, client, [MONDAY]
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(False, 'open'))
    monkeypatch.setitem(FEATURES, 'weekly_monday_bonus', Feature(True, 'open'))
    original = weekly.utc_instant
    monkeypatch.setattr(weekly, 'utc_instant', lambda value=None: original(value or f.clock[0]))
    return f


def receipt(f, pid=1):
    with f() as s:
        return [(g.source_key, g.amount, g.reward_type, g.category_key, g.contest_result_id)
                for g in s.scalars(select(RewardGrant).where(RewardGrant.profile_id==pid,
                    RewardGrant.source_key.startswith('weekly-monday:')).order_by(RewardGrant.id))]


def balance(f, pid=1):
    with f() as s:
        state=s.get(WoodchuckState,pid)
        return state.state_json['progress']['credits'], state.revision


def unchanged(before, after, except_tables=()):
    for name in before:
        if name not in except_tables:assert before[name]==after[name],name


@pytest.mark.parametrize('path', PATHS)
def test_opening_once_independent_daily_and_no_prerequisites(monday_db,path,record_property):
    f=monday_db
    with f.client() as c:
        assert receipt(f)==[]  # PIN login alone pays only the existing daily reward.
        base=balance(f);before=rows(f.engine)
        assert c.get(path).status_code==200
        assert balance(f)==(base[0]+75,base[1]+1)
        assert receipt(f)==[('weekly-monday:2026-10-19',75,'dandelion',None,None)]
        saved=rows(f.engine)
        unchanged(before,saved,('reward_grants','woodchuck_states'))
        assert saved['weekly_plans']==saved['weekly_wheel_plays']==[]
        for page in PATHS:
            assert c.get(page).status_code==200
        assert c.post('/account/login-streak').status_code==200
        assert rows(f.engine)==saved
        with f.client() as second:
            assert second.get(path).status_code==200
        assert rows(f.engine)==saved
        record_property('persisted',json.dumps({'path':path,'before':base,'after':balance(f),'receipt':receipt(f)}))


@pytest.mark.parametrize('instant,week',[
    ('2026-10-19T00:00:00-05:00',None), ('2026-10-19T09:00:00-05:00',None),
    ('2026-10-19T13:29:59-05:00',None), ('2026-10-19T13:30:00-05:00','2026-10-19'),
    ('2026-10-20T00:00:00-05:00','2026-10-19'), ('2026-10-21T12:00:00-05:00','2026-10-19'),
    ('2026-10-25T23:59:59-05:00','2026-10-19'), ('2026-10-26T00:00:00-05:00',None),
    ('2026-10-26T13:29:00-05:00',None), ('2026-10-26T13:30:00-05:00','2026-10-26'),
    ('2026-03-08T01:59:59-06:00','2026-03-02'), ('2026-03-08T03:00:00-05:00','2026-03-02'),
    ('2026-03-09T04:59:59+00:00','2026-03-02'), ('2026-03-09T05:00:00+00:00',None),
    ('2026-03-09T18:29:59+00:00',None), ('2026-03-09T18:30:00+00:00','2026-03-09'),
    ('2026-11-01T01:30:00-05:00','2026-10-26'), ('2026-11-01T01:30:00-06:00','2026-10-26'),
    ('2026-11-02T05:59:59+00:00','2026-10-26'), ('2026-11-02T06:00:00+00:00',None),
    ('2026-11-02T19:29:59+00:00',None), ('2026-11-02T19:30:00+00:00','2026-11-02'),
])
def test_calendar_boundaries(monday_db,instant,week,record_property):
    f=monday_db;f.clock[0]=datetime.fromisoformat(instant)
    with f.client() as c:
        before=rows(f.engine);base=balance(f)
        assert c.get('/store').status_code==200
        awards=week is not None
        assert balance(f)==(base[0]+75*awards,base[1]+awards)
        if awards:assert receipt(f)==[('weekly-monday:'+week,75,'dandelion',None,None)]
        else:assert rows(f.engine)==before
        record_property('boundary',json.dumps({'instant':instant,'week':week,'receipt':receipt(f),'balance':balance(f)}))


@pytest.mark.parametrize('visits',[
    [('2026-10-19T09:00:00-05:00',None),('2026-10-19T13:30:00-05:00','2026-10-19'),
     ('2026-10-20T12:00:00-05:00','2026-10-19'),('2026-10-25T23:59:59-05:00','2026-10-19')],
    [('2026-10-21T12:00:00-05:00','2026-10-19'),('2026-10-25T12:00:00-05:00','2026-10-19'),
     ('2026-10-26T00:00:00-05:00',None),('2026-10-26T13:29:00-05:00',None),
     ('2026-10-26T13:30:00-05:00','2026-10-26')],
    [('2026-10-25T23:59:59-05:00','2026-10-19'),('2026-10-26T13:30:00-05:00','2026-10-26'),
     ('2026-11-18T12:00:00-06:00','2026-11-16')],
])
def test_weeklong_idempotency_and_no_missed_week_accumulation(monday_db,visits,record_property):
    f=monday_db;expected=[];observations=[]
    with f.client() as c:
        base=balance(f)
        for index,(instant,week) in enumerate(visits):
            f.clock[0]=datetime.fromisoformat(instant)
            before=rows(f.engine)
            new=week is not None and week not in expected
            assert c.get(PATHS[index%len(PATHS)]).status_code==200
            if new:expected.append(week)
            else:assert rows(f.engine)==before
            assert balance(f)==(base[0]+75*len(expected),base[1]+len(expected))
            assert receipt(f)==[('weekly-monday:'+w,75,'dandelion',None,None) for w in expected]
            observations.append({'instant':instant,'receipt':receipt(f),'balance':balance(f)})
        record_property('visits',json.dumps(observations))


@pytest.mark.parametrize('headers',[
    {'Sec-Fetch-Mode':'cors'}, {'Sec-Fetch-Dest':'empty'}, {'Purpose':'prefetch'},
    {'Sec-Purpose':'prefetch;prerender'}, {'Sec-Fetch-Mode':'no-cors'},
])
def test_polling_prefetch_do_not_award(monday_db,headers):
    f=monday_db
    with f.client() as c:
        before=rows(f.engine)
        for path in PATHS:assert c.get(path,headers=headers).status_code==200
        for path in ('/account/me','/account/state','/family/practice/data'):
            assert c.get(path).status_code==200
        for path in ('/weekly/pantry','/weekly/claims','/weekly/wheel'):
            assert c.get(path).status_code==403
        assert rows(f.engine)==before
        assert c.get('/home',headers={'Sec-Fetch-Mode':' Navigate ','Sec-Fetch-Dest':' Document '}).status_code==200
        assert len(receipt(f))==1


def test_disabled_gate_and_receipt_survives_spending(monday_db,monkeypatch):
    f=monday_db
    with f.client() as c:
        monkeypatch.setitem(FEATURES,'weekly_monday_bonus',Feature(False,'open'))
        before=rows(f.engine)
        for path in PATHS:assert c.get(path).status_code==200
        assert rows(f.engine)==before
        monkeypatch.setitem(FEATURES,'weekly_monday_bonus',Feature(True,'open'))
        assert c.get('/home').status_code==200
        # Synthetic already-spent protected balance: an extant receipt never pays again.
        with f.begin() as s:
            state=s.get(WoodchuckState,1);state.state_json={'progress':{'credits':0}};state.revision+=1
        before=rows(f.engine)
        assert c.get('/home').status_code==200 and rows(f.engine)==before
        f.clock[0]+=timedelta(days=7)
        assert c.get('/home').status_code==200 and balance(f)[0]==75 and len(receipt(f))==2


@pytest.mark.parametrize('stage',['grant','balance','flush','commit','template'])
def test_rollback_and_recovery(monday_db,monkeypatch,stage):
    from app import contests, main, weekly_rewards
    f=monday_db
    with f.client() as c:
        before=rows(f.engine)
        def fail(*a,**k):raise RuntimeError('synthetic Monday '+stage)
        with monkeypatch.context() as mp:
            if stage in ('grant','balance'):
                module,name=(weekly_rewards,'grant') if stage=='grant' else (contests,'_add_dandelion')
                original=getattr(module,name)
                def after(*a,**k):original(*a,**k);fail()
                mp.setattr(module,name,after)
            elif stage=='template':mp.setattr(main.templates,'TemplateResponse',fail)
            else:
                def failure(s,*args):
                    if stage=='commit':s.flush()
                    fail()
                event.listen(f.class_,'after_flush' if stage=='flush' else 'before_commit',failure)
            try:
                with pytest.raises(RuntimeError,match='synthetic Monday'):c.get('/store')
            finally:
                if stage in ('flush','commit'):event.remove(f.class_,'after_flush' if stage=='flush' else 'before_commit',failure)
        assert rows(f.engine)==before
        assert c.get('/store').status_code==200 and len(receipt(f))==1


def test_account_binding_and_revocation(monday_db):
    from app.main import app
    f=monday_db
    with f.client() as c:
        before=rows(f.engine)
        assert c.get('/home',headers={'x-woodshed-account':'WL2-PLAN-2'}).status_code==401
        assert rows(f.engine)==before
        cookie=c.cookies.get('session');assert c.post('/account/logout').status_code==200
        before=rows(f.engine)
        with TestClient(app) as stale:
            stale.cookies.set('session',cookie)
            assert stale.get('/family/practice').status_code==401
        assert rows(f.engine)==before
        with f.client(2) as other:
            assert other.get('/home').status_code==200
        assert receipt(f,1)==[] and len(receipt(f,2))==1


def test_director_and_mixed_parent_context_no_bonus(monday_db):
    from app.models import ProfileCapability
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    from app.main import app, SESSION_SECRET
    def signed_client(facts):
        # Negative role-context fixtures only; no positive authorization is inferred.
        c=TestClient(app)
        c.cookies.set('session',TimestampSigner(SESSION_SECRET).sign(b64encode(json.dumps(facts).encode())).decode())
        return c
    f=monday_db
    with f.client() as c:
        with f.begin() as s:s.add(ProfileCapability(profile_id=1,capability='band_director'))
        before=rows(f.engine)
        for path in PATHS:assert c.get(path).status_code==200
        assert rows(f.engine)==before
    for facts in ({'child_parent_consent':900}, {'child_director_permission':900},
                  {'woodchuck_profile_id':2,'woodchuck_session_version':0,'child_parent_consent':900}):
        with signed_client(facts) as c:
            before=rows(f.engine)
            c.get('/home')
            unchanged(before,rows(f.engine),('revoked_browser_sessions',))
            assert receipt(f,2)==[]


def test_default_off_catalog():
    assert FEATURES['weekly_monday_bonus']==Feature(False,'open')


def test_authorized_child_and_withdrawn_private_entry(arcade_factory,monkeypatch):
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    f=arcade_factory;monkeypatch.setattr(testers,'clock',lambda:CLAIMED)
    # Retained activation assertions use synthetic provider evidence, never a live provider.
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(f,monkeypatch,'/prebeta/C002','C002','DIRECTOR1')
    original=weekly.utc_instant
    monkeypatch.setattr(weekly,'utc_instant',lambda value=None:original(value or MONDAY))
    monkeypatch.setitem(FEATURES,'weekly_monday_bonus',Feature(True,'open'))
    with f() as s:
        p=s.scalar(select(WoodchuckProfile));pid,wid=p.id,p.woodchuck_id
    with TestClient(app) as c:
        assert c.post('/account/login',data={'woodchuck_id':wid,'pin':'2468'}).status_code==200
        base=balance(f,pid);before=rows(f.engine)
        response=c.get('/family/practice')
        assert response.status_code==200 and 'practice' in response.text.lower()
        assert balance(f,pid)==(base[0]+75,base[1]+1)
        assert receipt(f,pid)==[('weekly-monday:2026-10-19',75,'dandelion',None,None)]
        unchanged(before,rows(f.engine),('reward_grants','woodchuck_states'))
        with f.begin() as s:consent.withdraw_evidence(s,s.scalar(select(ConsentEvidence)))
        before=rows(f.engine)
        assert c.get('/family/practice').status_code==401
        unchanged(before,rows(f.engine),('revoked_browser_sessions',))
