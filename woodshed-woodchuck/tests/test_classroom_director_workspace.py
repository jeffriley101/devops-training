"""Director workspace source separation and actual HTTP authorization boundaries."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import select, event

from app import classroom as s1, classroom_s2 as s2, classroom_reporting as reporting
from app import classroom_director_workspace_routes as routes, classroom_identity as identity
from app import classroom_chart_sharing as sharing
from app.models import PracticeChart, WoodchuckProfile, StudentVerifierConnection
from app.classroom_models import ClassroomTeachingAssignment
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW
from test_classroom_enrollment_ui import enrollment_web, protected_counts
from test_classroom_management_ui import management_web, signed
from test_classroom_identity import identity_web


@pytest.fixture
def workspace_web(identity_web,monkeypatch):
    refs=identity_web
    monkeypatch.setattr(routes,'SessionLocal',refs.factory)
    clock=[NOW+timedelta(days=1)]
    monkeypatch.setattr(s2,'_now',lambda:clock[0]);monkeypatch.setattr(reporting,'_now',lambda:clock[0])
    with refs.factory() as session:
        assignment=s2.assign_teacher(session,actor=actor(session),program_id=1,class_id=refs.class_id,verifier_id=2)
        refs.teacher_id=assignment.id
        identity.set_school_name(session,actor=actor(session),program_id=1,class_id=refs.class_id,
            woodchuck_id='WC-TEST0001',school_name='SCHOOL_ADMIN_SENTINEL',expected_version=0)
        session.add(StudentVerifierConnection(profile_id=2,verifier_id=2,role='band_director',status='accepted'))
        chart=PracticeChart(profile_id=1,practice_date=clock[0].date(),minutes=17,instrument='Trumpet',
            created_at=clock[0],note='EXPLICIT_CHART_SENTINEL')
        session.add(chart);session.flush();refs.chart_id=chart.id
        sharing.set_sharing(session,student=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468'),
            chart_id=chart.id,assignment_ids=[assignment.id])
        session.add(PracticeChart(profile_id=1,practice_date=clock[0].date(),minutes=999,instrument='Trumpet',
            created_at=clock[0],note='PRIVATE_UNSHARED_SENTINEL'))
        session.commit();session.get(WoodchuckProfile,1).display_name='PUBLIC_NICKNAME_SENTINEL';session.commit()
    refs.workspace_path=f'{routes.PATH}/1/classes/{refs.class_id}'
    refs.student_path=refs.workspace_path+f'/students/{refs.member_id}'
    refs.clock=clock
    return refs


def open_page(client,path,**extra):
    first=client.get(path)
    data={'csrf':first.context['csrf'],'director':str(client.management_actor),'pin':'2468'}
    if '/students/' not in path:data['before']=''
    return client.post(path,data={**data,**extra})


@pytest.mark.parametrize('flag',['CLASSROOM_S1_ENABLED','CLASSROOM_S2_ENABLED','CLASSROOM_DIRECTOR_ENABLED',
    'CLASSROOM_ENROLLMENT_V2_ENABLED','CLASSROOM_PRIVATE_IDENTITY_ENABLED'])
def test_gate_off_has_no_classroom_queries(workspace_web,monkeypatch,flag):
    refs=workspace_web;client=signed(2);monkeypatch.delenv(flag)
    statements=[]
    def capture(conn,cursor,sql,params,ctx,many):statements.append(sql.lower())
    engine=refs.factory.kw['bind'];event.listen(engine,'before_cursor_execute',capture)
    try:
        for path in [routes.PATH,refs.workspace_path,refs.student_path]:
            assert client.get(path).status_code==client.post(path,data={}).status_code==404
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert not any('classroom_' in sql for sql in statements)


def test_home_get_and_catalog_never_become_a_roster_or_connected_students(workspace_web):
    refs=workspace_web;client=signed(2)
    page=client.get(routes.PATH)
    assert 'SCHOOL_ADMIN_SENTINEL' not in page.text and 'EXPLICIT_CHART' not in page.text
    opened=open_page(client,routes.PATH)
    assert opened.status_code==200 and 'Concert Band' in opened.text
    assert 'SCHOOL_ADMIN_SENTINEL' not in opened.text and 'EXPLICIT_CHART' not in opened.text
    assert '/band-director/dashboard' in opened.text
    connected=client.get('/band-director/dashboard')
    assert 'WC-TEST0002' in connected.text and 'SCHOOL_ADMIN_SENTINEL' not in connected.text


def test_class_administration_is_separate_from_practice_queries(workspace_web):
    refs=workspace_web;client=signed(2);statements=[]
    def capture(conn,cursor,sql,params,ctx,many):statements.append(sql.lower())
    engine=refs.factory.kw['bind'];event.listen(engine,'before_cursor_execute',capture)
    try:page=open_page(client,refs.workspace_path)
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert page.status_code==200
    assert 'SCHOOL_ADMIN_SENTINEL' in page.text and 'WC-TEST0001' in page.text
    assert not any(value in page.text for value in ['PUBLIC_NICKNAME_SENTINEL','EXPLICIT_CHART_SENTINEL','PRIVATE_UNSHARED_SENTINEL','WC-TEST0002'])
    assert not any('practice_chart' in sql for sql in statements)
    data=page.context['data']
    assert set(data)=={'admin','statistics','shared_pcharts'}
    assert data['statistics']['authorization_available'] is None and data['statistics']['values'] is None
    assert page.headers['cache-control']=='no-store' and page.headers['referrer-policy']=='no-referrer'


def test_student_detail_exposes_only_selected_current_charts_and_admin_identity(workspace_web):
    refs=workspace_web;before=protected_counts(refs)
    page=open_page(signed(2),refs.student_path)
    assert page.status_code==200
    for allowed in ['SCHOOL_ADMIN_SENTINEL','WC-TEST0001','EXPLICIT_CHART_SENTINEL']:
        assert allowed in page.text
    for denied in ['PUBLIC_NICKNAME_SENTINEL','PRIVATE_UNSHARED_SENTINEL','WC-TEST0002']:
        assert denied not in page.text
    data=page.context['data'];assert data['statistics']['values'] is None
    assert data['shared_pcharts']['source']=='explicitly_shared_charts'
    assert data['shared_pcharts']['students'][0]['lifetime']['total']==17
    assert not any(value in json.dumps(data) for value in ['pin_hash','profile_id','notice_digest','accepted_at','source_revision'])
    assert protected_counts(refs)==before
    owner=open_page(signed(1),refs.student_path)
    assert 'SCHOOL_ADMIN_SENTINEL' in owner.text and 'EXPLICIT_CHART_SENTINEL' not in owner.text


@pytest.mark.parametrize('scope',['program','class','member'])
def test_cross_scope_student_routes_fail_without_target_information(workspace_web,scope):
    refs=workspace_web
    path=(f'{routes.PATH}/2/classes/{refs.foreign_id}/students/{refs.member_id}' if scope=='program' else
        f'{routes.PATH}/1/classes/{refs.other_id}/students/{refs.member_id}' if scope=='class' else
        refs.workspace_path+f'/students/{refs.other_member_id}')
    page=open_page(signed(2),path)
    assert page.status_code==404
    assert 'SCHOOL_ADMIN_SENTINEL' not in page.text and 'EXPLICIT_CHART' not in page.text


def test_stale_page_rechecks_share_withdrawal_assignment_and_membership(workspace_web):
    refs=workspace_web;client=signed(2)
    assert 'EXPLICIT_CHART_SENTINEL' in open_page(client,refs.student_path).text
    with refs.factory() as session:
        sharing.set_sharing(session,student=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468'),
            chart_id=refs.chart_id,assignment_ids=[]);session.commit()
    page=open_page(client,refs.student_path)
    assert page.status_code==200 and 'EXPLICIT_CHART_SENTINEL' not in page.text
    with refs.factory() as session:
        s2.end_teaching(session,actor=actor(session),program_id=1,assignment_id=refs.teacher_id);session.commit()
    assert open_page(client,refs.student_path).status_code==404
    with refs.factory() as session:
        s2.suspend_member(session,actor=actor(session),program_id=1,class_id=refs.class_id,membership_id=refs.member_id);session.commit()
    owner=open_page(signed(1),refs.student_path)
    assert owner.status_code==200 and 'EXPLICIT_CHART_SENTINEL' not in owner.text


def test_strict_forms_current_account_csrf_origin_and_no_profile_lookup(workspace_web):
    refs=workspace_web;client=signed(2)
    page=client.get(refs.student_path)
    base={'csrf':page.context['csrf'],'director':'2','pin':'2468'}
    for extra,status in [({'csrf':'bad'},403),({'director':'1'},401),({'pin':'9999'},401),
        ({'profile_id':'1'},400),({'woodchuck_id':'WC-TEST0001'},400),({'fields':'rating'},400)]:
        assert client.post(refs.student_path,data={**base,**extra}).status_code==status
    assert client.post(refs.student_path,data=base,headers={'Origin':'https://outside.invalid'}).status_code==403
    assert client.get(refs.student_path+'?profile_id=1').status_code==400
    assert client.post(refs.student_path,json=base).status_code==415


def test_unknown_or_restricted_statistics_never_display_zero_or_private_values(workspace_web):
    refs=workspace_web
    with refs.factory() as session:
        from app.age_models import AccountPrivacy
        session.get(AccountPrivacy,1).age_band='under13';session.commit()
    page=open_page(signed(1),refs.student_path)
    assert page.status_code==200
    assert page.context['data']['statistics']['values'] is None
    assert 'SCHOOL_ADMIN_SENTINEL' not in page.text and 'EXPLICIT_CHART_SENTINEL' not in page.text
