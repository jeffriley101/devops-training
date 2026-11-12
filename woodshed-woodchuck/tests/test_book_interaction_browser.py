"""Interaction, full-history chart, and capture-boundary acceptance on local PG."""
from datetime import datetime, timedelta
import json

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select

from app.models import PracticeChart, WoodchuckState, RewardGrant
from test_age_screening import age_db
from test_book_cleanup_browser import book_db, book_site, chart_rows, ready, activate


@pytest.mark.parametrize('browser_name', ['chromium', 'firefox'])
def test_methods_drafts_verifiers_and_stat_charts(book_site, tmp_path, browser_name):
    origin, factory, emails = book_site
    with factory() as session:
        date = session.scalar(select(PracticeChart.practice_date).where(PracticeChart.profile_id == 1).order_by(PracticeChart.practice_date.desc()))
        for _ in range(110):
            session.add(PracticeChart(profile_id=1, practice_date=date, minutes=1, source='p-book',
                instrument='Flute', note='Additional saved history', include_contests=False, include_team_contests=False))
        session.add(PracticeChart(profile_id=1, practice_date=date - timedelta(days=75), minutes=0, source='manual',
            instrument='Flute', note='Historical zero-time chart', include_contests=False, include_team_contests=False))
        session.commit()
    original = chart_rows(factory)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome') if browser_name == 'chromium' else pw.firefox.launch()
        context = browser.new_context(base_url=origin, has_touch=True, reduced_motion='reduce')
        stopped_tracks = []
        context.expose_function("testTrackStopped", lambda: stopped_tracks.append(True))
        context.add_init_script('''
          window.copied = []; navigator.clipboard.writeText = async text => window.copied.push(text);
          window.mediaRequests = 0; window.stoppedTracks = 0;
          navigator.mediaDevices.getUserMedia = async () => {
            window.mediaRequests++;
            const stream = {bookSynthetic:true, oscillator:null};
            stream.getTracks = () => [{stop: () => {
              if (stream.oscillator) { try {stream.oscillator.stop();} catch (_) {} }
              window.stoppedTracks++; void window.testTrackStopped();
            }}];
            return stream;
          };
          if (location.pathname === '/practice/pristine') {
            const NativeContext = window.AudioContext;
            window.bookAudioContexts = [];
            window.AudioContext = class extends NativeContext {
              constructor(...args) { super(...args); window.bookAudioContexts.push(this); }
              // This headless host has no reliable Firefox audio output device.
              // Keep native context/node interfaces for the shared sound library,
              // but provide synthetic microphone samples at the capture boundary.
              resume() { return Promise.resolve(); }
              createMediaStreamSource(stream) {
                if (!stream.bookSynthetic) return super.createMediaStreamSource(stream);
                window.syntheticInputStarted = performance.now();
                return {connect() {}};
              }
              createAnalyser() {
                const analyser = super.createAnalyser();
                analyser.getFloatTimeDomainData = samples => samples.fill(
                  performance.now() - (window.syntheticInputStarted ?? performance.now()) > 2100 ? .2 : 0);
                return analyser;
              }
            };
          }
        ''')
        assert context.request.post('/account/login', form={'woodchuck_id':'WC-AGE-A','pin':'2468'}, headers={'Origin':origin}).status == 200
        all_charts = context.request.get('/practice-charts').json()['charts']
        totals = context.request.get('/practice-charts/totals').json()
        assert len(all_charts) == 124
        page = context.new_page(); page.set_default_timeout(12000)
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_viewport_size({'width':1440,'height':950}); page.goto('/p-book'); ready(page)
        for width in (1440, 390):
            page.set_viewport_size({'width':width,'height':950})
            touch = width == 390
            help_control = page.get_by_role('button', name='About Stopwatch', exact=True)
            if touch: help_control.tap()
            else: page.locator('#practice-timer-toggle-btn').hover()
            expect(page.locator('#stopwatch-help')).to_be_visible()
            expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stopwatch')
            page.screenshot(path=str(tmp_path / f'help-{width}.png'))
            page.keyboard.press('Escape'); expect(page.locator('#stopwatch-help')).to_be_hidden()
            activate(page.get_by_role('button', name='About Pristine P-Chart'), touch)
            expect(page.locator('#pristine-help')).to_be_visible()
            assert page.url == origin + '/p-book' and page.evaluate('mediaRequests') == 0
            page.keyboard.press('Escape')
            for key in ('week','career','days','charts'):
                card = page.locator(f'[data-book-stat="{key}"]')
                activate(card, touch)
                dialog = page.locator('#p-book-stat-dialog'); expect(dialog).to_be_visible()
                page.wait_for_function('document.querySelector("[data-book-stat-status]").textContent === ""')
                body = page.locator('[data-book-stat-content]')
                if key == 'week':
                    expect(body.locator('tbody tr')).to_have_count(7)
                    expected = page.evaluate('(seconds) => WWPracticeDuration.seconds(seconds)', totals['this_week_seconds'])
                    expect(body).to_contain_text(expected)
                if key == 'career':
                    expected = page.evaluate('(seconds) => WWPracticeDuration.seconds(seconds)', totals['career_seconds'])
                    expect(body.locator('tbody tr').last).to_contain_text(expected)
                if key == 'days':
                    expect(body).to_contain_text('12 practice days across all saved history.')
                    expect(body.get_by_label('Calendar month')).to_be_visible()
                if key == 'charts':
                    expect(body).to_contain_text('124 P-Charts across all saved history.')
                    assert sum(int(value) for value in body.locator('tbody td').all_text_contents()) == 124
                page.screenshot(path=str(tmp_path / f'stat-{key}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.keyboard.press('Escape'); expect(dialog).to_be_hidden(); expect(card).to_be_focused()
        assert chart_rows(factory) == original
        page.set_viewport_size({'width':390 if browser_name == 'chromium' else 1440,'height':950})
        touch = browser_name == 'chromium'
        # Use yesterday, an allowed Chicago practice date, and retain it on every return/error.
        date = page.evaluate('() => {const d = new Date(WWState.localDateKey() + "T12:00:00Z");d.setUTCDate(d.getUTCDate()-1);return d.toISOString().slice(0,10)}')
        page.locator('#p-book-date').fill(date)
        page.locator('#p-book-minutes').fill('14'); page.locator('#p-book-note').fill('Kept across practice methods')
        page.locator('#p-book-include-contests').uncheck(); page.locator('#p-book-include-team').uncheck()
        activate(page.locator('.p-book-work-field summary'), touch)
        page.get_by_label('Scales', exact=True).check()
        page.locator('#p-book-verifier').select_option('1')
        selector = page.locator('#p-book-verifier'); selector.focus()
        expect(page).to_have_url(origin + '/p-book')
        selector.press('Alt+ArrowDown')
        page.screenshot(path=str(tmp_path / 'verifier-dropdown.png'))
        page.keyboard.press('Escape')
        page.clock.install(); activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
        page.clock.fast_forward(65000)
        blocked = context.new_page(); blocked.goto('/practice/pristine')
        activate(blocked.locator('[data-pristine-start]'), touch)
        expect(blocked.locator('[data-pristine-feedback]')).to_contain_text('Another practice method is active')
        assert blocked.evaluate('mediaRequests') == 0
        blocked.close(); page.bring_to_front()
        page.locator('#p-book-verifier').select_option('add-verifier')
        page.wait_for_url(origin + '/trusted-verifiers?return_to=p-book')
        expect(page.locator('#trusted-verifier-invite-form')).to_be_visible()
        expect(page.locator('#trusted-verifier-invite-form button')).to_have_text('Add Verifier')
        page.go_back(); ready(page)
        for field, value in [('date',date),('minutes','14'),('note','Kept across practice methods'),('verifier','1')]:
            expect(page.locator('#p-book-' + field)).to_have_value(value)
        expect(page.locator('#p-book-include-contests')).not_to_be_checked()
        expect(page.locator('#p-book-include-team')).not_to_be_checked()
        expect(page.get_by_label('Scales', exact=True)).to_be_checked()
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Resume Stopwatch')
        paused = page.evaluate('JSON.parse(sessionStorage.getItem("woodshed:practice-timer-started-at")).pausedSeconds')
        assert paused >= 65
        expect(page.locator('#practice-timer-display')).to_have_text(f'{int(paused)//60:02}:{int(paused)%60:02}')
        activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
        page.once('dialog', lambda dialog: dialog.dismiss())
        activate(page.locator('.p-book-pristine'), touch)
        expect(page).to_have_url(origin + '/p-book')
        page.once('dialog', lambda dialog: dialog.accept())
        activate(page.locator('.p-book-pristine'), touch)
        page.wait_for_url(origin + '/practice/pristine')
        activate(page.locator('[data-pristine-start]'), touch)
        try:
            page.wait_for_function('document.querySelector("[data-pristine-time]").textContent >= "00:00:02"')
        except Exception:
            diagnostic = page.evaluate('''() => ({
              contexts: (window.bookAudioContexts || []).map(c => ({state:c.state,time:c.currentTime})),
              mic: document.querySelector('[data-pristine-microphone]').textContent,
              status: document.querySelector('[data-pristine-status]').textContent,
              feedback: document.querySelector('[data-pristine-feedback]').textContent,
              playing: document.querySelector('[data-pristine-time]').textContent,
              requests: window.mediaRequests, hidden: document.hidden
            })''')
            (tmp_path/'capture-diagnostic.json').write_text(json.dumps(diagnostic,indent=2))
            raise
        other = context.new_page(); other.goto('/p-book'); ready(other)
        activate(other.locator('#practice-timer-toggle-btn'), touch)
        if other.locator('#practice-timer-toggle-btn').inner_text() == 'Stopwatch':
            expect(other.locator('#practice-timer-feedback')).to_contain_text('Another practice method is active')
        else:
            # Existing Pristine background handling may stop capture before the
            # other tab starts. In that case the methods still never overlap.
            assert stopped_tracks

        other.close(); page.bring_to_front()
        page.once('dialog', lambda dialog: dialog.dismiss())
        activate(page.locator('[data-pristine-leave]'), touch)
        expect(page).to_have_url(origin + '/practice/pristine')
        page.once('dialog', lambda dialog: dialog.accept())
        activate(page.locator('[data-pristine-leave]'), touch)
        page.wait_for_url(origin + '/store')
        assert stopped_tracks
        assert chart_rows(factory) == original  # Neither method auto-saves or merges a record.
        page.goto('/p-book'); ready(page)
        expect(page.locator('#p-book-date')).to_have_value(date)
        expect(page.locator('#p-book-minutes')).to_have_value('14')
        expect(page.locator('#p-book-note')).to_have_value('Kept across practice methods')
        expect(page.locator('#p-book-verifier')).to_have_value('1')
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Resume Stopwatch')
        page.locator('#p-book-minutes').fill('0'); activate(page.locator('#submit-p-chart-btn'), touch)
        expect(page.locator('#p-book-error')).to_contain_text('positive number')
        expect(page.locator('#p-book-date')).to_have_value(date)
        expect(page.locator('#p-book-include-contests')).not_to_be_checked()
        expect(page.locator('#p-book-include-team')).not_to_be_checked()
        page.locator('#p-book-minutes').fill('14')
        activate(page.locator('#submit-p-chart-btn'), touch)
        with page.expect_response(lambda r: r.url == origin+'/practice-charts' and r.request.method == 'POST') as saved:
            activate(page.locator('#p-book-confirm-submit'), touch)
        result = saved.value.json(); request = saved.value.request.post_data_json
        assert result['created'] is True and result['chart']['minutes'] == 14
        assert request['verifier_id'] == 1 and request['practice_date'] == date
        assert request['include_contests'] is False and request['include_team_contests'] is False
        assert len(emails) == 1 and result['chart']['credits_awarded'] == 0
        expect(page.locator('.ww-character-reaction-speaker')).to_have_text('Coach Whistler')
        activate(page.locator('.ww-character-reaction-dismiss'), touch)
        expect(page.locator('#p-book-feedback')).to_contain_text('copied')
        assert len(page.evaluate('copied')) == 1
        assert len(chart_rows(factory)) == len(original) + 1
        assert {key:chart_rows(factory)[key] for key in original} == original
        (tmp_path/'interaction-results.json').write_text(json.dumps({'full_chart_count':124,'method_mutual_exclusion':True,'draft_restored':True,'checkbox_choices_preserved':True,'no_auto_save_or_merge':True,'errors':errors},indent=2))
        assert errors == []
        context.close(); browser.close()
