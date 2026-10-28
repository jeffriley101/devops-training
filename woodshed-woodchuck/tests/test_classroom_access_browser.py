"""Real desktop/mobile rendering against a disposable, enrolled Classroom."""
from datetime import timedelta
import json
import socket
from threading import Thread
import time

import pytest
from playwright.sync_api import sync_playwright
import uvicorn

from app.main import app
from app.models import WoodchuckState
from test_classroom_access import access_web, enroll
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web


@pytest.fixture
def access_site(access_web, monkeypatch):
    enroll(access_web)
    with access_web() as session:
        session.add(WoodchuckState(profile_id=1, revision=0, state_json={
            'account': {'woodchuckId': 'WC-SYNTHETIC', 'authenticated': True, 'serverRevision': 0},
            'profile': {'woodchuckName': 'Private Student', 'instrument': 'Trumpet',
                        'level': 'Beginner', 'goal': 'Build daily consistency'},
        }))
        session.commit()
    monkeypatch.delenv('CLASSROOM_DIRECTOR_ENABLED')
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
            yield origin, access_web
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


@pytest.mark.parametrize('browser_name', ['chrome', 'firefox'])
def test_student_benefits_desktop_mobile(access_site, tmp_path, browser_name):
    origin, factory = access_site
    evidence = {'browser': browser_name, 'layouts': [], 'page_errors': []}
    with sync_playwright() as playwright:
        browser = (playwright.chromium.launch(executable_path='/opt/google/chrome/chrome')
                   if browser_name == 'chrome' else playwright.firefox.launch())
        context = browser.new_context(base_url=origin, reduced_motion='reduce')
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on('pageerror', lambda error: evidence['page_errors'].append(str(error)))
        response = context.request.post('/account/login', form={'woodchuck_id': 'WC-SYNTHETIC', 'pin': '2468'},
                                        headers={'Origin': origin})
        assert response.status == 200
        for width, height in [(390, 844), (1440, 900)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('/practice/skill-building')
            page.get_by_text('Classroom access confirmed.', exact=False).wait_for(state='visible')
            assert page.locator('.arcade-classroom li').count() == 5
            assert page.locator('.arcade-classroom a').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(tmp_path / f'exercises-{width}.png'), full_page=True)
            page.goto('/p-book')
            panel = page.locator('#practice-insights')
            panel.scroll_into_view_if_needed()
            page.wait_for_function('document.querySelector("[data-insights-content]").textContent.includes("Four-week total:")')
            assert panel.locator('[data-insights-active]').is_visible()
            assert panel.locator('[data-insights-locked]').is_hidden()
            assert 'Four-week total: 17m' in panel.inner_text()
            assert '999' not in panel.inner_text()
            assert 'rating' not in panel.inner_text().lower()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(tmp_path / f'insights-{width}.png'), full_page=True)
            evidence['layouts'].append([width, height])
        # Existing client refresh must clear a summary when enrollment benefits end.
        factory.clock[0] += timedelta(days=15)
        page.evaluate('document.dispatchEvent(new Event("visibilitychange"))')
        page.wait_for_function('!document.querySelector("[data-insights-locked]").hidden')
        assert page.locator('[data-insights-content]').inner_text() == ''
        assert context.request.get('/practice-charts/insights').status == 403
        page.goto('/practice/skill-building')
        assert 'Classroom access confirmed.' not in page.locator('.arcade-classroom').inner_text()
        assert page.locator('.arcade-classroom').inner_text().count('Locked') == 6
        assert not evidence['page_errors'], evidence['page_errors']
        browser.close()
    evidence['result'] = 'PASS'
    (tmp_path / 'classroom-access-results.json').write_text(json.dumps(evidence, indent=2) + '\n')
