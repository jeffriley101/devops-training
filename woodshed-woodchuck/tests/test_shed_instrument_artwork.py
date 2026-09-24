import hashlib
from pathlib import Path
import re

import pytest

from app.instruments import (
    INSTRUMENT_OPTIONS,
    SHED_ARTWORK_BY_INSTRUMENT_KEY,
    SHED_CHARACTER_BY_INSTRUMENT_KEY,
    canonical_instrument_key,
    shed_artwork_url,
    shed_character_url,
)
from test_world_entry import client, login


ROOT = Path(__file__).resolve().parents[1]
SAX_ART = "/static/img/shed-cabin-new.png"
CHARACTER_ART = {
    "Flute": "/static/img/woodchuck-flute.png",
    "Clarinet": "/static/img/woodchuck-clarinet.png",
    "Oboe": "/static/img/woodchuck-oboe.png",
    "Bassoon": "/static/img/woodchuck-bassoon.png",
    "Saxophone": "/static/img/woodchuck-saxophone.png",
    "Trumpet": "/static/img/woodchuck-trumpet.png",
    "French Horn": "/static/img/woodchuck-french-horn.png",
    "Trombone": "/static/img/woodchuck-trombone.png",
    "Baritone": "/static/img/woodchuck-baritone.png",
    "Tuba": "/static/img/woodchuck-tuba.png",
    "Percussion": "/static/img/woodchuck-percussion.png",
    "Violin": "/static/img/woodchuck-violin.png",
    "Guitar": "/static/img/woodchuck-guitar.png",
    "Banjo": "/static/img/woodchuck-banjo.png",
    "Piano / Keyboard": "/static/img/woodchuck-keys.png",
    "Vocals": "/static/img/woodchuck-vocals.png",
}
CHARACTER_FREE_CABIN_SHA256 = (
    "2c5f1fb36ede596e79db7a81e92cf4ab66170cf264ee6441de6f937e7e861603"
)
INSTRUMENT_ART = {
    "Flute": "/static/img/shed/instruments/flute.png",
    "Clarinet": "/static/img/shed/instruments/clarinet.png",
    "Trumpet": "/static/img/shed/instruments/trumpet.png",
    "Trombone": "/static/img/shed/instruments/trombone.png",
    "Percussion": "/static/img/shed/instruments/percussion.png",
}


@pytest.mark.parametrize(("instrument", "artwork"), INSTRUMENT_ART.items())
def test_approved_instrument_artwork_resolves_from_the_authoritative_mapping(
    instrument, artwork
):
    assert shed_artwork_url(instrument) == artwork
    asset = ROOT / "static" / artwork.removeprefix("/static/")
    assert asset.is_file() and asset.stat().st_size > 0


@pytest.mark.parametrize("instrument", ["Saxophone", "Tuba", "Vocals"])
def test_instruments_without_approved_artwork_keep_the_safe_fallback(instrument):
    assert shed_artwork_url(instrument) == SAX_ART


def test_shed_artwork_matching_is_case_insensitive_and_has_safe_fallback():
    assert shed_artwork_url("  tRuMpEt  ") == INSTRUMENT_ART["Trumpet"]
    assert shed_artwork_url("PIANO / KEYBOARD") == SAX_ART
    assert shed_artwork_url(None) == SAX_ART
    assert shed_artwork_url("") == SAX_ART
    assert shed_artwork_url("Unknown Instrument") == SAX_ART


def test_production_cabin_is_the_approved_character_free_replacement():
    cabin = ROOT / "static/img/shed-cabin-new.png"
    data = cabin.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[16:24] == bytes.fromhex("000003ad00000688")
    assert hashlib.sha256(data).hexdigest() == CHARACTER_FREE_CABIN_SHA256


@pytest.mark.parametrize(("instrument", "character"), CHARACTER_ART.items())
def test_approved_standalone_character_mapping(instrument, character):
    assert shed_character_url(instrument) == character
    asset = ROOT / "static" / character.removeprefix("/static/")
    assert asset.is_file() and asset.stat().st_size > 0
    header = asset.read_bytes()[:29]
    assert header.startswith(b"\x89PNG\r\n\x1a\n")
    assert header[24:26] == bytes((8, 6))


def test_character_fallback_never_uses_flattened_scene_artwork():
    flattened_scenes = {
        "/static/img/woodchuck-home.png",
        "/static/img/woodchuck-drum.png",
        *INSTRUMENT_ART.values(),
    }
    for instrument in (*INSTRUMENT_OPTIONS, None, "Unknown Instrument"):
        character = shed_character_url(instrument)
        assert character == CHARACTER_ART.get(instrument, CHARACTER_ART["Saxophone"])
        assert character not in flattened_scenes


@pytest.mark.parametrize(("instrument", "character"), CHARACTER_ART.items())
def test_home_renders_character_separately_from_fixed_cabin(
    client, instrument, character,
):
    login(client)
    result = client.patch('/account/appearance', json={
        'instrument': instrument, 'hoodie': 'blue', 'hat': 'green'})
    assert result.status_code == 200
    response = client.get('/home')

    assert response.status_code == 200
    character_src = re.search(
        r'class="character-art woodshed-character-art" data-student-woodchuck\s+src="([^"]+)"',
        response.text,
    )
    assert character_src is not None
    assert character_src.group(1) == character
    assert character_src.group(1) not in INSTRUMENT_ART.values()
    assert character_src.group(1) != "/static/img/shed-cabin-new.png"
    assert 'class="room-scene-art" src="/static/img/shed-cabin-new.png"' in response.text



def test_selected_artwork_is_wired_only_to_the_shed():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    store = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    welcome = (ROOT / "templates/welcome.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")

    assert "shed_character_url(profile.instrument)" in main
    assert "shed_cabin_background_url=shed_artwork_url(None)" in main
    assert "shed_character_url=character_url" in main
    assert 'src="{{ shed_cabin_background_url }}"' in home
    assert 'src="{{ shed_character_url }}"' in home
    assert 'src="{{ shed_artwork_url }}"' not in home
    scene_css = (ROOT / 'static/css/scene-hotspots.css').read_text()
    assert 'object-fit: contain' in scene_css
    assert 'aspect-ratio: var(--art-width) / var(--art-height)' in scene_css
    shared = (ROOT / "static/js/woodchuck-appearance.js").read_text()
    assert "[data-student-woodchuck]" in shared
    assert "data-student-woodchuck" in store
    assert "backgroundImage" not in shared
    assert "/static/img/shed-cabin-new.png" in css
    assert "/static/img/woodchuck-home.png" not in css
    assert "/static/img/shed-cabin-new.png" not in store
    assert "/static/img/shed-cabin-new.png" not in welcome


def test_future_artwork_mapping_has_one_authoritative_canonical_key_path():
    assert SHED_ARTWORK_BY_INSTRUMENT_KEY == {
        canonical_instrument_key(instrument): artwork
        for instrument, artwork in INSTRUMENT_ART.items()
    }
    assert {canonical_instrument_key(instrument) for instrument in INSTRUMENT_OPTIONS} == {
        "flute",
        "clarinet",
        "oboe",
        "bassoon",
        "saxophone",
        "trumpet",
        "french-horn",
        "trombone",
        "baritone",
        "tuba",
        "percussion",
        "violin",
        "guitar",
        "banjo",
        "piano-keyboard",
        "vocals",
    }
    assert {
        "flute", "clarinet", "saxophone", "trumpet", "trombone", "tuba",
        "percussion", "vocals", "oboe", "bassoon", "french-horn", "baritone",
    }.issubset({canonical_instrument_key(instrument) for instrument in INSTRUMENT_OPTIONS})
    source = (ROOT / "app/instruments.py").read_text(encoding="utf-8")
    assert "woodchuck-sax-prototype.png" not in source
    assert "woodchuck-trumpet-prototype.png" not in source
    assert "woodchuck-drum.png" not in source
    asset = ROOT / "static" / SAX_ART.removeprefix("/static/")
    assert asset.is_file() and asset.stat().st_size > 0


def test_character_mapping_contains_only_the_approved_production_assets():
    assert SHED_CHARACTER_BY_INSTRUMENT_KEY == {
        canonical_instrument_key(instrument): character
        for instrument, character in CHARACTER_ART.items()
    }
