"""Student response boundary; shared adult calculations and authority stay intact."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import json
import sys

from alembic import command
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import family_routes as family, session_revocations
from app.age_models import AccountPrivacy
from app.age_privacy import declare_age
from app.feature_access import FEATURES, Feature, can_use_feature
from app.main import app
from app.models import (WoodchuckProfile, WoodchuckState, TrustedVerifier,
                        PracticeChart, PracticeChartVerification, StudentVerifierConnection)
from app.security import hash_pin
from app.weekly import CENTRAL
from tests.test_weekly2_newsletter import db
from tests.test_weekly2_foundation_migration import config, rows
from tests.test_weekly2_game_mode import assert_only_session_retirement
from test_private_practice import captured, authorize, connect, open_parent, post
from test_age_screening import login
from test_memberships import grant

PATH='/family/practice/data'
FORBIDDEN={'rating','trend','delta','rating_week_values','rating_history','practice_rating',
           'rating_value','rating_delta','score','history','direction','arrow','baseline'}


def safe(value):
    if isinstance(value,dict):
        assert not FORBIDDEN.intersection(value),value
        for child in value.values():safe(child)
    elif isinstance(value,list):
        for child in value:safe(child)


@pytest.fixture
def privacy_db(db,monkeypatch):
    command.upgrade(config(),'w25weekly005')
    with db.connect() as c:
        assert c.scalar(text('SELECT version_num FROM alembic_version'))=='w25weekly005'
    factory=sessionmaker(db,expire_on_commit=False,autoflush=False);factory.engine=db
    for name,module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module,'SessionLocal'):
            monkeypatch.setattr(module,'SessionLocal',factory)
    for key in FEATURES:
        if key.startswith('weekly_'):monkeypatch.setitem(FEATURES,key,Feature(False,'open'))
    today=datetime.now(CENTRAL).date()
    with factory.begin() as s:
        for pid,label in [(1,'A'),(2,'B')]:
            s.add(WoodchuckProfile(id=pid,woodchuck_id='WC-AGE-'+label,
                display_name='Synthetic '+label,pin_hash=hash_pin('2468'),
                instrument='Flute',level='Beginner',goal='Keep practicing'))
        s.flush()
        for pid in (1,2):
            declare_age(s,pid,'13to17')
            s.add(WoodchuckState(profile_id=pid,revision=7,
                                state_json={'progress':{'credits':37}}))
        s.add(TrustedVerifier(id=1,email='synthetic-verifier@example.test',
                             display_name='Synthetic verifier',pin_hash=hash_pin('1357')))
        s.flush()
        chart=PracticeChart(profile_id=1,practice_date=today,minutes=2,source='manual',
                            instrument='Flute',note='Own saved practice')
        s.add(chart);s.flush()
        s.add(PracticeChartVerification(practice_chart_id=chart.id,verifier_id=1,status='approved'))
        s.add_all([
            PracticeChart(profile_id=1,practice_date=today,minutes=0,source='pristine',
                          instrument='Flute',detected_playing_seconds=19),
            PracticeChart(profile_id=1,practice_date=today-timedelta(days=7),minutes=5,
                          source='manual',instrument='Flute'),
            PracticeChart(profile_id=2,practice_date=today,minutes=999,source='manual',
                          instrument='Trumpet',note='FOREIGN PRIVATE RECORD'),
        ])
    return factory


@pytest.mark.parametrize('full',[False,True])
@pytest.mark.parametrize('mixed',[False,True])
def test_student_json_html_and_progress(privacy_db,full,mixed,record_property):
    f=privacy_db
    if full:grant(f,'student')
    with TestClient(app) as c:
        login(c)
        if mixed:
            assert c.post('/trusted-verifiers/login',data={
                'email':'synthetic-verifier@example.test','pin':'1357'}).status_code==200
        with f() as s:
            assert can_use_feature(s,1,'practice_insights') is full
            raw=family.metrics(s,s.get(WoodchuckProfile,1))
        assert isinstance(raw['rating'],(int,float)) and 'delta' in raw['trend']
        expected=jsonable_encoder({key:value for key,value in raw.items()
                                   if key not in ('rating','trend')})
        before=rows(f.engine)
        response=c.get(PATH);assert response.status_code==200
        data=response.json();safe(data['metrics'])
        assert data['metrics']==expected
        assert data['metrics']['lifetime']['total_seconds']==439
        assert data['metrics']['lifetime']['verified_seconds']==120
        assert data['metrics']['lifetime']['pristine_seconds']==19
        assert len(data['charts'])==3 and 'FOREIGN PRIVATE RECORD' not in response.text
        page=c.get('/family/practice');assert page.status_code==200
        safe(page.context['metrics'])
        assert jsonable_encoder(page.context['metrics'])==data['metrics']
        assert 'Own saved practice' in page.text and 'Rating:' not in page.text
        assert '<script' not in page.text and 'FOREIGN PRIVATE RECORD' not in page.text
        # Unknown selectors never select another subject; native account binding refuses B.
        selected=c.get(PATH+'?profile_id=2&woodchuck_id=WC-AGE-B')
        assert selected.json()==data
        assert c.get(PATH,headers={'X-Woodshed-Account':'WC-AGE-B'}).status_code==401
        for _ in range(2):assert c.get(PATH).json()==data
        assert rows(f.engine)==before
        insights=c.get('/practice-charts/insights')
        assert insights.status_code==(200 if full else 403)
        if full:
            safe(insights.json());assert insights.json()==data['metrics']['insights']
        for path in ('/account/me','/account/state'):
            account=c.get(path);assert account.status_code==200;safe(account.json())
        record_property('student_projection',data['metrics'])
        record_property('html_context_safe',True)
        record_property('all_business_rows_unchanged',True)


def test_nested_allowlist_does_not_mutate_adult_input():
    raw={'weekly':{'total_seconds':19,'rating':93},'lifetime':{'charts':2,'score':94},
         'rating':95,'trend':{'delta':96,'direction':'up'},'rating_history':[97],
         'practice_streak':2,'season':{'name':'Season','rating':98},'team':None,
         'achievements':[{'label':'Saved award','rating_delta':99}],
         'insights':{'total_seconds':19,'rating':100,
                     'weeks':[{'seconds':19,'rating_value':101}]}}
    saved=deepcopy(raw);projected=family.student_metrics_projection(raw)
    safe(projected);assert raw==saved
    assert projected['weekly']=={'total_seconds':19}
    assert projected['insights']['weeks']==[{'seconds':19}]


@pytest.mark.parametrize('denial',['guest','pending','version','logout','expired'])
def test_denials_and_no_business_writes(privacy_db,monkeypatch,denial):
    f=privacy_db
    with TestClient(app) as c:
        if denial!='guest':login(c)
        cookie=c.cookies.get('session')
        if denial in ('pending','version'):
            with f.begin() as s:
                if denial=='pending':s.get(AccountPrivacy,1).age_band='under13'
                else:s.get(WoodchuckProfile,1).session_version+=1
        if denial=='logout':
            assert c.post('/account/logout').status_code==200
            c.cookies.clear();c.cookies.set('session',cookie)
        if denial=='expired':
            clock=session_revocations.time.time
            monkeypatch.setattr(session_revocations,'time',SimpleNamespace(
                time=lambda:clock()+session_revocations.SESSION_LIFETIME+1))
        before=rows(f.engine);response=c.get(PATH)
        assert response.status_code==(403 if denial=='pending' else 401),response.text
        if denial=='pending':
            from app.age_privacy import AgeScreenRequired
            assert response.json()=={'detail':AgeScreenRequired().detail,
                                     'age_screen_required':True,'next':'/account/age'}
        else:assert set(response.json())=={'detail'}
        after=rows(f.engine)
        if denial=='version':assert_only_session_retirement(before,after,cookie)
        else:assert after==before


def test_authorized_child_mixed_parent_and_withdrawal(privacy_db,captured,record_property):
    f=privacy_db
    with f.begin() as s:
        rule=s.get(AccountPrivacy,1);rule.age_band='unknown';rule.public_from=None
    child,_,pid,wid=authorize(f,captured)
    director=connect(captured[0])
    # Native parent verification in the student's existing browser: both roles are real.
    open_parent(child,captured,wid)
    before=rows(f.engine)
    student=child.get(PATH);assert student.status_code==200;safe(student.json()['metrics'])
    page=child.get('/family/practice');assert page.status_code==200;safe(page.context['metrics'])
    parent=child.get('/family/parent/data');assert parent.status_code==200
    adult=director.get('/family/director/data');assert adult.status_code==200
    with f() as s:raw=family.metrics(s,s.get(WoodchuckProfile,pid))
    # Phase 1 adult policy: native consent is not a paid rating entitlement,
    # and a legacy named director is not Classroom reporting authority.
    safe(parent.json()['metrics']);safe(adult.json()['metrics'])
    assert parent.json()['metrics']['lifetime']==jsonable_encoder(raw['lifetime'])
    assert adult.json()['metrics']['lifetime']==jsonable_encoder(raw['lifetime'])
    assert rows(f.engine)==before
    assert post(child,'/family/parent/withdraw',{'withdraw':'yes'},page='/family/parent').status_code==200
    before=rows(f.engine);cookie=child.cookies.get('session')
    assert child.get(PATH).status_code in (401,403)
    after=rows(f.engine)
    if after!=before:assert_only_session_retirement(before,after,cookie)
    assert director.get('/family/director/data').status_code==403
    record_property('native_consent_and_mixed_parent_redaction',True)
    record_property('parent_director_progress_retained_rating_withheld',True)


def test_existing_verifier_progress_retained_without_rating(privacy_db):
    f=privacy_db
    with f.begin() as s:
        s.add(StudentVerifierConnection(profile_id=1,verifier_id=1,role='verifier',status='accepted'))
    with TestClient(app) as adult:
        assert adult.post('/trusted-verifiers/login',data={
            'email':'synthetic-verifier@example.test','pin':'1357'}).status_code==200
        before=rows(f.engine);page=adult.get('/trusted-verifiers/dashboard')
        assert page.status_code==200
        with f() as s:raw=family.metrics(s,s.get(WoodchuckProfile,1))
        safe(page.context['student'])
        assert page.context['student']['lifetime']==raw['lifetime']
        assert 'verifier-rating-card' not in page.text
        assert adult.get(PATH).status_code==401
        assert rows(f.engine)==before
