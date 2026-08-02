from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def test_band_camp_heading_wraps_prominently_on_phone() -> None:
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    template = (ROOT / "templates/quest.html").read_text(encoding="utf-8")

    assert 'class="board-season-title"' in template
    assert 'class="letter-gap"' in template
    assert "@media (max-width: 640px)" in css
    assert ".board-season-title .letter-gap" in css
    assert "flex-basis: 100%" in css
    assert "font-size: 2.15rem" in css


def test_instrument_assets_are_cache_busted_and_failures_are_visible() -> None:
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")

    assert "/static/css/styles.css?v=65" in base
    assert "/static/manifest.webmanifest?v=4" in base
    assert "/static/js/instruments.js?v=3" in base
    assert "/static/js/app.js?v=30" in base
    assert "/static/js/account.js?v=12" in base
    assert 'id="change-instrument-feedback"' in home
    assert 'role="status"' in home
    assert 'feedback.classList.add("error-text")' in account_js
    assert "Check your connection and try again." in account_js
    assert 'method: "PATCH"' in account_js


def test_verifier_mailto_uses_percent_spaces_and_preserves_real_plus() -> None:
    javascript = (
        ROOT / "static/js/trusted-verifiers.js"
    ).read_text(encoding="utf-8")
    app_javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    template = (
        ROOT / "templates/trusted_verifiers.html"
    ).read_text(encoding="utf-8")

    assert 'params.set("subject", subject)' in javascript
    assert 'params.set("body", message)' in javascript
    assert '.replace(/\\+/g, "%20")' in javascript
    assert 'id="email-p-chart-btn"' not in (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    assert "?${params.toString()}" not in javascript
    assert "?${params.toString()}" not in app_javascript
    assert "trusted-verifiers.js?v=8" in template

    subject = "Woodshed Woodchuck Trusted Verifier Invitation"
    verification_url = "https://example.test/verify?token=a%2Bb%20c"
    body = f"Hello trusted adult + guest\n{verification_url}"
    query = urlencode({"subject": subject, "body": body}).replace("+", "%20")
    mailto = f"mailto:{quote('adult+helper@example.test')}?{query}"

    assert "+" not in urlsplit(mailto).query
    decoded = parse_qs(urlsplit(mailto).query)
    assert decoded["subject"] == [subject]
    assert decoded["body"] == [body]
    assert decoded["body"][0].splitlines()[1] == verification_url
    assert urlsplit(mailto).path == "adult%2Bhelper%40example.test"


def test_verifier_invitation_remains_plain_text_only() -> None:
    javascript = (
        ROOT / "static/js/trusted-verifiers.js"
    ).read_text(encoding="utf-8")

    assert '].join("\\n")' in javascript
    assert "text/html" not in javascript
