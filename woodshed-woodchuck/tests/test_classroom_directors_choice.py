"""Saved assignment security on real migrated SQLite and disposable PostgreSQL."""
from datetime import date, timedelta
from uuid import uuid4
import re

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, update, delete, text
from sqlalchemy.exc import IntegrityError
from app import classroom as s1, classroom_s2 as s2, classroom_directors_choice as choice
from app import classroom_guardian_authorization as guardian
from app.classroom_choice_models import ClassroomChoiceAssignment as Assignment, ClassroomChoiceVersion as Version
from app.classroom_models import ClassroomEntitlement, ClassroomClassState
from app.main import app
from app.operator_schema_compatibility import installed_revision, classroom_snapshot
from app.operator_classroom_choice_contract import C36
from app.weekly_homework import exercise_spec
from test_classroom_guardian_authorization import (guardian_data, migrated_db, captured, adult_pin_hash,
    actor, student, parent_accept, parent_proof, read)
from test_classroom_student_home import rows
from test_classroom_s2 import create, code, institutional
from test_classroom_s1_postgres import ordered_wait
from tests.test_weekly2_foundation_migration import config, rows as reflected_rows, schema_contract

CUSTOM = {'kind': 'custom', 'title': 'Two quiet measures', 'instructions': 'Play measures 1–2 slowly. Listen for even tone.'}
RECIPE = {'kind': 'recipe', 'instrument': 'flute', 'focus': 'Improve tone and control', 'mode': 'Challenge'}
PATH = '/classroom/directors-choice'


@pytest.fixture
def choice_data(guardian_data, monkeypatch):
    monkeypatch.setenv('CLASSROOM_DIRECTORS_CHOICE_ENABLED', '1')
    with guardian_data.factory() as session:
        parent_accept(session, guardian_data, 'participation')
        session.commit()
    return guardian_data


def save(session, refs, **kwargs):
    return choice.create(session, **(dict(actor=actor(session, 2), program_id=1, class_id=refs.class_id,
        week_start=choice.week(), request_key=uuid4().hex, fields=CUSTOM) | kwargs))


def saved(refs, **kwargs):
    with refs.factory() as session:
        result = save(session, refs, **kwargs)
        session.commit()
        return result


def student_read(session, refs, **kwargs):
    return choice.student_page(session, **(dict(student=student(session), program_id=1, class_id=refs.class_id) | kwargs))


def edit(session, refs, row, **kwargs):
    return choice.change(session, **(dict(actor=actor(session, 2), program_id=1, class_id=refs.class_id,
        assignment_id=row['id'], expected_version=row['version'], fields=CUSTOM | {'title': 'Edited'}) | kwargs))


def director(refs):
    client = TestClient(app, base_url='https://testserver')
    assert client.post('/trusted-verifiers/login', data={'email': 'adult2@example.test', 'pin': '2468'},
        headers={'Origin': 'https://testserver'}).status_code == 200
    return client


def submit(client, refs, **fields):
    url = PATH + f'/1/classes/{refs.class_id}'
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get(url).text).group(1)
    return client.post(url, data={'csrf': csrf, 'director': '2', 'pin': '2468', **fields}, headers={'Origin': 'https://testserver'})


def test_versions_receipts_no_student_data_and_recipe_reuse(choice_data):
    refs = choice_data
    before = rows(refs.factory.engine)
    key = uuid4().hex
    one = saved(refs, request_key=key)
    assert saved(refs, request_key=key) == one
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): save(session, refs, request_key=key, fields=RECIPE)
        two = edit(session, refs, one, fields=RECIPE)
        session.commit()
        assert two['version'] == 2
        assert two['content'] == {**exercise_spec(choice.week(), RECIPE['focus'], 'flute', 'Challenge'), **RECIPE}
        with pytest.raises(s1.ClassroomDenied): edit(session, refs, one)
        three = edit(session, refs, two, fields=None, cancel=True)
        session.commit()
        assert three['state'] == 'cancelled'
        with pytest.raises(s1.ClassroomDenied): edit(session, refs, three)
        history = choice.history(session, actor=actor(session, 2), program_id=1, class_id=refs.class_id, assignment_id=one['id'])
        assert [v['version'] for v in history['versions']] == [3, 2, 1]
        assert history['versions'][-1]['content']['title'] == CUSTOM['title']
        assert student_read(session, refs)['assignments'][0]['state'] == 'cancelled'
        with pytest.raises(s1.ClassroomDenied): read(session, refs)  # participation never authorizes reports
    after = rows(refs.factory.engine)
    assert {k: v for k, v in before.items() if k not in C36} == {k: v for k, v in after.items() if k not in C36}
    assert len(after[Assignment.__tablename__]) == 1 and len(after[Version.__tablename__]) == 3


@pytest.mark.parametrize('failure', ['owner', 'unassigned', 'program', 'class', 'entitlement', 'archived', 'role-ended', 'gate'])
def test_exact_director_authority(choice_data, monkeypatch, failure):
    refs = choice_data
    row = saved(refs)
    with refs.factory() as session:
        args = {}
        if failure in {'owner', 'unassigned'}: args['actor'] = actor(session, 1 if failure == 'owner' else 3)
        if failure == 'program': args['program_id'] = 2
        if failure == 'class': args['class_id'] = refs.class_id + 1
        if failure == 'entitlement': institutional(session, status='ended'); session.commit()
        if failure == 'archived': s2.set_class_state(session, actor=actor(session), program_id=1, class_id=refs.class_id, state='archived'); session.commit()
        if failure == 'role-ended':
            refs.clock[0] += timedelta(seconds=2)
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id); session.commit()
        if failure == 'gate': monkeypatch.delenv('CLASSROOM_DIRECTORS_CHOICE_ENABLED')
        with pytest.raises(s1.ClassroomDenied): save(session, refs, **args)
        with pytest.raises(s1.ClassroomDenied): edit(session, refs, row, **args)
        with pytest.raises(s1.ClassroomDenied): choice.director_page(session, **(dict(actor=actor(session,2),program_id=1,class_id=refs.class_id) | args))


def test_class_work_survives_teacher_change_and_closed_enrollment(choice_data):
    refs=choice_data
    row=saved(refs)
    refs.clock[0]+=timedelta(seconds=2)
    with refs.factory() as session:
        s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=refs.assignment_id)
        session.commit()
        replacement=s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=refs.class_id, verifier_id=3)
        session.get(ClassroomClassState,refs.class_id).enrollment_open=False
        session.commit()
        # The accepted parent contract binds the current teacher set. A role
        # replacement keeps work but requires a fresh parent participation choice.
        with pytest.raises(s1.ClassroomDenied): student_read(session,refs)
        parent_accept(session,refs,'participation');session.commit()
        assert student_read(session,refs)['assignments'][0]['id']==row['id']
        changed=edit(session,refs,row,actor=actor(session,3))
        session.commit()
        assert session.get(Version,(row['id'],1)).author_assignment_id==refs.assignment_id
        assert session.get(Version,(row['id'],2)).author_assignment_id==replacement.id
        assert changed['content']['title']=='Edited'


@pytest.mark.parametrize('failure',['withdrawal','leave','hold','program','class','entitlement','archived'])
def test_student_boundaries_do_not_leak_assignments(choice_data,failure):
    refs=choice_data
    saved(refs)
    refs.clock[0]+=timedelta(seconds=2)
    with refs.factory() as session:
        args={}
        if failure=='withdrawal': guardian.withdraw(session,parent=parent_proof(session,refs),program_id=1,class_id=refs.class_id,purpose='participation')
        if failure=='leave': s2.leave_class(session,student=student(session),program_id=1,class_id=refs.class_id)
        if failure=='hold': s2.suspend_member(session,actor=actor(session),program_id=1,class_id=refs.class_id,membership_id=refs.membership_id)
        if failure=='program': args['program_id']=2
        if failure=='class': args['class_id']=refs.class_id+1
        if failure=='entitlement': institutional(session, status='ended')
        if failure=='archived': s2.set_class_state(session,actor=actor(session),program_id=1,class_id=refs.class_id,state='archived')
        session.commit()
        with pytest.raises(s1.ClassroomDenied): student_read(session,refs,**args)
    if failure not in {'program', 'class'}:
        assert CUSTOM['title'] not in refs.child.get('/classroom').text


@pytest.mark.parametrize('guardian_data',[{'under13':True}],indirect=True)
def test_authorized_younger_learning_does_not_open_reporting(choice_data):
    refs=choice_data
    saved(refs)
    with refs.factory() as session:
        assert student_read(session,refs)['assignments']
        with pytest.raises(s1.ClassroomDenied): read(session,refs)
    assert CUSTOM['title'] in refs.child.get('/classroom').text


@pytest.mark.parametrize('start,hours',[(date(2027,3,8),167),(date(2027,11,1),169),(date(2026,11,2),168)])
def test_central_week_dst_boundaries(start,hours):
    beginning,end=choice.bounds(start)
    assert (end-beginning).total_seconds()==hours*3600
    assert beginning.astimezone(choice.CENTRAL).weekday()==0
    assert end.astimezone(choice.CENTRAL).hour==0


def test_future_and_expired_work_is_read_only(choice_data):
    refs=choice_data
    upcoming=choice.week()+timedelta(weeks=1)
    row=saved(refs,week_start=upcoming)
    assert row['timing']=='scheduled'
    with refs.factory() as session:
        assert student_read(session,refs)['assignments']==[]
        assert student_read(session,refs,week_start=upcoming)['assignments'][0]['id']==row['id']
    refs.clock[0]=choice.bounds(upcoming)[1]-timedelta(microseconds=1)
    with refs.factory() as session:
        updated=edit(session,refs,row);session.commit()
    refs.clock[0]+=timedelta(microseconds=1)
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): edit(session,refs,updated)
        with pytest.raises(s1.ClassroomDenied): edit(session,refs,updated,cancel=True,fields=None)
        with pytest.raises(s1.ClassroomDenied): save(session,refs,week_start=upcoming)
        assert student_read(session,refs,week_start=upcoming)['assignments'][0]['timing']=='expired'


def test_saved_history_cannot_be_deleted_updated_or_downgraded(choice_data):
    refs=choice_data;row=saved(refs)
    for query in (update(Version).values(state='cancelled'),delete(Version)):
        with refs.factory() as session, pytest.raises(IntegrityError):
            session.execute(query);session.commit()
    if refs.factory.engine.dialect.name == 'postgresql':
        with refs.factory() as session, pytest.raises(IntegrityError):
            session.execute(text('TRUNCATE classroom_choice_versions'))
    with pytest.raises(RuntimeError,match='history_in_use'): command.downgrade(config(),'c35school001')
    with refs.factory.engine.connect() as connection:
        assert installed_revision(connection,RuntimeError)=='c36choice001'
        assert set(C36)<=classroom_snapshot(connection,'c36choice001').keys()


def test_empty_additive_migration_roundtrip_and_drift(migrated_db):
    command.upgrade(config(),'c35school001')
    before=reflected_rows(migrated_db);schema=schema_contract(migrated_db)
    command.upgrade(config(),'head')
    after=reflected_rows(migrated_db)
    assert {k:v for k,v in after.items() if k not in C36}==before
    with migrated_db.connect() as connection: assert installed_revision(connection,RuntimeError)=='c36choice001'
    command.downgrade(config(),'c35school001')
    assert reflected_rows(migrated_db)==before
    assert schema_contract(migrated_db)==schema
    command.upgrade(config(),'head')
    with migrated_db.begin() as connection:
        connection.exec_driver_sql('DROP TRIGGER classroom_choice_update' if connection.dialect.name=='sqlite' else 'ALTER TABLE classroom_choice_versions DISABLE TRIGGER classroom_choice_change')
        with pytest.raises(RuntimeError,match='guard'): installed_revision(connection,RuntimeError)


def test_http_native_authentication_plaintext_and_form_boundaries(choice_data):
    refs=choice_data;client=director(refs)
    evil=CUSTOM|{'title':'<script>bad()</script>','instructions':'<img src=x onerror=bad()>'}
    response=submit(client,refs,action='create',week=choice.week().isoformat(),request_key=uuid4().hex,**evil)
    assert response.status_code==200,response.text
    assert '<script>bad()' not in response.text and '&lt;script&gt;' in response.text
    assert response.headers['cache-control']=='no-store'
    own=refs.child.get(PATH+f'/student/1/classes/{refs.class_id}')
    assert own.status_code==200 and '&lt;script&gt;' in own.text and 'name="pin"' not in own.text
    for fields,status in [({'pin':'0000'},401),({'director':'1'},401),({'notes':'private'},400),({'before':'1 OR 1=1'},400)]:
        response=submit(client,refs,**({'action':'view','week':choice.week().isoformat(),'before':''}|fields))
        assert response.status_code==status,response.text
    url=PATH+f'/1/classes/{refs.class_id}'
    assert client.post(url,data={},headers={'Origin':'https://other.test'}).status_code==403
    assert client.post(url,data={},headers={'Origin':''}).status_code==403
    assert client.post(url,data={'action':'view'},headers={'Origin':'https://testserver'}).status_code==403
    assert refs.child.get(PATH+f'/student/2/classes/{refs.class_id}').status_code==403
    assert refs.child.get(PATH+f'/student/1/classes/{refs.class_id}?profile_id=2').status_code==400
    assert refs.child.get(PATH+'/student/99999999999999999999/classes/1').status_code==400
    assert client.post(PATH+'/99999999999999999999/classes/1',data={'action':'view'}).status_code==400
    assert refs.child.get(PATH+f'/student/1/classes/{refs.class_id}?week=2026-11-03').status_code==403


def test_content_validation_and_transaction_rollback(choice_data):
    refs=choice_data
    with refs.factory() as session:
        for fields in (CUSTOM|{'title':''},CUSTOM|{'instructions':'a'*1001},RECIPE|{'mode':'new'},CUSTOM|{'completed':True}, {'kind':'custom'}):
            with pytest.raises(s1.ClassroomDenied): save(session,refs,fields=fields)
        save(session,refs)
        session.rollback()
        assert list(session.scalars(select(Assignment)))==[]


@pytest.mark.parametrize('migrated_db',['postgresql'],indirect=True)
@pytest.mark.parametrize('operation',['duplicate','edit-edit','edit-cancel','role-revocation','parent-withdrawal','entitlement','expiry','rollback'])
def test_postgresql_observed_waits(choice_data,operation):
    refs=choice_data;row=saved(refs)
    refs.clock[0]+=timedelta(seconds=2)
    key=uuid4().hex
    def first(s):
        if operation=='duplicate': return save(s,refs,request_key=key)
        if operation=='role-revocation': return s2.end_teaching(s,actor=actor(s),program_id=1,assignment_id=refs.assignment_id).id
        if operation=='parent-withdrawal': return guardian.withdraw(s,parent=parent_proof(s,refs),program_id=1,class_id=refs.class_id,purpose='participation').id
        if operation=='entitlement':
            institutional(s, status='ended');return True
        if operation=='expiry':
            s1._lock_program(s,1);refs.clock[0]=choice.bounds(date.fromisoformat(row['week']))[1];return True
        return edit(s,refs,row)
    def second(s):
        if operation=='duplicate': return save(s,refs,request_key=key)
        if operation in {'parent-withdrawal','entitlement'}: return student_read(s,refs)
        return edit(s,refs,row,**({'cancel':True,'fields':None} if operation=='edit-cancel' else {}))
    results=ordered_wait(refs.factory,first,second,rollback_first=operation=='rollback')
    assert [r[0] for r in results]==['ok','ok' if operation in {'duplicate','rollback'} else 'denied']


def test_populated_accepted_upgrade_preserves_every_existing_row(choice_data):
    refs=choice_data
    before=rows(refs.factory.engine)
    command.downgrade(config(),'c35school001')
    command.upgrade(config(),'head')
    assert rows(refs.factory.engine)==before
    with refs.factory.engine.connect() as connection:
        assert installed_revision(connection,RuntimeError)=='c36choice001'
