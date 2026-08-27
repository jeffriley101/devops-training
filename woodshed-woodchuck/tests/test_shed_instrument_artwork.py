from pathlib import Path

import pytest

from app.instruments import (
    INSTRUMENT_OPTIONS,
    SHED_ARTWORK_BY_INSTRUMENT_KEY,
    canonical_instrument_key,
    shed_artwork_url,
)


ROOT = Path(__file__).resolve().parents[1]
SAX_ART = "/static/img/shed-cabin-new.png"
@pytest.mark.parametrize("instrument", INSTRUMENT_OPTIONS)
def test_every_current_instrument_uses_the_safe_shared_artwork(instrument):
    assert instrument in INSTRUMENT_OPTIONS
    assert shed_artwork_url(instrument) == SAX_ART


def test_shed_artwork_matching_is_case_insensitive_and_has_safe_fallback():
    assert shed_artwork_url("  tRuMpEt  ") == SAX_ART
    assert shed_artwork_url("PIANO / KEYBOARD") == SAX_ART
    assert shed_artwork_url(None) == SAX_ART
    assert shed_artwork_url("") == SAX_ART
    assert shed_artwork_url("Unknown Instrument") == SAX_ART


def test_selected_artwork_is_wired_only_to_the_shed():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    store = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    welcome = (ROOT / "templates/welcome.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")

    assert "shed_artwork_url(profile.instrument)" in main
    assert "shed_artwork_url=artwork_url" in main
    assert 'style="background-image: url(\'{{ shed_artwork_url }}\');"' in home
    assert 'src="{{ shed_artwork_url }}"' in home
    assert "background-position: center" in css
    assert "background-size: cover" in css
    assert "payload.shed_artwork_url" in account_js
    assert "scene.style.backgroundImage" in account_js
    assert "/static/img/shed-cabin-new.png" in css
    assert "/static/img/woodchuck-home.png" not in css
    assert "/static/img/shed-cabin-new.png" not in store
    assert "/static/img/shed-cabin-new.png" not in welcome


def test_future_artwork_mapping_has_one_authoritative_canonical_key_path():
    assert SHED_ARTWORK_BY_INSTRUMENT_KEY == {}
    assert {canonical_instrument_key(instrument) for instrument in INSTRUMENT_OPTIONS} == {
        "flute",
        "clarinet",
        "saxophone",
        "trumpet",
        "trombone",
        "tuba",
        "percussion",
        "drum-major",
        "color-guard",
        "violin",
        "guitar",
        "banjo",
        "piano-keyboard",
        "accordion",
        "harp",
        "hand-percussion",
        "auxiliary-percussion",
    }
    source = (ROOT / "app/instruments.py").read_text(encoding="utf-8")
    assert "woodchuck-trumpet.png" not in source
    assert "woodchuck-drum.png" not in source
    assert "woodchuck-guitar.png" not in source
    asset = ROOT / "static" / SAX_ART.removeprefix("/static/")
    assert asset.is_file() and asset.stat().st_size > 0
