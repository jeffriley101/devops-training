"""Audience changes, scope constraints and observed native-authentication races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import time
from uuid import uuid4
import json
from base64 import b64decode
from itsdangerous import TimestampSigner
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app import classroom as s1, classroom_s2 as s2, classroom_directors_choice as choice
from app import classroom_student_invitations as invitations, child_authorization
from app.classroom_choice_models import ClassroomChoiceAssignment as Assignment, ClassroomChoiceVersion as Version
from app.child_models import ConsentEvidence
from app.models import WoodchuckProfile, TrustedVerifier
from app.security import hash_pin
from app.session_revocations import NONCE, retire
from app.main import app, SESSION_SECRET
from test_classroom_directors_choice import (choice_data, guardian_data, migrated_db, captured, adult_pin_hash,
    actor, student, parent_accept, parent_proof, saved, save, edit, student_read, CUSTOM, PATH, director, submit, rows)
from test_classroom_s2 import create, code, institutional


def other_student(refs):
    client=TestClient(app,base_url='https://testserver')
    assert client.post('/account/login',data={'woodchuck_id':'WC-AGE-B','pin':'2468'},headers={'Origin':'https://testserver'}).status_code==200
    age=client.get('/account/age')
    result=client.post('/account/age',data={'csrf':age.context['csrf'],'confirm_account':'WC-AGE-B','age_band':'adult'},follow_redirects=False)
    assert result.status_code==303,result.text
    return client


def test_current_membership_audience_and_pchart_invitation_independence(choice_data):
    refs=choice_data;row=saved(refs)
    second=other_student(refs)
    url=PATH+f'/student/1/classes/{refs.class_id}'
    assert second.get(url).status_code==403
    with refs.factory() as session:
        invite=invitations.issue(session,actor=actor(session,2),program_id=1,class_id=refs.class_id,assignment_id=refs.assignment_id,profile_id=2)
        session.commit()
        invitations.accept(session,student=s2.authenticate_student(session,woodchuck_id='WC-AGE-B',pin='2468'),invitation_id=invite.id)
        session.commit()
    assert second.get(url).status_code==403  # sharing invitation is never membership
    refs.clock[0]+=timedelta(seconds=2)
    with refs.factory() as session:
        s2.join_class(session,student=s2.authenticate_student(session,woodchuck_id='WC-AGE-B',pin='2468'),program_id=1,code='BAND2026')
        session.commit()
    assert CUSTOM['title'] in second.get(url).text  # current member; no per-student audience copy
    with refs.factory() as session:
        refs.clock[0]+=timedelta(seconds=2)
        s2.leave_class(session,student=s2.authenticate_student(session,woodchuck_id='WC-AGE-B',pin='2468'),program_id=1,class_id=refs.class_id)
        session.commit()
    assert second.get(url).status_code==403
    assert CUSTOM['title'] in refs.child.get(url).text


def test_scope_foreign_keys_and_wrong_class_assignment_reference(choice_data):
    refs=choice_data;row=saved(refs)
    with refs.factory() as session:
        other=create(session,name='Other Class');session.commit()
        other_role=s2.assign_teacher(session,actor=actor(session),program_id=1,class_id=other.id,verifier_id=2);session.commit()
        with pytest.raises(s1.ClassroomDenied): edit(session,refs,row,class_id=other.id)
        with pytest.raises(s1.ClassroomDenied): choice.history(session,actor=actor(session,2),program_id=1,class_id=other.id,assignment_id=row['id'])
        with pytest.raises(IntegrityError),session.begin_nested():
            session.add(Version(assignment_id=row['id'],version=2,program_id=1,class_id=other.id,
                author_assignment_id=other_role.id,authored_at=refs.clock[0],state='active',content={}));session.flush()
        with pytest.raises(IntegrityError),session.begin_nested():
            session.add(Version(assignment_id=row['id'],version=2,program_id=1,class_id=refs.class_id,
                author_assignment_id=other_role.id,authored_at=refs.clock[0],state='active',content={}));session.flush()
        with pytest.raises(IntegrityError),session.begin_nested():
            session.add(Assignment(program_id=2,class_id=refs.class_id,week_start=choice.week(),created_at=refs.clock[0],request_key=uuid4().hex,current_version=1));session.flush()


def test_assignment_and_history_pagination_do_not_mix_classes_or_weeks(choice_data,monkeypatch):
    refs=choice_data
    monkeypatch.setattr(choice,'PAGE_SIZE',2)
    ids=[saved(refs)['id'] for _ in range(3)]
    saved(refs,week_start=choice.week()+timedelta(weeks=1))
    with refs.factory() as session:
        first=student_read(session,refs)
        second=student_read(session,refs,before=first['next_before'])
        assert [v['id'] for v in first['assignments']+second['assignments']]==ids[::-1]
        row=first['assignments'][0]
        for title in ('Edit two','Edit three'):
            row=edit(session,refs,row,fields=CUSTOM|{'title':title});session.commit()
        history=choice.history(session,actor=actor(session,2),program_id=1,class_id=refs.class_id,assignment_id=row['id'])
        older=choice.history(session,actor=actor(session,2),program_id=1,class_id=refs.class_id,assignment_id=row['id'],before=history['next_before'])
        assert [v['version'] for v in history['versions']+older['versions']]==[3,2,1]


def test_unicode_multiline_payload_and_gate_and_cookie_replay(choice_data,monkeypatch):
    refs=choice_data;client=director(refs)
    response=submit(client,refs,action='create',week=choice.week().isoformat(),request_key=uuid4().hex,
        **(CUSTOM|{'instructions':'音'*990+'\r\nend'}))
    assert response.status_code==200,response.text
    csrf=client.get(PATH+f'/1/classes/{refs.class_id}').context['csrf']
    cookie=client.cookies.get('session')
    payload=json.loads(b64decode(TimestampSigner(SESSION_SECRET).unsign(cookie)))
    retire(payload[NONCE])
    assert client.post(PATH+f'/1/classes/{refs.class_id}',data={'csrf':csrf,'director':'2','pin':'2468','action':'view','week':choice.week().isoformat(),'before':''},headers={'Origin':'https://testserver'}).status_code in {401,403}
    monkeypatch.delenv('CLASSROOM_DIRECTORS_CHOICE_ENABLED')
    assert refs.child.get(PATH+f'/student/1/classes/{refs.class_id}').status_code==404
    assert 'id="student-directors-choice"' not in refs.child.get('/classroom').text


@pytest.mark.parametrize('migrated_db',['postgresql'],indirect=True)
@pytest.mark.parametrize('change',['director-credentials','director-nonce','student-credentials','student-nonce','native-withdrawal'])
def test_http_rechecks_native_authority_after_observed_program_wait(choice_data,change):
    refs=choice_data;saved(refs)
    adult=change.startswith('director')
    client=director(refs) if adult else refs.child
    cookie=client.cookies.get('session')
    payload=json.loads(b64decode(TimestampSigner(SESSION_SECRET).unsign(cookie)))
    url=PATH+f'/1/classes/{refs.class_id}' if adult else PATH+f'/student/1/classes/{refs.class_id}'
    form={'csrf':client.get(url).context['csrf'],'director':'2','pin':'2468','action':'create','week':choice.week().isoformat(),'request_key':uuid4().hex,**CUSTOM} if adult else None
    ready,release=Event(),Event();pids=[]
    def invalidator():
        with refs.factory() as session:
            s1._lock_program(session,1)
            pids.append(session.scalar(text('SELECT pg_backend_pid()')))
            ready.set();assert release.wait(15)
            session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(invalidator);assert ready.wait(10)
        second=pool.submit(client.post,url,data=form,headers={'Origin':'https://testserver'}) if adult else pool.submit(client.get,url)
        try:
            with refs.factory.control_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if observer.scalar(text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid=ANY(pg_blocking_pids(pid)))'),{'pid':pids[0]}): break
                    time.sleep(.02)
                else: pytest.fail('No observed PostgreSQL Program lock wait')
            if change.endswith('nonce'): retire(payload[NONCE])
            else:
                with refs.factory() as session:
                    if change=='director-credentials': session.get(TrustedVerifier,2).pin_hash=hash_pin('8642')
                    elif change=='student-credentials': session.get(WoodchuckProfile,refs.profile_id).session_version+=1
                    else:
                        evidence=session.scalar(select(ConsentEvidence).where(ConsentEvidence.profile_id==refs.profile_id).with_for_update())
                        child_authorization.withdraw_evidence(session,evidence)
                    session.commit()
        finally: release.set()
        first.result(timeout=15);response=second.result(timeout=15)
    assert response.status_code in {401,403,409},response.text
    assert CUSTOM['title'] not in response.text
    with refs.factory() as session:
        assert len(list(session.scalars(select(Assignment))))==1
