"""Browser submissions through the actual management routes on synthetic data."""
from datetime import timedelta
import socket
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
import uvicorn

from app import classroom_s2 as s2
from app.classroom_models import (ClassroomClass, ClassroomProgram, ClassroomRoleGrant,
    ClassroomTeachingAssignment, ClassroomStudentMembership, ClassroomMembershipPeriod)
from app.email_service import DeliveryResult, EmailService
from app.main import app
from test_classroom_s1 import adult_pin_hash, classroom_db, count
from test_classroom_s2 import s2_db, NOW
from test_classroom_enrollment_ui import enrollment_web, protected_counts
from test_classroom_management_ui import management_web, PATH
from test_classroom_enrollment_ui_browser import launch


@pytest.fixture
def management_site(management_web, monkeypatch):
    clock = [NOW + timedelta(minutes=1)]
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('PUBLIC_BASE_URL', origin)
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', lifespan='off'))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started: break
                assert thread.is_alive()
                time.sleep(.05)
            assert server.started
            yield origin, management_web, clock
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def adult_context(browser, origin, width, adult, errors):
    context = browser.new_context(base_url=origin, viewport={'width': width, 'height': 950}, reduced_motion='reduce')
    response = context.request.post('/trusted-verifiers/login', form={
        'email': f'adult{adult}@example.test', 'pin': '2468'}, headers={'Origin': origin})
    assert response.status == 200
    page = context.new_page(); page.set_default_timeout(12000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    return context, page


def open_scope(page, path):
    page.goto(path, wait_until='domcontentloaded')
    page.locator('#access-pin').fill('2468')
    page.get_by_role('button', name='Open management controls', exact=True).click()


def submit_change(page, pin_id):
    field = page.locator('#' + pin_id)
    field.fill('2468')
    field.locator('..').locator('button[type=submit]').click()
    expect(page.locator('#confirm-title')).to_be_visible()
    page.locator('#confirm-pin').fill('2468')
    page.locator('.management-confirm button[type=submit]').click()
    expect(page.get_by_role('status').filter(has_text='completed.')).to_be_visible()


def snapshot(page, path):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    for button in page.locator('button:visible').all():
        assert button.inner_text().strip() and button.bounding_box()['height'] >= 44
    page.screenshot(path=str(path), full_page=True)


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_actual_management_release_workflow(management_site, tmp_path, engine, width):
    origin, refs, clock = management_site
    errors = []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        owner_context, owner = adult_context(browser, origin, width, 1, errors)
        before = protected_counts(refs)
        owner.goto(PATH)
        owner.locator('#catalog-pin').fill('2468')
        owner.get_by_role('button', name='View my Programs & Classes', exact=True).click()
        assert 'Program 2' not in owner.locator('main').inner_text()
        owner.get_by_role('link', name='Music Program 1', exact=False).click()
        owner.locator('#access-pin').fill('2468')
        owner.get_by_role('button', name='Open management controls', exact=True).click()
        expect(owner.get_by_role('heading', name='Program roles', exact=True)).to_be_visible()
        snapshot(owner, tmp_path / f'program-{engine}-{width}.png')

        owner.locator('#class-name').fill('Browser Wind Ensemble')
        submit_change(owner, 'create-class-pin')
        expect(owner.get_by_role('heading', name='Browser Wind Ensemble', exact=True)).to_be_visible()
        with refs.factory() as session:
            class_id = session.scalar(select(ClassroomClass.id).where(ClassroomClass.display_name == 'Browser Wind Ensemble'))
        class_path = refs.program_path + f'/classes/{class_id}'
        owner.locator('#preferred-code').fill('BROWSER26')
        submit_change(owner, 'code-pin')
        expect(owner.locator('#management-new-code')).to_have_text('BROWSER26')
        assert owner.locator('.enrollment-qr img').evaluate('image => image.complete && image.naturalWidth > 0')
        snapshot(owner, tmp_path / f'code-{engine}-{width}.png')
        open_scope(owner, class_path)
        assert 'BROWSER26' not in owner.content()
        owner.locator('#preferred-code').fill('BROWSER27')
        submit_change(owner, 'code-pin')
        owner.locator('#teacher-email').fill('adult2@example.test')
        submit_change(owner, 'assign-teacher-pin')
        teacher_context, teacher = adult_context(browser, origin, width, 2, errors)
        open_scope(teacher, class_path)
        expect(teacher.get_by_role('heading', name='Browser Wind Ensemble', exact=True)).to_be_visible()
        assert teacher.locator('#code-pin').count() == 0 and teacher.locator('#archive-pin').count() == 0

        open_scope(owner, refs.program_path)
        owner.locator('#role-email').fill('adult4@example.test')
        owner.locator('#role-kind').select_option('head_director')
        submit_change(owner, 'grant-role-pin')
        with refs.factory() as session:
            grant = session.scalar(select(ClassroomRoleGrant.id).where(ClassroomRoleGrant.program_id == 1,
                ClassroomRoleGrant.verifier_id == 4, ClassroomRoleGrant.ended_at.is_(None)))
        successor_context, successor = adult_context(browser, origin, width, 4, errors)
        open_scope(successor, refs.program_path)
        assert successor.locator('#create-class-pin').count() == 0 and successor.locator('#grant-role-pin').count() == 0
        assert successor.locator('#propose-transfer-pin').count() == 0
        snapshot(successor, tmp_path / f'head-director-{engine}-{width}.png')
        submit_change(owner, f'revoke-role-{grant}')
        open_scope(successor, refs.program_path)
        expect(successor.get_by_role('alert')).to_contain_text('refused')
        assert 'WC-TEST' not in successor.content()

        open_scope(owner, class_path)
        submit_change(owner, 'close-pin')
        student_context = browser.new_context(base_url=origin, viewport={'width': width, 'height': 950})
        assert student_context.request.post('/account/login', form={'woodchuck_id': 'WC-TEST0001', 'pin': '2468'}, headers={'Origin': origin}).status == 200
        student = student_context.new_page(); student.set_default_timeout(12000)
        student.on('pageerror', lambda error: errors.append(str(error)))
        entry = '/classroom/enrollment#program=1&code=BROWSER27'
        student.goto(entry)
        student.get_by_role('button', name='Review Class', exact=True).click()
        expect(student.get_by_role('alert')).to_contain_text('Class entry is unavailable')
        submit_change(owner, 'open-pin')
        student.goto(entry)
        student.get_by_role('button', name='Review Class', exact=True).click()
        student.get_by_role('button', name='Join and Share', exact=True).click()
        expect(student.get_by_role('status').filter(has_text='Class joined.')).to_be_visible()
        with refs.factory() as session:
            member_id = session.scalar(select(ClassroomStudentMembership.id).where(
                ClassroomStudentMembership.class_id == class_id, ClassroomStudentMembership.profile_id == 1))
        open_scope(owner, class_path)
        assert 'WC-TEST0001' in owner.content() and 'PRIVATE_PUBLIC_ALIAS' not in owner.content()
        snapshot(owner, tmp_path / f'roster-{engine}-{width}.png')
        clock[0] += timedelta(minutes=1)
        submit_change(owner, f'suspend-member-{member_id}')
        expect(owner.locator(f'[data-membership="{member_id}"] .enrollment-badge')).to_have_text('Suspended')
        open_scope(teacher, class_path)
        assert 'WC-TEST0001' not in teacher.content()
        clock[0] += timedelta(minutes=1)
        submit_change(owner, f'reinstate-member-{member_id}')
        clock[0] += timedelta(minutes=1)
        submit_change(owner, f'remove-member-{member_id}')
        expect(owner.locator(f'[data-membership="{member_id}"] .enrollment-badge')).to_have_text('Removed')
        clock[0] += timedelta(minutes=1)
        submit_change(owner, f'reinstate-member-{member_id}')
        submit_change(owner, 'archive-pin')
        open_scope(teacher, class_path)
        assert 'WC-TEST0001' not in teacher.content()
        submit_change(owner, 'reactivate-pin')
        assert owner.locator('#close-pin').count() == 0 and owner.locator('#open-pin').count() == 1
        owner.locator('#preferred-code').fill('FRESHB26')
        submit_change(owner, 'code-pin')
        submit_change(owner, 'open-pin')
        with refs.factory() as session:
            assignment_id = session.scalar(select(ClassroomTeachingAssignment.id).where(
                ClassroomTeachingAssignment.class_id == class_id, ClassroomTeachingAssignment.verifier_id == 2,
                ClassroomTeachingAssignment.ended_at.is_(None)))
        submit_change(owner, f'end-teaching-{assignment_id}')
        open_scope(teacher, class_path)
        expect(teacher.get_by_role('alert')).to_contain_text('refused')

        open_scope(owner, refs.program_path)
        owner.locator('#transfer-email').fill('adult4@example.test')
        submit_change(owner, 'propose-transfer-pin')
        successor.goto(PATH)
        successor.locator('#catalog-pin').fill('2468')
        successor.get_by_role('button', name='View my Programs & Classes', exact=True).click()
        successor.get_by_role('link', name='Ownership offer', exact=False).click()
        successor.locator('#access-pin').fill('2468')
        successor.get_by_role('button', name='Open management controls', exact=True).click()
        assert successor.locator('#create-class-pin').count() == 0
        submit_change(successor, 'accept-transfer-pin')
        expect(successor.get_by_role('heading', name='Program roles', exact=True)).to_be_visible()
        snapshot(successor, tmp_path / f'new-owner-{engine}-{width}.png')
        open_scope(owner, class_path)
        expect(owner.get_by_role('alert')).to_contain_text('refused')
        with refs.factory() as session:
            assert session.get(ClassroomProgram, 1).owner_verifier_id == 4
            assert count(session, ClassroomStudentMembership) == 3
            assert count(session, ClassroomMembershipPeriod) == 5
        assert protected_counts(refs) == before
        assert not errors
        browser.close()


@pytest.mark.parametrize('engine,width', [('chromium', 1440), ('chromium', 390), ('firefox', 1440), ('firefox', 390)])
def test_approved_manual_onboarding_to_trial_and_class_setup(management_site, monkeypatch, tmp_path, engine, width):
    origin, refs, clock = management_site
    delivered = []
    def capture(self, *, recipient, acceptance_url):
        delivered.append(acceptance_url)
        return DeliveryResult(True, 'captured')
    monkeypatch.setattr(EmailService, 'send_program_invitation', capture)
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        admin_context = browser.new_context(base_url=origin, viewport={'width': width, 'height': 950})
        admin = admin_context.new_page(); admin.set_default_timeout(12000)
        admin.goto('/admin/login')
        admin.locator('input[name=token]').fill('synthetic-classroom-s2-admin')
        admin.locator('form button').click()
        admin.goto('/admin/classroom/invitations')
        admin.locator('#organization-id').fill('3')
        admin.locator('#director-email').fill('adult4@example.test')
        admin.get_by_role('button', name='Send invitation', exact=True).click()
        assert len(delivered) == 1
        context = browser.new_context(base_url=origin, viewport={'width': width, 'height': 950})
        founder = context.new_page(); founder.set_default_timeout(12000)
        founder.goto(delivered[0])
        founder.locator('#director-pin').fill('2468')
        founder.get_by_role('button', name='Accept invitation', exact=True).click()
        founder.wait_for_url('**/classroom/programs/3')
        founder.locator('#access-pin').fill('2468')
        founder.get_by_role('button', name='Open management controls', exact=True).click()
        expect(founder.get_by_role('heading', name='Missing', exact=True)).to_be_visible()
        submit_change(founder, 'trial-pin')
        expect(founder.get_by_role('heading', name='Active · Trial', exact=True)).to_be_visible()
        assert founder.locator('#trial-pin').count() == 0
        founder.locator('#class-name').fill('First Approved Class')
        submit_change(founder, 'create-class-pin')
        expect(founder.get_by_role('heading', name='First Approved Class', exact=True)).to_be_visible()
        snapshot(founder, tmp_path / f'onboarded-{engine}-{width}.png')
        with refs.factory() as session:
            assert session.get(ClassroomProgram, 3).owner_verifier_id == 4
            assert count(session, ClassroomStudentMembership) == 2
        assert context.request.get(delivered[0]).status == 410
        browser.close()
