"""Native GIF playback and BOOK persistence checks with disposable PostgreSQL."""
import hashlib
import json
from pathlib import Path
import struct
import time

import pytest
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select

from app.models import RewardGrant, WoodchuckState
from test_age_screening import age_db
from test_book_cleanup_browser import book_db, book_site, chart_rows, ready, activate, contrast


APPROVED_SHA256 = '203909f47e7ec810670ed8f4acab898088bc16413d81c78e1a397ba5d63005b9'
ASSET = '/static/img/characters/coach_whistler_book_play_once.gif'


def test_supplied_whistler_gif_is_exact_approved_single_play_asset():
    data = (Path(__file__).resolve().parents[1] / ASSET.lstrip('/')).read_bytes()
    assert hashlib.sha256(data).hexdigest() == APPROVED_SHA256
    assert data[:6] == b'GIF89a' and struct.unpack('<HH', data[6:10]) == (252, 380)
    assert b'NETSCAPE2.0' not in data and b'ANIMEXTS1.0' not in data


@pytest.mark.parametrize('browser_name', ['chromium', 'firefox'])
@pytest.mark.parametrize('width', [1440, 390])
def test_whistler_plays_once_restarts_and_never_resubmits(book_site, tmp_path, browser_name, width):
    origin, factory, emails = book_site
    original = chart_rows(factory)
    touch = width == 390
    evidence = {'browser':browser_name, 'viewport':width, 'playbacks':[], 'page_errors':[]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome') if browser_name == 'chromium' else pw.firefox.launch()
        context = browser.new_context(base_url=origin, viewport={'width':width,'height':900}, has_touch=touch)
        context.add_init_script('navigator.clipboard.writeText = async () => {};')
        assert context.request.post('/account/login', form={'woodchuck_id':'WC-AGE-A','pin':'2468'}, headers={'Origin':origin}).status == 200
        with factory() as session:
            credits = session.get(WoodchuckState, 1).state_json['progress']['credits']
            rewards = list(session.execute(select(RewardGrant.__table__)))
        page = context.new_page(); page.set_default_timeout(12000)
        page.on('pageerror', lambda error: evidence['page_errors'].append(str(error)))
        posts = []
        page.on('request', lambda request: posts.append(request) if request.url == origin + '/practice-charts' and request.method == 'POST' else None)
        page.goto('/p-book'); ready(page)
        page.screenshot(path=str(tmp_path / 'book-before.png'))
        expect(page.locator('.ww-character-reaction')).to_have_count(0)
        page.evaluate('''() => {
          window.whistlerCalls = [];
          const play = WoodshedAudio.play;
          WoodshedAudio.play = function (name) {
            window.whistlerCalls.push({name, enabled:WoodshedAudio.isEnabled(), volume:WoodshedAudio.getVolume()});
            return play.apply(this, arguments);
          };
        }''')
        previous_end = None
        for iteration in (1, 2):
            # The first success is muted; the second uses the same saved volume.
            activate(page.locator('#sound-effects-button'), touch)
            page.locator('#sound-effects-enabled').set_checked(iteration == 2)
            page.locator('#sound-effects-volume').fill('20')
            activate(page.locator('#sound-effects-button'), touch)
            page.locator('#p-book-minutes').fill(str(7 + iteration))
            page.locator('#p-book-note').fill(f'Whistler synthetic submission {iteration}')
            page.locator('#p-book-include-contests').uncheck(); page.locator('#p-book-include-team').uncheck()
            page.locator('#p-book-verifier').select_option('1')
            activate(page.locator('#submit-p-chart-btn'), touch)
            expect(page.locator('#p-book-final-confirmation')).to_be_visible()
            page.screenshot(path=str(tmp_path / f'confirmation-{iteration}.png'))
            with page.expect_response(lambda r: r.url == origin + '/practice-charts' and r.request.method == 'POST') as saved:
                # Rapid repeated confirmation must still issue exactly one save.
                page.evaluate('''() => {
                  const confirm = document.getElementById('p-book-confirm-submit');
                  confirm.click(); confirm.click();
                }''')
            result = saved.value.json()
            assert result['created'] is True and result['chart']['credits_awarded'] == 0
            assert result['chart']['minutes'] == 7 + iteration
            assert result['chart']['verification']['status'] == 'pending'
            assert len(posts) == iteration
            popup = page.locator('.ww-character-reaction'); expect(popup).to_have_count(1)
            image = popup.locator('img')
            expect(image).to_have_attribute('src', ASSET + f'?play={result["chart"]["id"]}')
            page.wait_for_function('''() => {const i = document.querySelector('.ww-character-reaction img');
              return i?.complete && i.naturalWidth === 252 && i.naturalHeight === 380;}''')
            start = time.monotonic()
            first = image.screenshot(path=str(tmp_path / f'play-{iteration}-start.png'), animations='allow')
            assert first != previous_end  # New URL replays a cached GIF from its beginning.
            page.wait_for_timeout(max(0, 1100 - (time.monotonic() - start) * 1000))
            middle = image.screenshot(path=str(tmp_path / f'play-{iteration}-moving.png'), animations='allow')
            assert first != middle  # Real browser animation, without a mocked GIF/clock.
            page.screenshot(path=str(tmp_path / f'popup-{iteration}-moving.png'))
            page.wait_for_timeout(max(0, 6400 - (time.monotonic() - start) * 1000))
            end = image.screenshot(path=str(tmp_path / f'play-{iteration}-end.png'), animations='allow')
            page.wait_for_timeout(700)
            held = image.screenshot(path=str(tmp_path / f'play-{iteration}-held.png'), animations='allow')
            assert end == held and end != first
            previous_end = end
            expect(popup).to_be_visible()  # Animation finishes; popup waits for dismissal.
            expect(popup.locator('.ww-character-reaction-speech')).to_have_text(
                'Coach Whistler: “Whee-oo-wheet! ... Good job, Synthetic A!”')
            assert popup.evaluate('e => getComputedStyle(e).backgroundColor') == 'rgb(33, 24, 15)'
            assert popup.evaluate('e => getComputedStyle(e).opacity') == '1'
            assert contrast(popup.locator('p').evaluate('e => [getComputedStyle(e).color, getComputedStyle(e).backgroundColor]')) >= 4.5
            x = popup.get_by_role('button', name='Dismiss character reaction')
            expect(x).to_have_text('×')
            box = x.bounding_box(); assert box['width'] == pytest.approx(44) and box['height'] == pytest.approx(44)
            assert box['x'] + box['width'] <= width and box['y'] >= 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(tmp_path / f'popup-{iteration}-finished.png'))
            assert page.evaluate('WoodshedAudio.isEnabled()') == (iteration == 2)
            assert page.evaluate('WoodshedAudio.getVolume()') == pytest.approx(.2)
            before_dismiss = chart_rows(factory)
            if iteration == 1:
                activate(x, touch)
            elif touch:
                popup.tap(position={'x':8,'y':250})  # Existing background/card dismissal.
            else:
                x.focus(); page.keyboard.press('Escape')
            expect(popup).to_have_count(0)
            assert chart_rows(factory) == before_dismiss
            evidence['playbacks'].append({'chart_id':result['chart']['id'],'src':ASSET + f'?play={result["chart"]["id"]}',
                'sample_sha256':{k:hashlib.sha256(v).hexdigest() for k,v in [('start',first),('moving',middle),('end',end),('held',held)]},
                'single_play_final_frame_held':True,'muted':iteration == 1})

        assert evidence['playbacks'][0]['src'] != evidence['playbacks'][1]['src']
        for playback in evidence['playbacks']:
            response = context.request.get(playback['src'])
            assert response.status == 200 and response.headers['content-type'] == 'image/gif'
            assert hashlib.sha256(response.body()).hexdigest() == APPROVED_SHA256

        # A UI retry of the same persisted payload is a server-side idempotent replay.
        page.locator('#p-book-minutes').fill('9'); page.locator('#p-book-note').fill('Whistler synthetic submission 2')
        page.locator('#p-book-include-contests').uncheck(); page.locator('#p-book-include-team').uncheck()
        page.route('**/practice-charts', lambda route: route.continue_(post_data=saved.value.request.post_data)
                   if route.request.method == 'POST' else route.continue_())
        activate(page.locator('#submit-p-chart-btn'), touch)
        with page.expect_response(lambda r: r.url == origin + '/practice-charts' and r.request.method == 'POST') as replay:
            activate(page.locator('#p-book-confirm-submit'), touch)
        assert replay.value.json()['created'] is False
        expect(page.locator('.ww-character-reaction')).to_have_count(0)
        expect(page.locator('#p-book-feedback')).to_contain_text('already saved')
        calls = page.evaluate('whistlerCalls')
        whistles = [c for c in calls if c['name'] == 'characterWhistle']
        assert len(whistles) == 2 and [c['enabled'] for c in whistles] == [False, True]
        assert all(c['volume'] == pytest.approx(.2) for c in whistles)
        assert len([c for c in calls if c['name'] == 'pChartSubmitted']) == 2
        after = chart_rows(factory)
        assert len(after) == len(original) + 2 and {key:after[key] for key in original} == original
        assert len(emails) == 2
        with factory() as session:
            assert session.get(WoodchuckState, 1).state_json['progress']['credits'] == credits
            assert list(session.execute(select(RewardGrant.__table__))) == rewards
        assert evidence['page_errors'] == []
        evidence.update({'new_saved_charts':2,'save_posts_including_replay':len(posts), 'verification_email_calls':2,
                         'credits_rewards_unchanged':True,'whistle_calls':whistles,'prior_records_unchanged':True})
        (tmp_path / 'whistler-results.json').write_text(json.dumps(evidence,indent=2))
        context.close(); browser.close()
