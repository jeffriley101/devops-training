from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.content import PRACTICE_DEFINITION


ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "templates" / "store.html"
JS = ROOT / "static" / "js" / "app.js"
CSS = ROOT / "static" / "css" / "styles.css"


def shop_markup() -> str:
    return STORE.read_text(encoding="utf-8")


def test_shop_is_one_viking_scene_with_balanced_control_columns() -> None:
    markup = shop_markup()
    assert 'class="shop-scene artwork-scene shop-artwork-scene"' in markup
    assert '/static/img/shop-viking-valley-fair.png' in markup
    assert '/static/img/shop3.png' not in markup
    assert markup.count('data-scene-cell=') == 10
    assert markup.index('id="shop-hotspots"') < markup.index('<dialog')
    assert 'data-presentation-only' in markup
    assert 'data-student-woodchuck' in markup
    assert '_your_woodchuck.html' not in markup
    assert 'shop-object-column' not in markup
    assert 'shop-donate-button' not in markup


def test_left_controls_preserve_rewards_and_community_actions() -> None:
    markup = shop_markup()
    from test_r4a_artwork import cells
    left = [a for _, a in cells(markup) if a['data-scene-cell'].startswith('L')]
    assert [a.get('data-shop-panel', a.get('id')) for a in left] == [
        'dandelion-object', 'crown', 'goat', 'artist', 'practice-room']
    assert 'id="credits-value"' in markup
    assert 'data-shop-panel="crown"' in markup
    assert 'data-shop-panel="goat"' in markup
    assert "The GOAT Tracker" in markup
    assert "{{ practice_definition }}" in markup
    assert PRACTICE_DEFINITION not in markup
    assert 'data-shop-panel="share"' in markup
    assert 'src="{{ public_site_qr }}"' in markup
    assert 'href="{{ public_site_url }}"' in markup


def test_right_controls_and_full_access_link_are_unique() -> None:
    markup = shop_markup()
    from test_r4a_artwork import cells
    right = [a for _, a in cells(markup) if a['data-scene-cell'].startswith('R')]
    assert [a.get('data-shop-panel', a.get('href')) for a in right] == [
        'gear', 'little-buddy', 'share', '/membership?as_account=student', 'practice-definition']
    assert "Spectrogram. Temporarily unavailable" in markup
    assert 'href="/practice/pristine" aria-label="Open Pristine Practice"' in markup
    assert "Clothing Shelf, coming soon" not in markup
    assert "Gear Shelf, coming soon" not in markup
    assert "direct file upload" not in markup
    assert 'href="/membership?as_account=student"' in markup
    assert markup.count('aria-label="Premium"') == 1


def test_shop_dialogs_and_keyboard_focus_behavior_are_wired() -> None:
    markup = shop_markup()
    javascript = JS.read_text(encoding="utf-8")
    assert markup.count("<dialog") == 2
    assert markup.count('class="shop-feature-dialog') == 2
    assert 'aria-labelledby="shop-dialog-title"' in markup
    for label in (
        "Open Crown Progress", "Open The GOAT Tracker", "Open Practice Definition",
        "Share Woodshed", "Open Gear Shelf", "Open Little Buddy Shelf",
        "Open Practice Room", "Open Artist instructions", "Premium",
    ):
        assert f'aria-label="{label}' in markup
    assert "dialog.showModal()" in javascript
    assert "dialog.close()" in javascript
    assert "title.focus" in javascript
    assert "activator.focus" in javascript
    assert 'dialog.addEventListener("close"' in javascript
    assert 'aria-live="polite"' in markup
    shop_wiring = javascript[javascript.index("function wireShopPolish"):javascript.index("function wirePBook")]
    # Purchases may cache the server's balance/revision, but must not submit
    # generic browser state or calculate their own authoritative deduction.
    authoritative_cache = 'stateApi.saveState(currentState, { sync: false });'
    assert authoritative_cache in shop_wiring
    assert 'stateApi.applyEconomy(currentState, payload)' in shop_wiring
    assert "saveState" not in shop_wiring.replace(authoritative_cache, "")
    assert 'fetch("/store/catalog"' in shop_wiring
    assert 'fetch("/store/inventory"' in shop_wiring
    assert 'fetch("/store/purchases"' in shop_wiring


def test_share_uses_one_canonical_url_and_accessible_qr(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://woodshed.example/public/?profile=private#session")
    monkeypatch.setattr(main, "qr_data_uri", lambda value: captured.append(value) or "data:image/svg+xml;base64,SAFE")
    response = TestClient(main.app).get("/store")
    assert response.status_code == 200
    assert captured == ["https://woodshed-woodchuck.onrender.com/"]
    assert "profile=private" not in response.text and "#session" not in response.text
    assert "QR code for the public Woodshed Woodchuck website" in response.text
    javascript = JS.read_text(encoding="utf-8")
    assert 'navigator.clipboard.writeText(address)' in javascript
    assert 'Website address copied.' in javascript


def test_artist_email_is_fixed_public_project_address(monkeypatch) -> None:
    monkeypatch.setenv("ART_SUBMISSION_EMAIL", "private@example.org?bcc=other@example.org")
    configured = TestClient(main.app).get("/store").text
    assert 'href="mailto:support@woodshedwoodchuck.com">Email Woodshed Support</a>' in configured
    assert "Email artwork instructions" not in configured
    assert "mailto:woodshedwoodchuck@gmail.com" not in configured
    assert "Artwork email coming soon." not in configured
    assert "private@example.org" not in configured and "bcc=" not in configured
    assert "We’d love to see your artwork of a woodchuck, the Viking Sax, or anything fun! Please ask an adult before emailing your artwork. You can also email us with questions, concerns, comments, or support requests." in configured


def test_mobile_css_avoids_fixed_width_overflow() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "@media (max-width: 430px)" in css
    assert ".shop-page { width: 100%; min-width: 0; }" in css
    assert "max-width: calc(100vw - 1.5rem)" in css
    assert "width: min(100%, 760px)" in css


def test_mobile_shop_keeps_both_vertical_columns_over_the_scene() -> None:
    css = (ROOT / 'static/css/scene-hotspots.css').read_text()
    assert 'grid-template-columns: repeat(2, 50%)' in css
    assert 'grid-template-rows: repeat(5, 20%)' in css
    assert 'gap: 0' in css
    assert 'object-fit: contain' in css
    assert '100dvh - var(--room-nav-height)' in css
    assert 'safe-area-inset-bottom' in css
    assert 'data-scene-cell="L5"' in shop_markup()
    assert 'data-scene-cell="R5"' in shop_markup()


def test_shop_dandelion_balance_reveal_is_maintained_application_behavior() -> None:
    markup = shop_markup()
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    wiring = javascript[
        javascript.index("function wireShopDandelionBalance"):
        javascript.index("function wireShopPolish")
    ]

    assert 'id="dandelion-object"' in markup
    assert 'id="credits-value" hidden' in markup
    count_rule = css[css.index(".shop-dandelion-count {"):css.index(
        "#dandelion-object {"
    )]
    assert "display: none" in count_rule
    assert 'control.addEventListener("click", revealBalance)' in wiring
    assert 'control.addEventListener("keydown"' in wiring
    assert 'burst.className = "shop-dandelion-balance-burst"' in wiring
    assert 'burst.textContent = count.textContent.trim() || "0"' in wiring
    assert 'new CustomEvent("woodshed:celebrate")' in wiring
    assert "}, 1400);" in wiring

    burst = css[css.index(".shop-dandelion-balance-burst {"):css.index(
        "@keyframes shop-dandelion-balance-fly"
    )]
    assert "position: fixed" in burst
    assert "left: 50%" in burst and "top: 48%" in burst
    assert "1250ms" in burst
    assert "scale(0.35)" in burst
    keyframes = css[css.index("@keyframes shop-dandelion-balance-fly"):]
    assert "scale(0.85)" in keyframes
    assert "scale(1.25)" in keyframes
    assert "scale(1.8)" in keyframes
    assert "TEMP SHED" not in css + javascript
