"""Private school identity: storage, real authorization, isolation and no grants."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, event

from app import classroom as s1, classroom_s2 as s2, classroom_identity as service, classroom_identity_routes as routes
from app.age_models import AccountPrivacy
from app.classroom_identity_models import ClassroomRosterIdentity as Identity, ClassroomRosterIdentityAudit as Audit
from app.classroom_models import ClassroomStudentMembership
from app.models import WoodchuckProfile, TrustedVerifier
from test_classroom_s1 import actor, adult_pin_hash, classroom_db, count
from test_classroom_s2 import s2_db, NOW
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2_postgres import s2_pg
from test_classroom_enrollment_v2 import setup_enrollment
from test_classroom_enrollment_ui import enrollment_web, protected_counts
from test_classroom_management_ui import management_web, signed, view


@pytest.fixture
def identity_web(management_web, monkeypatch):
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED', '1')
    monkeypatch.setattr(routes, 'SessionLocal', management_web.factory)
    return management_web


@pytest.fixture(params=['sqlite', 'postgresql'])
def identity_db(request, monkeypatch):
    if request.param == 'sqlite':
        return request.getfixturevalue('identity_web')
    factory = request.getfixturevalue('s2_pg')
    refs = setup_enrollment(factory, monkeypatch)
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED', '1')
    monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
    with factory() as session:
        for n in (1, 2): session.get(WoodchuckProfile, n).woodchuck_id = f'WC-TEST000{n}'
        s2.join_class(session, student=s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468'), program_id=1, code='BAND2026')
        session.commit()
    return refs


def save(session, refs, *, adult=1, name='Morgan Synthetic', expected=0, wc='WC-TEST0001'):
    return service.set_school_name(session, actor=actor(session, adult), program_id=1,
        class_id=refs.class_id, woodchuck_id=wc, school_name=name, expected_version=expected)


def post_name(client, refs, *, name='Morgan School-Synthetic', version='0', wc='WC-TEST0001', **extra):
    page = client.get(refs.class_path)
    return client.post(refs.class_path + '/identity', data={'csrf': page.context['csrf'],
        'director': str(client.management_actor), 'pin': '2468', 'woodchuck_id': wc,
        'school_name': name, 'expected_version': version, **extra})


def test_store_correct_current_name_only_and_metadata_audit(identity_db):
    refs = identity_db
    before = protected_counts(refs)
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        nickname = profile.display_name
        assert save(session, refs)['identity_version'] == 1
        assert save(session, refs, name='Mo\u0301rgan Corrected', expected=1)['identity_version'] == 2
        assert save(session, refs, name='Mórgan Corrected', expected=2)['changed'] is False
        session.commit()
    with refs.factory() as session:
        row = session.scalar(select(Identity))
        assert row.school_name == 'Mórgan Corrected' and row.version == 2
        assert session.get(WoodchuckProfile, 1).display_name == nickname
        assert count(session, Audit) == 2
        assert not any('name' in c.name or 'email' in c.name or 'hash' in c.name for c in Audit.__table__.columns)
        assert [(a.version, a.action) for a in session.scalars(select(Audit).order_by(Audit.version))] == [(1,'created'),(2,'corrected')]
    assert protected_counts(refs) == before


@pytest.mark.parametrize('bad', ['', '   ', 'X'*151, 'A\nB', 'A\x00B', 'A\u202eB', 'A\u2028B'])
def test_invalid_names_leave_no_record_or_audit(identity_db, bad):
    refs = identity_db
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): save(session, refs, name=bad)
        session.commit()
        assert count(session, Identity) == count(session, Audit) == 0


def test_stale_version_and_unenrolled_wc_never_overwrite(identity_db):
    refs = identity_db
    with refs.factory() as session:
        save(session, refs); session.commit()
        with pytest.raises(s1.ClassroomDenied): save(session, refs, name='Stale Synthetic', expected=0)
        with pytest.raises(s1.ClassroomDenied): save(session, refs, wc='WC-TEST0002')
        session.commit()
        assert session.scalar(select(Identity.school_name)) == 'Morgan Synthetic' and count(session, Audit) == 1


@pytest.mark.parametrize('loss', ['assignment','hold','child','consent_link','expiry','archive'])
def test_current_sources_fail_closed(identity_db, monkeypatch, loss):
    refs = identity_db
    with refs.factory() as session:
        s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=refs.class_id, verifier_id=2)
        session.commit()
        if loss == 'assignment':
            assignment = session.scalar(select(s2.ClassroomTeachingAssignment).where(s2.ClassroomTeachingAssignment.class_id==refs.class_id,
                s2.ClassroomTeachingAssignment.verifier_id==2, s2.ClassroomTeachingAssignment.ended_at.is_(None)))
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignment.id)
        elif loss == 'hold':
            monkeypatch.setattr(s2, '_now', lambda:NOW+timedelta(minutes=1))
            member = session.scalar(select(ClassroomStudentMembership.id).where(ClassroomStudentMembership.class_id==refs.class_id,
                ClassroomStudentMembership.profile_id==1))
            s2.suspend_member(session, actor=actor(session), program_id=1,class_id=refs.class_id,membership_id=member)
        elif loss in {'child','consent_link'}:
            rule=session.get(AccountPrivacy,1)
            if loss=='child': rule.age_band='under13'
            else:
                from app import child_authorization
                from app.child_models import ConsentEvidence
                monkeypatch.setattr(child_authorization,'under13_available',lambda:True)
                monkeypatch.setattr(child_authorization,'notice_policy',lambda *a:('synthetic','Synthetic','a'*64))
                evidence=ConsentEvidence(profile_id=1,parent_email='synthetic-parent@example.test',
                    notice_version='synthetic',notice_sha256='a'*64,approved_at=NOW,confirmed_at=NOW)
                session.add(evidence);session.flush();rule.consent_id=evidence.id
        elif loss == 'archive': s2.set_class_state(session,actor=actor(session),program_id=1,class_id=refs.class_id,state='archived')
        else: monkeypatch.setattr(s2,'_now',lambda:NOW+timedelta(days=15))
        session.commit()
        with pytest.raises(s1.ClassroomDenied): save(session,refs,adult=2)
        session.commit()
        assert count(session,Identity)==count(session,Audit)==0


def test_unassigned_roles_and_wc_are_not_authentication(identity_web):
    refs=identity_web
    for n in [2,3,4]:
        with refs.factory() as session:
            with pytest.raises(s1.ClassroomDenied): save(session,refs,adult=n)
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied):
            service.set_school_name(session,actor=1,program_id=1,class_id=refs.class_id,woodchuck_id='WC-TEST0001',school_name='Synthetic',expected_version=0)
    guest=TestClient(__import__('app.main',fromlist=['app']).app)
    csrf=guest.get(refs.class_path).context['csrf']
    assert guest.post(refs.class_path+'/identity',data={'csrf':csrf,'director':'1','pin':'2468',
        'woodchuck_id':'WC-TEST0001','school_name':'Synthetic','expected_version':'0'}).status_code==401


def test_http_authority_csrf_fields_no_store_and_gate(identity_web, monkeypatch):
    refs=identity_web;client=signed()
    assert post_name(client,refs,csrf='bad').status_code==403
    assert post_name(client,refs,director='2').status_code==401
    assert post_name(client,refs,pin='9999').status_code==401
    assert post_name(client,refs,profile_id='1').status_code==400
    assert post_name(client,refs,version='-1').status_code==400
    saved=post_name(client,refs)
    assert saved.status_code==200 and 'Morgan School-Synthetic' in saved.text
    assert 'PRIVATE_PUBLIC_ALIAS' not in saved.text and 'pin_hash' not in saved.text
    assert saved.headers['cache-control']=='no-store' and saved.headers['referrer-policy']=='no-referrer'
    assert post_name(client,refs,name='Stale',version='0').status_code==409
    monkeypatch.delenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED')
    assert post_name(client,refs).status_code==404
    page=view(client,refs.class_path)
    assert 'Morgan School-Synthetic' not in page.text and 'identity-name-' not in page.text


def test_audit_or_refresh_failure_rolls_back_entire_save(identity_web, monkeypatch):
    refs=identity_web
    engine=refs.factory.kw['bind']
    def broken(conn,cursor,statement,params,context,many):
        if statement.startswith('INSERT INTO classroom_roster_identity_audits'):raise RuntimeError('synthetic audit failure')
    event.listen(engine,'before_cursor_execute',broken)
    try:
        with refs.factory() as session:
            with pytest.raises(RuntimeError): save(session,refs)
            session.commit()
            assert count(session,Identity)==0
    finally:event.remove(engine,'before_cursor_execute',broken)
    from app import classroom_management_views
    monkeypatch.setattr(classroom_management_views,'detail',lambda *a,**k: (_ for _ in ()).throw(s1.ClassroomDenied('synthetic integrity failure')))
    assert post_name(signed(),refs).status_code==409
    with refs.factory() as session: assert count(session,Identity)==count(session,Audit)==0


def test_postgres_competing_corrections_observe_lock_and_reject_stale(s2_pg, monkeypatch):
    refs=setup_enrollment(s2_pg,monkeypatch)
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED','1');monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED','1')
    with refs.factory() as session:
        session.get(WoodchuckProfile,1).woodchuck_id='WC-TEST0001'
        session.flush()
        s2.join_class(session,student=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468'),program_id=1,code='BAND2026')
        session.commit()
    with refs.factory() as session:save(session,refs);session.commit()
    first,second=ordered_wait(refs.factory,
        lambda session:save(session,refs,name='First correction',expected=1),
        lambda session:save(session,refs,name='Stale correction',expected=1))
    assert first[0]=='ok' and second[0]=='denied'
    with refs.factory() as session:
        assert session.scalar(select(Identity.school_name))=='First correction'
        assert count(session,Audit)==2


def test_postgres_revoked_teacher_waiting_on_program_cannot_write(s2_pg,monkeypatch):
    refs=setup_enrollment(s2_pg,monkeypatch)
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED','1');monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED','1')
    with refs.factory() as session:
        session.get(WoodchuckProfile,1).woodchuck_id='WC-TEST0001';session.flush()
        s2.join_class(session,student=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468'),program_id=1,code='BAND2026')
        assignment=s2.assign_teacher(session,actor=actor(session),program_id=1,class_id=refs.class_id,verifier_id=2)
        assignment_id=assignment.id;session.commit()
    first,second=ordered_wait(refs.factory,
        lambda session:s2.end_teaching(session,actor=actor(session),program_id=1,assignment_id=assignment_id).id,
        lambda session:save(session,refs,adult=2))
    assert first[0]=='ok' and second[0]=='denied'
    with refs.factory() as session:assert count(session,Identity)==count(session,Audit)==0


def test_read_helper_requires_locked_program_and_errors_never_echo_name(identity_web, monkeypatch):
    refs=identity_web
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied):service._roster_identity(session,program_id=1,profile_id=1)
    from sqlalchemy.exc import OperationalError
    def unavailable(*args,**kwargs):raise OperationalError('synthetic statement',{'school_name':'PRIVATE_ERROR_SENTINEL'},Exception('synthetic outage'))
    monkeypatch.setattr(service,'set_school_name',unavailable)
    result=post_name(signed(),refs,name='PRIVATE_ERROR_SENTINEL')
    assert result.status_code==503 and 'PRIVATE_ERROR_SENTINEL' not in result.text
    with refs.factory() as session:assert count(session,Identity)==count(session,Audit)==0


def test_final_helper_and_form_origin_escaping(identity_web):
    refs=identity_web;client=signed()
    name='<script>window.PRIVATE_XSS=true</script>'
    result=post_name(client,refs,name=name)
    assert result.status_code==200 and name not in result.text and '&lt;script&gt;' in result.text
    page=client.get(refs.class_path)
    denied=client.post(refs.class_path+'/identity',data={'csrf':page.context['csrf'],'director':'1','pin':'2468',
        'woodchuck_id':'WC-TEST0001','school_name':'Cross site must fail','expected_version':'1'},headers={'Origin':'https://outside.invalid'})
    assert denied.status_code==403
    with refs.factory() as session:assert session.scalar(select(Identity.school_name))==name and count(session,Audit)==1


def test_postgres_private_read_waits_for_account_deletion(s2_pg,monkeypatch):
    refs=setup_enrollment(s2_pg,monkeypatch)
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED','1');monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED','1')
    with refs.factory() as session:
        session.get(WoodchuckProfile,1).woodchuck_id='WC-TEST0001';session.flush()
        s2.join_class(session,student=s2.authenticate_student(session,woodchuck_id='WC-TEST0001',pin='2468'),program_id=1,code='BAND2026')
        save(session,refs);session.commit()
    def deletion(session):
        profile=session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id==1).with_for_update())
        profile.status='deleted'
        session.scalar(select(Identity)).school_name='[removed]'
        session.flush();return True
    def read(session):
        from app.classroom_management_views import detail
        return detail(session,actor=actor(session),program_id=1,class_id=refs.class_id)['selected_class']['roster'][0]
    first,second=ordered_wait(refs.factory,deletion,read)
    assert first[0]==second[0]=='ok'
    assert second[1]['school_name'] is None and second[1]['woodchuck_id'] is None


@pytest.mark.parametrize('loss',['child','unknown','deleted'])
def test_existing_private_name_is_masked_after_account_authorization_loss(identity_web,loss):
    refs=identity_web
    with refs.factory() as session:
        save(session,refs,name='SCHOOL_PRIVATE_MASK_SENTINEL');session.commit()
        if loss=='deleted':session.get(WoodchuckProfile,1).status='deleted'
        else:session.get(AccountPrivacy,1).age_band='under13' if loss=='child' else 'unknown'
        session.commit()
    page=view(signed(),refs.class_path)
    assert page.status_code==200 and 'SCHOOL_PRIVATE_MASK_SENTINEL' not in page.text
    assert page.context['detail']['selected_class']['roster'][0]['school_name'] is None


def test_postgres_shared_program_pages_lock_accounts_in_global_order(s2_pg,monkeypatch):
    from app.models import Organization
    from app.classroom_management_views import detail
    from test_classroom_s1 import provision
    from test_classroom_s2 import ready
    refs=setup_enrollment(s2_pg,monkeypatch)
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED','1');monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED','1')
    with refs.factory() as session:
        session.add(Organization(id=2,name='Second synthetic Program',organization_type='music'))
        session.flush();provision(session,2);session.commit()
        room,_,_=ready(session,program_id=2,preferred='OTHERPG26');other_id=room.id;session.commit()
        for profile_id,code,program in [(1,'BAND2026',1),(2,'BAND2026',1),(2,'OTHERPG26',2),(1,'OTHERPG26',2)]:
            wc=session.get(WoodchuckProfile,profile_id).woodchuck_id
            s2.join_class(session,student=s2.authenticate_student(session,woodchuck_id=wc,pin='2468'),program_id=program,code=code)
            session.commit()
    observed=[]
    def capture(conn,cursor,statement,params,context,many):
        if 'FROM woodchuck_profiles' in statement and 'FOR UPDATE' in statement:
            observed.append(params['id_1'])
    engine=refs.factory.kw['bind'];event.listen(engine,'before_cursor_execute',capture)
    try:
        for program,class_id in [(1,refs.class_id),(2,other_id)]:
            with refs.factory() as session:
                assert len(detail(session,actor=actor(session),program_id=program,class_id=class_id)['selected_class']['roster'])==2
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert observed==[1,2,1,2]
