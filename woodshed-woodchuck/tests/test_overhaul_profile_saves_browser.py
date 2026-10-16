"""Real server commits with delayed browser responses, using disposable accounts."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright
from test_r4_browser import SERVER

ROOT = Path(__file__).resolve().parents[1]
FORMS = {'appearance': '#woodchuck-editor-form', 'level': '#change-level-form'}
PATHS = {'appearance': '/account/appearance', 'level': '/account/profile/level', 'state': '/account/state'}


@pytest.fixture
def save_site(tmp_path):
    source = SERVER.replace('    session.commit()',
        '    from app.seasons import bootstrap_canonical_seasons\n    bootstrap_canonical_seasons(session)\n    session.commit()')
    (tmp_path / 'local_app.py').write_text(source)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    database = tmp_path / 'local.db'
    env = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'PYTHONPATH': str(ROOT),
        'DATABASE_URL': 'sqlite:///' + str(database), 'SESSION_SECRET': 'profile-save-disposable-test-secret',
        'SESSION_COOKIE_SECURE': 'false', 'LOGIN_RATE_LIMIT_MODE': 'off',
        'LOGIN_RATE_LIMIT_REQUIRED': 'false', 'PYTHONDONTWRITEBYTECODE': '1'}
    with (tmp_path / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'local_app:app',
            '--app-dir', str(tmp_path), '--host', '127.0.0.1', '--port', str(port),
            '--no-proxy-headers', '--timeout-graceful-shutdown', '2'], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            for _ in range(600):
                try:
                    with urlopen(origin + '/guest', timeout=2):
                        break
                except URLError:
                    assert server.poll() is None, (tmp_path / 'server.log').read_text()
                    time.sleep(.05)
            else:
                pytest.fail('Disposable test server did not start')
            yield origin, database
        finally:
            server.terminate()
            server.wait(timeout=15)


def stored(database, account='WC-GUEST-A'):
    with sqlite3.connect(database) as db:
        row = db.execute('select p.level,p.instrument,s.revision,s.state_json from woodchuck_profiles p '
            'join woodchuck_states s on s.profile_id=p.id where p.woodchuck_id=?', (account,)).fetchone()
    return {'level': row[0], 'instrument': row[1], 'revision': row[2], 'state': json.loads(row[3])}


class Journey:
    def __init__(self, context, site, evidence):
        self.context = context
        self.origin, self.database = site
        self.evidence = evidence
        self.page = context.new_page()
        self.page.set_default_timeout(15000)
        self.requests, self.responses, self.dialogs, self.errors, self.navigations = [], [], [], [], []
        self.discard = False
        self.page.on('request', lambda r: self.requests.append({'path': r.url.removeprefix(self.origin), 'method': r.method}))
        self.page.on('response', lambda r: self.responses.append({'path': r.url.removeprefix(self.origin), 'status': r.status, 'method': r.request.method}))
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('framenavigated', lambda frame: self.navigations.append(frame.url) if frame == self.page.main_frame else None)
        self.page.on('dialog', self.answer)
        assert context.request.post(self.origin + '/account/login',
            form={'woodchuck_id': 'WC-GUEST-A', 'pin': '2468'}, headers={'Origin': self.origin}).status == 200
        self.room(self.page)
        self.page.locator('#instrument-object').click()
        self.page.wait_for_function('WWSurfaces.current()?.id === "your-woodchuck"')
        self.page.locator('#woodchuck-instrument').select_option('Trumpet')
        self.page.locator('#woodchuck-hoodie').select_option('red')
        self.page.locator('#change-level-select').select_option('Intermediate')

    def answer(self, dialog):
        self.dialogs.append(dialog.message)
        if dialog.type == 'confirm' and not self.discard:
            dialog.dismiss()
        else:
            dialog.accept()

    def room(self, page):
        page.goto(self.origin + '/home')
        page.wait_for_function('window.WWWorldEntry && window.WWAccountSync && !document.querySelector(".app-shell").hidden')
        page.evaluate('async () => { await WWWorldEntry.arrivalReady; await WWAccountSync.syncNow(); }')
        if page.evaluate('WWSurfaces.current()?.id === "xp-panel"'):
            page.keyboard.press('Escape')
            page.wait_for_function('!WWSurfaces.current()')

    def count(self, kind):
        method = 'PUT' if kind == 'state' else 'PATCH'
        return sum(r == {'path': PATHS[kind], 'method': method} for r in self.requests)

    def wait(self, condition):
        deadline = time.monotonic() + 15
        while not condition():
            assert time.monotonic() < deadline, 'Timed out waiting for test-held response'
            self.page.wait_for_timeout(25)

    def hold(self, kind):
        held = []
        def intercept(route):
            if held:
                route.continue_()
                return
            response = route.fetch()  # Real server mutation commits before its response is held.
            assert response.status == 200
            held.append((route, response))
        self.page.route('**' + PATHS[kind], intercept)
        return held

    def release(self, held):
        held[0][0].fulfill(response=held[0][1])

    def submit(self, kind):
        self.page.locator(FORMS[kind] + ' button[type="submit"]').click()

    def blocked(self):
        assert self.page.locator('#your-woodchuck').get_attribute('data-busy') == 'true'
        for form in FORMS.values():
            assert self.page.locator(form + ' button[type="submit"]').is_disabled()
        before = [self.count(kind) for kind in ['appearance', 'level', 'state']]
        # requestSubmit bypasses a disabled submit button; handlers must also guard.
        self.page.evaluate('''() => {
            for (const id of ['woodchuck-editor-form', 'change-level-form']) {
                const form = document.getElementById(id);
                form.requestSubmit(); form.requestSubmit();
                form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
                form.querySelector('button[type="submit"]').click();
            }
        }''')
        self.page.keyboard.press('Enter')
        self.page.keyboard.press('Enter')
        self.page.keyboard.press('Escape')
        self.page.evaluate('history.back()')
        self.page.wait_for_timeout(150)
        assert self.page.locator('#your-woodchuck').evaluate('(node) => node.open')
        assert [self.count(kind) for kind in ['appearance', 'level', 'state']] == before
        self.selected()

    def selected(self):
        assert self.page.locator('#woodchuck-instrument').input_value() == 'Trumpet'
        assert self.page.locator('#woodchuck-hoodie').input_value() == 'red'
        assert self.page.locator('#change-level-select').input_value() == 'Intermediate'

    def success(self, kind):
        expression = ('document.querySelector("#woodchuck-editor-status").textContent.includes("saved")'
            if kind == 'appearance' else 'document.querySelector("#change-level-feedback").textContent.includes("successfully")')
        self.page.wait_for_function(expression)
        self.page.wait_for_function('document.querySelector("#your-woodchuck").dataset.busy !== "true"')
        for form in FORMS.values():
            assert self.page.locator(form + ' button[type="submit"]').is_enabled()
        self.selected()

    def dirty_sibling(self):
        before = len(self.dialogs)
        self.page.keyboard.press('Escape')
        assert self.dialogs[before:] == ['Discard unsaved changes?']
        assert self.page.locator('#your-woodchuck').evaluate('(node) => node.open')
        self.selected()

    def agreement(self, page=None, account='WC-GUEST-A', level='Intermediate', instrument='Trumpet'):
        page = page or self.page
        authoritative = stored(self.database, account)
        client = page.evaluate('WWState.getState()')
        assert authoritative['level'] == authoritative['state']['profile']['level'] == client['profile']['level'] == level
        assert authoritative['instrument'] == authoritative['state']['profile']['instrument'] == client['profile']['instrument'] == instrument
        assert client['account']['woodchuckId'] == account
        assert client['account']['serverRevision'] == authoritative['revision']
        assert page.locator('#level-value').inner_text() == level
        assert client.get('appearance') == authoritative['state'].get('appearance')
        return authoritative

    def finish(self):
        self.agreement()
        # Opening/closing a surface changes history without loading a document.
        assert sum(r == {'path': '/home', 'method': 'GET'} for r in self.requests) == 1, self.navigations
        assert not any(r['status'] == 409 for r in self.responses)
        assert set(self.dialogs) <= {'Discard unsaved changes?'}, self.dialogs
        assert self.errors == []
        self.page.screenshot(path=str(self.evidence / 'saved.png'))
        before = len(self.dialogs)
        self.page.keyboard.press('Escape')
        self.page.wait_for_function('!WWSurfaces.current()')
        assert len(self.dialogs) == before, 'Saved forms must have clean checkpoints'
        assert self.page.locator('#instrument-object').evaluate('(node) => document.activeElement === node')
        self.room(self.page)  # Intentional reload: persistence, not only current UI.
        self.agreement()


@pytest.fixture(params=['chrome', 'firefox'])
def journey(save_site, tmp_path, request):
    evidence = Path(os.getenv('WW_OVERHAUL_EVIDENCE_DIR', str(tmp_path))) / 'save-coordination' / request.node.name
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome') if request.param == 'chrome' else p.firefox.launch()
        context = browser.new_context(viewport={'width':390, 'height':844}, reduced_motion='reduce', base_url=save_site[0])
        j = Journey(context, save_site, evidence)
        try:
            yield j
        finally:
            (evidence / 'results.json').write_text(json.dumps({'browser': browser.version,
                'requests': j.requests, 'responses': j.responses, 'dialogs': j.dialogs,
                'errors': j.errors, 'navigations': j.navigations,
                'authoritative_A': stored(j.database), 'authoritative_B': stored(j.database, 'WC-GUEST-B')}, indent=2))
            context.close()
            browser.close()


@pytest.mark.parametrize('first', ['appearance', 'level'])
def test_delayed_committed_response_serializes_both_forms(journey, first):
    j = journey
    patch, state = j.hold(first), j.hold('state')
    j.submit(first)
    j.wait(lambda: bool(patch))
    j.blocked()
    j.page.screenshot(path=str(j.evidence / 'pending.png'))
    j.release(patch)
    if first == 'level':
        j.wait(lambda: bool(state))
        assert 'successfully' not in j.page.locator('#change-level-feedback').inner_text()
        j.blocked()
        j.release(state)
    j.success(first)
    j.dirty_sibling()
    second = 'level' if first == 'appearance' else 'appearance'
    j.submit(second)
    if second == 'level':
        j.wait(lambda: bool(state))
        assert 'successfully' not in j.page.locator('#change-level-feedback').inner_text()
        j.blocked()
        j.release(state)
    j.success(second)
    assert j.count('appearance') == j.count('level') == 1
    j.finish()


@pytest.mark.parametrize('failure', ['appearance', 'level', 'state'])
def test_failure_releases_forms_retains_drafts_and_recovers_on_explicit_save(journey, failure):
    j = journey
    calls = []
    def fail_once(route):
        calls.append(route.request.method)
        if len(calls) == 1:
            route.fulfill(status=503, content_type='application/json', body='{"detail":"Synthetic unavailable"}')
        else:
            route.continue_()
    j.page.route('**' + PATHS[failure], fail_once)
    kind = 'appearance' if failure == 'appearance' else 'level'
    j.submit(kind)
    j.page.wait_for_function('document.querySelector("#your-woodchuck").dataset.busy !== "true"')
    assert calls == ['PATCH' if failure != 'state' else 'PUT']
    feedback = j.page.locator('#woodchuck-editor-status' if kind == 'appearance' else '#change-level-feedback').inner_text()
    assert 'successfully' not in feedback and 'Your Woodchuck is saved' not in feedback
    if failure == 'state':
        assert 'could not synchronize' in feedback
        assert stored(j.database)['level'] == 'Intermediate'
        assert stored(j.database)['state']['profile']['level'] == 'Beginner'
    for form in FORMS.values():
        assert j.page.locator(form + ' button[type="submit"]').is_enabled()
    j.dirty_sibling()
    j.page.wait_for_timeout(600)
    assert len(calls) == 1, 'No automatic retry after a failure'
    j.submit(kind)
    j.success(kind)
    if failure == 'state':
        assert j.count('level') == 1, 'Synchronization recovery must not repeat the cooldown-protected PATCH'
    else:
        assert j.count(kind) == 2
    other = 'level' if kind == 'appearance' else 'appearance'
    j.submit(other)
    j.success(other)
    j.finish()


@pytest.mark.parametrize('stage', ['appearance', 'level', 'state'])
def test_account_switch_rejects_pending_response_and_releases_busy_state(journey, stage):
    j = journey
    held = j.hold(stage)
    j.submit('appearance' if stage == 'appearance' else 'level')
    j.wait(lambda: bool(held))
    j.blocked()
    next_page = j.context.new_page()
    next_page.goto(j.origin + '/')
    next_page.wait_for_function('window.WWSessionBoundary')
    result = next_page.evaluate('''async () => {
        const logout = await fetch('/account/logout', {method:'POST'});
        const login = await fetch('/account/login', {method:'POST', body:new URLSearchParams({woodchuck_id:'WC-GUEST-B', pin:'2468'})});
        return [logout.status, login.status];
    }''')
    assert result == [200, 200]
    j.room(next_page)
    before = j.agreement(next_page, 'WC-GUEST-B', 'Beginner', 'Flute')
    assert json.loads(next_page.locator('#woodchuck-appearance-data').inner_text())['saved'] == {'hoodie': 'blue', 'hat': 'green'}
    writes = [j.count(kind) for kind in ['appearance', 'level', 'state']]
    j.release(held)
    j.page.wait_for_function('document.querySelector("#your-woodchuck").dataset.busy !== "true"')
    assert j.page.locator('.app-shell').is_hidden()
    assert 'successfully' not in j.page.locator('#change-level-feedback').inner_text()
    assert 'Your Woodchuck is saved' not in j.page.locator('#woodchuck-editor-status').inner_text()
    assert [j.count(kind) for kind in ['appearance', 'level', 'state']] == writes
    assert j.agreement(next_page, 'WC-GUEST-B', 'Beginner', 'Flute') == before
    assert 'Synthetic A' not in next_page.locator('body').inner_text()
    assert j.errors == [] and j.dialogs == []
    for form in FORMS.values():
        assert j.page.locator(form + ' button[type="submit"]').is_enabled()
    next_page.screenshot(path=str(j.evidence / 'new-account.png'))


@pytest.mark.parametrize('pending', ['scheduled', 'in_flight'])
def test_existing_state_save_drains_before_appearance_mutation(journey, pending):
    j = journey
    held = j.hold('state')
    j.page.evaluate('''() => {
        const state = WWState.getState(); state.testUnsynchronizedDraft = 'Pending test draft';
        WWState.saveState(state);
    }''')
    if pending == 'in_flight':
        j.wait(lambda: bool(held))
    j.submit('appearance')
    j.wait(lambda: bool(held))
    assert j.count('appearance') == 0, 'PATCH must await the prior state response/revision'
    j.blocked()
    j.release(held)
    j.success('appearance')
    # A disposable browser draft, rather than a server-owned profile field.
    assert stored(j.database)['state']['testUnsynchronizedDraft'] == 'Pending test draft'
    j.submit('level')
    j.success('level')
    j.finish()
    assert stored(j.database)['state']['testUnsynchronizedDraft'] == 'Pending test draft'
