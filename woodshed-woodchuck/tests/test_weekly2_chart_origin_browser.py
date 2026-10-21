"""Actual first-party chart forms; synthetic audio is not microphone certification."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import sync_playwright
from tests.test_weekly2_first_streak_browser import snapshot, reward_state


# The real Pristine detector/timer consumes a real Web Audio stream. Only the
# physical input is synthetic: silence for calibration, followed by a tone.
# No request/response, detector result, session or Origin is replaced.
SYNTHETIC_INPUT = """(() => {
  navigator.mediaDevices.getUserMedia = async () => {
    const ac = new AudioContext(); await ac.resume();
    const tone = ac.createOscillator(), gain = ac.createGain();
    const destination = ac.createMediaStreamDestination();
    tone.frequency.value = 440;
    gain.gain.setValueAtTime(0, ac.currentTime);
    gain.gain.setValueAtTime(0.15, ac.currentTime + 2.5);
    tone.connect(gain).connect(destination); tone.start();
    window.__originSyntheticAudio = ac;
    return destination.stream;
  };
})();"""


def server_source(source, migration):
    # Initialize the existing progress defaults so later normal BOOK hydration
    # is not confused with a reward mutation. Both owners use the same shape.
    return source.replace('Base.metadata.create_all(engine)', migration).replace(
        "'progress':{'credits':credits}",
        "'progress':{'credits':credits,'level':1,'streak':0,'lastCompletedDate':None}")


@pytest.fixture
def site(tmp_path, monkeypatch):
    import test_overhaul_profile_saves_browser as harness
    root = Path(__file__).resolve().parents[1]
    migrate = ("from alembic import command\nfrom alembic.config import Config\n"
               f"command.upgrade(Config({str(root / 'alembic.ini')!r}),'w25weekly004')")
    monkeypatch.setattr(harness, 'SERVER', server_source(harness.SERVER, migrate))
    yield from harness.save_site.__wrapped__(tmp_path)


@pytest.fixture
def output(tmp_path):
    return tmp_path


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
@pytest.mark.parametrize('scenario', ['book', 'family', 'pristine'])
def test_chart_forms(site, output, browser_name, scenario):
    origin, database = site
    result = {'browser': browser_name, 'scenario': scenario, 'page_errors': [], 'mutations': []}
    page = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/opt/google/chrome/chrome') if browser_name == 'chrome' else pw.firefox.launch()
            result['version'] = browser.version
            context = browser.new_context(base_url=origin, reduced_motion='reduce', timezone_id='America/Chicago')
            if scenario == 'pristine':
                context.add_init_script(SYNTHETIC_INPUT)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
            def observed(response):
                request = response.request
                if request.method == 'POST' and ('practice-charts' in request.url or request.url.endswith('/family/practice')):
                    headers = request.all_headers()
                    result['mutations'].append({'path': request.url.removeprefix(origin),
                        'status': response.status, 'origin': headers.get('origin'),
                        'sec-fetch-site': headers.get('sec-fetch-site'),
                        'sec-fetch-mode': headers.get('sec-fetch-mode'),
                        'sec-fetch-dest': headers.get('sec-fetch-dest')})
            page.on('response', observed)
            assert context.request.post('/account/login', form={'woodchuck_id': 'WC-GUEST-A', 'pin': '2468'},
                                        headers={'Origin': origin}).status == 200
            result['post_login'] = reward_state(database)
            path = {'book': '/p-book', 'family': '/family/practice', 'pristine': '/practice/pristine'}[scenario]
            response = page.goto(origin + path)
            assert response.status == 200
            if scenario == 'book':
                page.wait_for_function('() => document.querySelector("#p-book-form").dataset.woodshedPBookWired === "true"')
                page.locator('#p-book-minutes').fill('5')
                page.locator('#p-book-note').fill('Synthetic Origin BOOK chart')
                page.locator('#p-book-include-contests').uncheck()
                page.locator('#p-book-include-team').uncheck()
            elif scenario == 'family':
                page.locator('[name="practice_date"]').fill(datetime.now(ZoneInfo('America/Chicago')).date().isoformat())
                page.locator('[name="minutes"]').fill('5')
                page.locator('[name="note"]').fill('Synthetic Origin private chart')
            else:
                # Authentication reveals the shell before the sequential account
                # scripts finish loading. Visibility alone is not tool readiness.
                # resetSession sets this only after the real handlers are wired.
                page.wait_for_function('() => document.querySelector("[data-pristine-practice]").dataset.pristineState === "listening"')
                page.locator('[data-pristine-start]').click()
                page.wait_for_function('() => document.querySelector("[data-pristine-time]").textContent !== "00:00:00"')
                result['detected_time_before_save'] = page.locator('[data-pristine-time]').text_content()
                result['microphone_before_save'] = page.locator('[data-pristine-microphone]').text_content()
                # A real authenticated HTTP boundary probe, separate from the
                # browser's microphone/timer journey; not an exploitability claim.
                before_rejection = snapshot(database)
                rejected = context.request.post('/practice-charts/pristine',
                    data={'detected_playing_seconds': 300,
                          'submission_key': 'foreign-origin-pristine-probe'},
                    headers={'Origin': 'https://foreign.example.invalid'})
                assert rejected.status == 403
                assert snapshot(database) == before_rejection
                result['foreign_origin_probe'] = {'status': rejected.status,
                    'all_business_rows_unchanged': True, 'transport': 'authenticated HTTP'}
            before = snapshot(database)
            result['before_submit'] = reward_state(database)
            if scenario == 'book':
                page.locator('#p-book-form button[type="submit"]').click()
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/practice-charts')) as submitted:
                    page.locator('#p-book-confirm-submit').click()
                assert submitted.value.status == 201
                page.wait_for_function('() => document.querySelector("#p-book-feedback").textContent.includes("saved")')
            elif scenario == 'family':
                with page.expect_navigation():
                    page.get_by_role('button', name='Save private chart', exact=True).click()
                assert page.locator('article').filter(has_text='Synthetic Origin private chart').count() == 1
            else:
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/practice-charts/pristine')) as submitted:
                    page.locator('[data-pristine-done]').click()
                assert submitted.value.status == 201
                page.wait_for_function('() => document.querySelector("[data-pristine-feedback]").textContent.includes("saved")')
            after = snapshot(database)
            result['after_submit'] = reward_state(database)
            assert len(after['practice_charts']) == len(before['practice_charts']) + 1
            for table in before:
                # BOOK's established state projection saves its local practice
                # history; check protected balances and the other owner below.
                if table not in ('practice_charts', 'woodchuck_states'):
                    assert after[table] == before[table], table
            with sqlite3.connect(database) as connection:
                row = connection.execute('SELECT profile_id,source,minutes,detected_playing_seconds,credits_awarded FROM practice_charts ORDER BY id DESC LIMIT 1').fetchone()
            assert row[0] == 1 and row[4] == 0
            assert row[1] == ('pristine' if scenario == 'pristine' else 'p-book')
            assert (row[3] >= 1) if scenario == 'pristine' else (row[2] == 5 and row[3] is None)
            for old, new in zip(result['before_submit']['states'], result['after_submit']['states']):
                assert old[0] == new[0]
                assert old[1].get('progress') == new[1].get('progress')
                if old[0] == 2:
                    assert old == new
            assert len(result['mutations']) == 1
            mutation = result['mutations'][0]
            assert mutation['origin'] == origin or (
                mutation['origin'] == 'null' and mutation['sec-fetch-site'] == 'same-origin'
                and mutation['sec-fetch-mode'] == 'navigate' and mutation['sec-fetch-dest'] == 'document')
            assert not result['page_errors'], result['page_errors']
            result['saved_chart'] = row
            result['synthetic_audio_only'] = scenario == 'pristine'
            page.screenshot(path=str(output / 'chart.png'))
            context.close()
            browser.close()
        result['result'] = 'PASS'
    except BaseException as error:
        result.update(result='FAIL', error=repr(error))
        raise
    finally:
        (output / 'focused-results.json').write_text(json.dumps(result, indent=2))
