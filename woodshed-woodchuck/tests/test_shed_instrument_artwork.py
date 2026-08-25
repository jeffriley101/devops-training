from pathlib import Path

import pytest

from app.instruments import INSTRUMENT_OPTIONS, shed_artwork_url


ROOT = Path(__file__).resolve().parents[1]
SAX_ART = "/static/img/shed-cabin-new.png"
TRUMPET_ART = SAX_ART
DRUM_ART = SAX_ART
GUITAR_ART = SAX_ART


@pytest.mark.parametrize(
    ("instrument", "expected_url"),
    [
        ("Flute", SAX_ART),
        ("Clarinet", SAX_ART),
        ("Saxophone", SAX_ART),
        ("Accordion", SAX_ART),
        ("Trumpet", TRUMPET_ART),
        ("Trombone", TRUMPET_ART),
        ("Tuba", TRUMPET_ART),
        ("Percussion", DRUM_ART),
        ("Hand Percussion", DRUM_ART),
        ("Auxiliary Percussion", DRUM_ART),
        ("Color Guard", DRUM_ART),
        ("Drum Major", DRUM_ART),
        ("Harp", GUITAR_ART),
        ("Piano / Keyboard", GUITAR_ART),
        ("Banjo", GUITAR_ART),
        ("Guitar", GUITAR_ART),
        ("Violin", GUITAR_ART),
    ],
)
def test_every_canonical_instrument_selects_its_artwork(instrument, expected_url):
    assert instrument in INSTRUMENT_OPTIONS
    assert shed_artwork_url(instrument) == expected_url


def test_shed_artwork_matching_is_case_insensitive_and_has_safe_fallback():
    assert shed_artwork_url("  tRuMpEt  ") == TRUMPET_ART
    assert shed_artwork_url("PIANO / KEYBOARD") == GUITAR_ART
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


def test_all_shed_artwork_assets_exist():
    for artwork in (SAX_ART, TRUMPET_ART, DRUM_ART, GUITAR_ART):
        asset = ROOT / "static" / artwork.removeprefix("/static/")
        assert asset.is_file()
        assert asset.stat().st_size > 0
