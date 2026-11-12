"""BOOK acceptance on a disposable local PostgreSQL schema and the real app.

WW_BOOK_TEST_POSTGRES_URL must name a dedicated Unix-socket test database.
No migration runner, Production connection, external email or payment is used.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
from threading import Thread
import time
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
import uvicorn

from app import memberships
from app.age_privacy import declare_age
from app.email_service import DeliveryResult, EmailService
from app.main import app
from app.models import (PracticeChart, PracticeChartVerification, RewardGrant,
                        StudentVerifierConnection, TrustedVerifier, WoodchuckState,
                        WoodchuckProfile)
from app.security import hash_pin
from test_age_screening import age_db, login


def contrast(colors):
    def luminance(rgb):
        channels = [int(value) / 255 for value in rgb.removeprefix('rgb(').removesuffix(')').split(',')]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    values = sorted(luminance(value) for value in colors)
    return (values[1] + .05) / (values[0] + .05)


def chart_rows(factory):
    with factory() as session:
        return {r.id: dict(r._mapping) for r in session.execute(
            select(PracticeChart.__table__).order_by(PracticeChart.id))}


def schema_shape(factory):
    with factory() as session:
        return [list(row) for row in session.execute(text("""
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns WHERE table_schema = current_schema()
            ORDER BY table_name, ordinal_position
        """))], [list(row) for row in session.execute(text("""
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = current_schema() ORDER BY tablename, indexname
        """))]


@pytest.fixture
def book_db(monkeypatch, request):
    configured = os.getenv('WW_BOOK_TEST_POSTGRES_URL')
    if not configured:
        pytest.skip('Requires an explicit disposable local WW_BOOK_TEST_POSTGRES_URL')
    url = make_url(configured)
    assert url.get_backend_name() == 'postgresql'
    assert not url.host and str(url.query.get('host', '')).startswith('/tmp/book-')
    assert url.database.startswith('book_') and url.database.endswith('_test')
    monkeypatch.setenv('WW_AGE_TEST_POSTGRES_URL', configured)
    factory = request.getfixturevalue('age_db')
    today = datetime.now(ZoneInfo('America/Chicago')).date()
    with factory() as session:
        for profile_id in (1, 2):
            declare_age(session, profile_id, 'adult')
            state = session.get(WoodchuckState, profile_id)
            state.state_json = {**state.state_json, 'practiceLog': []}
        memberships.create_complimentary_membership(
            session, memberships.Actor('student', 1), memberships.Actor('admin'))
        restricted = WoodchuckProfile(woodchuck_id='WC-AGE-C', display_name='Restricted synthetic account',
            pin_hash=hash_pin('2468'), instrument='Flute', level='Beginner', goal='Practice')
        session.add(restricted); session.flush()
        declare_age(session, restricted.id, 'under13')
        verifier = TrustedVerifier(email='reviewer@example.test', display_name='Practice Reviewer',
                                   pin_hash=hash_pin('9876'))
        session.add(verifier); session.flush()
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=verifier.id,
                    role='verifier', status='accepted'))
        for index in range(12):
            session.add(PracticeChart(profile_id=1, practice_date=today - timedelta(days=index + 1),
                minutes=index + 1, instrument='Flute', source='p-book',
                note=f'Saved practice {index + 1}', practice_details=['Scales'],
                include_contests=False, include_team_contests=False))
        session.add(PracticeChart(profile_id=1, practice_date=today - timedelta(days=2),
            minutes=0, instrument='Flute', source='pristine', detected_playing_seconds=59,
            note='Saved microphone practice', include_contests=False, include_team_contests=False))
        session.add(PracticeChart(profile_id=2, practice_date=today, minutes=23,
            instrument='Flute', note='OTHER PRIVATE ACCOUNT', include_contests=False,
            include_team_contests=False))
        session.commit()
    emails = []
    def deliver(_self, **message):
        emails.append(message)
        return DeliveryResult(True, 'sent')
    monkeypatch.setattr(EmailService, 'send_practice_chart', deliver)
    before_schema = schema_shape(factory)
    yield factory, emails
    assert schema_shape(factory) == before_schema


@pytest.fixture
def book_site(book_db, monkeypatch):
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
            yield origin, *book_db
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def ready(page):
    page.wait_for_function('window.WWSessionBoundary && !document.querySelector(".app-shell").hidden')
    expect(page.locator('#p-book-entries > p')).to_have_count(10)
    expect(page.locator('#p-book-verifier')).to_be_enabled()


def activate(locator, touch=False):
    if touch:
        locator.tap()
    else:
        locator.focus()
        locator.press('Enter')


@pytest.mark.parametrize('browser_name', ['chromium', 'firefox'])
def test_book_layout_navigation_timer_submission_and_history(book_site, tmp_path, browser_name):
    origin, factory, emails = book_site
    original_charts = chart_rows(factory)
    evidence = {'browser': browser_name, 'layouts': [], 'page_errors': []}
    with sync_playwright() as pw:
        browser = (pw.chromium.launch(executable_path='/usr/bin/google-chrome')
                   if browser_name == 'chromium' else pw.firefox.launch())
        context = browser.new_context(base_url=origin, has_touch=True, reduced_motion='reduce')
        if browser_name == 'chromium':
            context.grant_permissions(['clipboard-read', 'clipboard-write'])
        # Capture export calls; Chromium also exercises its native clipboard.
        context.add_init_script('''window.bookCopies = [];
            const nativeCopy = navigator.clipboard.writeText.bind(navigator.clipboard);
            navigator.clipboard.writeText = async value => {
                window.bookCopies.push(value);
                NATIVE_COPY
            };'''.replace('NATIVE_COPY', 'return nativeCopy(value);' if browser_name == 'chromium' else ''))
        response = context.request.post('/account/login', form={'woodchuck_id': 'WC-AGE-A', 'pin': '2468'},
                                        headers={'Origin': origin})
        assert response.status == 200
        totals = context.request.get('/practice-charts/totals').json()
        rating = context.request.get('/personal-development/rating').json()
        history = context.request.get('/practice-charts').json()['charts']
        insights = context.request.get('/practice-charts/insights').json()
        assert totals['career_seconds'] == sum(range(1, 13)) * 60 + 59
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on('pageerror', lambda error: evidence['page_errors'].append(str(error)))
        for width, height in [(1440, 900), (390, 844), (320, 740), (430, 932), (768, 1024), (844, 390)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('/p-book'); ready(page)
            expect(page.locator('.pdd-book-rating strong')).to_have_text(f"{rating['rating']:.1f}")
            expect(page.locator('#p-book-career-practice')).to_have_text(totals['career_display'])
            assert page.locator('.p-book-spiral').count() == 0
            shortcut = page.get_by_role('link', name='Pristine P-Chart', exact=True)
            expect(shortcut).to_have_count(1)
            expect(page.locator('#p-book-note')).to_have_attribute('placeholder', 'Notes.')
            expect(page.locator('.p-book-work-field summary')).to_have_text('What did you work on?')
            expect(shortcut).to_have_attribute('href', '/practice/pristine')
            expect(page.locator('#stopwatch-help')).to_have_text('Start and stop your practice timer.')
            expect(page.locator('#pristine-help')).to_have_text('Use your microphone to track practice.')
            expect(shortcut).to_have_attribute('aria-describedby', 'pristine-help')
            expect(page.locator('.practice-insights-week')).to_have_count(4)
            for index, week in enumerate(reversed(insights['weeks'])):
                row = page.locator('.practice-insights-week').nth(index)
                expect(row.locator('h3')).to_have_text(['Last week', '2 weeks ago', '3 weeks ago', '4 weeks ago'][index])
                expect(row.locator('dt')).to_have_text(['Practice', 'Practice days', 'Verified', 'Pristine'])
                expect(row.locator('dd').nth(1)).to_have_text(str(week['days']))
            expect(page.locator('#p-book-current-team')).to_contain_text('No team selected')
            expect(page.locator('label[for="p-book-include-team"] strong')).to_have_text('Include in Team Standings')
            expect(page.locator('label[for="p-book-include-contests"] strong')).to_have_text('Include in Leaderboards')
            assert page.locator('link[href*="practice-book.css"]').count() == 1
            assert page.locator('script[src="/static/js/app.js?v=wl2-17c-classroom1-book-interaction1"]').count() == 1
            dimensions = page.evaluate('''() => {
                const rect = s => document.querySelector(s).getBoundingClientRect().toJSON();
                const timer = rect('.practice-timer-metal'), minutes = rect('#p-book-minutes');
                return {width: innerWidth, scroll: document.documentElement.scrollWidth,
                    timer, minutes, shortcut: rect('.p-book-pristine'),
                    submit: rect('#submit-p-chart-btn'),
                    headphones: rect('.sound-effects-controls'), book: rect('.p-book-page'),
                    inputFont: getComputedStyle(document.querySelector('#p-book-minutes')).fontSize,
                    timerBackground: getComputedStyle(document.querySelector('#practice-timer-toggle-btn')).backgroundImage,
                    background: getComputedStyle(document.querySelector('.p-book-page')).backgroundImage};
            }''')
            assert dimensions['scroll'] <= width
            assert dimensions['timer']['bottom'] <= dimensions['minutes']['top']
            assert dimensions['shortcut']['height'] >= 44
            assert dimensions['submit']['height'] >= 44
            assert dimensions['background'] == 'none'
            assert dimensions['timerBackground'] == 'none'
            assert float(dimensions['inputFont'].removesuffix('px')) >= 16
            assert dimensions['headphones']['top'] >= dimensions['book']['bottom']
            evidence['layouts'].append(dimensions)
            # The yellow palette must stay readable in both checkbox-driven states.
            button = page.locator('#submit-p-chart-btn')
            button_colors = []
            for included in (True, False):
                page.locator('#p-book-include-team').set_checked(included)
                colors = button.evaluate('(e) => [getComputedStyle(e).color, getComputedStyle(e).backgroundColor]')
                assert contrast(colors) >= 4.5
                button_colors.append({'included': included, 'colors': colors, 'contrast': contrast(colors)})
            page.locator('#p-book-include-team').check()
            button.hover()
            colors = button.evaluate('(e) => [getComputedStyle(e).color, getComputedStyle(e).backgroundColor]')
            assert contrast(colors) >= 4.5
            evidence['layouts'][-1]['submit_states'] = button_colors
            page.mouse.move(0, 0)
            page.screenshot(path=str(tmp_path / f'book-{width}x{height}.png'), full_page=True)
            if width not in (1440, 390):
                continue
            touch = width == 390
            page.locator('#practice-insights').screenshot(path=str(tmp_path / f'insights-{width}.png'))
            button.scroll_into_view_if_needed()
            page.screenshot(path=str(tmp_path / f'actions-{width}.png'))
            button.screenshot(path=str(tmp_path / f'submit-{width}.png'))
            page.locator('.practice-timer-metal').scroll_into_view_if_needed()
            page.screenshot(path=str(tmp_path / f'book-viewport-{width}.png'))
            activate(page.locator('#sound-effects-button'), touch)
            expect(page.locator('#sound-effects-panel')).to_be_visible()
            assert page.locator('#sound-effects-panel').evaluate(
                '(e) => e.getBoundingClientRect().top >= document.querySelector(".p-book-page").getBoundingClientRect().bottom')
            page.screenshot(path=str(tmp_path / f'audio-{width}.png'))
            activate(page.locator('#sound-effects-button'), touch)
            expect(page.locator('#sound-effects-panel')).to_be_hidden()
            activate(shortcut, touch)
            page.wait_for_url(origin + '/practice/pristine')
            expect(page.get_by_role('heading', name='Pristine Practice', exact=True)).to_be_visible()
            expect(page.locator('[data-pristine-microphone]')).to_have_text('Microphone not started')
            assert page.locator('link[href*="practice-book.css"]').count() == 0
            assert page.locator('script[src="/static/js/app.js?v=wl2-17c-classroom1"]').count() == 1
            page.go_back(); ready(page)
            activate(page.locator('.pdd-book-rating'), touch)
            page.wait_for_url(origin + '/personal-development')
            expect(page.locator('.bd-rating')).to_have_text(f"{rating['rating']:.1f}")
            page.go_back(); ready(page)
            activate(page.locator('#p-book-show-more'), touch)
            expect(page.locator('#p-book-entries > p')).to_have_count(13)
            for rendered, chart in zip(page.locator('#p-book-entries > p').all_text_contents(), history):
                assert rendered.startswith(chart['practice_date'] + ' — ')
                assert f" — {chart['note']}" in rendered
            activate(page.locator('#p-book-show-less'), touch)
            expect(page.locator('#p-book-entries > p')).to_have_count(10)
            activate(page.locator('.p-book-work-field > summary'), touch)
            expect(page.locator('input[name="practice-detail"]')).to_have_count(23)
            expect(page.get_by_label('Long Tones', exact=True)).to_be_visible()
            page.screenshot(path=str(tmp_path / f'book-expanded-{width}.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        assert chart_rows(factory) == original_charts  # Reading/navigation creates no practice.
        width = 390 if browser_name == 'chromium' else 1440
        touch = width == 390
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto('/p-book'); ready(page)
        with factory() as session:
            credits = session.get(WoodchuckState, 1).state_json['progress']['credits']
            rewards = list(session.execute(select(RewardGrant.__table__)))
        page.clock.install()
        activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
        page.clock.fast_forward(91000)
        expect(page.locator('#practice-timer-display')).to_have_text('01:31')
        page.once('dialog', lambda dialog: dialog.accept())
        activate(page.get_by_role('link', name='Pristine P-Chart', exact=True), touch)
        page.wait_for_url(origin + '/practice/pristine')
        page.go_back(); ready(page)
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Resume Stopwatch')
        activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
        page.once('dialog', lambda dialog: dialog.accept())
        activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#p-book-minutes')).to_have_value('2')
        page.locator('#p-book-minutes').fill('7')
        page.locator('#p-book-note').fill('Working on breath and steady tone')
        activate(page.locator('.p-book-work-field > summary'), touch)
        page.get_by_label('Long Tones', exact=True).check()
        page.get_by_label('Scales', exact=True).check()
        page.locator('#p-book-verifier').select_option('1')
        page.screenshot(path=str(tmp_path / 'timer-and-entry.png'), full_page=True)
        activate(page.locator('#submit-p-chart-btn'), touch)
        expect(page.locator('#p-book-missing-selection')).to_be_visible()
        activate(page.locator('#p-book-without-missing'), touch)
        expect(page.locator('#p-book-final-confirmation')).to_be_visible()
        page.screenshot(path=str(tmp_path / 'confirmation.png'))
        with page.expect_response(lambda r: r.url == origin + '/practice-charts' and r.request.method == 'POST') as saved:
            activate(page.locator('#p-book-confirm-submit'), touch)
        payload = saved.value.json()
        assert saved.value.status == 201 and payload['created'] is True
        assert payload['chart']['minutes'] == 7 and payload['chart']['credits_awarded'] == 0
        assert payload['chart']['verification']['status'] == 'pending'
        assert set(payload['chart']['practice_details']) == {'Long Tones', 'Scales'}
        expect(page.locator('.ww-character-reaction-speaker')).to_have_text('Coach Whistler')
        page.screenshot(path=str(tmp_path / 'whistler.png'))
        activate(page.locator('.ww-character-reaction-dismiss'), touch)
        expect(page.locator('#p-book-feedback')).to_contain_text('copied to your clipboard')
        expect(page.locator('#p-book-feedback')).to_have_class('body-copy success-callout')
        copied = page.evaluate('window.bookCopies')
        assert len(copied) == 1 and 'Long Tones' in copied[0] and '7' in copied[0]
        assert 'OTHER PRIVATE ACCOUNT' not in copied[0]
        if browser_name == 'chromium':
            assert page.evaluate('navigator.clipboard.readText()') == copied[0]
        assert len(emails) == 1 and emails[0]['recipient'] == 'reviewer@example.test'
        assert emails[0]['minutes'] == 7
        page.screenshot(path=str(tmp_path / 'success.png'), full_page=True)
        duplicate = context.request.post('/practice-charts', data=saved.value.request.post_data,
            headers={'Origin': origin, 'Content-Type': 'application/json'})
        assert duplicate.status == 201 and duplicate.json()['created'] is False
        assert len(emails) == 1
        expected_total = totals['career_seconds'] + 420
        assert context.request.get('/practice-charts/totals').json()['career_seconds'] == expected_total
        page.reload(); ready(page)
        expect(page.locator('#p-book-entries')).to_contain_text('Working on breath and steady tone')
        with factory() as session:
            assert session.get(WoodchuckState, 1).state_json['progress']['credits'] == credits
            assert list(session.execute(select(RewardGrant.__table__))) == rewards
        after = chart_rows(factory)
        assert {key: after[key] for key in original_charts} == original_charts
        assert len(after) == len(original_charts) + 1
        assert context.request.get('/personal-development/rating').json() == rating
        activate(page.get_by_role('link', name='Back to SHED'), touch)
        page.wait_for_url(origin + '/home')
        evidence.update({'prior_records_unchanged': True, 'new_charts': 1,
                         'history_order': 'practice_date descending, server ID descending on equal dates',
                         'email_calls': len(emails), 'copy_calls': len(copied),
                         'career_seconds_before': totals['career_seconds'],
                         'career_seconds_after': expected_total,
                         'rating_unchanged': True, 'credits_and_rewards_unchanged': True})
        (tmp_path / 'results.json').write_text(json.dumps(evidence, indent=2))
        assert evidence['page_errors'] == []
        context.close(); browser.close()


def test_book_pristine_and_account_boundaries_on_postgres(book_db):
    factory, emails = book_db
    client = TestClient(app)
    before = chart_rows(factory)
    assert client.get('/practice-charts').status_code == 401
    assert client.post('/practice-charts/pristine', json={
        'detected_playing_seconds': 59, 'submission_key': 'anonymous'}).status_code == 401
    login(client, 'B')
    assert client.get('/p-book').status_code == 200
    assert client.get('/practice/pristine').status_code == 200
    assert client.get('/personal-development/rating').status_code == 403
    own = client.get('/practice-charts?profile_id=1')
    assert 'Saved practice' not in own.text and 'OTHER PRIVATE ACCOUNT' in own.text
    assert chart_rows(factory) == before
    payload = {'detected_playing_seconds': 59, 'submission_key': 'private-59',
               'include_contests': False, 'include_team_contests': False}
    assert client.post('/practice-charts/pristine', json=payload,
                       headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert chart_rows(factory) == before
    saved = client.post('/practice-charts/pristine', json=payload)
    assert saved.status_code == 201 and saved.json()['created'] is True
    assert saved.json()['chart']['detected_playing_seconds'] == 59
    assert saved.json()['chart']['minutes'] == 0
    assert saved.json()['chart']['credits_awarded'] == 0
    assert client.post('/practice-charts/pristine', json=payload).json()['created'] is False
    after = chart_rows(factory)
    assert {key: after[key] for key in before} == before
    assert len(after) == len(before) + 1 and emails == []
    client = TestClient(app)
    login(client, 'C')
    protected = chart_rows(factory)
    assert client.get('/practice-charts').status_code == 403
    assert client.post('/practice-charts/pristine', json={**payload, 'submission_key': 'blocked'}).status_code == 403
    assert client.get('/p-book', headers={'Accept': 'text/html'}, follow_redirects=False).status_code == 303
    assert chart_rows(factory) == protected
