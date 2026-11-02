"""Real desktop/mobile enrollment paths on a loopback-only disposable site."""
from datetime import timedelta
import socket
from threading import Thread
import time
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select
import uvicorn

from app import classroom_s2 as s2, child_authorization
from app.age_models import AccountPrivacy
from app.classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation
from app.classroom_models import ClassroomStudentMembership, ClassroomMembershipPeriod
from app.main import app
from test_classroom_s1 import adult_pin_hash, classroom_db, count
from test_classroom_s2 import s2_db, NOW
from test_classroom_enrollment_ui import enrollment_web, PATH, invite, protected_counts


@pytest.fixture
def enrollment_site(enrollment_web, monkeypatch):
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
            yield origin, enrollment_web
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def launch(playwright, engine):
    return (playwright.chromium.launch(executable_path='/opt/google/chrome/chrome')
        if engine == 'chromium' else playwright.firefox.launch())


def context(browser, origin, width):
    return browser.new_context(base_url=origin, viewport={'width': width, 'height': 900},
        reduced_motion='reduce')


def student_login(page, ident=1):
    responses = []
    def status(response):
        responses.append((urlsplit(response.url).path, response.status))
    page.on('response', status)
    page.get_by_label('Woodchuck ID', exact=True).fill(f'WC-TEST000{ident}')
    page.get_by_label('Four-digit PIN').fill('2468')
    page.get_by_role('button', name='Sign in and continue', exact=True).click()
    try:
        page.wait_for_function('document.getElementById("enrollment-code-form") || document.getElementById("login-error")?.textContent.trim()', timeout=15000)
    except Exception as error:
        raise AssertionError({'path': urlsplit(page.url).path, 'headings': page.locator('h1').all_text_contents(),
            'responses': responses[-6:], 'login_form': page.locator('#account-login-form').count()}) from error
    finally:
        page.remove_listener('response', status)
    assert page.locator('#enrollment-code-form').count(), page.locator('#login-error').inner_text()


def director_open(page, refs):
    page.goto(refs.path)
    page.locator('#view-pin').fill('2468')
    page.get_by_role('button', name='View enrollment invitations', exact=True).click()
    page.locator('#invite-wc').wait_for(state='visible')


def issue(page, ident=1):
    page.locator('#invite-wc').fill(f'WC-TEST000{ident}')
    page.locator('#issue-pin').fill('2468')
    page.get_by_role('button', name='Send enrollment invitation', exact=True).click()
    page.get_by_role('status').filter(has_text='Enrollment invitation sent').wait_for(state='visible')


def snapshot(page, target):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Every visible action has a readable accessible label and touch-sized height.
    for button in page.locator('button:visible').all():
        assert button.inner_text().strip() and button.bounding_box()['height'] >= 44
    page.screenshot(path=str(target), full_page=True)


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
@pytest.mark.parametrize('width', [1440, 390, 320])
def test_complete_student_director_manual_qr_inbox_flows(enrollment_site, monkeypatch, tmp_path, engine, width):
    origin, refs = enrollment_site
    errors, requests = [], []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        director_context = context(browser, origin, width)
        assert director_context.request.post('/trusted-verifiers/login', form={'email': 'adult1@example.test', 'pin': '2468'}, headers={'Origin': origin}).status == 200
        director = director_context.new_page()
        director.set_default_timeout(10000)
        director.on('pageerror', lambda error: errors.append(str(error)))
        director.goto(PATH + '/director')
        director.locator('#classes-pin').fill('2468')
        director.get_by_role('button', name='View assigned classes', exact=True).click()
        assert 'Private Other' not in director.locator('main').inner_text()
        director.get_by_role('link', name='Program 1 Concert Band', exact=False).click()
        director.locator('#view-pin').fill('2468')
        director.get_by_role('button', name='View enrollment invitations', exact=True).click()
        issue(director, 1)
        issue(director, 2)
        assert 'Private Student' not in director.content() and '@example.test' not in director.content()
        snapshot(director, tmp_path / f'director-{engine}-{width}.png')
        director.locator('#qr-code').fill('BAND2026')
        director.locator('#qr-pin').fill('2468')
        director.get_by_role('button', name='Make QR', exact=True).click()
        qr_link = director.locator('[data-enrollment-join-link]').get_attribute('href')
        assert '#program=1&code=BAND2026' in qr_link
        assert director.locator('.enrollment-qr img').evaluate('image => image.complete && image.naturalWidth > 0')
        snapshot(director, tmp_path / f'qr-{engine}-{width}.png')

        student_context = context(browser, origin, width)
        student = student_context.new_page()
        student.set_default_timeout(10000)
        student.on('pageerror', lambda error: errors.append(str(error)))
        student.on('request', lambda request: requests.append(request.url))
        student.goto(PATH)
        student_login(student)
        before = protected_counts(refs)
        student.get_by_label('Music Program number').fill('1')
        student.get_by_label('Class code', exact=True).fill('BAND2026')
        student.get_by_role('button', name='Review Class', exact=True).click()
        expect(student.get_by_role('heading', name='Is this your Class?', exact=True)).to_be_visible()
        expect(student.get_by_role('heading', name='Concert Band', exact=True)).to_be_visible()
        snapshot(student, tmp_path / f'confirmation-{engine}-{width}.png')
        with refs.factory() as session:
            assert count(session, ClassroomStudentMembership) == 0
        student.get_by_role('button', name='Join and Share', exact=True).click()
        expect(student.get_by_role('status').filter(has_text='Class joined.')).to_be_visible()
        snapshot(student, tmp_path / f'membership-{engine}-{width}.png')
        student.goto(PATH + '/invitations')
        student.get_by_role('button', name='Decline', exact=True).click()
        expect(student.locator('[data-state=declined]')).to_be_visible()
        assert 'Membership: Member' in student.locator('main').inner_text()
        assert protected_counts(refs) == before

        # Scan handoff: the fragment survives normal sign-in, fills the same form,
        # is removed from the URL, and cannot enroll through navigation alone.
        second_context = context(browser, origin, width)
        second = second_context.new_page()
        second.set_default_timeout(10000)
        second.on('pageerror', lambda error: errors.append(str(error)))
        second.on('request', lambda request: requests.append(request.url))
        second.goto(qr_link)
        student_login(second, 2)
        expect(second.get_by_label('Music Program number')).to_have_value('1')
        expect(second.get_by_label('Class code', exact=True)).to_have_value('BAND2026')
        assert '#' not in second.url
        with refs.factory() as session:
            assert count(session, ClassroomStudentMembership) == 1
        second.get_by_role('button', name='Review Class', exact=True).click()
        expect(second.get_by_role('heading', name='Is this your Class?', exact=True)).to_be_visible()
        second.goto(PATH + '/invitations')
        second.get_by_role('button', name='Not Now', exact=True).click()
        expect(second.locator('[data-state=deferred]')).to_be_visible()
        snapshot(second, tmp_path / f'inbox-{engine}-{width}.png')
        second.get_by_role('button', name='Accept', exact=True).click()
        expect(second.locator('[data-state=accepted]')).to_be_visible()
        second.goto(qr_link)
        second.get_by_role('button', name='Review Class', exact=True).click()
        second.get_by_role('button', name='Join and Share', exact=True).click()
        expect(second.get_by_role('status').filter(has_text='Class joined.')).to_be_visible()
        with refs.factory() as session:
            assert count(session, ClassroomStudentMembership) == count(session, ClassroomMembershipPeriod) == 2
        assert protected_counts(refs) == before
        # A QR arriving while this same page is already open also fills the
        # form. Unsupported payloads are cleared and cannot become grant fields.
        second.evaluate('location.hash = "program=1&code=BAND2026"')
        expect(second.get_by_label('Class code', exact=True)).to_have_value('BAND2026')
        assert '#' not in second.url
        second.evaluate('location.hash = "program=1&code=BAND2026&assignment_ids=1"')
        expect(second.locator('#enrollment-qr-status')).to_contain_text('not a supported Class entry')

        director_open(director, refs)
        issue(director, 1)
        with refs.factory() as session:
            newest = session.scalar(select(Invitation.id).where(Invitation.profile_id == 1, Invitation.state == 'pending'))
        director.locator('#revoke-pin').fill('2468')
        director.get_by_role('button', name=f'Revoke invitation {newest}', exact=True).click()
        student.goto(PATH + '/invitations')
        expect(student.locator('[data-state=revoked]')).to_be_visible()

        director_open(director, refs)
        issue(director, 1)
        monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(days=7))
        student.goto(PATH + '/invitations')
        expect(student.locator('[data-state=expired]')).to_be_visible()
        assert student.get_by_role('button', name='Accept', exact=True).count() == 0
        snapshot(student, tmp_path / f'expired-{engine}-{width}.png')

        director.goto(f'{PATH}/director/1/classes/{refs.other_id}')
        director.locator('#view-pin').fill('2468')
        director.get_by_role('button', name='View enrollment invitations', exact=True).click()
        expect(director.get_by_role('alert')).to_contain_text('Enrollment access is unavailable')
        assert 'Private Other' not in director.content() and 'WC-TEST' not in director.content()
        assert all('code=BAND2026' not in url.split('#')[0] for url in requests)
        assert not errors
        browser.close()


@pytest.mark.parametrize('engine', ['chromium', 'firefox'])
def test_ordinary_student_signin_still_returns_home(enrollment_site, engine):
    origin, refs = enrollment_site
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        student_context = context(browser, origin, 1440)
        page = student_context.new_page()
        page.set_default_timeout(10000)
        page.goto('/login', wait_until='domcontentloaded')
        page.locator('#login-woodchuck-id').fill('WC-TEST0001')
        page.locator('#login-pin').fill('2468')
        page.locator('#account-login-form button[type=submit]').click()
        page.wait_for_url('**/home', wait_until='domcontentloaded')
        assert page.locator('#enrollment-code-form').count() == 0
        browser.close()


def test_enrollment_without_javascript_never_submits_credentials_in_url(enrollment_site):
    origin, refs = enrollment_site
    with sync_playwright() as playwright:
        browser = launch(playwright, 'chromium')
        student_context = browser.new_context(base_url=origin, java_script_enabled=False,
            viewport={'width': 390, 'height': 844})
        page = student_context.new_page()
        requests = []
        page.on('request', lambda request: requests.append((request.method, request.url)))
        page.goto(PATH)
        assert page.locator('#account-login-form').get_attribute('method') == 'post'
        assert page.locator('#account-login-form').get_attribute('action') == '/account/login'
        page.get_by_label('Woodchuck ID', exact=True).fill('WC-TEST0001')
        page.get_by_label('Four-digit PIN').fill('2468')
        page.get_by_role('button', name='Sign in and continue', exact=True).click()
        page.wait_for_url('**/account/login')
        assert ('POST', origin + '/account/login') in requests
        assert not any(method == 'GET' and urlsplit(url).path == '/account/login' for method, url in requests)
        assert not any('pin=' in url or 'woodchuck_id=' in url for _, url in requests)
        page.goto(PATH)
        page.get_by_label('Music Program number').fill('1')
        page.get_by_label('Class code', exact=True).fill('BAND2026')
        page.get_by_role('button', name='Review Class', exact=True).click()
        expect(page.get_by_role('heading', name='Is this your Class?', exact=True)).to_be_visible()
        page.get_by_role('button', name='Join and Share', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Class joined.')).to_be_visible()
        with refs.factory() as session:
            assert count(session, ClassroomStudentMembership) == 1
        browser.close()


@pytest.mark.parametrize('engine,width', [('chromium', 1440), ('chromium', 390), ('firefox', 1440), ('firefox', 390)])
@pytest.mark.parametrize('age_band', ['13to17', 'under13'])
def test_account_authorization_handoff_and_parent_fail_closed(enrollment_site, monkeypatch, tmp_path, engine, width, age_band):
    origin, refs = enrollment_site
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda *args: ('synthetic-notice', 'Synthetic', 'a' * 64))
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band, rule.public_from = 'unknown', None
        session.commit()
    errors = []
    with sync_playwright() as playwright:
        browser = launch(playwright, engine)
        student_context = context(browser, origin, width)
        page = student_context.new_page()
        page.set_default_timeout(10000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(PATH + '#program=1&code=BAND2026')
        student_login(page)
        page.get_by_role('button', name='Review Class', exact=True).click()
        expect(page.get_by_role('heading', name='Account authorization needed')).to_be_visible()
        snapshot(page, tmp_path / f'authorization-{age_band}-{engine}-{width}.png')
        page.get_by_role('link', name='Complete account authorization', exact=True).click()
        page.get_by_label('Age band', exact=True).select_option(age_band)
        page.get_by_role('button', name='Save age declaration', exact=True).click()
        if age_band == '13to17':
            expect(page.get_by_role('heading', name='Is this your Class?', exact=True)).to_be_visible()
            with refs.factory() as session:
                assert count(session, ClassroomStudentMembership) == 0
            page.get_by_role('button', name='Join and Share', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='Class joined.')).to_be_visible()
        else:
            expect(page.get_by_role('heading', name='Parent permission', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name='Start a parent permission request', exact=True)).to_be_visible()
            page.get_by_role('link', name='Return to Classroom enrollment', exact=True).click()
            expect(page.get_by_role('heading', name='Account authorization needed')).to_be_visible()
            assert page.get_by_role('button', name='Join and Share', exact=True).count() == 0
            with refs.factory() as session:
                assert count(session, ClassroomStudentMembership) == 0
        assert not errors
        browser.close()
