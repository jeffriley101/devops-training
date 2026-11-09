"""Isolated real browser workflows; synthetic DB and deterministic local audio."""
import socket
from threading import Thread
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from app import main
from app.classroom_models import ClassroomEntryCode
from test_classroom_guest_s4 import s4_web, classroom_db, adult_pin_hash, fingerprints


@pytest.fixture
def s4_site(s4_web, monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('PUBLIC_BASE_URL', origin)
        server = uvicorn.Server(uvicorn.Config(main.app, log_level='warning', lifespan='off'))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started: break
                assert thread.is_alive(); time.sleep(.05)
            assert server.started
            yield origin, s4_web
        finally:
            server.should_exit = True; thread.join(timeout=10)
            assert not thread.is_alive()


@pytest.fixture(params=['chromium', 'firefox'])
def s4_browser(request):
    with sync_playwright() as api:
        browser = (api.chromium.launch(executable_path='/opt/google/chrome/chrome')
                   if request.param == 'chromium' else api.firefox.launch())
        yield browser
        browser.close()


# Only browser-local APIs are controlled. Actual application JS, HTTP routes,
# cookies, CSP, sessions, authority, detector and timer all run unmodified.
MICROPHONE = """(() => {
  window.microphoneTracks = []; window.audioClosed = 0; window.permissionRequests = 0;
  navigator.mediaDevices.getUserMedia = async () => {
    window.permissionRequests++;
    if (window.denyMicrophone) throw new Error('permission denied');
    if (window.holdMicrophone) await new Promise(resolve => { window.grantMicrophone = resolve; });
    const track = { stopped: false, stop() { this.stopped = true; } };
    window.microphoneTracks.push(track);
    return { getTracks: () => [track] };
  };
  window.AudioContext = class {
    resume() { return Promise.resolve(); }
    close() { window.audioClosed++; return Promise.resolve(); }
    createMediaStreamSource() { return {connect() {}}; }
    createAnalyser() { return {fftSize:1024, getFloatTimeDomainData(a) { a.fill(window.loud ? .2 : 0); }}; }
  };
})();"""


def open_page(browser, origin, *, audio=False):
    ctx = browser.new_context(base_url=origin, viewport={'width':390, 'height':900})
    if audio: ctx.add_init_script(MICROPHONE)
    page = ctx.new_page(); page.set_default_timeout(15000)
    errors = []; requests = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda req: requests.append((req.method, req.url, req.post_data)))
    page.goto('/guest/classroom')
    expect(page.locator('#guest-classroom-admit')).to_be_enabled()
    return ctx, page, errors, requests


def admit(page, code='BAND2026'):
    page.locator('#guest-program').fill('1')
    page.locator('#guest-class-code').fill(code)
    page.locator('#guest-classroom-admit').click()
    expect(page.locator('#guest-pristine')).to_be_visible()


def test_s4_admission_local_pristine_stop_exit_and_no_database_writes(s4_site, s4_browser):
    origin, refs = s4_site; before = fingerprints(refs.factory)
    ctx, page, errors, requests = open_page(s4_browser, origin, audio=True)
    admit(page)
    assert all(s not in page.content() for s in ('WC-SYNTHETIC', 'Private Student', 'adult1@example.test'))
    page.locator('#guest-pristine-start').click()
    page.wait_for_function('window.permissionRequests === 1 && window.microphoneTracks.length === 1')
    page.wait_for_timeout(1800); page.evaluate('window.loud = true')
    expect(page.locator('#guest-pristine-time')).not_to_have_text('00:00')
    page.locator('#guest-pristine-stop').click()
    assert page.evaluate('microphoneTracks.every(t => t.stopped) && audioClosed === 1')
    page.locator('#guest-classroom-leave').click()
    expect(page.locator('#guest-pristine')).to_be_hidden()
    expect(page.locator('#guest-pristine-time')).to_have_text('00:00')
    page.wait_for_function("document.getElementById('guest-classroom-status').textContent.includes('ended')")
    assert fingerprints(refs.factory) == before
    posts = [(url, data) for method, url, data in requests if method == 'POST']
    assert posts and all('/guest/classroom/' in url for url, _ in posts)
    assert all('playing' not in data and 'seconds' not in data for _, data in posts)
    assert not errors
    ctx.close()


def test_rotation_expiry_and_stale_page_deny_and_clear(s4_site, s4_browser):
    origin, refs = s4_site
    ctx, page, errors, _ = open_page(s4_browser, origin, audio=True)
    admit(page)
    with refs.factory() as session:
        code = session.get(ClassroomEntryCode, refs.code)
        from app import classroom_s2 as s2
        code.is_current = False; code.revoked_at = s2._now(); code.revoked_by_verifier_id = 1
        session.commit()
    page.locator('#guest-pristine-start').click()
    expect(page.locator('#guest-pristine')).to_be_hidden()
    assert page.evaluate('permissionRequests') == 0
    page.reload()
    with refs.factory() as session:
        code = session.get(ClassroomEntryCode, refs.code)
        code.is_current = True; code.revoked_at = None; code.revoked_by_verifier_id = None
        session.commit()
    admit(page)
    refs.clock[0] += 900
    page.locator('#guest-pristine-start').click()
    expect(page.locator('#guest-pristine')).to_be_hidden()
    expect(page.locator('#guest-classroom-admit')).to_be_disabled()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    expect(page.locator('#guest-pristine')).to_be_hidden()
    assert not errors
    ctx.close()


def test_delayed_permission_and_hidden_page_release_resources(s4_site, s4_browser):
    origin, refs = s4_site; before = fingerprints(refs.factory)
    ctx, page, errors, _ = open_page(s4_browser, origin, audio=True)
    admit(page); page.evaluate('window.holdMicrophone = true')
    page.locator('#guest-pristine-start').click()
    page.wait_for_function('typeof window.grantMicrophone === "function"')
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    page.evaluate('window.grantMicrophone()')
    page.wait_for_function('microphoneTracks.length === 1 && microphoneTracks[0].stopped')
    expect(page.locator('#guest-pristine')).to_be_hidden()
    assert fingerprints(refs.factory) == before and not errors
    ctx.close()


def test_account_switch_clears_context_and_never_imports_activity(s4_site, s4_browser):
    origin, refs = s4_site
    ctx, page, errors, _ = open_page(s4_browser, origin, audio=True)
    admit(page)
    other = ctx.new_page(); other.goto('/guest/login')
    other.locator('#login-woodchuck-id').fill('WC-SYNTHETIC')
    other.locator('#login-pin').fill('2468')
    other.locator('#account-login-form button').click()
    expect(page.locator('#guest-pristine')).to_be_hidden()
    expect(page.locator('#guest-pristine-time')).to_have_text('00:00')
    # Account login may legitimately update session security metadata; neither
    # Guest implementation has a practice submission endpoint.
    with refs.factory() as session:
        from app.models import PracticeChart
        from sqlalchemy import select
        assert list(session.scalars(select(PracticeChart))) == []
    assert not errors
    ctx.close()


def test_csp_and_general_guest_fetch_prohibition_remain(s4_site, s4_browser):
    origin, refs = s4_site; before = fingerprints(refs.factory)
    ctx, page, errors, requests = open_page(s4_browser, origin)
    result = page.evaluate("""async () => {
      try { await fetch('/account/me'); return 'unsafe'; } catch (_) { return 'blocked'; }
    }""")
    assert result == 'blocked'
    admit(page)
    page.locator('#guest-classroom-back').click(); page.wait_for_url('**/guest')
    assert page.evaluate("document.body.dataset.guest === 'local'")
    result = page.evaluate("""async () => {
      try { await WWSessionBoundary.guestClassroomFetch('admit', {}); return 'unsafe'; }
      catch (_) { return 'blocked'; }
    }""")
    assert result == 'blocked'
    assert not any('/account/me' in url for _, url, _ in requests)
    assert fingerprints(refs.factory) == before and not errors
    ctx.close()


def test_permission_denial_and_authority_network_failure_stop_safely(s4_site, s4_browser):
    origin, refs = s4_site; before = fingerprints(refs.factory)
    ctx, page, errors, _ = open_page(s4_browser, origin, audio=True)
    page.clock.install()
    admit(page); page.evaluate('window.denyMicrophone = true')
    page.locator('#guest-pristine-start').click()
    expect(page.locator('#guest-pristine-status')).to_contain_text('Microphone unavailable')
    assert page.evaluate('microphoneTracks.length') == 0
    page.evaluate('window.denyMicrophone = false')
    page.locator('#guest-pristine-start').click()
    expect(page.locator('#guest-pristine-stop')).to_be_enabled()
    page.route('**/guest/classroom/check', lambda route: route.abort())
    page.clock.fast_forward(15001)
    expect(page.locator('#guest-pristine')).to_be_hidden()
    assert page.evaluate('microphoneTracks.every(t => t.stopped)')
    expect(page.locator('#guest-classroom-admit')).to_be_disabled()
    page.goto('/guest')
    expect(page.locator('#guest-setup-fields')).to_be_enabled()
    assert fingerprints(refs.factory) == before and not errors
    ctx.close()


def test_browser_expiry_does_not_accept_late_renewal(s4_site, s4_browser):
    origin, refs = s4_site
    ctx, page, errors, _ = open_page(s4_browser, origin)
    page.clock.install(); admit(page)
    # Fast-forward suspended time: even if the heartbeat response succeeds,
    # it cannot extend an already expired local document.
    page.clock.fast_forward(900001)
    expect(page.locator('#guest-pristine')).to_be_hidden()
    expect(page.locator('#guest-classroom-admit')).to_be_disabled()
    assert not errors
    ctx.close()
