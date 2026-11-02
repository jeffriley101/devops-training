"""Real browser roster identity editing on synthetic, loopback-only data."""
from datetime import timedelta
import socket
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
import uvicorn

from app import classroom_s2 as s2, classroom_identity_routes
from app.classroom_identity_models import ClassroomRosterIdentity, ClassroomRosterIdentityAudit
from app.classroom_models import ClassroomTeachingAssignment
from app.main import app
from app.models import WoodchuckProfile
from test_classroom_s1 import actor, adult_pin_hash, classroom_db, count
from test_classroom_s2 import s2_db, NOW
from test_classroom_enrollment_ui import enrollment_web
from test_classroom_management_ui import management_web
from test_classroom_management_ui_browser import adult_context, open_scope, snapshot
from test_classroom_enrollment_ui_browser import launch


@pytest.fixture
def identity_browser_web(management_web, monkeypatch):
    monkeypatch.setattr(classroom_identity_routes, 'SessionLocal', management_web.factory)
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED', '1')
    return management_web


@pytest.fixture
def identity_site(identity_browser_web, monkeypatch):
    refs = identity_browser_web
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(minutes=1))
    # The same account is deliberately enrolled in a second Program. Its private
    # school identity there must neither overwrite nor disclose the first one.
    with refs.factory() as session:
        student = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
        s2.join_class(session, student=student, program_id=2, code='FOREIGN26')
        session.commit()
        # Set a durable public sentinel after all fixture service calls, which
        # deliberately refresh profile identities during authentication.
        session.get(WoodchuckProfile, 1).display_name = 'PUBLIC_NICKNAME_SENTINEL'
        session.commit()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('PUBLIC_BASE_URL', origin)
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', lifespan='off'))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                assert thread.is_alive()
                time.sleep(.05)
            assert server.started
            yield origin, refs
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def save_name(page, membership_id, value, *, expected_status=200):
    name = page.locator(f'#identity-name-{membership_id}')
    name.fill(value)
    form = name.locator('..')
    # The editor's label/input are direct form children, like the accepted
    # management forms; no account credential is accepted from the WC# field.
    page.locator(f'#identity-pin-{membership_id}').fill('2468')
    with page.expect_response(lambda response: response.request.method == 'POST'
                              and response.url.endswith('/identity')) as result:
        form.get_by_role('button', name='Save school name', exact=True).click()
    assert result.value.status == expected_status
    if expected_status == 200:
        expect(page.get_by_role('status').filter(has_text='School name saved.')).to_be_visible()
    else:
        expect(page.get_by_role('alert')).to_be_visible()


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_private_school_names_edit_isolation_revocation_and_public_privacy(identity_site, tmp_path, engine, width):
    origin, refs = identity_site
    initial_name = 'Morgan School-Synthetic'
    corrected_name = 'Mórgan Revised-Synthetic'
    final_name = 'Morgan Final-Synthetic'
    second_program_name = 'Program Two School-Synthetic'
    forbidden_names = [initial_name, corrected_name, final_name, second_program_name]
    errors, requests = [], []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        owner_context, owner = adult_context(browser, origin, width, 1, errors)
        owner.on('request', lambda request: requests.append(request.url))
        open_scope(owner, refs.class_path)
        expect(owner.locator(f'#identity-name-{refs.member_id}')).to_have_value('')
        assert 'PRIVATE_PUBLIC_ALIAS' not in owner.locator('main').inner_text()
        assert 'PUBLIC_NICKNAME_SENTINEL' not in owner.locator('main').inner_text()
        assert 'WC-TEST0001' in owner.locator('main').inner_text()
        save_name(owner, refs.member_id, initial_name)
        row = owner.locator(f'[data-membership="{refs.member_id}"]')
        expect(row.get_by_role('heading', name=initial_name, exact=True)).to_be_visible()
        expect(row).to_contain_text('WC-TEST0001')
        assert 'PRIVATE_PUBLIC_ALIAS' not in owner.content()
        assert 'PUBLIC_NICKNAME_SENTINEL' not in owner.content()

        # Two real pages read the same revision; a later stale save must refuse
        # instead of silently overwriting a correction from the other tab.
        stale = owner_context.new_page(); stale.set_default_timeout(12000)
        open_scope(stale, refs.class_path)
        save_name(owner, refs.member_id, corrected_name)
        save_name(stale, refs.member_id, 'Obsolete Synthetic Name', expected_status=409)
        open_scope(owner, refs.class_path)
        expect(owner.locator(f'#identity-name-{refs.member_id}')).to_have_value(corrected_name)

        teacher_context, teacher = adult_context(browser, origin, width, 2, errors)
        open_scope(teacher, refs.class_path)
        expect(teacher.get_by_role('alert')).to_be_visible()
        assert corrected_name not in teacher.content()
        other_class = refs.program_path + f'/classes/{refs.other_id}'
        open_scope(teacher, other_class)
        assert corrected_name not in teacher.content() and 'WC-TEST0001' not in teacher.content()
        csrf = teacher.locator('input[name=csrf]').first.get_attribute('value')
        forged = teacher_context.request.post(other_class + '/identity', form={
            'csrf': csrf, 'director': '2', 'pin': '2468', 'woodchuck_id': 'WC-TEST0001',
            'school_name': 'Unauthorized Synthetic Override', 'expected_version': '2'}, headers={'Origin': origin})
        assert forged.status == 409

        foreign_path = f'/classroom/programs/2/classes/{refs.foreign_id}'
        open_scope(teacher, foreign_path)
        with refs.factory() as session:
            from app.classroom_models import ClassroomStudentMembership
            foreign_member = session.scalar(select(ClassroomStudentMembership.id).where(
                ClassroomStudentMembership.class_id == refs.foreign_id,
                ClassroomStudentMembership.profile_id == 1))
        expect(teacher.locator(f'#identity-name-{foreign_member}')).to_have_value('')
        assert corrected_name not in teacher.content()
        save_name(teacher, foreign_member, second_program_name)
        snapshot(teacher, tmp_path / f'private-program-two-{engine}-{width}.png')
        open_scope(owner, refs.class_path)
        expect(owner.locator(f'#identity-name-{refs.member_id}')).to_have_value(corrected_name)
        assert second_program_name not in owner.content()

        with refs.factory() as session:
            assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, verifier_id=2)
            assignment_id = assignment.id
            session.commit()
        open_scope(teacher, refs.class_path)
        expect(teacher.locator(f'#identity-name-{refs.member_id}')).to_have_value(corrected_name)
        save_name(teacher, refs.member_id, final_name)
        snapshot(teacher, tmp_path / f'private-roster-{engine}-{width}.png')
        with refs.factory() as session:
            s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignment_id)
            session.commit()
        save_name(teacher, refs.member_id, 'Revoked Synthetic Override', expected_status=409)
        open_scope(owner, refs.class_path)
        expect(owner.locator(f'#identity-name-{refs.member_id}')).to_have_value(final_name)
        assert 'PRIVATE_PUBLIC_ALIAS' not in owner.content()

        student_context = browser.new_context(base_url=origin, viewport={'width': width, 'height': 950})
        assert student_context.request.post('/account/login', form={
            'woodchuck_id': 'WC-TEST0001', 'pin': '2468'}, headers={'Origin': origin}).status == 200
        account = student_context.request.get('/account/me')
        assert account.status == 200
        assert account.json()['profile']['display_name'] == 'PUBLIC_NICKNAME_SENTINEL'
        assert not any(value in account.text() for value in forbidden_names)
        student = student_context.new_page(); student.set_default_timeout(12000)
        student.on('pageerror', lambda error: errors.append(str(error)))
        student.goto('/home', wait_until='domcontentloaded')
        assert not any(value in student.content() for value in forbidden_names)
        guest_context = browser.new_context(base_url=origin)
        guest = guest_context.request.get('/guest')
        assert guest.status == 200 and not any(value in guest.text() for value in forbidden_names)

        with refs.factory() as session:
            identities = list(session.scalars(select(ClassroomRosterIdentity).where(
                ClassroomRosterIdentity.profile_id == 1).order_by(ClassroomRosterIdentity.program_id)))
            assert [(row.program_id, row.school_name, row.version) for row in identities] == [
                (1, final_name, 3), (2, second_program_name, 1)]
            assert count(session, ClassroomRosterIdentityAudit) == 4
            assert session.get(WoodchuckProfile, 1).display_name == 'PUBLIC_NICKNAME_SENTINEL'
        assert not any('school_name=' in url or 'pin=' in url for url in requests)
        assert not errors
        browser.close()
