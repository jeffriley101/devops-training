"""Firefox desktop/mobile navigation, rendered charts, and account boundaries."""
import json
from datetime import timedelta

import pytest
from playwright.sync_api import sync_playwright

from app import memberships
from sqlalchemy import select
from app.models import PracticeChart, WoodchuckState
from app.practice_charts import PRACTICE_CATEGORIES
from test_personal_development import dashboard_db, PAGE, DATA, RATING, CHARTS, TODAY
from test_classroom_access import access_web
from test_classroom_access_browser import access_site
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web


@pytest.fixture
def dashboard_site(dashboard_db, access_site):
    origin, factory = access_site
    with factory() as session:
        memberships.create_complimentary_membership(session, memberships.Actor('student', 2),
                                                    memberships.Actor('admin'))
        session.add(WoodchuckState(profile_id=2, revision=0, state_json={
            'account': {'woodchuckId': 'WC-SECOND', 'authenticated': True, 'serverRevision': 0},
            'profile': {'woodchuckName': 'Another private student', 'instrument': 'Flute',
                        'level': 'Beginner', 'goal': 'Practice'},
        }))
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1).order_by(PracticeChart.id)).all()
        charts[0].practice_details = list(PRACTICE_CATEGORIES)
        charts[-1].practice_details = ['Long Tones', 'Scales']
        session.commit()
    return origin, factory


def signed_in(browser, origin):
    context = browser.new_context(base_url=origin, reduced_motion='reduce', has_touch=True)
    assert context.request.post('/account/login', form={'woodchuck_id': 'WC-SYNTHETIC', 'pin': '2468'},
                                headers={'Origin': origin}).status == 200
    page = context.new_page()
    page.set_default_timeout(15000)
    return context, page


def ready(page):
    page.wait_for_function('window.WWSessionBoundary && !document.querySelector(".app-shell").hidden')


def dashboard_ready(page):
    page.locator('[data-pdd-content] .bd-rating').wait_for(state='visible')


def test_firefox_book_dashboard_desktop_and_mobile(dashboard_site, tmp_path):
    origin, factory = dashboard_site
    errors = []
    with sync_playwright() as p:
        browser = p.firefox.launch()
        context, page = signed_in(browser, origin)
        page.on('pageerror', lambda error: errors.append(str(error)))
        expected = context.request.get(DATA).json()
        for width, height in [(320, 740), (390, 844), (1440, 900)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('/p-book'); ready(page)
            page.wait_for_function('document.querySelector(".pdd-book-rating strong").textContent !== "—" && document.querySelector(".pdd-book-rating strong").textContent.length > 0')
            assert page.locator('.pdd-book-rating strong').inner_text() == f"{expected['rating']:.1f}"
            assert page.locator('#p-book-include-contests').is_checked()
            page.locator('.pdd-book-rating').scroll_into_view_if_needed()
            page.screenshot(path=str(tmp_path / f'book-rating-{width}.png'), full_page=True)
            if width < 768:
                page.locator('.pdd-book-rating').tap()
            else:
                page.locator('.pdd-book-rating').focus()
                page.keyboard.press('Enter')
            page.wait_for_url(origin + PAGE); dashboard_ready(page)
            assert page.get_by_role('heading', name='Personal Development Dashboard', exact=True).is_visible()
            assert page.locator('.bd-rating').inner_text() == f"{expected['rating']:.1f}"
            assert page.locator('.pdd-chart .bar').count() == 7
            heights = page.locator('.pdd-chart .bar').evaluate_all('(nodes)=>nodes.map(n=>Number(n.getAttribute("height")))')
            assert heights[1] == 0 and heights[2] == 200
            assert heights[0] == pytest.approx(345 / 420 * 200)
            assert page.locator('.pdd-chart .dot').count() == len(expected['lifetime'])
            assert page.locator('[data-director-table], select[name=week], a[href*=".csv"]').count() == 0
            assert page.locator('.main-nav, a[href="/family/parent-access"], a[href="/director"]').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert 'PRIVATE CHART TEXT' not in page.locator('[data-pdd-content]').inner_text()
            assert 'trend' not in page.locator('[data-pdd-content]').inner_text().lower()
            page.screenshot(path=str(tmp_path / f'dashboard-{width}.png'), full_page=True)
            # Historical graph selection preserves the current Personal Practice Rating.
            page.locator('#pdd-week').select_option('2026-09-21')
            assert page.locator('.bd-rating').inner_text() == f"{expected['rating']:.1f}"
            assert page.locator('.pdd-chart-total').last.inner_text() == '10 minutes'
            old_heights = page.locator('.pdd-chart .bar').evaluate_all('(nodes)=>nodes.map(n=>Number(n.getAttribute("height")))')
            assert old_heights == [200, 0, 0, 0, 0, 0, 0]
            page.locator('#pdd-week').select_option(expected['week_start'])
            # Keyboard-operable category table, readable counts and history.
            category_table = page.locator('.pdd-activity-table')
            assert category_table.locator('tbody th').all_text_contents() == ['Long Tones', 'Scales']
            category_toggle = page.get_by_role('button', name='Show All 23 Categories', exact=True)
            category_toggle.focus(); page.keyboard.press('Enter')
            assert category_table.locator('tbody th').count() == 23
            assert category_table.locator('tbody tr').first.all_text_contents() == ['Long Tones12']
            assert category_table.locator('td').first.evaluate('(n)=>parseFloat(getComputedStyle(n).fontSize)') >= 13
            page.get_by_text('Weekly totals', exact=True).click()
            weekly_table = page.get_by_role('region', name='Weekly practice history', exact=True)
            assert weekly_table.locator('tbody tr').count() == len(expected['history'])
            if width < 768:
                weekly_table.focus(); page.keyboard.press('ArrowRight')
                page.wait_for_function('document.querySelector("#pdd-history .pdd-table-wrap").scrollLeft > 0')
            page.get_by_text('Practice Chart submissions (7)', exact=True).click()
            page.wait_for_function('document.querySelectorAll(".pdd-submission-list li").length === 7')
            assert page.locator('.pdd-submission-list li').first.is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert 'PRIVATE' not in page.locator('[data-pdd-content]').inner_text()
            page.locator('#pdd-history').screenshot(path=str(tmp_path / f'history-{width}.png'))
            page.locator('#pdd-categories').screenshot(path=str(tmp_path / f'categories-{width}.png'))
            page.get_by_role('link', name='Back to Practice BOOK').click()
            page.wait_for_url(origin + '/p-book'); ready(page)
        # Withdraw subscription access while the dashboard is open.
        page.goto(PAGE); dashboard_ready(page)
        factory.clock[0] += timedelta(days=15)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        page.locator('[data-pdd-invitation]').wait_for(state='visible')
        assert page.locator('[data-pdd-content]').inner_text() == ''
        assert page.locator('svg, .bd-rating').count() == 0
        page.screenshot(path=str(tmp_path / 'quiet-invitation.png'), full_page=True)
        page.goto('/p-book'); ready(page)
        assert page.locator('.pdd-book-rating strong').inner_text() == 'View'
        page.locator('.pdd-book-rating').click(); page.wait_for_url(origin + PAGE)
        assert page.locator('[data-pdd-invitation]').is_visible()
        assert context.request.get(DATA).status == 403
        assert not errors, errors
        browser.close()
    (tmp_path / 'firefox-results.json').write_text(json.dumps({
        'result': 'PASS', 'viewports': [[320, 740], [390, 844], [1440, 900]], 'page_errors': errors,
        'checks': ['BOOK circle tap/keyboard navigation', 'rating and chart accuracy',
                   'historical graph selection', '23 categories and submission history',
                   'keyboard disclosures and mobile table scrolling',
                   'entitlement loss clears statistics', 'quiet free invitation'],
    }, indent=2))


def activity_rows(page):
    return page.locator('.pdd-activity-table tbody tr:has(th)').evaluate_all(
        '(rows) => rows.map(row => Array.from(row.cells, cell => cell.textContent))')


def assert_activity_layout(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('.pdd-activity-table').evaluate('''table =>
        table.scrollWidth <= table.clientWidth + 1 &&
        Array.from(table.querySelectorAll('th, td')).every(cell => cell.scrollWidth <= cell.clientWidth + 1)''')
    for button in page.locator('#pdd-categories button').all():
        assert button.bounding_box()['height'] >= 44
    assert page.locator('.pdd-activity-table').evaluate('(table) => parseFloat(getComputedStyle(table).fontSize)') >= 13
    assert page.locator('.pdd-activity-sort span').nth(2).evaluate('''label => {
        const range = document.createRange(); range.selectNodeContents(label);
        return range.getClientRects().length === 1;
    }''')  # The Lifetime heading must not split inside the word at 320px.
    assert page.locator('.pdd-activity-table tbody th').evaluate_all('''labels => labels.every(label => {
        if (label.textContent.includes(' ')) return true;
        const range = document.createRange(); range.selectNodeContents(label);
        return range.getClientRects().length === 1;
    })''')  # Keep single-word activity names intact, too.


@pytest.mark.parametrize('width,height', [(320, 740), (390, 844), (1440, 900)])
def test_firefox_activity_table_counts_sorting_and_keyboard(dashboard_site, tmp_path, width, height):
    origin, factory = dashboard_site
    with factory() as session:
        first = session.scalar(select(PracticeChart).where(PracticeChart.profile_id == 1).order_by(PracticeChart.id))
        first.practice_details = [name for name in PRACTICE_CATEGORIES if name not in ('Busking', 'Fingerings')]
        for days_ago, details in [
            (0, ['Etudes', 'Long Tones', 'Etudes']), (0, ['Etudes', 'Arpeggios']),
            (0, ['Dynamics']), (0, ['Dynamics', 'Long Tones']),
            *[(10, ['Patterns']) for _ in range(11)],
            *[(10, ['Etudes', 'Long Tones']) for _ in range(4)],
        ]:
            session.add(PracticeChart(profile_id=1, practice_date=TODAY - timedelta(days=days_ago),
                minutes=1, source='p-book', instrument='Trumpet', practice_details=details))
        session.commit()
    errors = []
    with sync_playwright() as p:
        browser = p.firefox.launch()
        context, page = signed_in(browser, origin)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_viewport_size({'width': width, 'height': height})
        expected = context.request.get(DATA).json()
        counts = {c['name']: [str(c['weekly_charts']), str(c['lifetime_charts'])] for c in expected['categories']}
        assert counts['Etudes'] == ['2', '7']  # Duplicate selection on one chart counts only once.
        assert counts['Long Tones'] == ['3', '8']  # Repeated on separate charts counts each chart.
        assert counts['Patterns'] == ['0', '12']  # Sorting must be numeric, not lexical.
        assert counts['Busking'] == counts['Fingerings'] == ['0', '0']
        page.goto(PAGE); dashboard_ready(page)
        panel = page.locator('#pdd-categories')
        assert panel.get_by_role('columnheader').count() == 3
        assert panel.get_by_role('columnheader', name='Practice Activity', exact=True).is_visible()
        assert panel.locator('.pdd-category-track, .pdd-category-list, [role="progressbar"]').count() == 0
        assert activity_rows(page) == [
            ['Long Tones', '3', '8'], ['Etudes', '2', '7'], ['Dynamics', '2', '3'],
            ['Arpeggios', '1', '2'], ['Scales', '1', '2'],
        ]
        assert panel.locator('[aria-sort="descending"]').count() == 1
        assert_activity_layout(page)
        panel.screenshot(path=str(tmp_path / f'activity-default-{width}.png'))
        toggle = panel.get_by_role('button', name='Show All 23 Categories', exact=True)
        assert toggle.get_attribute('aria-expanded') == 'false'
        toggle.focus(); page.keyboard.press('Enter')
        collapse = panel.get_by_role('button', name='Collapse', exact=True)
        assert collapse.get_attribute('aria-expanded') == 'true'
        assert collapse.evaluate('(button) => button === document.activeElement')
        rows = activity_rows(page)
        assert len(rows) == 23 and {r[0] for r in rows} == set(PRACTICE_CATEGORIES)
        assert {row[0]: row[1:] for row in rows} == counts
        assert all(int(row[1]) > 0 for row in rows[:5]) and all(row[1] == '0' for row in rows[5:])
        assert_activity_layout(page)
        panel.screenshot(path=str(tmp_path / f'activity-all-{width}.png'))
        # Native keyboard buttons keep focus, announce sort direction, and sort
        # all displayed rows numerically (including zero-count categories).
        weekly = panel.locator('thead th').nth(1).get_by_role('button')
        lifetime = panel.locator('thead th').nth(2).get_by_role('button')
        weekly.focus(); page.keyboard.press('Enter')
        assert weekly.evaluate('(button) => button === document.activeElement')
        assert weekly.get_attribute('aria-label') == 'This Week: sort descending'
        assert panel.locator('thead th').nth(1).get_attribute('aria-sort') == 'ascending'
        assert [int(row[1]) for row in activity_rows(page)] == sorted(int(row[1]) for row in rows)
        page.keyboard.press('Space')
        assert [int(row[1]) for row in activity_rows(page)] == sorted((int(row[1]) for row in rows), reverse=True)
        page.keyboard.press('Tab')
        assert lifetime.evaluate('(button) => button === document.activeElement')
        assert lifetime.evaluate('(button) => getComputedStyle(button).outlineStyle') == 'solid'
        page.keyboard.press('Enter')
        assert panel.locator('thead th').nth(2).get_attribute('aria-sort') == 'descending'
        assert panel.locator('[aria-sort]').count() == 1
        assert [int(row[2]) for row in activity_rows(page)] == sorted((int(row[2]) for row in rows), reverse=True)
        page.keyboard.press('Space')
        assert lifetime.get_attribute('aria-label') == 'Lifetime: sort descending'
        assert panel.locator('thead th').nth(2).get_attribute('aria-sort') == 'ascending'
        assert [int(row[2]) for row in activity_rows(page)] == sorted(int(row[2]) for row in rows)
        assert {row[0]: row[1:] for row in activity_rows(page)} == counts
        page.keyboard.press('Tab')
        assert collapse.evaluate('(button) => button === document.activeElement')
        page.keyboard.press('Space')
        assert toggle.get_attribute('aria-expanded') == 'false'
        assert len(activity_rows(page)) == 5 and all(int(row[1]) > 0 for row in activity_rows(page))
        assert [int(row[2]) for row in activity_rows(page)] == [2, 2, 3, 7, 8]
        assert toggle.evaluate('(button) => button === document.activeElement')
        assert panel.get_by_text('Counts represent Practice Charts where an activity was selected—not minutes practiced or skill proficiency.', exact=True).is_visible()
        assert panel.locator('#pdd-category-explanation').evaluate('node => node.previousElementSibling.tagName') == 'TABLE'
        assert page.locator('.bd-rating').inner_text() == f"{expected['rating']:.1f}"
        assert context.request.get(DATA).json() == expected  # Sorting/filtering never changes source data.
        assert not errors, errors
        browser.close()
    (tmp_path / 'activity-results.json').write_text(json.dumps({
        'result': 'PASS', 'viewport': [width, height], 'page_errors': errors,
        'checks': ['23 original categories and exact saved counts', 'weekly activity first by default',
                   'Show All and Collapse', 'both sort columns in both directions',
                   'keyboard focus and aria-sort', 'no horizontal category scrolling'],
    }, indent=2))


def test_firefox_activity_table_empty_week(dashboard_site, tmp_path):
    origin, factory = dashboard_site
    with factory() as session:
        for chart in session.scalars(select(PracticeChart).where(
                PracticeChart.profile_id == 1, PracticeChart.practice_date >= TODAY - timedelta(days=TODAY.weekday()))):
            chart.practice_details = []
        session.commit()
    with sync_playwright() as p:
        browser = p.firefox.launch()
        context, page = signed_in(browser, origin)
        for width, height in [(320, 740), (390, 844), (1440, 900)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto(PAGE); dashboard_ready(page)
            panel = page.locator('#pdd-categories')
            assert activity_rows(page) == []
            assert panel.get_by_text('No activities were reported this week. Show all 23 categories to see your lifetime counts.', exact=True).is_visible()
            assert_activity_layout(page)
            panel.screenshot(path=str(tmp_path / f'activity-empty-{width}.png'))
            panel.get_by_role('button', name='Show All 23 Categories', exact=True).click()
            rows = activity_rows(page)
            assert len(rows) == 23 and all(row[1:] == ['0', '1'] for row in rows)
            assert_activity_layout(page)
            panel.get_by_role('button', name='Collapse', exact=True).click()
            assert activity_rows(page) == []
            assert panel.locator('.pdd-category-empty').is_visible()
        browser.close()


def test_firefox_delayed_response_and_account_switch(dashboard_site):
    origin, factory = dashboard_site
    with sync_playwright() as p:
        browser = p.firefox.launch()
        context, page = signed_in(browser, origin)
        page.goto(PAGE); dashboard_ready(page)
        held = []
        def delay(route):
            if held:
                route.continue_()
            else:
                held.append((route, route.fetch()))
        page.route('**' + DATA, delay)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        for _ in range(100):
            if held: break
            page.wait_for_timeout(25)
        assert held and held[0][1].status == 200
        factory.clock[0] += timedelta(days=15)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        page.locator('[data-pdd-invitation]').wait_for(state='visible')
        held[0][0].fulfill(response=held[0][1])
        page.wait_for_timeout(100)
        assert page.locator('[data-pdd-content]').inner_text() == ''
        page.unroute('**' + DATA, delay)
        factory.clock[0] -= timedelta(days=15)
        page.reload(); dashboard_ready(page)
        other = context.new_page()
        other.goto('/login')
        other.locator('#login-woodchuck-id').fill('WC-SECOND')
        other.locator('#login-pin').fill('2468')
        other.locator('#account-login-form button[type=submit]').click()
        other.wait_for_url(origin + '/home')
        page.wait_for_function('document.querySelector(".app-shell").hidden')
        assert page.locator('[data-pdd-content]').inner_text() == ''
        other.goto(PAGE); dashboard_ready(other)
        second = context.request.get(DATA).json()
        assert second['lifetime_seconds'] == 999 * 60
        assert other.locator('.pdd-chart-total').first.inner_text() == '999 minutes'
        assert context.request.get(RATING, headers={'X-Woodshed-Account': 'WC-SYNTHETIC'}).status == 401
        browser.close()


def test_firefox_submission_pagination_rechecks_access_and_ignores_stale_history(dashboard_site):
    origin, factory = dashboard_site
    with factory() as session:
        for i in range(26):
            session.add(PracticeChart(profile_id=1, practice_date=TODAY - timedelta(days=i),
                minutes=i + 1, source='p-book', instrument='Trumpet', practice_details=['Scales']))
        session.commit()
    with sync_playwright() as p:
        browser = p.firefox.launch()
        context, page = signed_in(browser, origin)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto(PAGE); dashboard_ready(page)
        page.get_by_text('Practice Chart submissions (33)', exact=True).click()
        page.wait_for_function('document.querySelectorAll(".pdd-submission-list li").length === 25')
        page.get_by_role('button', name='Older submissions').click()
        page.wait_for_function('document.querySelectorAll(".pdd-submission-list li").length === 8')
        assert page.get_by_text('Page 2 of 2 · 33 saved charts', exact=True).is_visible()
        assert page.get_by_role('button', name='Older submissions').is_disabled()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        factory.clock[0] += timedelta(days=15)
        page.get_by_role('button', name='Newer submissions').click()
        page.locator('[data-pdd-invitation]').wait_for(state='visible')
        assert page.locator('[data-pdd-content]').inner_text() == ''
        factory.clock[0] -= timedelta(days=15)
        page.reload(); dashboard_ready(page)
        held = []
        def delay(route):
            held.append((route, route.fetch()))
        page.route('**' + CHARTS + '?*', delay)
        page.get_by_text('Practice Chart submissions (33)', exact=True).click()
        for _ in range(100):
            if held: break
            page.wait_for_timeout(25)
        assert held and held[0][1].status == 200
        factory.clock[0] += timedelta(days=15)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        page.locator('[data-pdd-invitation]').wait_for(state='visible')
        held[0][0].fulfill(response=held[0][1]); page.wait_for_timeout(100)
        assert page.locator('[data-pdd-content]').inner_text() == ''
        browser.close()
