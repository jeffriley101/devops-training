"""Desktop/mobile Director Dashboard over three separate authorized sources."""
from datetime import timedelta
import importlib
import re
import socket
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
import uvicorn

from app import (classroom_s2 as s2, classroom_reporting as reporting,
    classroom_chart_sharing as sharing, classroom_identity as identity,
    classroom_progress_authorization as progress)
from app.classroom_identity_models import ClassroomRosterIdentity
from app.classroom_models import ClassroomTeachingAssignment
from app.main import app
from app.models import PracticeChart, PracticeChartVerification, WoodchuckProfile, StudentVerifierConnection
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW
from test_classroom_enrollment_ui import enrollment_web
from test_classroom_management_ui import management_web
from test_classroom_identity import identity_web
from test_classroom_management_ui_browser import adult_context, snapshot
from test_classroom_enrollment_ui_browser import launch

PATH = '/classroom/dashboard'
SCHOOL_NAME = 'Director Dashboard Synthetic'
PUBLIC_NICKNAME = 'PUBLIC_WORKSPACE_NICKNAME'
SHARED_NOTE = 'SHARED_WORKSPACE_SENTINEL'
PRIVATE_NOTE = 'PRIVATE_WORKSPACE_SENTINEL'


@pytest.fixture
def workspace_browser_data(identity_web, monkeypatch):
    refs = identity_web
    module = importlib.import_module(next(route.endpoint.__module__ for route in app.routes if route.path == PATH))
    monkeypatch.setattr(module, 'SessionLocal', refs.factory)
    clock = [NOW + timedelta(minutes=1)]
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    monkeypatch.setattr(reporting, '_now', lambda: clock[0])
    # A valid future reporting authorization must still not approve a dataset.
    monkeypatch.setenv('CLASSROOM_S3_ENABLED', '1')
    monkeypatch.setenv('CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED', '1')
    with refs.factory() as session:
        assignment = s2.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=refs.class_id, verifier_id=2)
        refs.workspace_assignment_id = assignment.id
        session.commit()
        row = session.scalar(select(ClassroomRosterIdentity).where(
            ClassroomRosterIdentity.program_id == 1, ClassroomRosterIdentity.profile_id == 1))
        identity.set_school_name(session, actor=actor(session), program_id=1,
            class_id=refs.class_id, woodchuck_id='WC-TEST0001', school_name=SCHOOL_NAME,
            expected_version=row.version if row else 0)
        session.commit()
        shared = PracticeChart(profile_id=1, practice_date=clock[0].date(), minutes=17,
            instrument='Trumpet', note=SHARED_NOTE, created_at=clock[0], source='p-book',
            practice_details=['Scales'])
        unshared = PracticeChart(profile_id=1, practice_date=clock[0].date(), minutes=999,
            instrument='Trumpet', note=PRIVATE_NOTE, created_at=clock[0], source='p-book')
        session.add_all([shared, unshared]); session.flush()
        session.add(PracticeChartVerification(practice_chart_id=shared.id, verifier_id=1,
            status='approved', response_note='PRIVATE_VERIFIER_RESPONSE'))
        session.flush()
        refs.workspace_chart_id = shared.id
        proof = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
        sharing.set_sharing(session, student=proof, chart_id=shared.id,
            assignment_ids=[refs.workspace_assignment_id])
        session.commit()
        proof = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
        choice = progress.choice(session, student=proof, program_id=1, class_id=refs.class_id)
        progress.accept(session, student=proof, program_id=1, class_id=refs.class_id,
            notice_version=progress.NOTICE_VERSION, assignment_ids=choice.assignment_ids,
            previous=choice.revision, source_revision=choice.source_revision, opted_in=True)
        session.commit()
        # Authentication deliberately refreshes profiles, so persist the public
        # sentinel only after all service calls that create the test sources.
        session.get(WoodchuckProfile, 1).display_name = PUBLIC_NICKNAME
        session.add(StudentVerifierConnection(profile_id=2, verifier_id=2,
            role='band_director', status='accepted'))
        session.commit()
    refs.workspace_path = f'{PATH}/1/classes/{refs.class_id}'
    refs.workspace_student_path = refs.workspace_path + f'/students/{refs.member_id}'
    refs.workspace_clock = clock
    return refs


@pytest.fixture
def workspace_site(workspace_browser_data, monkeypatch):
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
            yield origin, workspace_browser_data
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def open_dashboard(page, path, *, expected_status=200):
    page.goto(path, wait_until='domcontentloaded')
    assert SCHOOL_NAME not in page.content() and SHARED_NOTE not in page.content()
    page.locator('#workspace-pin').fill('2468')
    with page.expect_response(lambda response: response.request.method == 'POST'
                              and response.url.split('?', 1)[0].endswith(path)) as result:
        page.get_by_role('button', name='Open dashboard', exact=True).click()
    assert result.value.status == expected_status


def assert_separate_sources(page):
    for selector in ('#administrative-records', '#authorized-statistics', '#shared-pcharts'):
        expect(page.locator(selector)).to_be_visible()
    stats = page.locator('#authorized-statistics').inner_text()
    assert SHARED_NOTE not in stats and PRIVATE_NOTE not in stats
    assert re.search(r'not.+approv|unavailable|not available|await|pending|withheld', stats, re.I), stats
    assert PUBLIC_NICKNAME not in page.content()
    assert PRIVATE_NOTE not in page.content()
    assert 'PRIVATE_VERIFIER_RESPONSE' not in page.content()


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_director_dashboard_sources_detail_withdrawal_and_scope(workspace_site, tmp_path, engine, width):
    origin, refs = workspace_site
    errors = []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        teacher_context, teacher = adult_context(browser, origin, width, 2, errors)
        open_dashboard(teacher, PATH)
        expect(teacher.get_by_role('link', name='Connected Students', exact=True)).to_be_visible()
        assert teacher.get_by_role('link', name='Connected Students', exact=True).get_attribute('href') == '/band-director/dashboard'
        # Home renders only authorized Class navigation, not student/chart rows.
        assert SCHOOL_NAME not in teacher.content() and SHARED_NOTE not in teacher.content()
        teacher.get_by_role('link', name='Concert Band', exact=False).click()
        teacher.locator('#workspace-pin').fill('2468')
        teacher.get_by_role('button', name='Open dashboard', exact=True).click()
        assert_separate_sources(teacher)
        expect(teacher.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        expect(teacher.locator('#administrative-records')).to_contain_text('WC-TEST0001')
        assert 'WC-TEST0002' not in teacher.locator('#administrative-records').inner_text()
        assert SHARED_NOTE not in teacher.content()  # Class shared pane is a link, not a second roster query.
        snapshot(teacher, tmp_path / f'dashboard-class-{engine}-{width}.png')

        student_link = teacher.locator('.workspace-student-link').filter(has_text='WC-TEST0001')
        expect(student_link).to_have_count(1)
        assert student_link.get_attribute('href') == refs.workspace_student_path
        student_link.click()
        teacher.locator('#workspace-pin').fill('2468')
        teacher.get_by_role('button', name='Open dashboard', exact=True).click()
        assert_separate_sources(teacher)
        expect(teacher.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        expect(teacher.locator('#shared-pcharts .workspace-chart')).to_have_count(1)
        expect(teacher.locator('#shared-pcharts')).to_contain_text(SHARED_NOTE)
        assert SHARED_NOTE not in teacher.locator('#administrative-records').inner_text()
        snapshot(teacher, tmp_path / f'dashboard-student-{engine}-{width}.png')
        connected = teacher_context.request.get('/band-director/dashboard')
        assert connected.status == 200 and 'WC-TEST0002' in connected.text()
        assert SCHOOL_NAME not in connected.text() and SHARED_NOTE not in connected.text()

        # Canonical ownership and a valid future-reporting notice cannot turn
        # another teacher's individually selected chart into an Owner's chart.
        owner_context, owner = adult_context(browser, origin, width, 1, errors)
        open_dashboard(owner, refs.workspace_student_path)
        assert_separate_sources(owner)
        expect(owner.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        assert SHARED_NOTE not in owner.content()
        assert owner.locator('.workspace-chart').count() == 0

        other_path = f'{PATH}/1/classes/{refs.other_id}/students/{refs.member_id}'
        open_dashboard(owner, other_path, expected_status=404)
        assert SCHOOL_NAME not in owner.content() and SHARED_NOTE not in owner.content()
        foreign_path = f'{PATH}/2/classes/{refs.foreign_id}/students/{refs.member_id}'
        open_dashboard(owner, foreign_path, expected_status=404)
        assert SCHOOL_NAME not in owner.content()

        # Removing a selected chart changes only that source. Admin identity
        # remains, while a fresh detail request cannot replay the old chart.
        with refs.factory() as session:
            proof = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
            sharing.set_sharing(session, student=proof, chart_id=refs.workspace_chart_id, assignment_ids=[])
            session.commit()
        open_dashboard(teacher, refs.workspace_student_path)
        assert_separate_sources(teacher)
        expect(teacher.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        assert teacher.locator('.workspace-chart').count() == 0 and SHARED_NOTE not in teacher.content()

        # Revocation must be evaluated again instead of treating a saved page,
        # WC#, membership ID or earlier PIN confirmation as a capability.
        with refs.factory() as session:
            s2.end_teaching(session, actor=actor(session), program_id=1,
                assignment_id=refs.workspace_assignment_id)
            session.commit()
        open_dashboard(teacher, refs.workspace_student_path, expected_status=404)
        assert SCHOOL_NAME not in teacher.content() and SHARED_NOTE not in teacher.content()
        assert PUBLIC_NICKNAME not in teacher.content()
        assert not errors
        browser.close()


@pytest.mark.parametrize('engine,width', [('chromium', 1440), ('firefox', 390)])
def test_reporting_withdrawal_does_not_replace_individual_chart_permission(workspace_site, engine, width):
    origin, refs = workspace_site
    errors = []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        context, page = adult_context(browser, origin, width, 2, errors)
        open_dashboard(page, refs.workspace_student_path)
        expect(page.locator('#authorized-statistics')).to_contain_text('Reporting authorization is currently valid')
        expect(page.locator('#shared-pcharts')).to_contain_text(SHARED_NOTE)
        with refs.factory() as session:
            proof = s2.authenticate_student(session, woodchuck_id='WC-TEST0001', pin='2468')
            progress.withdraw(session, student=proof, program_id=1, class_id=refs.class_id)
            session.commit()
        open_dashboard(page, refs.workspace_student_path)
        assert_separate_sources(page)
        assert 'Reporting authorization is currently valid' not in page.locator('#authorized-statistics').inner_text()
        expect(page.locator('#administrative-records')).to_contain_text(SCHOOL_NAME)
        expect(page.locator('#shared-pcharts .workspace-chart')).to_have_count(1)
        expect(page.locator('#shared-pcharts')).to_contain_text(SHARED_NOTE)
        assert not errors
        browser.close()
