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
    assert 'class="shop-scene"' in markup
    assert '/static/img/shop3.png' in markup
    assert markup.count("shop-object-column-left") == 1
    assert markup.count("shop-object-column-right") == 1
    assert markup.index("shop-object-column-left") < markup.index("shop-object-column-right")
    assert markup.index("shop-feature-dialog") > markup.index("</div>\n\n  <dialog")
    assert "Share the Woodshed" not in markup
    assert "Open the Woodshed website" not in markup
    assert "Your Permanent Crown" not in markup
    assert "shop-share-card" not in markup
    assert "shop-donate-button" not in markup


def test_left_controls_preserve_rewards_and_community_actions() -> None:
    markup = shop_markup()
    controls = ["🌼", "👑", "🐐", "📬", "💝"]
    assert [markup.index(item) for item in controls] == sorted(markup.index(item) for item in controls)
    assert 'id="credits-value"' in markup
    assert 'data-shop-panel="crown"' in markup
    assert 'data-shop-panel="goat"' in markup
    assert "The GOAT Tracker" in markup
    assert "{{ practice_definition }}" in markup
    assert PRACTICE_DEFINITION not in markup
    assert 'data-shop-panel="share"' in markup
    assert 'src="{{ public_site_qr }}"' in markup
    assert 'href="{{ public_site_url }}"' in markup


def test_right_controls_and_donation_link_are_unique() -> None:
    markup = shop_markup()
    controls = ["🎒", "🐛", "🔗", "🚪", "🗿"]
    assert [markup.index(item) for item in controls] == sorted(markup.index(item) for item in controls)
    assert "Open Spectrogram" in markup
    assert "Pristine P-Chart — Coming Soon" in markup
    assert "Clothing Shelf, coming soon" not in markup
    assert "Gear Shelf, coming soon" not in markup
    assert "direct file upload" not in markup
    assert markup.count("venmo.com/u/jeffriley101") == 1
    assert markup.count('aria-label="Donate"') == 1


def test_shop_dialogs_and_keyboard_focus_behavior_are_wired() -> None:
    markup = shop_markup()
    javascript = JS.read_text(encoding="utf-8")
    assert markup.count("<dialog") == 2
    assert markup.count('class="shop-feature-dialog') == 2
    assert 'aria-labelledby="shop-dialog-title"' in markup
    for label in (
        "Open Crown Progress", "Open The GOAT Tracker", "Open Practice Definition",
        "Share Woodshed", "Open Gear Shelf", "Open Little Buddy Shelf",
        "Open Practice Room", "Open Artist instructions", "Donate",
    ):
        assert f'aria-label="{label}' in markup
    assert "dialog.showModal()" in javascript
    assert "dialog.close()" in javascript
    assert "title.focus" in javascript
    assert "activator.focus" in javascript
    assert 'dialog.addEventListener("close"' in javascript
    assert 'aria-live="polite"' in markup
    shop_wiring = javascript[javascript.index("function wireShopPolish"):javascript.index("function wirePBook")]
    assert "saveState" not in shop_wiring
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
    assert "mailto:woodshedwoodchuck@gmail.com?subject=Woodshed%20Woodchuck%20Artwork" in configured
    assert "Artwork email coming soon." not in configured
    assert "private@example.org" not in configured and "bcc=" not in configured
    assert "The Viking Sax would love to see your artwork of a woodchuck, the Viking Sax, or anything fun (please ask an adult before emailing your artwork). And feel free to email questions, concerns, and comments about this app, too." in configured


def test_mobile_css_avoids_fixed_width_overflow() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "@media (max-width: 430px)" in css
    assert ".shop-page { width: 100%; min-width: 0; }" in css
    assert "max-width: calc(100vw - 1.5rem)" in css
    assert "width: min(100%, 760px)" in css


def test_mobile_shop_keeps_both_vertical_columns_over_the_scene() -> None:
    css = CSS.read_text(encoding="utf-8")
    markup = shop_markup()
    mobile_start = css.index("@media (max-width: 430px)", css.index("/* SHOP */"))
    mobile = css[mobile_start:css.index("@media (max-width: 640px)", mobile_start)]

    assert "display: block" in mobile
    assert ".shop-object-column {" in mobile
    assert "position: absolute" in mobile
    assert "display: flex" in mobile
    assert "flex-direction: column" in mobile
    assert "top: .65rem" in mobile
    assert "bottom: .65rem" in mobile
    assert "justify-content: space-between" in mobile
    assert "translateY(-50%)" not in mobile
    assert ".shop-object-column-left { left: .35rem; }" in mobile
    assert ".shop-object-column-right { right: .35rem; }" in mobile
    assert "flex-direction: row" not in mobile
    assert "display: none" not in mobile
    assert "width: 100%" not in mobile
    assert "overflow-x" not in mobile
    assert "min-height: 44px" in mobile

    left = markup[markup.index("shop-object-column-left"):markup.index("shop-object-column-right")]
    right_start = markup.index("shop-object-column-right")
    right = markup[
        right_start:markup.index("</div>\n  </div>", right_start)
    ]
    for control in ("🌼", "👑", "🐐", "📬", "💝"):
        assert control in left
    for control in ("🎒", "🐛", "🔗", "🚪", "🗿"):
        assert control in right
    assert 'class="shop-dandelion-count"' in left
    assert 'aria-label="Shop rewards and community"' in markup
    assert 'aria-label="Shop shelves, sharing, and rooms"' in markup
