"""BOOK-only close controls, compact layout and event-popup acceptance on local PG."""
import json

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select

from app.models import RewardGrant, WoodchuckState
from test_age_screening import age_db
from test_book_cleanup_browser import book_db, book_site, chart_rows, ready, activate


def draft(page):
    return page.locator('#p-book-form').evaluate('''form => [...form.elements]
      .filter(e => e.matches('input, textarea, select'))
      .map(e => [e.id || e.name, e.value, e.checked ?? null])''')


@pytest.mark.parametrize('browser_name', ['chromium', 'firefox'])
def test_compact_controls_close_navigation_and_success_popup(book_site, tmp_path, browser_name):
    origin, factory, emails = book_site
    original = chart_rows(factory)
    evidence = {'browser': browser_name, 'layouts': [], 'panels': [], 'errors': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome') if browser_name == 'chromium' else pw.firefox.launch()
        context = browser.new_context(base_url=origin, has_touch=True, reduced_motion='reduce')
        context.add_init_script('''window.mediaRequests = 0;
          navigator.mediaDevices.getUserMedia = async () => { window.mediaRequests++; throw Error('Not permitted from BOOK'); };
          navigator.clipboard.writeText = async () => {};''')
        assert context.request.post('/account/login', form={'woodchuck_id':'WC-AGE-A','pin':'2468'}, headers={'Origin':origin}).status == 200
        # Successful sign-in grants the existing daily login reward. BOOK actions
        # must be compared with the authenticated economy, not the pre-login seed.
        with factory() as session:
            credits = session.get(WoodchuckState, 1).state_json['progress']['credits']
            rewards = list(session.execute(select(RewardGrant.__table__)))
        evidence['credits_after_login'] = credits
        page = context.new_page(); page.set_default_timeout(12000)
        page.on('pageerror', lambda error: evidence['errors'].append(str(error)))
        page.goto('/p-book'); ready(page)
        expect(page.locator('.ww-character-reaction')).to_have_count(0)
        values = page.locator('.practice-total-cards strong').all_text_contents()
        assert 'Totals use saved charts.' not in page.locator('body').inner_text()
        assert page.locator('.p-book-method-or').count() == 0
        close_book = page.get_by_role('link', name='Close BOOK and return to SHED')
        expect(close_book).to_have_attribute('href', '/home')
        expect(close_book).to_have_text('×')
        geometry = '''() => Object.fromEntries(['.p-book-return', '#practice-timer-toggle-btn',
          '#practice-timer-display', '.p-book-pristine', '.p-book-methods', '#p-book-minutes']
          .map(s => [s, document.querySelector(s).getBoundingClientRect().toJSON()]))'''
        for width in (1440, 390, 320):
            page.set_viewport_size({'width':width,'height':900})
            page.evaluate('scrollTo(0, 0)'); page.mouse.move(0, 0)
            rects = page.evaluate(geometry)
            button, clock, pristine, close = [rects[s] for s in ('#practice-timer-toggle-btn', '#practice-timer-display', '.p-book-pristine', '.p-book-return')]
            assert button['right'] <= clock['left'] <= button['right'] + 10
            assert abs((button['top'] + button['bottom']) - (clock['top'] + clock['bottom'])) <= 2
            assert pristine['top'] >= max(button['bottom'], clock['bottom'])
            assert pristine['width'] == rects['.p-book-methods']['width']
            assert close['width'] >= 44 and close['height'] >= 44 and close['bottom'] <= button['top']
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            for info, tooltip in [('About Stopwatch','#stopwatch-help'), ('About Pristine P-Chart','#pristine-help')]:
                control = page.get_by_role('button', name=info, exact=True)
                assert 24 <= control.bounding_box()['width'] <= 32
                activate(control, width != 1440)
                expect(page.locator(tooltip)).to_be_visible()
                assert page.evaluate(geometry) == rects  # Help never reflows the form.
                help_box = page.locator(tooltip).bounding_box()
                assert help_box['y'] >= 0
                assert help_box['y'] + help_box['height'] <= button['top']
                assert help_box['x'] + help_box['width'] <= close['left']
                assert page.url == origin + '/p-book' and page.evaluate('mediaRequests') == 0
                expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stopwatch')
                page.keyboard.press('Escape'); expect(page.locator(tooltip)).to_be_hidden()
            page.keyboard.press('Tab'); close_book.focus()
            assert close_book.evaluate('e => getComputedStyle(e).outlineStyle') == 'solid'
            page.screenshot(path=str(tmp_path / f'stacked-controls-{width}.png'))
            evidence['layouts'].append({'width':width,'rectangles':rects,'help_no_reflow':True})

        width = 390 if browser_name == 'chromium' else 1440
        touch = browser_name == 'chromium'
        page.set_viewport_size({'width':width,'height':900})
        page.locator('#p-book-minutes').fill('9'); page.locator('#p-book-note').fill('Retained through close controls')
        page.locator('#p-book-include-team').uncheck(); page.locator('#p-book-include-contests').uncheck()
        page.locator('#p-book-verifier').select_option('1')
        activate(page.locator('.p-book-work-field summary'), touch); page.get_by_label('Scales', exact=True).check()
        before_draft = draft(page)
        page.clock.install()
        activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
        page.clock.fast_forward(65000)
        timer = page.evaluate('sessionStorage.getItem("woodshed:practice-timer-started-at")')
        for key in ('week', 'career', 'days', 'charts'):
            card = page.locator(f'[data-book-stat="{key}"]')
            card.scroll_into_view_if_needed(); card.focus()
            scroll = page.evaluate('scrollY')
            activate(card, touch)
            dialog = page.locator('#p-book-stat-dialog'); expect(dialog).to_be_visible()
            page.wait_for_function('document.querySelector("[data-book-stat-status]").textContent === ""')
            x = dialog.get_by_role('button', name='Close chart'); expect(x).to_have_text('×')
            expect(x).to_be_focused()
            bounds = x.bounding_box()
            assert bounds['width'] == pytest.approx(44) and bounds['height'] == pytest.approx(44)
            page.screenshot(path=str(tmp_path / f'panel-{key}-{width}.png'))
            activate(x, touch); expect(dialog).to_be_hidden(); expect(card).to_be_focused()
            assert abs(page.evaluate('scrollY') - scroll) <= 1
            assert draft(page) == before_draft
            assert page.evaluate('sessionStorage.getItem("woodshed:practice-timer-started-at")') == timer
            expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
            assert page.locator('.practice-total-cards strong').all_text_contents() == values
            evidence['panels'].append({'key':key,'x_closes_only_panel':True,'focus_scroll_draft_timer_retained':True})
        page.locator('.practice-total-cards').scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / f'clean-statistics-{width}.png'))
        for _ in range(2):
            # The second return has no new input event: the close action must keep
            # the restored draft as well as the existing running-timer timestamp.
            activate(close_book, touch); page.wait_for_url(origin + '/home')
            page.goto('/p-book'); ready(page)
            assert draft(page) == before_draft
            expect(page.locator('#practice-timer-toggle-btn')).to_have_text('Stop Stopwatch')
            assert page.evaluate('sessionStorage.getItem("woodshed:practice-timer-started-at")') == timer
        assert chart_rows(factory) == original
        with factory() as session:
            assert session.get(WoodchuckState, 1).state_json['progress']['credits'] == credits
            assert list(session.execute(select(RewardGrant.__table__))) == rewards
            evidence['credits_after_shed_navigation'] = credits
            evidence['reward_grants_after_shed_navigation'] = [dict(r._mapping) for r in rewards]
        page.once('dialog', lambda dialog: dialog.dismiss())
        activate(page.locator('#practice-timer-toggle-btn'), touch)
        expect(page.locator('#p-book-minutes')).to_have_value('9')

        # Test the real event popup after persistence, with existing mute/volume
        # settings. Failure and an idempotent replay must never emit it.
        page.evaluate('''() => {
          window.reactionEvents = 0;
          new MutationObserver(records => { for (const r of records) for (const node of r.addedNodes)
            if (node.classList?.contains('ww-character-reaction')) window.reactionEvents++;
          }).observe(document.body, {childList:true});
        }''')
        activate(page.locator('#sound-effects-button'), touch)
        page.locator('#sound-effects-enabled').uncheck()
        page.locator('#sound-effects-volume').fill('20')
        activate(page.locator('#sound-effects-button'), touch)
        page.route('**/practice-charts', lambda route: route.fulfill(status=503, json={'detail':'Synthetic unavailable save'})
                   if route.request.method == 'POST' else route.continue_())
        activate(page.locator('#submit-p-chart-btn'), touch)
        activate(page.locator('#p-book-confirm-submit'), touch)
        expect(page.locator('#p-book-error')).not_to_be_empty()
        assert page.evaluate('reactionEvents') == 0 and chart_rows(factory) == original
        assert draft(page) == before_draft
        page.unroute('**/practice-charts')
        activate(page.locator('#submit-p-chart-btn'), touch)
        with page.expect_response(lambda r: r.url == origin + '/practice-charts' and r.request.method == 'POST') as saved:
            activate(page.locator('#p-book-confirm-submit'), touch)
        assert saved.value.json()['created'] is True
        popup = page.locator('.ww-character-reaction'); expect(popup).to_be_visible()
        expect(popup.locator('img')).to_have_attribute('src', '/static/img/characters/whistler.png')
        page.wait_for_function('document.querySelector(".ww-character-reaction img").naturalWidth > 0')
        expect(page.locator('#sound-effects-enabled')).not_to_be_checked()
        expect(page.locator('#sound-effects-volume')).to_have_value('20')
        assert page.evaluate('reactionEvents') == 1
        for capture_width in (1440, 390):
            page.set_viewport_size({'width':capture_width,'height':900})
            page.screenshot(path=str(tmp_path / f'whistler-popup-over-book-{capture_width}.png'))
        page.set_viewport_size({'width':width,'height':900})
        popup_draft = draft(page)
        page.keyboard.press('Tab'); popup.locator('button').focus()
        assert popup.locator('button').evaluate('e => getComputedStyle(e).outlineStyle') == 'solid'
        page.clock.fast_forward(30000); expect(popup).to_be_visible()  # No new auto-dismiss.
        if touch: popup.locator('button').tap()
        else: page.keyboard.press('Escape')
        expect(popup).to_have_count(0)
        assert draft(page) == popup_draft
        page.locator('#p-book-minutes').fill('9'); page.locator('#p-book-note').fill('Retained through close controls')
        page.locator('#p-book-include-team').uncheck(); page.locator('#p-book-include-contests').uncheck()
        page.locator('#p-book-verifier').select_option('1')
        page.route('**/practice-charts', lambda route: route.continue_(post_data=saved.value.request.post_data)
                   if route.request.method == 'POST' else route.continue_())
        activate(page.locator('#submit-p-chart-btn'), touch)
        with page.expect_response(lambda r: r.url == origin + '/practice-charts' and r.request.method == 'POST') as duplicate:
            activate(page.locator('#p-book-confirm-submit'), touch)
        assert duplicate.value.json()['created'] is False
        expect(page.locator('#p-book-feedback')).to_contain_text('already saved')
        assert page.evaluate('reactionEvents') == 1
        expect(popup).to_have_count(0)
        after = chart_rows(factory)
        assert len(after) == len(original) + 1 and {key:after[key] for key in original} == original
        assert len(emails) == 1
        with factory() as session:
            assert session.get(WoodchuckState, 1).state_json['progress']['credits'] == credits
            assert list(session.execute(select(RewardGrant.__table__))) == rewards
        assert evidence['errors'] == []
        evidence.update({'unchanged_original_records':True,'new_charts':1,'failed_save_popups':0,
                         'created_save_popups':1,'duplicate_popups':0,'rewards_unchanged':True,
                         'mute_volume_preserved':True,'book_x_draft_timer_roundtrips':2})
        (tmp_path / 'refinement-results.json').write_text(json.dumps(evidence, indent=2, default=str))
        context.close(); browser.close()
