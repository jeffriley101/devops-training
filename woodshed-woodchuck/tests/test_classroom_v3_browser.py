"""Firefox checks against disposable authorized Classroom data, not the prototype."""
from datetime import date, timedelta
import json
import socket
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import select
import uvicorn

from app import classroom_s2 as s2, classroom_chart_sharing as sharing
from app.main import app
from app.models import PracticeChart, WoodchuckProfile
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, student
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web
from test_classroom_v3 import v3_web, url


@pytest.fixture
def v3_site(v3_web, monkeypatch):
    with v3_web() as session:
        for i in range(26):
            chart = PracticeChart(profile_id=1, practice_date=v3_web.monday, minutes=0,
                instrument='Trumpet', note=f'Extra synthetic chart {i}', created_at=v3_web.clock[0])
            session.add(chart); session.flush()
            sharing.set_sharing(session, student=student(session), chart_id=chart.id, assignment_ids=[v3_web.assignment_id])
        other = s2.authenticate_student(session, woodchuck_id='WC-SECOND', pin='2468')
        s2.join_class(session, student=other, program_id=1, code='FIRST2026')
        session.get(WoodchuckProfile, 2).display_name = 'Alex Reed'
        chart = PracticeChart(profile_id=2, practice_date=v3_web.monday, minutes=7, instrument='Flute',
            note='<script>window.PRIVATE_XSS=true</script>', created_at=v3_web.clock[0])
        session.add(chart); session.flush()
        sharing.set_sharing(session, student=other, chart_id=chart.id, assignment_ids=[v3_web.assignment_id])
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
            yield origin, v3_web
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844), (320, 740)])
def test_firefox_v3_navigation_spreadsheet_graphs_details(v3_site, tmp_path, width, height):
    origin, factory = v3_site
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch()
        context = browser.new_context(base_url=origin, viewport={'width': width, 'height': height}, reduced_motion='reduce')
        response = context.request.post('/trusted-verifiers/login', form={'email': 'adult2@example.test', 'pin': '2468'}, headers={'Origin': origin})
        assert response.status == 200
        page = context.new_page(); page.set_default_timeout(15000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('/classroom/director')
        page.get_by_label('Verifier PIN').fill('2468')
        page.get_by_role('button', name='View classes', exact=True).click()
        page.get_by_role('link', name='Concert Band', exact=False).wait_for(state='visible')
        assert page.get_by_role('link', name='Concert Band', exact=False).count() == 1
        assert 'Jazz Ensemble' not in page.locator('.classroom-v3').inner_text()
        page.get_by_role('link', name='Concert Band', exact=False).click()
        page.get_by_label('Verifier PIN').fill('2468')
        page.get_by_role('button', name='Open class', exact=True).click()
        page.locator('#v3-data-view').wait_for(state='visible')
        assert page.locator('#v3-sheet-body tr').count() == 2
        assert page.locator('#v3-week-total').inner_text() == '18.25 min'
        assert page.locator('#v3-history-total').inner_text() == '38.25 min'
        assert page.locator('#v3-week-graph rect').count() == 7
        assert page.locator('#v3-week-graph rect title').first.text_content().endswith('18.25 minutes')
        assert page.locator('#v3-history-graph circle title').last.text_content().endswith('38.25 cumulative minutes')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert 'Full-roster submission percentage: unavailable.' in page.locator('.classroom-v3').inner_text()
        assert 'UNSHARED CHART' not in page.content()
        page.screenshot(path=str(tmp_path / f'classroom-v3-{width}.png'), full_page=True)
        # Sort uses numeric values, with keyboard operation and aria-sort.
        sort = page.locator('#v3-sort-total')
        sort.focus(); page.keyboard.press('Enter')
        assert page.locator('#v3-sheet-body tr').first.inner_text().startswith('Alex Reed')
        page.keyboard.press('Enter')
        assert page.locator('#v3-sheet-body tr').first.inner_text().startswith('Private Student')
        assert sort.locator('..').get_attribute('aria-sort') == 'descending'
        assert sort.evaluate('el => el === document.activeElement')
        # The name column stays frozen inside a keyboard-accessible scroll region.
        scroller = page.locator('.v3-table-scroll')
        first_name = page.locator('#v3-sheet-body .v3-name').first
        before = first_name.bounding_box()['x']
        scroller.evaluate('el => { el.scrollLeft = el.scrollWidth; }')
        assert abs(first_name.bounding_box()['x'] - before) < 1
        scroller.evaluate('el => { el.scrollLeft = 0; }')
        chooser = page.locator('.v3-column-picker > summary')
        chooser.focus(); page.keyboard.press('Enter')
        assert page.locator('#v3-column-groups fieldset').last.locator('input').count() == 23
        category = page.locator('#v3-column-groups').get_by_label('Scales', exact=True)
        category.focus(); page.keyboard.press('Space')
        assert category.is_checked()
        assert page.locator('#v3-sheet-head').get_by_role('button', name='Scales').count() == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        chooser.focus(); page.keyboard.press('Enter')
        # Selection is temporary, never an authorization or a persisted roster.
        page.get_by_role('button', name='Clear selection', exact=True).click()
        assert page.locator('#v3-week-total').inner_text() == '0 min'
        assert 'No students selected' in page.locator('#v3-empty').inner_text()
        page.get_by_label('Show selected only', exact=True).check()
        assert page.locator('#v3-sheet-body tr').count() == 0
        page.get_by_role('button', name='Select all shared students').click()
        assert page.locator('#v3-sheet-body tr').count() == 2
        # Student detail and page two remain bounded to this row's shared charts.
        page.get_by_role('button', name='Private Student', exact=True).click()
        assert page.locator('#v3-detail').is_visible()
        assert page.locator('#v3-detail-charts article').count() == 25
        page.get_by_role('button', name='Next charts').click()
        assert page.locator('#v3-detail-charts article').count() == 5
        assert 'Page 2 of 2' in page.locator('#v3-charts-page').inner_text()
        page.get_by_text('What Did You Work On? · all 23 categories', exact=True).click()
        assert page.locator('#v3-detail-categories tbody tr').count() == 23
        scales = page.locator('#v3-detail-categories tbody tr').filter(has_text='Scales')
        assert scales.locator('td').all_text_contents() == ['2', '3']
        assert page.locator('#v3-detail').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
        page.locator('#v3-detail').evaluate('el => { el.scrollTop = 0; }')
        page.screenshot(path=str(tmp_path / f'classroom-v3-student-{width}.png'))
        page.keyboard.press('Escape')
        assert not page.locator('#v3-detail').is_visible()
        page.get_by_role('button', name='Alex Reed', exact=True).click()
        assert '<script>window.PRIVATE_XSS=true</script>' in page.locator('#v3-detail-charts').inner_text()
        assert page.evaluate('typeof window.PRIVATE_XSS') == 'undefined'
        page.keyboard.press('Escape')
        # An older week uses occurrence dates, not the later submission dates.
        earlier = factory.monday - timedelta(days=7)
        page.get_by_label('Displayed week', exact=True).select_option(earlier.isoformat())
        assert page.locator('#v3-week-total').inner_text() == '20 min'
        assert page.locator('#v3-week-graph rect title').last.text_content().endswith('20 minutes')
        empty = factory.monday - timedelta(days=14)
        page.get_by_label('Displayed week', exact=True).select_option(empty.isoformat())
        assert 'Other practice may be private' in page.locator('#v3-empty').inner_text()
        assert page.locator('#v3-week-total').inner_text() == '0 min'
        # Shared records are never retained on a session change or page restoration.
        page.evaluate('window.dispatchEvent(new Event("ww:session-changed"))')
        assert page.locator('#v3-data-view').inner_text() == ''
        assert 'Reopen this class' in page.locator('#v3-status').inner_text()
        assert not page.locator('#v3-dashboard-data').count()
        assert not errors, errors
        browser.close()
    (tmp_path / f'firefox-v3-{width}.json').write_text(json.dumps({'result': 'PASS', 'width': width, 'page_errors': errors}, indent=2))


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844), (320, 740)])
def test_firefox_v3_historical_detail_displays_selected_week(v3_site, tmp_path, width, height):
    origin, factory = v3_site
    # Fix the practice dates without changing live eligibility timestamps, so
    # these historical examples remain valid when tests run in another week.
    offset = date(2026, 10, 26) - factory.monday
    with factory() as session:
        for chart in session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1)):
            chart.practice_date += offset
        session.commit()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch()
        context = browser.new_context(base_url=origin, viewport={'width': width, 'height': height},
                                      timezone_id='America/Los_Angeles', reduced_motion='reduce')
        assert context.request.post('/trusted-verifiers/login', form={'email': 'adult2@example.test', 'pin': '2468'},
                                    headers={'Origin': origin}).status == 200
        page = context.new_page(); page.set_default_timeout(15000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url(factory))
        page.get_by_label('Verifier PIN').fill('2468')
        page.get_by_role('button', name='Open class', exact=True).click()
        page.locator('#v3-data-view').wait_for(state='visible')
        examples = [('2026-10-19', 'October 19 – 25, 2026', '1', '20'),
                    ('2026-10-26', 'October 26 – November 1, 2026', '2', '11.25')]
        # Reopen the same student after each selection, including switching back.
        for start, label, scales_count, minutes in [*examples, examples[0]]:
            page.get_by_label('Displayed week', exact=True).select_option(start)
            page.get_by_role('button', name='Private Student', exact=True).click()
            dialog = page.locator('#v3-detail')
            assert dialog.is_visible()
            period = page.locator('#v3-detail-week')
            assert period.count() == 1
            assert ' '.join(period.inner_text().split()) == f'Displayed week: {label} · America/Chicago'
            assert period.is_visible()
            assert dialog.get_attribute('aria-describedby') == 'v3-detail-week'
            assert page.locator('#v3-detail-title').inner_text() == 'Private Student'
            stats = page.locator('#v3-detail-stats > div')
            assert stats.filter(has=page.get_by_text('Weekly minutes', exact=True)).locator('strong').inner_text() == minutes
            assert stats.filter(has=page.get_by_text('Shared history minutes', exact=True)).locator('strong').inner_text() == '31.25'
            details = page.locator('#v3-detail-categories').locator('..')
            if details.get_attribute('open') is None:
                details.locator('summary').click()
            assert page.locator('#v3-detail-categories thead th').all_text_contents() == ['Practice activity', 'Displayed week', 'Shared history']
            scales = page.locator('#v3-detail-categories tbody tr').filter(has_text='Scales')
            assert scales.locator('td').all_text_contents() == [scales_count, '3']
            assert page.locator('#v3-detail-categories tbody tr').count() == 23
            assert 'this week' not in dialog.inner_text().lower()
            assert 'this central calendar week' not in page.locator('#v3-submissions').inner_text().lower()
            assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
            dialog.evaluate('el => { el.scrollTop = 0; }')
            page.screenshot(path=str(tmp_path / f'r1-detail-{start}-{width}.png'))
            page.keyboard.press('Escape')
        assert not errors, errors
        browser.close()
