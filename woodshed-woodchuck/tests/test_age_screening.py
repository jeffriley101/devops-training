"""Focused synthetic age-gate tests; isolated PostgreSQL schemas, no email."""
import json
import os
import sys
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import Base
from app.models import (WoodchuckProfile, WoodchuckState, AccountPrivacy, AnalyticsEvent,
    RewardGrant, TrustedVerifier, StudentVerifierConnection, PracticeChart,
    PracticeChartVerification, ArcadeHighScore, BillingAccount, Membership, MembershipSeat)
from app.security import hash_pin
from app.age_privacy import declare_age, eligible, AgeScreenRequired, chart_public

@pytest.fixture
def age_db(monkeypatch):
    url = os.getenv('WW_AGE_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set WW_AGE_TEST_POSTGRES_URL to a disposable local PostgreSQL database.')
    control = create_engine(url)
    schema = 'age_test_' + uuid.uuid4().hex
    with control.begin() as c:
        c.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url, connect_args={'options': f'-c search_path={schema} -c statement_timeout=5000'})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    with factory() as s:
        for label, credit in [('A',37),('B',83)]:
            p=WoodchuckProfile(woodchuck_id='WC-AGE-'+label,display_name='Synthetic '+label,
                pin_hash=hash_pin('2468'),instrument='Flute',level='Beginner',goal='Practice every day',plunge_best_score=19)
            s.add(p);s.flush()
            s.add(WoodchuckState(profile_id=p.id,revision=7,state_json={
                'account':{'woodchuckId':p.woodchuck_id,'authenticated':True,'serverRevision':7},
                'profile':{'woodchuckName':p.display_name,'instrument':'Flute','level':'Beginner','goal':'Practice every day'},
                'progress':{'credits':credit},'practiceLog':[{'note':'saved '+label}],
            }))
        s.commit()
    yield factory
    engine.dispose()
    with control.begin() as c:c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    control.dispose()

def counts(factory):
    with factory() as s:return {t.name:s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}

def login(client,label='A'):
    r=client.post('/account/login',data={'woodchuck_id':'WC-AGE-'+label,'pin':'2468'})
    assert r.status_code==200,r.text
    return r.json()

def screen(client,band='adult',**extra):
    r=client.get('/account/age',follow_redirects=False)
    assert r.status_code==200,r.text
    return client.post('/account/age',data={'csrf':r.context['csrf'],
        'confirm_account':r.context['confirm_account'],'age_band':band,**extra},follow_redirects=False)

def state(factory,pid=1):
    with factory() as s:
        row=s.get(WoodchuckState,pid)
        return row.revision,deepcopy(row.state_json)

@pytest.mark.parametrize('band',[None,'','unknown','under13','forged'])
def test_signup_denied_before_any_account_record(age_db,band):
    c=TestClient(app);before=counts(age_db)
    data={'display_name':'Synthetic New','pin':'2468','instrument':'Flute','level':'Beginner',
          'goal':'Practice every day','initial_state':json.dumps({'progress':{'credits':9999}})}
    if band is not None:data['age_band']=band
    assert c.post('/account/create',data=data).status_code==403
    assert counts(age_db)==before
    assert c.get('/account/me').json()['authenticated'] is False

@pytest.mark.parametrize('band',['13to17','adult'])
def test_eligible_signup_creates_one_age_record_and_normal_free_account(age_db,band):
    c=TestClient(app)
    r=c.post('/account/create',data={'age_band':band,'display_name':'Synthetic New','pin':'2468',
        'instrument':'Flute','level':'Beginner','goal':'Practice every day','initial_state':'{}'})
    assert r.status_code==200,r.text
    with age_db() as s:
        assert s.scalar(select(func.count(AccountPrivacy.profile_id)))==1
        assert s.scalar(select(func.count(Membership.id)))==0
        assert s.scalar(select(func.count(WoodchuckProfile.id)))==3
    assert c.get('/account/state').status_code==200
    assert c.get('/c001').status_code==404

@pytest.mark.parametrize('band',[None,'unknown','under13'])
def test_existing_login_is_limited_and_keeps_history_balance_and_rewards(age_db,band):
    if band:
        with age_db() as s:declare_age(s,1,band);s.commit()
    before=counts(age_db);saved=state(age_db)
    c=TestClient(app);payload=login(c)
    assert payload['age_screen_required'] is True
    assert payload['login_streak'] is None
    assert c.get('/account/state').status_code==403
    r=c.get('/home',headers={'Accept':'text/html'},follow_redirects=False)
    assert r.status_code==303 and r.headers['location']=='/account/age'
    page=c.get('/account/age');assert page.status_code==200
    assert 'parent_email' not in page.text and 'birthday' not in page.text
    if band!='under13':assert '<select' in page.text and ' selected' not in page.text
    else:assert '<select' not in page.text
    assert c.get('/account/privacy').status_code==200
    assert c.get('/account/help').status_code==200
    assert c.get('/account/me').json()['authenticated'] is True
    assert state(age_db)==saved and counts(age_db)==before
    assert c.post('/account/logout').json()=={'authenticated':False}
    assert c.get('/account/state').status_code==401
    assert c.get('/guest').status_code==200

WRITES=[('put','/account/state',{'account':{'woodchuckId':'WC-AGE-A','serverRevision':7},'progress':{'credits':99999}}),
    ('post','/practice-charts',{'practice_date':str(date.today()),'minutes':10,'note':'synthetic'}),
    ('post','/practice-charts/pristine',{'detected_playing_seconds':60,'submission_key':'synthetic-age'}),
    ('post','/arcade/plays',{'game_key':'blue'}),
    ('post','/arcade/plays/fake-token/complete',{'score':100}),
    ('post','/teams',{'name':'Synthetic Ages','emblem_key':'emoji:bear'}),
    ('post','/teams/selection',{'team_id':1}),
    ('post','/contests/camp-points/awards',{'activity_type':'care','activity_date':str(date.today())}),
    ('post','/account/daily-secret',{'passcode':'union'}),
    ('post','/account/login-streak',{}),
    ('post','/trusted-verifiers/invitations',{'email':'adult@example.test','role':'verifier'})]
@pytest.mark.parametrize('method,path,payload',WRITES)
def test_signed_in_direct_writes_cannot_bypass_screen(age_db,method,path,payload):
    c=TestClient(app);login(c);before=counts(age_db);saved=state(age_db)
    r=c.request(method,path,**({'data':payload} if path=='/trusted-verifiers/invitations' else {'json':payload}),headers={'X-Woodshed-Account':'WC-AGE-A'})
    assert r.status_code==403,r.text
    assert r.json()['age_screen_required'] is True
    assert counts(age_db)==before and state(age_db)==saved

def test_legacy_signed_in_cookie_is_gated_on_next_request(age_db):
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    from app.main import SESSION_SECRET
    from app.account_routes import SESSION_PROFILE_ID,SESSION_PROFILE_VERSION
    cookie=TimestampSigner(SESSION_SECRET).sign(b64encode(json.dumps({SESSION_PROFILE_ID:1,SESSION_PROFILE_VERSION:0}).encode())).decode()
    c=TestClient(app);c.cookies.set('session',cookie)
    before=counts(age_db)
    assert c.get('/account/state').status_code==403
    assert c.get('/practice/pristine',headers={'Accept':'text/html'},follow_redirects=False).status_code==303
    assert counts(age_db)==before
    assert c.get('/account/me').json()['authenticated'] is True

def test_eligible_existing_continues_same_id_and_state_without_grants(age_db):
    c=TestClient(app);login(c);saved=state(age_db);before=counts(age_db)
    assert screen(c,'13to17').status_code==303
    r=c.get('/account/state');assert r.status_code==200,r.text
    assert r.json()['state']['practiceLog']==saved[1]['practiceLog']
    assert state(age_db)==saved
    after=counts(age_db);assert after.pop('account_privacy_rules')==1
    before.pop('account_privacy_rules');assert after==before
    assert c.get('/home').status_code==200
    assert c.get('/account/me').json()['profile']['woodchuck_id']=='WC-AGE-A'

def test_csrf_wrong_account_extra_fields_and_missing_age_denied(age_db):
    c=TestClient(app);login(c);page=c.get('/account/age');data={'csrf':page.context['csrf'],'confirm_account':'WC-AGE-A','age_band':'adult'}
    for edit,code in [({'csrf':'forged'},403),({'confirm_account':'WC-AGE-B'},409),({'parent_email':'no@example.test'},400),({'age_band':''},409)]:
        assert c.post('/account/age',data={**data,**edit},follow_redirects=False).status_code==code
    assert counts(age_db)['account_privacy_rules']==0

def test_known_child_cannot_self_override_and_retry_does_not_change_public_boundary(age_db):
    c=TestClient(app);login(c)
    with age_db() as s:rule=declare_age(s,1,'under13');stamp=rule.declared_at;s.commit()
    page=c.get('/account/age');data={'csrf':page.context['csrf'],'confirm_account':'WC-AGE-A'}
    assert c.post('/account/age',data={**data,'age_band':'adult'}).status_code==409
    assert c.post('/account/age',data={**data,'age_band':'under13'},follow_redirects=False).status_code==303
    with age_db() as s:
        rule=s.get(AccountPrivacy,1);assert rule.age_band=='under13' and rule.public_from is None and rule.declared_at==stamp
    assert c.get('/account/state').status_code==403

def test_background_writers_fail_closed_including_old_queued_observation(age_db):
    from app.analytics import record_event
    from app.economy import lock_state
    from app.login_streaks import apply_daily_login
    from app.contests import _grant_once
    instant=datetime.now(timezone.utc)
    before=counts(age_db)
    record_event(age_db,profile_id=1,event_type='arcade_entered',occurred_at=instant)
    with age_db() as s:
        for operation in (lock_state,apply_daily_login):
            with pytest.raises(AgeScreenRequired):operation(s,profile_id=1)
        assert _grant_once(s,profile_id=1,result_id=None,source_key='synthetic-finalizer',reward_type='dandelion') is False
        s.commit()
    assert counts(age_db)==before
    with age_db() as s:declare_age(s,1,'adult',at=instant+timedelta(seconds=1));s.commit()
    record_event(age_db,profile_id=1,event_type='arcade_entered',occurred_at=instant)
    assert counts(age_db)['analytics_events']==0
    record_event(age_db,profile_id=1,event_type='arcade_entered',occurred_at=instant+timedelta(seconds=2))
    assert counts(age_db)['analytics_events']==1

def connected_chart(factory):
    with factory() as s:
        v=TrustedVerifier(email='adult@example.test',display_name='Synthetic Verifier',pin_hash=hash_pin('2468'));s.add(v);s.flush()
        link=StudentVerifierConnection(profile_id=1,verifier_id=v.id,role='verifier',status='accepted',accepted_at=datetime.now(timezone.utc));s.add(link)
        chart=PracticeChart(profile_id=1,practice_date=date.today()-timedelta(days=2),minutes=10,note='private saved chart',instrument='Flute',credits_awarded=2,include_contests=True)
        s.add(chart);s.flush()
        review=PracticeChartVerification(practice_chart_id=chart.id,verifier_id=v.id,status='pending');s.add(review);s.commit()
        return v.id,link.id,chart.id,review.id

def test_verifier_reads_reviews_disconnect_and_preservation(age_db):
    vid,lid,cid,rid=connected_chart(age_db)
    c=TestClient(app);assert c.post('/trusted-verifiers/login',data={'email':'adult@example.test','pin':'2468'}).status_code==200
    before=counts(age_db)
    assert c.get('/trusted-verifiers/me').json()['student_connections']==[]
    assert c.get('/trusted-verifiers/practice-charts').json()['pending_charts']==[]
    assert c.get('/trusted-verifiers/practice-charts',params={'connection_id':lid}).status_code==404
    assert c.post(f'/trusted-verifiers/practice-charts/{rid}/respond',json={'decision':'approved'}).status_code==403
    assert counts(age_db)==before
    with age_db() as s:declare_age(s,1,'adult');s.commit()
    assert len(c.get('/trusted-verifiers/me').json()['student_connections'])==1
    assert len(c.get('/trusted-verifiers/practice-charts').json()['pending_charts'])==1
    student=TestClient(app);login(student,'B')
    assert student.delete(f'/trusted-verifiers/connections/{lid}').status_code==404
    login(student)
    assert student.delete(f'/trusted-verifiers/connections/{lid}').status_code==200
    assert c.get('/trusted-verifiers/practice-charts').json()['pending_charts']==[]
    assert c.post(f'/trusted-verifiers/practice-charts/{rid}/respond',json={'decision':'approved'}).status_code==400
    with age_db() as s:
        assert s.get(PracticeChartVerification,rid).status=='pending'
        assert s.get(PracticeChart,cid).note=='private saved chart'

def test_full_does_not_bypass_and_membership_identity_is_private(age_db):
    from app.membership_routes import detail
    with age_db() as s:
        b=BillingAccount(profile_id=2);s.add(b);s.flush()
        m=Membership(billing_account_id=b.id,source='manual',status='active');s.add(m);s.flush()
        seat=MembershipSeat(membership_id=m.id,profile_id=1,slot_number=1);s.add(seat);s.commit();mid=m.id
        projected=detail(s,m)['seats'][0]
        assert projected[0]['profile_id'] is None and projected[1]=='Private member'
    c=TestClient(app);login(c);assert c.get('/account/state').status_code==403
    saved=state(age_db)
    assert screen(c).status_code==303
    with age_db() as s:
        m=s.get(Membership,mid);assert m.source=='manual' and m.status=='active' and m.access_until is None
        assert s.scalar(select(MembershipSeat.profile_id))==1
    assert state(age_db)==saved

def test_declaration_never_publishes_old_chart_or_arcade_record(age_db):
    from app.arcade_scores import arcade_score_payload
    vid,lid,cid,rid=connected_chart(age_db)
    old=datetime.now(timezone.utc)-timedelta(days=2)
    with age_db() as s:
        s.add(ArcadeHighScore(profile_id=1,game_key='blue',best_score=100,created_at=old,updated_at=old));s.commit()
        declare_age(s,1,'adult');declare_age(s,2,'adult');s.commit()
        assert not chart_public(s,s.get(PracticeChart,cid))
        payload=arcade_score_payload(s,profile_id=2,game_key='blue')
        assert 'Synthetic A' not in json.dumps(payload)
        assert s.get(WoodchuckProfile,1).plunge_best_score==19
        assert s.get(AccountPrivacy,1).private_plunge_best==19
        assert s.get(PracticeChartVerification,rid).status=='pending'

@pytest.mark.parametrize('band',['unknown','under13'])
def test_recorded_ineligible_cannot_use_sharing_or_rewards(age_db,band):
    with age_db() as s:declare_age(s,1,band);s.commit()
    c=TestClient(app);login(c);before=counts(age_db)
    assert c.post('/trusted-verifiers/invitations',data={'email':'adult@example.test','role':'verifier'}).status_code==403
    assert c.post('/practice-charts',json={'practice_date':str(date.today()),'minutes':10,'note':'synthetic'}).status_code==403
    assert c.post('/account/login-streak').status_code==403
    assert counts(age_db)==before

def test_unknown_screen_and_idempotent_eligible_retry_preserve_boundary(age_db):
    c=TestClient(app);login(c)
    assert screen(c,'unknown').status_code==303
    assert c.get('/account/state').status_code==403
    assert screen(c,'adult').status_code==303
    with age_db() as s:
        r=s.get(AccountPrivacy,1);stamp=r.public_from
        declare_age(s,1,'adult',at=stamp+timedelta(days=1));s.commit()
        assert s.get(AccountPrivacy,1).public_from==stamp
        assert s.scalar(select(func.count(AccountPrivacy.profile_id)))==1

def test_eligible_normal_reward_retry_and_state_save_remain_authoritative(age_db):
    c=TestClient(app);login(c);screen(c)
    first=c.post('/account/login-streak');assert first.status_code==200
    saved=c.get('/account/state').json()['state'];assert saved['progress']['credits']==38
    again=c.post('/account/login-streak');assert again.status_code==200
    assert c.get('/account/state').json()['state']['progress']['credits']==38
    saved['practiceLog'].append({'note':'new eligible practice'})
    saved['progress']['credits']=99999
    assert c.put('/account/state',json=saved).status_code==409 # Reward revised authoritative state.
    saved['account']['serverRevision']=c.get('/account/state').json()['revision']
    assert c.put('/account/state',json=saved).status_code==200
    after=c.get('/account/state').json()['state']
    assert after['practiceLog']==saved['practiceLog'] and after['progress']['credits']==38
    assert after['account']['woodchuckId']=='WC-AGE-A'

def test_team_and_saved_rewards_preserved_but_private_aggregate_not_published(age_db):
    from app.models import Season,TeamFamily,Team,TeamMembership
    from app.age_privacy import team_public
    with age_db() as s:
        season=Season(key='synthetic',name='Synthetic',starts_on=date(2026,1,1),status='active');s.add(season);s.flush()
        family=TeamFamily();s.add(family);s.flush()
        team=Team(season_id=season.id,family_id=family.id,display_name='Synthetic team',normalized_name='synthetic team',emblem_key='emoji:bear',creator_profile_id=1);s.add(team);s.flush()
        link=TeamMembership(season_id=season.id,team_id=team.id,profile_id=1,selected_week_start=date(2026,9,14),started_at=datetime.now(timezone.utc));s.add(link)
        reward=RewardGrant(profile_id=1,source_key='synthetic-old-reward',reward_type='dandelion',amount=9);s.add(reward);s.commit();tid=team.id
        assert not team_public(s,tid)
        declare_age(s,1,'adult');s.commit()
        assert s.get(Team,tid).display_name=='Synthetic team'
        assert s.scalar(select(TeamMembership.profile_id))==1
        assert s.scalar(select(RewardGrant.amount))==9
        assert team_public(s,tid)
        chart=PracticeChart(profile_id=1,team_id=tid,practice_date=date.today()-timedelta(days=2),minutes=10,instrument='Flute',credits_awarded=2);s.add(chart);s.commit()
        assert not team_public(s,tid) # Conservative whole-aggregate exclusion avoids historical disclosure.
        assert s.scalar(select(TeamMembership.profile_id))==1

def test_hall_and_raw_results_exclude_unknown_and_earlier_private_history(age_db):
    from app.models import Season,ContestWeek,ContestResult,Contest,CrownProgress,CrownAward
    now=datetime.now(timezone.utc)
    with age_db() as s:
        season=Season(key='synthetic-hall',name='Synthetic Hall',starts_on=date(2026,1,1),status='active');s.add(season);s.flush()
        contest=Contest(key='weekly-points-leaders',name='Synthetic practice',metric_type='practice_minutes',subject_type='student');s.add(contest);s.flush()
        week=ContestWeek(season_id=season.id,week_start=date(2026,9,14),week_end=date(2026,9,21),status='finalized',finalized_at=now,verification_deadline_at=now,finalize_after=now,practice_scoring_mode='precise_seconds');s.add(week);s.flush()
        result=ContestResult(contest_week_id=week.id,contest_id=contest.id,division='open',subject_type='student',subject_key='1',profile_id=1,display_name_snapshot='Synthetic A',score=10,precise_score=10.0,rank=1,medal='gold',created_at=now-timedelta(days=2));s.add(result)
        s.add(CrownProgress(profile_id=1,category_key='weekly-points-leaders',qualifying_wins=7))
        s.add(CrownAward(profile_id=1,category_key='weekly-points-leaders',source_key='synthetic-private-crown',earned_at=now-timedelta(days=20)))
        declare_age(s,2,'adult',at=now-timedelta(days=20));s.commit()
    c=TestClient(app);login(c,'B')
    for path in ('/contests/hall-of-champions','/contests/weeks/2026-09-14/results'):
        r=c.get(path);assert r.status_code==200,r.text;assert 'Synthetic A' not in r.text
        assert r.headers['cache-control']=='no-store'
    with age_db() as s:declare_age(s,1,'13to17');s.commit()
    # Hall-only legacy compatibility restores the immutable finalized personal
    # snapshot for a current 13+ account. Raw weekly history keeps the stricter
    # public_from boundary.
    r=c.get('/contests/hall-of-champions')
    assert r.status_code==200 and 'Synthetic A' in r.text
    assert 'Synthetic A' not in c.get('/contests/weeks/2026-09-14/results').text

    # A snapshot created after screening for a week that began before the
    # publication boundary is not a legacy snapshot and stays hidden.
    with age_db() as s:
        result=s.scalar(select(ContestResult))
        result.created_at=now+timedelta(minutes=1)
        s.commit()
    assert 'Synthetic A' not in c.get('/contests/hall-of-champions').text

    # A new snapshot in a genuinely later qualifying week may be public.
    with age_db() as s:
        result=s.scalar(select(ContestResult));result.created_at=now+timedelta(days=20)
        week=s.get(ContestWeek,result.contest_week_id);week.week_start=date(2026,10,5);week.week_end=date(2026,10,12);s.commit()
    r=c.get('/contests/hall-of-champions');assert r.status_code==200 and 'Synthetic A' in r.text

    crown=r.json()['students'][0]['crown']
    assert crown['qualifying_wins']==7 and crown['earned_count']==1 and crown['earned'] is True
    from app.contests import hall_of_champions_payload
    with age_db() as s:
        internal=hall_of_champions_payload(s,_include_internal=True)['students'][0]['crown']
        assert internal['qualifying_wins']==7 and internal['earned_count']==1

def test_legacy_local_import_blocked_but_fresh_game_completion_still_works(age_db):
    c=TestClient(app);login(c);screen(c)
    before=counts(age_db)
    r=c.post('/xp/plunge-best',json={'score':999999});assert r.status_code==409,r.text
    assert counts(age_db)==before
    r=c.get('/xp/plunge-best');assert r.status_code==200 and r.json()['allow_local_score_import'] is False
    assert r.json()['best_score']==19
    start=c.post('/arcade/plays',json={'game_key':'plunge-burrow'});assert start.status_code==200,start.text
    token=start.json()['play_token']
    completion=c.post(f'/arcade/plays/{token}/complete',json={'score':20});assert completion.status_code==200,completion.text
    assert completion.json()['best_score']==20
    with age_db() as s:assert s.get(AccountPrivacy,1).private_plunge_best==19


def support_lookup(client, woodchuck_id='WC-AGE-A'):
    page=client.get('/admin/age')
    assert page.status_code==200,page.text
    r=client.post('/admin/age/lookup',data={'csrf':page.context['csrf'],'woodchuck_id':woodchuck_id})
    assert r.status_code==200,r.text
    return {'csrf':r.context['csrf'],'woodchuck_id':r.context['woodchuck_id'],
            'previous_band':r.context['previous_band'],'expected_declared_at':r.context['expected_declared_at'],
            'age_band':'13to17','case_reference':'SUPPORT-TEST-1','support_confirmed':'yes'}

def admin_sign_in(client,monkeypatch):
    monkeypatch.setenv('SITE_ADMIN_TOKEN','synthetic-support-admin-only-token')
    page=client.get('/admin/login')
    assert client.post('/admin/login',data={'csrf':page.context['csrf'],'token':'synthetic-support-admin-only-token'},follow_redirects=False).status_code==303


def test_support_correction_requires_admin_not_student_or_verifier_pin(age_db,monkeypatch):
    with age_db() as s:declare_age(s,1,'under13');s.commit()
    connected_chart(age_db)
    verifier=TestClient(app)
    assert verifier.post('/trusted-verifiers/login',data={'email':'adult@example.test','pin':'2468'}).status_code==200
    assert verifier.get('/admin/age').status_code==403
    assert verifier.post('/admin/age/correct',data={'age_band':'adult'}).status_code==403
    student=TestClient(app);login(student)
    assert student.get('/admin/age').status_code==403
    assert student.post('/admin/age/correct',data={'age_band':'adult','pin':'2468'}).status_code==403
    assert screen(student,'adult').status_code==409
    assert student.get('/account/help').status_code==200
    outsider=TestClient(app);monkeypatch.setenv('SITE_ADMIN_TOKEN','synthetic-support-admin-only-token')
    page=outsider.get('/admin/login')
    assert outsider.post('/admin/login',data={'csrf':page.context['csrf'],'token':'2468'}).status_code==403
    assert outsider.get('/admin/age').status_code==403


def test_authorized_correction_is_audited_replay_safe_and_preserves_private_data(age_db,monkeypatch):
    from app.age_models import AgeCorrection
    vid,lid,cid,rid=connected_chart(age_db)
    with age_db() as s:
        declare_age(s,1,'under13');s.commit()
        old=s.get(AccountPrivacy,1).declared_at
        existing=s.get(StudentVerifierConnection,lid).status
        s.add(BillingAccount(id=91,profile_id=1));s.flush()
        s.add(Membership(id=91,billing_account_id=91,status='active',source='manual',plan_code='full',starts_at=datetime.now(timezone.utc)));s.flush()
        s.add(MembershipSeat(membership_id=91,slot_number=1,profile_id=1));s.commit()
    saved=state(age_db);before=counts(age_db)
    student=TestClient(app);login(student);assert student.get('/account/state').status_code==403
    admin=TestClient(app);admin_sign_in(admin,monkeypatch);data=support_lookup(admin)
    assert admin.post('/admin/age/correct',data={**data,'csrf':'forged'}).status_code==403
    assert admin.post('/admin/age/correct',data={**data,'support_confirmed':''}).status_code==400
    assert admin.post('/admin/age/correct',data={**data,'expected_declared_at':'stale'}).status_code==409
    assert admin.post('/admin/age/correct',data={**data,'case_reference':'not a case\n'}).status_code==409
    assert counts(age_db)==before
    corrected=admin.post('/admin/age/correct',data=data);assert corrected.status_code==200
    assert 'Correction recorded' in corrected.text and corrected.headers['cache-control']=='no-store'
    assert admin.post('/admin/age/correct',data=data).status_code==409
    assert state(age_db)==saved
    with age_db() as s:
        rule=s.get(AccountPrivacy,1);audit=s.scalar(select(AgeCorrection))
        assert rule.age_band=='13to17' and rule.public_from>old and rule.private_plunge_best==19
        assert audit.previous_band=='under13' and audit.corrected_band=='13to17'
        assert audit.case_reference=='SUPPORT-TEST-1' and len(audit.actor_fingerprint)==64
        assert audit.wording_version=='support-age-correction-v1'
        assert not chart_public(s,s.get(PracticeChart,cid))
        assert s.get(PracticeChartVerification,rid).status=='pending'
        assert s.get(StudentVerifierConnection,lid).status==existing
        assert s.get(Membership,91).status=='active' and s.get(MembershipSeat,1).profile_id==1
        assert s.scalar(select(func.count(AgeCorrection.id)))==1
    # The already-signed-in student's next request sees the correction, same ID/state.
    r=student.get('/account/state');assert r.status_code==200 and r.json()['state']['practiceLog']==saved[1]['practiceLog']
    assert student.get('/account/me').json()['profile']['woodchuck_id']=='WC-AGE-A'


def test_support_correction_is_bound_to_restricted_record(age_db,monkeypatch):
    with age_db() as s:
        declare_age(s,1,'under13');declare_age(s,2,'adult');s.commit()
    admin=TestClient(app);admin_sign_in(admin,monkeypatch);data=support_lookup(admin)
    saved=state(age_db,2)
    assert admin.post('/admin/age/correct',data={**data,'woodchuck_id':'WC-AGE-B'}).status_code==409
    assert state(age_db,2)==saved
    with age_db() as s:assert s.get(AccountPrivacy,1).age_band=='under13'


def test_live_team_boards_exclude_private_sources_retain_new_eligible_totals_and_ranks(age_db):
    from app.models import Season,ContestWeek,TeamFamily,Team,TeamMembership,CampPointAward,Contest,ContestResult
    from app.contests import team_leaderboards
    from app.age_privacy import filter_result_rows,team_public
    old=datetime(2026,9,14,12,tzinfo=timezone.utc);cutoff=datetime(2026,9,15,12,tzinfo=timezone.utc);new=cutoff+timedelta(days=1)
    with age_db() as s:
        season=Season(key='board-test',name='Board test',starts_on=date(2026,1,1),status='active');s.add(season);s.flush()
        week=ContestWeek(season_id=season.id,week_start=date(2026,9,14),week_end=date(2026,9,21),status='open',verification_deadline_at=new,finalize_after=new,practice_scoring_mode='precise_seconds');s.add(week);s.flush()
        teams=[]
        for pid in (1,2):
            f=TeamFamily();s.add(f);s.flush()
            t=Team(season_id=season.id,family_id=f.id,display_name='Synthetic team '+str(pid),normalized_name='synthetic team '+str(pid),emblem_key=('emoji:bear' if pid==1 else 'emoji:fox'),creator_profile_id=pid);s.add(t);s.flush();teams.append(t)
            s.add(TeamMembership(season_id=season.id,team_id=t.id,profile_id=pid,selected_week_start=week.week_start,started_at=old))
            declare_age(s,pid,'adult',at=cutoff)
        for tid,pid,minutes,when in [(teams[0].id,1,900,old),(teams[0].id,1,10,new),(teams[1].id,2,20,new)]:
            s.add(PracticeChart(profile_id=pid,team_id=tid,practice_date=when.date(),minutes=minutes,instrument='Flute',credits_awarded=0,include_contests=True,include_team_contests=True,created_at=when))
        for points,created,occurred,key in [(99,old,old,'old'),(7,new,new,'new'),(88,new,old,'backdated')]:
            s.add(CampPointAward(profile_id=1,team_id=teams[0].id,activity_type='care',points_awarded=points,created_at=created,occurred_at=occurred,duplicate_key='synthetic-'+key))
        s.commit();saved=state(age_db)
        public=team_leaderboards(s,season=season,contest_week=week)
        private=team_leaderboards(s,season=season,contest_week=week,_include_private=True)
        rows=public['team-weekly-practice']['open']
        assert [(r['team_id'],r['score'],r['rank']) for r in rows]==[(teams[1].id,20,1),(teams[0].id,10,2)]
        assert public['team-lifetime-practice']['open'][1]['score']==10
        assert public['team-weekly-activity-points']['open'][0]['score']==7
        assert private['team-weekly-practice']['open'][0]['score']==910
        assert private['team-weekly-activity-points']['open'][0]['score']==194
        assert not team_public(s,teams[0].id) and team_public(s,teams[0].id,identity_only=True)
        contest=Contest(key='synthetic-team-snapshot',name='Stored team',metric_type='practice_minutes',subject_type='team');s.add(contest);s.flush()
        result=ContestResult(contest_week_id=week.id,contest_id=contest.id,division='open',subject_type='team',subject_key=str(teams[0].id),team_id=teams[0].id,display_name_snapshot='Synthetic team 1',score=910,precise_score=910.0,rank=1,medal='gold',created_at=new);s.add(result);s.commit()
        assert filter_result_rows(s,[(result,contest,teams[0])])==[]
        assert s.get(ContestResult,result.id).score==910 and s.get(ContestResult,result.id).rank==1
        assert s.scalar(select(func.count(PracticeChart.id)))==3
        assert s.scalar(select(func.count(CampPointAward.id)))==3
    assert state(age_db)==saved


def test_live_team_with_restricted_identity_stays_hidden(age_db):
    from app.models import Season,ContestWeek,TeamFamily,Team,TeamMembership
    from app.contests import team_leaderboards
    now=datetime.now(timezone.utc)
    with age_db() as s:
        season=Season(key='private-team',name='Private team',starts_on=date(2026,1,1),status='active');s.add(season);s.flush()
        family=TeamFamily();s.add(family);s.flush()
        team=Team(season_id=season.id,family_id=family.id,display_name='Private synthetic',normalized_name='private synthetic',emblem_key='emoji:bear',creator_profile_id=1);s.add(team);s.flush()
        declare_age(s,1,'adult',at=now-timedelta(days=20));declare_age(s,2,'under13',at=now-timedelta(days=20))
        for pid in (1,2):s.add(TeamMembership(season_id=season.id,team_id=team.id,profile_id=pid,selected_week_start=date(2026,9,14),started_at=now-timedelta(days=20)))
        week=ContestWeek(season_id=season.id,week_start=date(2026,9,14),week_end=date(2026,9,21),status='open',verification_deadline_at=now,finalize_after=now,practice_scoring_mode='precise_seconds');s.add(week);s.flush()
        s.add(PracticeChart(profile_id=1,team_id=team.id,practice_date=date(2026,9,16),minutes=10,instrument='Flute',credits_awarded=0,include_contests=True,include_team_contests=True,created_at=now));s.commit()
        assert team_leaderboards(s,season=season,contest_week=week)['team-weekly-practice']['open']==[]


def test_weekly_historical_snapshot_not_hidden_by_unrelated_older_private_chart(age_db):
    from app.models import Season,ContestWeek,TeamFamily,Team,TeamMembership,Contest,ContestResult
    from app.age_privacy import filter_result_rows
    cutoff=datetime(2026,9,15,12,tzinfo=timezone.utc);future=datetime(2026,10,6,12,tzinfo=timezone.utc)
    with age_db() as s:
        season=Season(key='safe-history',name='Safe history',starts_on=date(2026,1,1),status='active');s.add(season);s.flush()
        family=TeamFamily();s.add(family);s.flush()
        team=Team(season_id=season.id,family_id=family.id,display_name='Safe team',normalized_name='safe team',emblem_key='emoji:bear',creator_profile_id=1);s.add(team);s.flush()
        s.add(TeamMembership(season_id=season.id,team_id=team.id,profile_id=1,selected_week_start=date(2026,10,5),started_at=cutoff));declare_age(s,1,'adult',at=cutoff)
        for when,minutes in [(cutoff-timedelta(days=1),900),(future,10)]:
            s.add(PracticeChart(profile_id=1,team_id=team.id,practice_date=when.date(),minutes=minutes,instrument='Flute',credits_awarded=0,include_contests=True,include_team_contests=True,created_at=when))
        week=ContestWeek(season_id=season.id,week_start=date(2026,10,5),week_end=date(2026,10,12),status='finalized',verification_deadline_at=future,finalize_after=future,finalized_at=future,practice_scoring_mode='precise_seconds');s.add(week);s.flush()
        rows=[]
        for key,score in [('team-weekly-practice',10),('team-lifetime-practice',910)]:
            contest=Contest(key=key,name=key,metric_type='practice_minutes',subject_type='team');s.add(contest);s.flush()
            result=ContestResult(contest_week_id=week.id,contest_id=contest.id,division='open',subject_type='team',subject_key=str(team.id),team_id=team.id,display_name_snapshot='Safe team',score=score,precise_score=float(score),rank=1,medal='gold',created_at=future);s.add(result);s.flush();rows.append((result,contest,team))
        s.commit()
        assert filter_result_rows(s,rows)==[rows[0]]
        assert rows[1][0].score==910 and rows[1][0].rank==1
        # A private source in that same week still suppresses its stored aggregate.
        s.add(PracticeChart(profile_id=2,team_id=team.id,practice_date=future.date(),minutes=5,instrument='Flute',credits_awarded=0,include_contests=True,include_team_contests=True,created_at=future));s.commit()
        assert filter_result_rows(s,rows)==[]
