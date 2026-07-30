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
    assert '/static/img/sax-viking-portrait.png' in markup
    assert markup.count("shop-object-column-left") == 1
    assert markup.count("shop-object-column-right") == 1
    assert markup.index("shop-object-column-left") < markup.index("shop-object-column-right")
    assert markup.index("shop-feature-dialog") > markup.index("</div>\n\n  <dialog")
    assert "Share the Woodshed" not in markup
    assert "Open the Woodshed website" not in markup
    assert "Your Permanent Crown" not in markup
    assert "shop-share-card" not in markup
    assert "shop-donate-button" not in markup


def test_left_controls_preserve_rewards_crowns_definition_and_share() -> None:
    markup = shop_markup()
    controls = ["🌼", "👑", "🐐", "🗿", "↗"]
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


def test_right_controls_and_donation_are_unique() -> None:
    markup = shop_markup()
    controls = ["🧢", "🧃", "🚪", "🎨", "💝"]
    assert [markup.index(item) for item in controls] == sorted(markup.index(item) for item in controls)
    assert "Trombone Practice Tool" in markup
    assert "More Practice Tools" in markup
    assert "water bottles" in markup and "instrument cases" in markup
    assert "direct file upload" not in markup
    assert markup.count("venmo.com/u/jeffriley101") == 1
    assert markup.count('aria-label="Donate"') == 1


def test_single_dialog_and_keyboard_focus_behavior_are_wired() -> None:
    markup = shop_markup()
    javascript = JS.read_text(encoding="utf-8")
    assert markup.count("<dialog") == 1
    assert 'aria-labelledby="shop-dialog-title"' in markup
    for label in (
        "Open Crown Progress", "Open The GOAT Tracker", "Open Practice Definition",
        "Share Woodshed", "Open Clothing Shelf", "Open Gear Shelf",
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
    assert 'fetch(' not in shop_wiring


def test_share_uses_one_canonical_url_and_accessible_qr(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://woodshed.example/public/?profile=private#session")
    monkeypatch.setattr(main, "qr_data_uri", lambda value: captured.append(value) or "data:image/svg+xml;base64,SAFE")
    response = TestClient(main.app).get("/store")
    assert response.status_code == 200
    assert captured == ["https://woodshed.example/public/"]
    assert "profile=private" not in response.text and "#session" not in response.text
    assert "QR code for the public Woodshed Woodchuck website" in response.text
    javascript = JS.read_text(encoding="utf-8")
    assert 'navigator.clipboard.writeText(address)' in javascript
    assert 'Website address copied.' in javascript


def test_artist_email_is_configured_safely_or_has_fallback(monkeypatch) -> None:
    monkeypatch.setenv("ART_SUBMISSION_EMAIL", "art@example.org")
    configured = TestClient(main.app).get("/store").text
    assert "mailto:art@example.org?subject=Woodshed%20Woodchuck%20Artwork" in configured
    assert "Artwork email coming soon." not in configured

    monkeypatch.delenv("ART_SUBMISSION_EMAIL")
    missing = TestClient(main.app).get("/store").text
    assert "Artwork email coming soon." in missing
    assert "mailto:" not in missing

    monkeypatch.setenv("ART_SUBMISSION_EMAIL", "art@example.org?bcc=private@example.org")
    unsafe = TestClient(main.app).get("/store").text
    assert "Artwork email coming soon." in unsafe
    assert "bcc=" not in unsafe


def test_mobile_css_avoids_fixed_width_overflow() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "@media (max-width: 430px)" in css
    assert ".shop-page { width: 100%; min-width: 0; }" in css
    assert "max-width: calc(100vw - 1.5rem)" in css
    assert "width: min(100%, 920px)" in css
